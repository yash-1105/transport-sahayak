// Mirror a registered Suraksha Mitra volunteer into the Aggregator DPG's
// responder registry as a responder_facility_1.0 item (Facility Type
// SURAKSHA_MITRA, Data Origin "volunteer"). This is REGISTRATION mirroring
// only — a record of who registered and where they cover; nothing here
// dispatches, activates, or tracks the volunteer (Hard Rules 1/3/5).
//
// Fire-and-forget from the UI: always responds 200 with a `source`
// discriminator so a Signals outage can never surface as an error in the
// volunteer-save flow (the Supabase record is the source of truth; this is a
// best-effort mirror on top). Idempotent: upserts by Facility ID so a
// volunteer editing their registration updates the same item, never duplicates.

import { NextRequest, NextResponse } from "next/server";
import {
  aggregatorEnabled,
  createResponderItem,
  fetchAllResponderItems,
  itemFacilityId,
  updateResponderItem,
} from "@/lib/aggregator/client";

interface VolunteerPayload {
  userId: string;
  name: string;
  phone?: string;
  lat: number;
  lng: number;
  locationLabel?: string;
  coverageRadiusKm?: number;
  occupation?: string;
  firstAidTrained?: boolean;
  firstAidLevel?: string;
}

export async function POST(req: NextRequest) {
  const v = (await req.json().catch(() => null)) as VolunteerPayload | null;
  if (!v?.userId || typeof v.lat !== "number" || typeof v.lng !== "number") {
    return NextResponse.json({ source: "invalid", error: "userId + coordinates required" });
  }
  if (!aggregatorEnabled()) {
    return NextResponse.json({ source: "no_key" });
  }

  // Stable per-user Facility ID → idempotent upsert (edit re-publishes here).
  const facilityId = `mitra-${v.userId}`;

  // Only schema-declared properties (responder_facility_1.0 is
  // additionalProperties:false). Contact Number is a `private` field in the
  // contract; the UI additionally hides it from non-operators.
  const state: Record<string, unknown> = {
    "Facility ID": facilityId,
    "Facility Name": v.name?.trim() || "Suraksha Mitra volunteer",
    "Facility Type": "SURAKSHA_MITRA",
    ...(v.phone ? { "Contact Number": v.phone } : {}),
    "Data Origin": "volunteer",
    // REAL user registration — never sample data (Hard Rule 4 labelling is for
    // the synthetic seed layers, not real volunteer records).
    "Sample Data": false,
    "Attributes": {
      occupation: v.occupation ?? "",
      firstAidTrained: v.firstAidTrained === true,
      firstAidLevel: v.firstAidLevel ?? "",
      // Coverage is the volunteer's base point + a radius (km), not free text.
      coverageRadiusKm: typeof v.coverageRadiusKm === "number" ? v.coverageRadiusKm : 8,
      locationLabel: v.locationLabel ?? "",
    },
  };
  const locations = [
    { lat: v.lat, lng: v.lng, label: v.locationLabel || "Volunteer base location" },
  ];

  try {
    const items = await fetchAllResponderItems();
    const existing = items.find((it) => itemFacilityId(it) === facilityId);
    if (existing) {
      await updateResponderItem(existing.item_id, state, locations);
      return NextResponse.json({ source: "signals", itemId: existing.item_id, updated: true });
    }
    const itemId = await createResponderItem(state, locations);
    return NextResponse.json({ source: "signals", itemId, updated: false });
  } catch (e) {
    return NextResponse.json({
      source: "unavailable",
      detail: e instanceof Error ? e.message : "publish failed",
    });
  }
}
