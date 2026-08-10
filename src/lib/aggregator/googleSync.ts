// Google Places → Aggregator DPG ingestion.
//
// Google Places is ONLY an ingestion source in this architecture: this module
// discovers POIs along the corridor, normalizes them, and upserts them as
// responder_facility_1.0 items in the Aggregator DPG. The frontend never
// consumes a Google Places response directly — it reads the Aggregator.
//
// ToS discipline: we persist the minimum needed to serve the map/matching
// layers (place ID, name, coordinates, address, phone, facility type) and
// stamp every item with "Last Synced At"; SYNC_TTL_MS drives re-sync so
// stored content is refreshed rather than accreting stale. Live-only signals
// (open-now status) are deliberately never persisted.

import { CORRIDOR_POLYLINE, CORRIDOR_WAYPOINTS } from "@/lib/corridorWaypoints";
import { distanceToCorridorKm } from "@/lib/corridorGeometry";
import type { GooglePlaceType } from "@/lib/types";
import {
  PLACE_TYPE_TO_FACILITY_TYPE,
  createResponderItem,
  fetchAllResponderItems,
  updateResponderItem,
  aggregatorEnabled,
} from "./client";

const NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby";
const FIELD_MASK =
  "places.id,places.displayName,places.location,places.formattedAddress,places.nationalPhoneNumber";

export const SYNC_PLACE_TYPES: GooglePlaceType[] = [
  "hospital",
  "police",
  "car_repair",
  "pharmacy",
  "gas_station",
];

// Re-sync cadence. Also the honesty backstop for stored Places content —
// everything Google-sourced is refreshed (or re-confirmed) at this cadence.
export const SYNC_TTL_MS = 24 * 60 * 60 * 1000;

const SYNC_RADIUS_M = 8000; // Guwahati metro (city-scale); matches CORRIDOR_WAYPOINT_RADIUS_M

interface RawPlace {
  id: string;
  displayName?: { text?: string };
  location?: { latitude: number; longitude: number };
  formattedAddress?: string;
  nationalPhoneNumber?: string;
}

async function fetchPlacesForType(type: GooglePlaceType, serverKey: string): Promise<RawPlace[]> {
  const perWaypoint = await Promise.all(
    CORRIDOR_WAYPOINTS.map(async (wp) => {
      try {
        const res = await fetch(NEARBY_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": serverKey,
            "X-Goog-FieldMask": FIELD_MASK,
          },
          body: JSON.stringify({
            includedTypes: [type],
            maxResultCount: 20,
            locationRestriction: {
              circle: { center: { latitude: wp.lat, longitude: wp.lng }, radius: SYNC_RADIUS_M },
            },
          }),
          cache: "no-store",
        });
        if (!res.ok) return [];
        const data = (await res.json()) as { places?: RawPlace[] };
        return data.places ?? [];
      } catch {
        return [];
      }
    }),
  );
  const seen = new Set<string>();
  return perWaypoint.flat().filter((p) => {
    if (!p.id || seen.has(p.id)) return false;
    seen.add(p.id);
    return true;
  });
}

export interface SyncSummary {
  ran: boolean;
  reason?: string;
  created: number;
  updated: number;
  unchanged: number;
}

let syncInFlight: Promise<SyncSummary> | null = null;

/** Runs a full Places→Aggregator sync (deduped against in-flight runs). */
export function runGoogleSync(): Promise<SyncSummary> {
  if (syncInFlight) return syncInFlight;
  syncInFlight = doSync().finally(() => {
    syncInFlight = null;
  });
  return syncInFlight;
}

async function doSync(): Promise<SyncSummary> {
  const serverKey = process.env.GOOGLE_MAPS_SERVER_KEY;
  if (!serverKey) return { ran: false, reason: "no_google_key", created: 0, updated: 0, unchanged: 0 };
  if (!aggregatorEnabled()) return { ran: false, reason: "no_aggregator_key", created: 0, updated: 0, unchanged: 0 };

  const existing = await fetchAllResponderItems();
  const byFacilityId = new Map(
    existing.map((it) => [String(it.item_state["Facility ID"] ?? ""), it]),
  );

  const now = new Date().toISOString();
  let created = 0;
  let updated = 0;
  let unchanged = 0;

  for (const type of SYNC_PLACE_TYPES) {
    const places = await fetchPlacesForType(type, serverKey);
    for (const p of places) {
      const lat = p.location?.latitude ?? 0;
      const lng = p.location?.longitude ?? 0;
      if (!lat || !lng) continue;
      const facilityId = `gp-${p.id}`;
      const name = p.displayName?.text ?? "Unknown";
      const state: Record<string, unknown> = {
        "Facility ID": facilityId,
        "Facility Name": name,
        "Facility Type": PLACE_TYPE_TO_FACILITY_TYPE[type],
        "Capability Source": "unverified",
        "Data Origin": "google_places",
        "Place ID": p.id,
        "Sample Data": false,
        "Attributes": {
          ...(p.formattedAddress ? { address: p.formattedAddress } : {}),
          ...(p.nationalPhoneNumber ? { phone: p.nationalPhoneNumber } : {}),
          distanceToCorridorKm:
            Math.round(distanceToCorridorKm({ lat, lng }, CORRIDOR_POLYLINE) * 10) / 10,
        },
        "Last Synced At": now,
      };
      const locations = [{ lat, lng, label: name }];

      const prior = byFacilityId.get(facilityId);
      if (!prior) {
        await createResponderItem(state, locations);
        created += 1;
        continue;
      }
      const lastSynced = Date.parse(String(prior.item_state["Last Synced At"] ?? "")) || 0;
      const nameChanged = prior.item_state["Facility Name"] !== name;
      if (nameChanged || Date.now() - lastSynced > SYNC_TTL_MS) {
        await updateResponderItem(prior.item_id, state, locations);
        updated += 1;
      } else {
        unchanged += 1;
      }
    }
  }

  return { ran: true, created, updated, unchanged };
}

/** True when the newest Google-synced item is older than the TTL (or none exist). */
export function isSyncStale(items: Array<{ item_state: Record<string, unknown> }>): boolean {
  let newest = 0;
  for (const it of items) {
    if (it.item_state["Data Origin"] !== "google_places") continue;
    const t = Date.parse(String(it.item_state["Last Synced At"] ?? "")) || 0;
    if (t > newest) newest = t;
  }
  return Date.now() - newest > SYNC_TTL_MS;
}
