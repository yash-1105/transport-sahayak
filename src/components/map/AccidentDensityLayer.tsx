"use client";

import { useEffect, useRef } from "react";
import { useMap } from "@vis.gl/react-google-maps";
import type { DbAccident } from "@/lib/types";
import { C } from "@/lib/design";

// Aggregated, Snapchat-map-style accident-DENSITY heat layer for citizens/guests.
// It NEVER exposes individual accident reports — no markers, no descriptions, no
// per-report popups. It grid-buckets reported accidents into coarse geographic
// cells and draws one translucent google.maps.Circle per cell, warm-coloured and
// sized by how many reports fall in that cell. Managed imperatively via useMap()
// (there is no <Circle> in @vis.gl), same pattern as ClusteredLayer.
//
// Privacy: only lat/lng/count are used here; description/mode/flags are ignored.
// (The raw rows still arrive over /api/accidents because auth in this app is
// client-only — see MapView's role branch + CLAUDE.md for that caveat.)

// ~0.12° ≈ ~13 km cells — coarse enough that no single report is pinpointable,
// fine enough that a real hotspot (e.g. the Noida cluster) stands out.
const CELL_DEG = 0.12;

type Bucket = { color: string; fillOpacity: number };

// Warm density scale: low → saffron/yellow, medium → orange, high → red.
// Hotter zones are more opaque and drawn on top.
function bucketFor(count: number): Bucket {
  if (count >= 6) return { color: C.red, fillOpacity: 0.3 };      // high
  if (count >= 3) return { color: "#EA580C", fillOpacity: 0.26 }; // medium
  return { color: C.saffron, fillOpacity: 0.22 };                 // low (1–2)
}

// Radius grows with count so denser areas read as bigger blobs (~5–14 km, capped).
function radiusMetersFor(count: number): number {
  return Math.min(14000, Math.max(5000, 4500 + count * 1500));
}

interface Cell {
  lat: number;
  lng: number;
  count: number;
}

export default function AccidentDensityLayer({
  accidents,
  onZoneClick,
}: {
  accidents: DbAccident[];
  onZoneClick: (position: { lat: number; lng: number }, count: number) => void;
}) {
  const map = useMap();
  const circlesRef = useRef<google.maps.Circle[]>([]);
  const listenersRef = useRef<google.maps.MapsEventListener[]>([]);

  useEffect(() => {
    if (!map) return;

    // Grid-bucket into cells.
    const cells = new Map<string, Cell>();
    for (const a of accidents) {
      // B5 de-dup: operator-ignored duplicates never inflate the public zones.
      if (a.review_status === "ignored") continue;
      if (typeof a.lat !== "number" || typeof a.lng !== "number") continue;
      const gi = Math.round(a.lat / CELL_DEG);
      const gj = Math.round(a.lng / CELL_DEG);
      const key = `${gi}:${gj}`;
      const existing = cells.get(key);
      if (existing) existing.count += 1;
      else cells.set(key, { lat: gi * CELL_DEG, lng: gj * CELL_DEG, count: 1 });
    }

    // One translucent circle per non-empty cell.
    for (const cell of cells.values()) {
      const b = bucketFor(cell.count);
      const circle = new google.maps.Circle({
        map,
        center: { lat: cell.lat, lng: cell.lng },
        radius: radiusMetersFor(cell.count),
        strokeColor: b.color,
        strokeOpacity: 0.35,
        strokeWeight: 1,
        fillColor: b.color,
        fillOpacity: b.fillOpacity,
        clickable: true,
        zIndex: 10 + cell.count, // hotter/denser on top
      });
      const listener = circle.addListener("click", () =>
        onZoneClick({ lat: cell.lat, lng: cell.lng }, cell.count)
      );
      circlesRef.current.push(circle);
      listenersRef.current.push(listener);
    }

    return () => {
      listenersRef.current.forEach((l) => l.remove());
      listenersRef.current = [];
      circlesRef.current.forEach((c) => c.setMap(null));
      circlesRef.current = [];
    };
  }, [map, accidents, onZoneClick]);

  return null;
}
