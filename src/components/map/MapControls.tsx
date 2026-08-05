"use client";

import { useMap } from "@vis.gl/react-google-maps";
import { C, SHADOW } from "@/lib/design";

// Top-right map control stack: zoom in / zoom out / reset-north. White rounded
// card of three 36×34 buttons separated by hairlines (design handoff). Wired to
// the live map instance so zoom/heading actually work.
export default function MapControls() {
  const map = useMap();

  const zoom = (delta: number) => {
    if (!map) return;
    const z = map.getZoom() ?? 8;
    map.setZoom(z + delta);
  };
  const resetNorth = () => {
    if (!map) return;
    map.setHeading?.(0);
    map.setTilt?.(0);
  };

  const btn: React.CSSProperties = {
    width: 36,
    height: 34,
    border: "none",
    background: "#fff",
    cursor: "pointer",
    color: C.body,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };
  const hairline: React.CSSProperties = { height: 1, background: "#EDEAE2" };

  return (
    <div
      className="absolute right-4 top-4 z-[500] flex flex-col overflow-hidden"
      style={{ background: "#fff", border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SHADOW.mapControl }}
    >
      {/* Zoom +/− are hidden on mobile — pinch-to-zoom is the norm on phones, so
          the control shrinks to just the reset-north (N) button there. The full
          +/−/N stack stays on ≥sm (desktop). */}
      <div className="hidden sm:flex sm:flex-col">
        <button style={{ ...btn, fontSize: 17 }} onClick={() => zoom(1)} aria-label="Zoom in">+</button>
        <div style={hairline} />
        <button style={{ ...btn, fontSize: 17 }} onClick={() => zoom(-1)} aria-label="Zoom out">−</button>
        <div style={hairline} />
      </div>
      <button style={{ ...btn, fontSize: 12, fontWeight: 600, color: C.blue }} onClick={resetNorth} aria-label="Reset north">N</button>
    </div>
  );
}
