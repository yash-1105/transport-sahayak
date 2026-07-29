"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AdvancedMarker, useMap } from "@vis.gl/react-google-maps";
import { MarkerClusterer, type Renderer } from "@googlemaps/markerclusterer";

// A layer of map markers that clusters into category-coloured count badges via
// @googlemaps/markerclusterer. One clusterer per layer keeps every cluster badge
// a single category colour (matching the design's per-category count badges).
// Individual markers keep their rich custom pins + click-to-open-InfoWindow
// behaviour; the clusterer only hides/shows them as the map zooms.

type AdvMarker = google.maps.marker.AdvancedMarkerElement;

export interface ClusterItem {
  key: string;
  position: { lat: number; lng: number };
  title?: string;
  onClick?: () => void;
  pin: React.ReactNode;
}

// Cluster badge: min 26px circle, category colour, 2px white ring, white 11/700
// text — per the design handoff.
function makeRenderer(color: string): Renderer {
  return {
    render({ count, position }) {
      const size = Math.max(26, 26 + Math.min(18, Math.log2(count + 1) * 5));
      const div = document.createElement("div");
      div.style.cssText = [
        `width:${size}px`,
        `height:${size}px`,
        "border-radius:50%",
        `background:${color}`,
        "border:2px solid #fff",
        "box-shadow:0 2px 6px rgba(28,36,52,.3)",
        "display:flex",
        "align-items:center",
        "justify-content:center",
        "color:#fff",
        "font-size:11px",
        "font-weight:700",
        "font-family:'IBM Plex Sans',sans-serif",
      ].join(";");
      div.textContent = String(count);
      return new google.maps.marker.AdvancedMarkerElement({
        position,
        content: div,
        zIndex: 1000 + count,
      });
    },
  };
}

export default function ClusteredLayer({ items, color }: { items: ClusterItem[]; color: string }) {
  const map = useMap();
  const [markers, setMarkers] = useState<Record<string, AdvMarker>>({});
  const clusterer = useRef<MarkerClusterer | null>(null);

  useEffect(() => {
    if (!map) return;
    clusterer.current = new MarkerClusterer({ map, renderer: makeRenderer(color) });
    return () => {
      clusterer.current?.clearMarkers();
      clusterer.current?.setMap(null);
      clusterer.current = null;
    };
  }, [map, color]);

  useEffect(() => {
    const c = clusterer.current;
    if (!c) return;
    c.clearMarkers();
    c.addMarkers(Object.values(markers));
  }, [markers]);

  const setRef = useCallback((marker: AdvMarker | null, key: string) => {
    setMarkers((prev) => {
      if (marker) {
        if (prev[key]) return prev;
        return { ...prev, [key]: marker };
      }
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }, []);

  // Stable ref callback per marker key. An inline `ref={m => setRef(m, key)}`
  // changes identity every render, so React would detach (ref(null)) then
  // reattach (ref(marker)) on every render — each toggling `markers` state and
  // triggering another render: an infinite update loop. Caching one callback
  // per key keeps ref identity stable, so React only fires it on real
  // mount/unmount.
  const refCbs = useRef(new Map<string, (m: AdvMarker | null) => void>());
  const getRef = useCallback(
    (key: string) => {
      const map = refCbs.current;
      let cb = map.get(key);
      if (!cb) {
        cb = (m) => setRef(m, key);
        map.set(key, cb);
      }
      return cb;
    },
    [setRef]
  );

  return (
    <>
      {items.map((it) => (
        <AdvancedMarker
          key={it.key}
          position={it.position}
          title={it.title}
          onClick={it.onClick}
          ref={getRef(it.key)}
        >
          {it.pin}
        </AdvancedMarker>
      ))}
    </>
  );
}
