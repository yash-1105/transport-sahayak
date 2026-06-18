import { NextRequest, NextResponse } from "next/server";

const NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby";

// Cost control: only request the fields we render.
// displayName + location = Basic SKU; formattedAddress + currentOpeningHours = Advanced SKU.
const FIELD_MASK =
  "places.id,places.displayName,places.location,places.formattedAddress,places.currentOpeningHours";

const ALLOWED_TYPES = new Set([
  "hospital",
  "police",
  "car_repair",
  "pharmacy",
  "gas_station",
]);

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const type = searchParams.get("type") ?? "";
  const lat = parseFloat(searchParams.get("lat") ?? "26.14");
  const lng = parseFloat(searchParams.get("lng") ?? "91.74");
  const radius = parseFloat(searchParams.get("radius") ?? "50000");

  if (!ALLOWED_TYPES.has(type)) {
    return NextResponse.json({ error: `Invalid type: ${type}` }, { status: 400 });
  }

  const serverKey = process.env.GOOGLE_MAPS_SERVER_KEY;
  if (!serverKey) {
    // Return empty list rather than crashing — map renders synthetic fallback.
    return NextResponse.json({ places: [], source: "no_key" });
  }

  let raw: Response;
  try {
    raw = await fetch(NEARBY_URL, {
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
          circle: {
            center: { latitude: lat, longitude: lng },
            radius,
          },
        },
      }),
      // Do not cache — Places detail must not be persisted beyond session (ToS §3.2.4)
      cache: "no-store",
    });
  } catch (err) {
    return NextResponse.json(
      { error: `Network error calling Places API: ${String(err)}` },
      { status: 502 }
    );
  }

  if (!raw.ok) {
    const body = await raw.text();
    return NextResponse.json(
      { error: `Places API ${raw.status}: ${body}` },
      { status: raw.status }
    );
  }

  const data = await raw.json();

  // Normalise to our GooglePlace shape.
  // Only place.id (an opaque identifier) is safe to surface — no detail fields persisted.
  const places = (data.places ?? []).map(
    (p: {
      id: string;
      displayName?: { text?: string };
      location?: { latitude: number; longitude: number };
      formattedAddress?: string;
      currentOpeningHours?: { openNow?: boolean };
    }) => ({
      id: p.id,
      name: p.displayName?.text ?? "Unknown",
      lat: p.location?.latitude ?? 0,
      lng: p.location?.longitude ?? 0,
      address: p.formattedAddress ?? "",
      isOpen: p.currentOpeningHours?.openNow ?? null,
      placeType: type,
    })
  );

  return NextResponse.json({ places, source: "google" });
}
