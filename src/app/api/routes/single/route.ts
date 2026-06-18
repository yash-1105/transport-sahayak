// Routes API — Compute Routes (v2, traffic-aware).
// Returns a single driving route (distance, duration, encoded polyline).
// Used for the #1 hospital and nearest police station route overlays.
// GOOGLE_MAPS_SERVER_KEY only — never sent to the browser.

import { NextRequest, NextResponse } from "next/server";

const ROUTES_URL =
  "https://routes.googleapis.com/directions/v2:computeRoutes";

const FIELD_MASK =
  "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline";

// ── Polyline decoder ──────────────────────────────────────────────────────────

function decodePolyline(encoded: string): [number, number][] {
  const coords: [number, number][] = [];
  let lat = 0;
  let lng = 0;
  let i = 0;
  while (i < encoded.length) {
    let b: number;
    let shift = 0;
    let result = 0;
    do {
      b = encoded.charCodeAt(i++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lat += result & 1 ? ~(result >> 1) : result >> 1;
    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(i++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lng += result & 1 ? ~(result >> 1) : result >> 1;
    coords.push([lat / 1e5, lng / 1e5]);
  }
  return coords;
}

// POST body: { origin: { lat, lng }, destination: { lat, lng } }
export async function POST(req: NextRequest) {
  const serverKey = process.env.GOOGLE_MAPS_SERVER_KEY;
  if (!serverKey) {
    return NextResponse.json({ route: null, source: "no_key" });
  }

  let body: { origin: { lat: number; lng: number }; destination: { lat: number; lng: number } };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { origin, destination } = body;
  if (!origin || !destination) {
    return NextResponse.json({ error: "origin and destination are required" }, { status: 400 });
  }

  const requestBody = {
    origin: {
      location: { latLng: { latitude: origin.lat, longitude: origin.lng } },
    },
    destination: {
      location: { latLng: { latitude: destination.lat, longitude: destination.lng } },
    },
    travelMode: "DRIVE",
    routingPreference: "TRAFFIC_AWARE",
    computeAlternativeRoutes: false,
  };

  let raw: Response;
  try {
    raw = await fetch(ROUTES_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": serverKey,
        "X-Goog-FieldMask": FIELD_MASK,
      },
      body: JSON.stringify(requestBody),
      cache: "no-store",
    });
  } catch (err) {
    return NextResponse.json({ error: `Network error: ${String(err)}` }, { status: 502 });
  }

  if (!raw.ok) {
    const txt = await raw.text();
    return NextResponse.json(
      { error: `Routes API ${raw.status}: ${txt}` },
      { status: raw.status }
    );
  }

  const data = await raw.json();
  const route = data.routes?.[0];
  if (!route) {
    return NextResponse.json({ route: null, source: "google" });
  }

  const durationSec = parseInt(String(route.duration ?? "0s").replace("s", ""), 10);
  const coords = route.polyline?.encodedPolyline
    ? decodePolyline(route.polyline.encodedPolyline)
    : [];

  return NextResponse.json({
    route: {
      distanceMeters: route.distanceMeters ?? 0,
      durationSec,
      coords, // [lat, lng][] pairs
    },
    source: "google",
  });
}
