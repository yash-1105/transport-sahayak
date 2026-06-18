// Routes API — Compute Route Matrix (v2, traffic-aware).
// Computes N→1 drive times from hospital candidates to the incident location.
// Uses GOOGLE_MAPS_SERVER_KEY (never sent to browser).
// Per Google ToS: do not persist route responses; use only at request time.

import { NextRequest, NextResponse } from "next/server";

const MATRIX_URL =
  "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix";

// Only the fields we display.
const FIELD_MASK =
  "originIndex,destinationIndex,distanceMeters,duration,condition,status";

export interface MatrixOrigin {
  lat: number;
  lng: number;
}

export interface MatrixResult {
  originIndex: number;
  distanceMeters: number;
  durationSec: number; // parsed from Google's "Xs" string
}

// POST body: { origins: MatrixOrigin[], destination: { lat, lng } }
export async function POST(req: NextRequest) {
  const serverKey = process.env.GOOGLE_MAPS_SERVER_KEY;
  if (!serverKey) {
    // Caller must degrade gracefully (straight-line ranking fallback).
    return NextResponse.json({ results: [], source: "no_key" });
  }

  let body: { origins: MatrixOrigin[]; destination: { lat: number; lng: number } };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { origins, destination } = body;
  if (!Array.isArray(origins) || origins.length === 0 || !destination) {
    return NextResponse.json({ error: "origins[] and destination are required" }, { status: 400 });
  }
  if (origins.length > 25) {
    return NextResponse.json({ error: "Max 25 origins" }, { status: 400 });
  }

  const requestBody = {
    origins: origins.map((o) => ({
      waypoint: { location: { latLng: { latitude: o.lat, longitude: o.lng } } },
    })),
    destinations: [
      {
        waypoint: {
          location: { latLng: { latitude: destination.lat, longitude: destination.lng } },
        },
      },
    ],
    travelMode: "DRIVE",
    routingPreference: "TRAFFIC_AWARE",
  };

  let raw: Response;
  try {
    raw = await fetch(MATRIX_URL, {
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
    return NextResponse.json(
      { error: `Network error: ${String(err)}` },
      { status: 502 }
    );
  }

  if (!raw.ok) {
    const txt = await raw.text();
    return NextResponse.json(
      { error: `Routes API ${raw.status}: ${txt}` },
      { status: raw.status }
    );
  }

  // Response is NDJSON (one RouteMatrixElement per line) or a JSON array.
  const text = await raw.text();
  let elements: unknown[];
  try {
    const parsed = JSON.parse(text);
    elements = Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    elements = text
      .trim()
      .split("\n")
      .filter(Boolean)
      .flatMap((line) => {
        try {
          return [JSON.parse(line)];
        } catch {
          return [];
        }
      });
  }

  const results: MatrixResult[] = [];
  for (const el of elements) {
    const e = el as {
      originIndex?: number;
      condition?: string;
      distanceMeters?: number;
      duration?: string;
    };
    if (
      e.condition === "ROUTE_EXISTS" &&
      typeof e.originIndex === "number" &&
      typeof e.distanceMeters === "number" &&
      typeof e.duration === "string"
    ) {
      // duration is in the form "123s"
      const durationSec = parseInt(e.duration.replace("s", ""), 10);
      if (!isNaN(durationSec)) {
        results.push({ originIndex: e.originIndex, distanceMeters: e.distanceMeters, durationSec });
      }
    }
  }

  return NextResponse.json({ results, source: "google" });
}
