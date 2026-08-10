"use client";
import React, { useEffect, useState } from "react";
import { APIProvider, Map, AdvancedMarker, useMap } from "@vis.gl/react-google-maps";
import { C, RADIUS } from "@/lib/design";
import { CORRIDOR_CENTER } from "@/lib/corridorWaypoints";
import { reverseGeocode } from "@/lib/geocode";

// Translucent coverage circle drawn around the base point (imperative
// google.maps.Circle via useMap — there is no <Circle> in @vis.gl, same
// pattern as AccidentDensityLayer). Shows the volunteer their notification zone.
function CoverageCircle({ center, radiusKm }: { center: { lat: number; lng: number }; radiusKm: number }) {
  const map = useMap();
  const { lat, lng } = center;
  useEffect(() => {
    if (!map) return;
    const circle = new google.maps.Circle({
      map,
      center: { lat, lng },
      radius: radiusKm * 1000,
      strokeColor: C.saffron,
      strokeOpacity: 0.7,
      strokeWeight: 1.5,
      fillColor: C.saffron,
      fillOpacity: 0.12,
      clickable: false,
    });
    return () => circle.setMap(null);
  }, [map, lat, lng, radiusKm]);
  return null;
}

// Compact embedded map picker used by the Suraksha Mitra form to set a base
// location by tapping / dragging a pin. Reuses the same @vis.gl map stack and
// DEMO_MAP_ID as the main map. Rendered ABOVE the Sheet (z > 2100). On confirm
// it reverse-geocodes a human label, mirroring the incident location capture.
// Draws the coverage-radius circle so the volunteer sees their zone.
export default function LocationPicker({
  initial,
  radiusKm,
  showHindi,
  onCancel,
  onConfirm,
}: {
  initial: { lat: number; lng: number } | null;
  radiusKm: number;
  showHindi: boolean;
  onCancel: () => void;
  onConfirm: (point: { lat: number; lng: number }, label: string) => void;
}) {
  const browserKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY ?? "";
  const [point, setPoint] = useState<{ lat: number; lng: number }>(initial ?? CORRIDOR_CENTER);
  const [busy, setBusy] = useState(false);
  // Zoom so a ~radiusKm circle roughly fills the frame (8km ≈ zoom 11).
  const initialZoom = radiusKm >= 10 ? 10 : radiusKm >= 8 ? 11 : 11;

  async function confirm() {
    setBusy(true);
    let label = `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
    try {
      label = await reverseGeocode(point.lat, point.lng);
    } catch {
      // keep coordinate label
    }
    onConfirm(point, label);
  }

  return (
    <>
      <div className="fixed inset-0 z-[2299]" style={{ background: "rgba(14,26,47,.5)" }} onClick={busy ? undefined : onCancel} />
      <div
        className="fixed z-[2300] flex flex-col bg-white"
        style={{
          left: "50%",
          top: "50%",
          transform: "translate(-50%,-50%)",
          width: "min(560px, 94vw)",
          maxHeight: "88vh",
          borderRadius: RADIUS.sheet,
          overflow: "hidden",
          boxShadow: "0 20px 60px rgba(14,26,47,.4)",
        }}
      >
        <div className="flex items-center" style={{ gap: 8, padding: "13px 18px", borderBottom: `1px solid ${C.hairline}` }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: C.ink }}>Set your location</span>
          {showHindi && <span style={{ fontSize: 12.5, color: C.muted, fontWeight: 500 }}> · अपना स्थान चुनें</span>}
        </div>

        <div style={{ padding: "10px 18px 0", fontSize: 12, color: C.secondary }}>
          Tap the map or drag the pin to your base point — the circle is your coverage zone.
          {showHindi && <span style={{ display: "block", color: C.muted, marginTop: 1 }}>नक्शे पर टैप करें या पिन खींचें — घेरा आपका कवरेज क्षेत्र है।</span>}
        </div>

        <div style={{ position: "relative", height: "min(48vh, 380px)", margin: "10px 18px 0", borderRadius: RADIUS.input, overflow: "hidden", border: `1px solid ${C.border}` }}>
          <APIProvider apiKey={browserKey}>
            <Map
              mapId="DEMO_MAP_ID"
              defaultCenter={point}
              defaultZoom={initialZoom}
              gestureHandling="greedy"
              disableDefaultUI
              className="absolute inset-0 w-full h-full"
              draggableCursor="crosshair"
              onClick={(e) => {
                if (e.detail.latLng) setPoint({ lat: e.detail.latLng.lat, lng: e.detail.latLng.lng });
              }}
            >
              <CoverageCircle center={point} radiusKm={radiusKm} />
              <AdvancedMarker
                position={point}
                draggable
                onDragEnd={(e) => {
                  if (e.latLng) setPoint({ lat: e.latLng.lat(), lng: e.latLng.lng() });
                }}
              >
                <PickerPin />
              </AdvancedMarker>
            </Map>
          </APIProvider>
        </div>

        <div style={{ padding: "8px 18px 4px", fontSize: 11.5, color: C.muted }}>
          {point.lat.toFixed(5)}, {point.lng.toFixed(5)} · {radiusKm} km coverage zone
        </div>

        <div className="flex" style={{ gap: 10, padding: "8px 18px 16px" }}>
          <button
            type="button"
            onClick={busy ? undefined : onCancel}
            style={{ flex: 1, padding: 12, border: `1px solid ${C.border}`, borderRadius: 11, background: "#fff", color: C.secondary, fontSize: 13.5, fontWeight: 600, cursor: "pointer" }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={busy}
            style={{ flex: 2, padding: 12, border: "none", borderRadius: 11, background: busy ? "#9CB3A6" : C.green, color: "#fff", fontSize: 13.5, fontWeight: 700, cursor: busy ? "default" : "pointer" }}
          >
            {busy ? "Saving…" : "Use this location"}
          </button>
        </div>
      </div>
    </>
  );
}

// Green teardrop pin matching the volunteer layer colour.
function PickerPin() {
  return (
    <svg width="30" height="38" viewBox="0 0 30 38" style={{ display: "block", filter: "drop-shadow(0 3px 4px rgba(0,0,0,0.4))" }}>
      <path d="M15 2C9.48 2 5 6.48 5 12c0 8 10 24 10 24s10-16 10-24C25 6.48 20.52 2 15 2z" fill={C.green} stroke="#12603b" strokeWidth="1.5" />
      <circle cx="15" cy="12" r="3.4" fill="white" />
    </svg>
  );
}
