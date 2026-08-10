// Ambulance stations layer: try Google Places (New) first, fall back to synthetic sample data.
// Uses the server-side Google key (GOOGLE_MAPS_SERVER_KEY) — never the browser key.
// NOTE: India Places coverage for "ambulance station" is sparse — the synthetic fallback is
// expected to trigger often. Sample markers MUST stay labelled sample:true with the amber banner.

import { CORRIDOR_POLYLINE } from "@/lib/corridorWaypoints";
import { isWithinCorridor } from "@/lib/corridorGeometry";

const CORRIDOR_MAX_KM = 15;

export type AmbulancePoint = {
  name: string;
  lat: number;
  lng: number;
  sample: boolean; // true = synthetic, render amber "sample" banner
};

// Synthetic 108-network posts across the Guwahati metro (sample data — not official).
const SYNTHETIC: AmbulancePoint[] = [
  { name: "Sample 108 Post — Jorabat",       lat: 26.099, lng: 91.862, sample: true },
  { name: "Sample 108 Post — Khanapara",     lat: 26.121, lng: 91.821, sample: true },
  { name: "Sample 108 Post — Ganeshguri",    lat: 26.154, lng: 91.782, sample: true },
  { name: "Sample 108 Post — Ulubari",       lat: 26.168, lng: 91.754, sample: true },
  { name: "Sample 108 Post — Paltan Bazaar", lat: 26.181, lng: 91.751, sample: true },
  { name: "Sample 108 Post — Maligaon",      lat: 26.150, lng: 91.696, sample: true },
  { name: "Sample 108 Post — Jalukbari",     lat: 26.143, lng: 91.644, sample: true },
  { name: "Sample 108 Post — Gorchuk",       lat: 26.105, lng: 91.713, sample: true },
];

export async function getAmbulanceStations(
  lat: number,
  lng: number,
  radiusM = 15000
): Promise<AmbulancePoint[]> {
  const key = process.env.GOOGLE_MAPS_SERVER_KEY;
  if (!key) return SYNTHETIC;

  try {
    const res = await fetch("https://places.googleapis.com/v1/places:searchNearby", {
      method: "POST",
      cache: "no-store", // Google ToS: do not persist Places data
      headers: {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.displayName,places.location",
      },
      body: JSON.stringify({
        includedTypes: ["ambulance_service"],
        locationRestriction: {
          circle: { center: { latitude: lat, longitude: lng }, radius: radiusM },
        },
        maxResultCount: 10,
      }),
    });
    const data = await res.json();
    const places: AmbulancePoint[] = (
      (data.places ?? []) as {
        displayName?: { text?: string };
        location?: { latitude?: number; longitude?: number };
      }[]
    )
      .map((p) => ({
        name: p.displayName?.text ?? "Ambulance service",
        lat: p.location?.latitude ?? 0,
        lng: p.location?.longitude ?? 0,
        sample: false,
      }))
      // Keep only stations within the corridor band — same rule as all other POI types.
      .filter((p) => isWithinCorridor({ lat: p.lat, lng: p.lng }, CORRIDOR_POLYLINE, CORRIDOR_MAX_KM));

    return places.length ? places : SYNTHETIC;
  } catch {
    return SYNTHETIC;
  }
}
