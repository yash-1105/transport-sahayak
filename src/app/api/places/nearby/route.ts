import { NextRequest, NextResponse } from "next/server";
import { CORRIDOR_POLYLINE } from "@/lib/corridorWaypoints";
import { distanceToCorridorKm } from "@/lib/corridorGeometry";

const NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby";

// Cost control: only request the fields we render.
// displayName + location = Basic SKU; formattedAddress + currentOpeningHours = Advanced SKU;
// nationalPhoneNumber = Enterprise SKU (real listed number, only when Google has it).
const FIELD_MASK =
  "places.id,places.displayName,places.location,places.formattedAddress,places.currentOpeningHours,places.nationalPhoneNumber";

const ALLOWED_TYPES = new Set([
  "hospital",
  "police",
  "car_repair",
  "pharmacy",
  "gas_station",
]);

// POIs beyond this distance from the expressway centreline are dropped.
const CORRIDOR_MAX_KM = 15;

type RawPlace = {
  id: string;
  displayName?: { text?: string };
  location?: { latitude: number; longitude: number };
  formattedAddress?: string;
  currentOpeningHours?: { openNow?: boolean };
  nationalPhoneNumber?: string;
};

// Specialty-clinic name fragments that indicate a non-emergency hospital.
// If a place name matches any of these (whole word, case-insensitive) it is
// excluded from hospital matching results.  Never applied to map-display calls.
const SPECIALTY_EXCLUDE_RE =
  /\b(eye|ophthal|dental|teeth|skin|derma|hair|ivf|fertility|cosmetic|vision|maternity)\b/i;

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const type = searchParams.get("type") ?? "";

  if (!ALLOWED_TYPES.has(type)) {
    return NextResponse.json({ error: `Invalid type: ${type}` }, { status: 400 });
  }

  // for_matching=1 → called by MatchingPanel for emergency hospital selection.
  //   • Skips the corridor proximity filter (we want the nearest hospital to the
  //     incident, not just hospitals hugging the highway).
  //   • Filters out obvious specialty clinics by name.
  const forMatching = searchParams.get("for_matching") === "1" && type === "hospital";

  // Parse waypoints array — corridor multi-point search.
  // Falls back to a single point (legacy or incident-pinned search).
  const waypointsRaw = searchParams.get("waypoints");
  const radius = parseFloat(searchParams.get("radius") ?? "15000");

  let waypoints: { lat: number; lng: number }[];
  if (waypointsRaw) {
    try {
      waypoints = JSON.parse(waypointsRaw);
    } catch {
      return NextResponse.json({ error: "Invalid waypoints JSON" }, { status: 400 });
    }
  } else {
    // Single-point fallback — default to corridor midpoint.
    const lat = parseFloat(searchParams.get("lat") ?? "29.6");
    const lng = parseFloat(searchParams.get("lng") ?? "77.6");
    waypoints = [{ lat, lng }];
  }

  const serverKey = process.env.GOOGLE_MAPS_SERVER_KEY;
  if (!serverKey) {
    return NextResponse.json({ places: [], source: "no_key" });
  }

  // Fetch Google Places per waypoint in parallel, then merge + dedup by place ID.
  // Places detail must not be persisted beyond the response (Google ToS §3.2.4).
  async function fetchWaypoint(wp: { lat: number; lng: number }): Promise<RawPlace[]> {
    try {
      const res = await fetch(NEARBY_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Goog-Api-Key": serverKey!,
          "X-Goog-FieldMask": FIELD_MASK,
        },
        body: JSON.stringify({
          includedTypes: [type],
          maxResultCount: 20,
          locationRestriction: {
            circle: { center: { latitude: wp.lat, longitude: wp.lng }, radius },
          },
        }),
        cache: "no-store",
      });
      if (!res.ok) return [];
      const data = await res.json();
      return (data.places ?? []) as RawPlace[];
    } catch {
      return [];
    }
  }

  const allRaw = (await Promise.all(waypoints.map(fetchWaypoint))).flat();

  // 1. Deduplicate by place ID — a location near two waypoints is one marker.
  const seen = new Set<string>();
  const deduped = allRaw.filter((p) => {
    if (!p.id || seen.has(p.id)) return false;
    seen.add(p.id);
    return true;
  });

  // 2. Map each place: compute corridor distance, apply name filter for matching calls.
  // 3. Corridor filter: for map display, drop anything >CORRIDOR_MAX_KM from centreline.
  //    For matching calls (forMatching=true) skip this filter — the nearest hospital
  //    to the incident may be off the expressway and we must not hide it.
  // 4. Sort by corridor distance (map display) or leave in Google's relevance order (matching).
  const mapped = deduped
    .map((p) => {
      const lat = p.location?.latitude ?? 0;
      const lng = p.location?.longitude ?? 0;
      const distKm = distanceToCorridorKm({ lat, lng }, CORRIDOR_POLYLINE);
      return {
        id: p.id,
        name: p.displayName?.text ?? "Unknown",
        lat,
        lng,
        address: p.formattedAddress ?? "",
        isOpen: p.currentOpeningHours?.openNow ?? null,
        phone: p.nationalPhoneNumber ?? null,
        placeType: type,
        distanceToCorridorKm: Math.round(distKm * 10) / 10,
      };
    });

  const places = (
    forMatching
      ? mapped.filter((p) => !SPECIALTY_EXCLUDE_RE.test(p.name))
      : mapped
          .filter((p) => p.distanceToCorridorKm <= CORRIDOR_MAX_KM)
          .sort((a, b) => a.distanceToCorridorKm - b.distanceToCorridorKm)
  );

  return NextResponse.json({ places, source: "google" });
}
