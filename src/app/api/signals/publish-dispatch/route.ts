// Mirror a dispatch notification record to Signals as a `dispatch` action
// (control_room → responder). The action's existence IS the notification
// record — its status stays at Signals' initial "created" and is never
// advanced, because any responder-side status update would fabricate an
// acknowledgement (hard rule 5: notification record only).
//
// This is the "Signals triggers an interaction with the Aggregator" edge of
// the architecture: the action's target is a responder item OWNED BY THE
// AGGREGATOR, resolved live by Facility ID (curated ids like "hosp-001",
// Google-synced ids like "gp-<placeId>"). Every hospital the matching flow
// can select exists in the Aggregator by construction, since matching reads
// its candidates from the Aggregator in the first place.

import { NextRequest, NextResponse } from "next/server";
import {
  SIGNALS_INCIDENT_DOMAIN,
  SIGNALS_INCIDENT_TYPE,
  SIGNALS_NETWORK,
  SIGNALS_RESPONDER_DOMAIN,
  SIGNALS_RESPONDER_TYPE,
  dispatchToSnapshot,
  signalsFetch,
} from "@/lib/signalsClient";
import { fetchAllResponderItems, itemFacilityId } from "@/lib/aggregator/client";
import type { DispatchRecord } from "@/lib/types";

// Facility ID → Aggregator item_id, cached briefly (the registry is written
// only by bootstrap/sync, so short-lived staleness is harmless; a cache miss
// forces a refresh before failing).
const TARGET_CACHE_TTL_MS = 5 * 60 * 1000;
let targetCache: { map: Map<string, string>; at: number } | null = null;

async function resolveTargetItemId(facilityId: string): Promise<string | null> {
  const fresh = targetCache && Date.now() - targetCache.at < TARGET_CACHE_TTL_MS;
  if (!fresh || !targetCache!.map.has(facilityId)) {
    const items = await fetchAllResponderItems();
    targetCache = {
      map: new Map(items.map((it) => [itemFacilityId(it), it.item_id])),
      at: Date.now(),
    };
  }
  return targetCache!.map.get(facilityId) ?? null;
}

export async function POST(req: NextRequest) {
  const { itemId, dispatch, assessedSeverity, googlePlaceId } = (await req.json()) as {
    itemId: string;
    dispatch: DispatchRecord;
    assessedSeverity?: string | null;
    googlePlaceId?: string | null;
  };
  if (!itemId || !dispatch?.entityId) {
    return NextResponse.json({ source: "invalid", error: "itemId and dispatch required" });
  }

  let targetItemId: string | null;
  try {
    targetItemId = await resolveTargetItemId(dispatch.entityId);
  } catch {
    return NextResponse.json({ source: "unavailable", detail: "aggregator unreachable" });
  }
  if (!targetItemId) {
    return NextResponse.json({
      source: "no_target",
      detail: `facility ${dispatch.entityId} not found in the Aggregator registry`,
    });
  }

  const result = await signalsFetch<{
    results: Array<{ status: string; action_id?: string; action_status?: string; error?: string }>;
  }>("/api/v1/action/perform", {
    method: "POST",
    body: JSON.stringify([
      {
        action_type: "dispatch",
        source_item: {
          item_network: SIGNALS_NETWORK,
          item_domain: SIGNALS_INCIDENT_DOMAIN,
          item_type: SIGNALS_INCIDENT_TYPE,
          item_id: itemId,
        },
        target_item: {
          item_network: SIGNALS_NETWORK,
          item_domain: SIGNALS_RESPONDER_DOMAIN,
          item_type: SIGNALS_RESPONDER_TYPE,
          item_id: targetItemId,
          item_instance_url: process.env.SIGNALS_API_URL ?? "http://localhost:2742",
        },
        requirements_snapshot: dispatchToSnapshot(dispatch, assessedSeverity, googlePlaceId ?? null),
      },
    ]),
  });

  if (!result.ok) {
    return NextResponse.json({ source: result.reason, detail: result.detail ?? null });
  }
  const first = result.data.results?.[0];
  if (first?.status !== "success") {
    return NextResponse.json({ source: "unavailable", detail: first?.error ?? "action rejected" });
  }
  return NextResponse.json({ source: "signals", actionId: first.action_id });
}
