"use client";

import React, { useState, useMemo, useCallback, useEffect, useRef, type ReactNode } from "react";
import {
  APIProvider,
  Map,
  AdvancedMarker,
  InfoWindow,
  Polyline,
} from "@vis.gl/react-google-maps";
import { useMap } from "@vis.gl/react-google-maps";
import { useRoutingStore, type SimulatedVehicleKind } from "@/store/routingStore";
import { useEventLog } from "@/store/eventLog";
import TimelinePanel from "@/components/TimelinePanel";
import LanguageToggle from "@/components/LanguageToggle";
import AuthControl from "@/components/auth/AuthControl";
import SafetyProfileSheet from "@/components/auth/SafetyProfileSheet";
import { useIsOperator, useAuthStore } from "@/store/authStore";
import InstallPWA from "@/components/InstallPWA";
import IncidentRecord from "@/components/IncidentRecord";
import { useT, useBilingual } from "@/hooks/useI18n";
import { useResponders, LAYER_TO_PLACE_TYPE } from "@/hooks/useResponders";
import { C, RADIUS, HEADER_GRADIENT, BRAND_GRADIENT, CTA_GRADIENT, SHADOW } from "@/lib/design";
import { ShieldCrossIcon, MicIcon } from "@/components/ui/icons";
import ClusteredLayer, { type ClusterItem } from "@/components/map/ClusteredLayer";
import AccidentDensityLayer from "@/components/map/AccidentDensityLayer";
import MapControls from "@/components/map/MapControls";
import FloatingPanel, {
  type ServiceLayerRow,
  type NearbyFacility,
  type ReportCardData,
} from "@/components/map/FloatingPanel";
import { usePotholes } from "@/hooks/usePotholes";
import { useAccidents } from "@/hooks/useAccidents";
import { CORRIDOR_CENTER } from "@/lib/corridorWaypoints";
import type { StringKey } from "@/i18n/strings";
import type { GooglePlace } from "@/lib/types";

import type {
  AmbulanceStation,
  FireStation,
  TowingStation,
  SurakshaMitra,
  AccidentReport,
  DbPothole,
  DbAccident,
  ServiceLayerType,
  AccidentLayerType,
  GeoPoint,
} from "@/lib/types";
import { reverseGeocode } from "@/lib/geocode";
import ReportPanel, { type ReportMode } from "@/components/report/ReportPanel";
import OperatorDashboard from "@/components/operator/OperatorDashboard";

// ── Constants ─────────────────────────────────────────────────────────────────

// Corridor constants imported from @/lib/corridorWaypoints

// ── Layer config ──────────────────────────────────────────────────────────────

// Category colours match the design handoff's panel/cluster palette. `hi` is
// the Hindi sub-label shown in the floating panel. `defaultOn` sets the initial
// layer visibility — only Hospitals / Ambulance / Fire / Towing start ON.
const SERVICE_LAYERS: {
  key: ServiceLayerType;
  labelKey: StringKey;
  hi: string;
  color: string;
  strokeColor: string;
  source: "google" | "synthetic" | "volunteer";
  defaultOn: boolean;
}[] = [
  { key: "HOSPITAL",          labelKey: "layerHospitals", hi: "अस्पताल",           color: "#2456A6", strokeColor: "#1B417D", source: "google",    defaultOn: true },
  { key: "AMBULANCE_STATION", labelKey: "layerAmbulance", hi: "एम्बुलेंस केंद्र",  color: "#1E7F4F", strokeColor: "#155C39", source: "synthetic", defaultOn: true },
  { key: "FIRE_STATION",      labelKey: "layerFire",      hi: "दमकल केंद्र",       color: "#C6362C", strokeColor: "#9E2A22", source: "synthetic", defaultOn: true },
  { key: "TOWING_STATION",    labelKey: "layerTowing",    hi: "टोइंग / रिकवरी",    color: "#6B7280", strokeColor: "#4B5563", source: "synthetic", defaultOn: true },
  { key: "MECHANIC",          labelKey: "layerMechanics", hi: "मैकेनिक",           color: "#374151", strokeColor: "#1F2937", source: "google",    defaultOn: false },
  { key: "POLICE",            labelKey: "layerPolice",    hi: "पुलिस थाने",        color: "#4F46E5", strokeColor: "#3730B3", source: "google",    defaultOn: false },
  { key: "PHARMACY",          labelKey: "layerPharmacy",  hi: "दवा दुकानें",       color: "#0D9488", strokeColor: "#0A6E64", source: "google",    defaultOn: false },
  { key: "GAS_STATION",       labelKey: "layerFuel",      hi: "पेट्रोल पंप",       color: "#B45309", strokeColor: "#8A3F07", source: "google",    defaultOn: false },
  // Community volunteer first-responders — REAL user registrations (not sample).
  // Saffron to read as the brand's "community" tone, distinct from the service
  // layers. Off by default, like the other secondary layers.
  { key: "SURAKSHA_MITRA",    labelKey: "layerSurakshaMitra", hi: "सुरक्षा मित्र", color: "#DD8A20", strokeColor: "#B06D14", source: "volunteer", defaultOn: false },
];

const ACCIDENT_LAYERS: {
  key: AccidentLayerType;
  labelKey: StringKey;
  hi: string;
  color: string;
  strokeColor: string;
  source: "synthetic" | "live";
}[] = [
  { key: "POTHOLE",           labelKey: "layerPotholes",           hi: "सड़क दोष",             color: "#6B4226", strokeColor: "#4E2F1A", source: "live" },
  { key: "REPORTED_ACCIDENT", labelKey: "layerReportedAccidents",  hi: "दुर्घटनाएँ",           color: "#D14036", strokeColor: "#A82C22", source: "live" },
];

const LAYER_COLOR: Record<string, { color: string; strokeColor: string }> = {
  ...Object.fromEntries(SERVICE_LAYERS.map((l) => [l.key, { color: l.color, strokeColor: l.strokeColor }])),
  ...Object.fromEntries(ACCIDENT_LAYERS.map((l) => [l.key, { color: l.color, strokeColor: l.strokeColor }])),
};

// ── Marker icon SVGs (14×14 viewBox, white on coloured background) ────────────

function HospitalIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="5.5" y="2" width="3" height="10" rx="1" fill="white"/>
      <rect x="2" y="5.5" width="10" height="3" rx="1" fill="white"/>
    </svg>
  );
}
function AmbulanceIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="7" cy="7" r="4.5" stroke="white" strokeWidth="1.5" fill="none"/>
      <path d="M7 3.8v6.4M3.8 7h6.4" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}
function FireIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M7 1.5c0 2-2 3-2 5a2.5 2.5 0 005 0c0-1.5-1-2.5-1-4 0 0-1 1.5-2 1.5z" fill="white"/>
      <circle cx="7" cy="11.5" r="1.4" fill="white"/>
    </svg>
  );
}
function TowingIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M2.5 9.5V4a1 1 0 011-1h4a1 1 0 011 1v5.5" stroke="white" strokeWidth="1.4" strokeLinejoin="round"/>
      <path d="M8.5 6.5h2l1.5 2v1h-3.5z" fill="rgba(255,255,255,0.2)" stroke="white" strokeWidth="1.3" strokeLinejoin="round"/>
      <circle cx="4.5" cy="10.5" r="1.2" stroke="white" strokeWidth="1.2" fill="none"/>
      <circle cx="10" cy="10.5" r="1.2" stroke="white" strokeWidth="1.2" fill="none"/>
    </svg>
  );
}
function PoliceIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M7 1L2 3.2v4c0 2.7 2.2 4.9 5 5.5 2.8-.6 5-2.8 5-5.5v-4L7 1z"
        fill="rgba(255,255,255,0.2)" stroke="white" strokeWidth="1.4" strokeLinejoin="round"/>
      <path d="M4.8 7l1.7 1.7L9.8 5" stroke="white" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
function MechanicIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <circle cx="7" cy="7" r="2" fill="white"/>
      <circle cx="7" cy="7" r="4.5" stroke="white" strokeWidth="1.3" fill="none"
        strokeDasharray="2.5 1.8"/>
    </svg>
  );
}
function PharmacyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1.5" y="4.5" width="11" height="5.5" rx="2.75"
        fill="rgba(255,255,255,0.2)" stroke="white" strokeWidth="1.4"/>
      <path d="M7 6v3M5.5 7.5h3" stroke="white" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
  );
}
function GasIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1.5" y="2.5" width="7" height="9" rx="1"
        fill="rgba(255,255,255,0.2)" stroke="white" strokeWidth="1.4"/>
      <path d="M8.5 5.5L11 4v3.5a1 1 0 002 0V4" stroke="white"
        strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M3.5 6.5h4" stroke="white" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
  );
}
function SurakshaMitraIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      {/* Shield with a person silhouette — community helper */}
      <path d="M7 1L2.2 2.9v3.6c0 2.6 2 4.6 4.8 5.4 2.8-.8 4.8-2.8 4.8-5.4V2.9L7 1z"
        fill="rgba(255,255,255,0.18)" stroke="white" strokeWidth="1.2" strokeLinejoin="round"/>
      <circle cx="7" cy="5.4" r="1.35" fill="white"/>
      <path d="M4.6 9.2c0-1.4 1.1-2.3 2.4-2.3s2.4.9 2.4 2.3z" fill="white"/>
    </svg>
  );
}
function ReportedAccidentIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      {/* Two cars colliding */}
      <rect x="1" y="6" width="5" height="3" rx="0.8" fill="white" opacity="0.9"/>
      <rect x="8" y="6" width="5" height="3" rx="0.8" fill="white" opacity="0.9"/>
      <path d="M6 7.5h2" stroke="white" strokeWidth="1.6" strokeLinecap="round"/>
      <circle cx="3" cy="9.5" r="0.8" fill="rgba(255,255,255,0.6)"/>
      <circle cx="11" cy="9.5" r="0.8" fill="rgba(255,255,255,0.6)"/>
    </svg>
  );
}
function PotholeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M2 5h4M8 5h4" stroke="white" strokeWidth="1.4" strokeLinecap="round"/>
      <ellipse cx="7" cy="9.5" rx="4" ry="2.5"
        fill="rgba(255,255,255,0.2)" stroke="white" strokeWidth="1.3"/>
      <ellipse cx="7" cy="9.5" rx="2" ry="1.2" fill="rgba(0,0,0,0.4)"/>
    </svg>
  );
}

// ── Marker shape primitives ───────────────────────────────────────────────────

interface MarkerProps { color: string; strokeColor: string; icon: ReactNode; }

function SquareMarker({ color, strokeColor, icon }: MarkerProps) {
  return (
    <div style={{
      width: 32, height: 32, borderRadius: 7,
      background: color, border: `2px solid ${strokeColor}`,
      boxShadow: "0 2px 6px rgba(0,0,0,0.30), 0 1px 2px rgba(0,0,0,0.18)",
      display: "flex", alignItems: "center", justifyContent: "center",
      cursor: "pointer", boxSizing: "border-box",
    }}>
      {icon}
    </div>
  );
}

function CircleMarker({ color, strokeColor, icon }: MarkerProps) {
  return (
    <div style={{
      width: 32, height: 32, borderRadius: "50%",
      background: color, border: `2px solid ${strokeColor}`,
      boxShadow: "0 2px 6px rgba(0,0,0,0.30), 0 1px 2px rgba(0,0,0,0.18)",
      display: "flex", alignItems: "center", justifyContent: "center",
      cursor: "pointer", boxSizing: "border-box",
    }}>
      {icon}
    </div>
  );
}

function TriangleMarker({ color, strokeColor, icon }: MarkerProps) {
  return (
    <div style={{ position: "relative", width: 34, height: 30, cursor: "pointer" }}>
      <svg width="34" height="30" viewBox="0 0 34 30" style={{ display: "block",
        filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.30))" }}>
        <path d="M17 3L31.5 27.5H2.5L17 3z"
          fill={color} stroke={strokeColor} strokeWidth="1.5" strokeLinejoin="round"/>
      </svg>
      <div style={{ position: "absolute", top: 7, left: 0, right: 0,
        display: "flex", alignItems: "center", justifyContent: "center" }}>
        {icon}
      </div>
    </div>
  );
}

function DiamondMarker({ color, strokeColor, icon }: MarkerProps) {
  return (
    <div style={{ position: "relative", width: 30, height: 30, cursor: "pointer" }}>
      <div style={{
        position: "absolute", top: "50%", left: "50%",
        transform: "translate(-50%, -50%) rotate(45deg)",
        width: 22, height: 22, borderRadius: 3,
        background: color, border: `2px solid ${strokeColor}`,
        boxShadow: "0 2px 6px rgba(0,0,0,0.30)", boxSizing: "border-box",
      }}/>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
        display: "flex", alignItems: "center", justifyContent: "center" }}>
        {icon}
      </div>
    </div>
  );
}

// ── Layer → marker config ─────────────────────────────────────────────────────

type MarkerShape = "square" | "circle" | "triangle" | "diamond";
const LAYER_MARKER: Record<string, { shape: MarkerShape; Icon: () => React.JSX.Element }> = {
  HOSPITAL:          { shape: "square",   Icon: HospitalIcon },
  AMBULANCE_STATION: { shape: "circle",   Icon: AmbulanceIcon },
  FIRE_STATION:      { shape: "circle",   Icon: FireIcon },
  TOWING_STATION:    { shape: "circle",   Icon: TowingIcon },
  MECHANIC:          { shape: "square",   Icon: MechanicIcon },
  POLICE:            { shape: "square",   Icon: PoliceIcon },
  PHARMACY:          { shape: "square",   Icon: PharmacyIcon },
  GAS_STATION:       { shape: "square",   Icon: GasIcon },
  SURAKSHA_MITRA:    { shape: "circle",   Icon: SurakshaMitraIcon },
  POTHOLE:             { shape: "diamond",  Icon: PotholeIcon },
  REPORTED_ACCIDENT:   { shape: "circle",   Icon: ReportedAccidentIcon },
};

function LayerMarker({ layerKey, color, strokeColor }: { layerKey: string; color: string; strokeColor: string }) {
  const m = LAYER_MARKER[layerKey];
  if (!m) return <CircleMarker color={color} strokeColor={strokeColor} icon={null} />;
  const icon = <m.Icon />;
  switch (m.shape) {
    case "square":   return <SquareMarker   color={color} strokeColor={strokeColor} icon={icon} />;
    case "triangle": return <TriangleMarker color={color} strokeColor={strokeColor} icon={icon} />;
    case "diamond":  return <DiamondMarker  color={color} strokeColor={strokeColor} icon={icon} />;
    default:         return <CircleMarker   color={color} strokeColor={strokeColor} icon={icon} />;
  }
}


// ── Incident pin (teardrop + pulse ring) ──────────────────────────────────────

function IncidentPin() {
  return (
    <div style={{ position: "relative", cursor: "pointer" }}>
      {/* Pulse ring */}
      <span
        className="animate-ping"
        style={{
          position: "absolute", top: -5, left: -5,
          width: 40, height: 40, borderRadius: "50%",
          background: "rgba(245,158,11,0.28)", display: "block",
          pointerEvents: "none",
        }}
      />
      {/* Teardrop pin */}
      <svg width="30" height="38" viewBox="0 0 30 38"
        style={{ display: "block", filter: "drop-shadow(0 3px 4px rgba(0,0,0,0.40))" }}>
        <path d="M15 2C9.48 2 5 6.48 5 12c0 8 10 24 10 24s10-16 10-24C25 6.48 20.52 2 15 2z"
          fill="#f59e0b" stroke="#92400e" strokeWidth="1.5"/>
        {/* Alert symbol inside pin */}
        <path d="M15 8v6" stroke="white" strokeWidth="2.2" strokeLinecap="round"/>
        <circle cx="15" cy="17" r="1.3" fill="white"/>
      </svg>
    </div>
  );
}

// ── Simulated ambulance marker (cosmetic demo animation, not a live position
// feed — see CLAUDE.md hard rule 1) ───────────────────────────────────────────

// Walks the already-computed route polyline at a constant pace over the
// estimate's duration. Purely a visual aid; never presented as GPS tracking —
// the marker itself is tagged "SIMULATED" and its popup repeats the disclaimer.
// Haversine distance in km — mirrors src/lib/matching.ts's haversineKm without
// importing it here (this is a client-only rendering helper).
function haversineKmLocal(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const R = 6371;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(h));
}

// Walks the polyline at constant real-world speed — cumulative-distance based,
// not a naive per-point-index fraction. Google's route points are unevenly
// spaced (dense on curves, sparse on straight stretches), so an index-based
// fraction would speed up/slow down for no reason; this keeps the marker's
// pace uniform and strictly on the road the whole way, matching the actual
// highlighted route rather than jumping between arbitrary vertices.
function interpolateAlongPath(coords: [number, number][], fraction: number): { lat: number; lng: number } {
  if (coords.length === 0) return { lat: 0, lng: 0 };
  const clamped = Math.min(1, Math.max(0, fraction));
  if (coords.length === 1 || clamped <= 0) return { lat: coords[0][0], lng: coords[0][1] };
  if (clamped >= 1) {
    const last = coords[coords.length - 1];
    return { lat: last[0], lng: last[1] };
  }

  const segLens: number[] = [];
  let total = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    const d = haversineKmLocal(
      { lat: coords[i][0], lng: coords[i][1] },
      { lat: coords[i + 1][0], lng: coords[i + 1][1] }
    );
    segLens.push(d);
    total += d;
  }
  if (total === 0) return { lat: coords[0][0], lng: coords[0][1] };

  const targetDist = clamped * total;
  let covered = 0;
  for (let i = 0; i < segLens.length; i++) {
    if (covered + segLens[i] >= targetDist || i === segLens.length - 1) {
      const segFraction = segLens[i] > 0 ? (targetDist - covered) / segLens[i] : 0;
      const a = coords[i];
      const b = coords[i + 1];
      const t = Math.min(1, Math.max(0, segFraction));
      return { lat: a[0] + (b[0] - a[0]) * t, lng: a[1] + (b[1] - a[1]) * t };
    }
    covered += segLens[i];
  }
  const last = coords[coords.length - 1];
  return { lat: last[0], lng: last[1] };
}

const SIM_VEHICLE_STYLE: Record<SimulatedVehicleKind, { color: string; stroke: string; Icon: () => React.JSX.Element; label: string }> = {
  AMBULANCE: { color: "#16a34a", stroke: "#15803d", Icon: AmbulanceIcon, label: "Simulated ambulance" },
  FIRE:      { color: "#dc2626", stroke: "#b91c1c", Icon: FireIcon,      label: "Simulated fire truck" },
  TOWING:    { color: "#57534e", stroke: "#3f3c3a", Icon: TowingIcon,    label: "Simulated tow truck" },
};

function SimulatedVehicleMarker({ kind }: { kind: SimulatedVehicleKind }) {
  const s = SIM_VEHICLE_STYLE[kind];
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", cursor: "pointer" }}>
      <div style={{
        width: 30, height: 30, borderRadius: "50%",
        background: s.color, border: `2px solid ${s.stroke}`,
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: "0 2px 6px rgba(0,0,0,0.35)",
      }}>
        <s.Icon />
      </div>
      <span style={{
        marginTop: 2, background: "#111827", color: "white", fontSize: 9, fontWeight: 700,
        padding: "1px 5px", borderRadius: 4, letterSpacing: 0.4, whiteSpace: "nowrap",
      }}>
        SIMULATED
      </span>
    </div>
  );
}

// ── Popup content ─────────────────────────────────────────────────────────────

// Singularise a layer label for a single-marker popup.
// Plain trailing-"s" stripping mangles "Pharmacies" → "Pharmacie"; handle "-ies" → "-y"
// first. Non-Latin labels (HI/AS) have no trailing "s" and pass through unchanged.
function singularLabel(label: string): string {
  if (/ies$/i.test(label)) return label.replace(/ies$/i, "y");
  return label.replace(/s$/, "");
}

function GooglePlacePopup({ p, label }: { p: GooglePlace; label: string }) {
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900">{p.name}</p>
      <p className="text-gray-500 mb-1">{label}</p>
      {p.address && <p className="text-gray-700 mb-1 break-words max-w-[220px]">{p.address}</p>}
      {p.phone && (
        <p className="mb-1">
          <a href={`tel:${p.phone}`} className="font-medium text-blue-700 hover:underline">
            {p.phone}
          </a>
        </p>
      )}
      {p.isOpen !== null && (
        <p className={`font-medium ${p.isOpen ? "text-green-700" : "text-red-600"}`}>
          {p.isOpen ? "Open now" : "Closed now"}
        </p>
      )}
      <p className="text-gray-400 text-[10px] mt-2 flex items-center gap-1">
        <span>Data:</span>
        <span className="font-medium text-gray-500">Google Places</span>
      </p>
    </div>
  );
}

function AmbulancePopup({ a }: { a: AmbulanceStation }) {
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900">{a.name}</p>
      <p className="text-gray-500 mb-1">{a.district}</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500">Ambulances</td><td>{a.ambulanceCount} ({a.types.join(", ")})</td></tr>
          <tr><td className="pr-2 text-gray-500">Hours</td><td>{a.operatingHours}</td></tr>
          <tr><td className="pr-2 text-gray-500">Contact</td><td className="font-medium text-green-800">{a.contactNumber}</td></tr>
          <tr><td className="pr-2 text-gray-500">Notes</td><td>{a.notes}</td></tr>
        </tbody>
      </table>
      <p className="text-amber-700 text-[10px] mt-2">⚠ Sample data</p>
    </div>
  );
}

function FireStationPopup({ f }: { f: FireStation }) {
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900">{f.name}</p>
      <p className="text-gray-500 mb-1">{f.district}</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500">Vehicles</td><td>{f.vehicleTypes.join(", ")}</td></tr>
          <tr><td className="pr-2 text-gray-500">Hours</td><td>{f.operatingHours}</td></tr>
          <tr><td className="pr-2 text-gray-500">Contact</td><td className="font-medium text-red-800">{f.contactNumber}</td></tr>
          <tr><td className="pr-2 text-gray-500">Notes</td><td>{f.notes}</td></tr>
        </tbody>
      </table>
      <p className="text-amber-700 text-[10px] mt-2">⚠ Sample data</p>
    </div>
  );
}

function TowingStationPopup({ w }: { w: TowingStation }) {
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900">{w.name}</p>
      <p className="text-gray-500 mb-1">{w.district}</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500">Vehicles</td><td>{w.vehicleTypes.join(", ")}</td></tr>
          <tr><td className="pr-2 text-gray-500">Hours</td><td>{w.operatingHours}</td></tr>
          <tr><td className="pr-2 text-gray-500">Contact</td><td className="font-medium text-gray-800">{w.contactNumber}</td></tr>
          <tr><td className="pr-2 text-gray-500">Notes</td><td>{w.notes}</td></tr>
        </tbody>
      </table>
      <p className="text-amber-700 text-[10px] mt-2">⚠ Sample data</p>
    </div>
  );
}

function PotholePopup({ p }: { p: DbPothole }) {
  const col = p.severity === "HIGH" ? "text-red-700" : p.severity === "MEDIUM" ? "text-amber-700" : "text-gray-700";
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900">Road Defect — Reported</p>
      <p className="text-gray-500 mb-1">{p.road}</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500">Severity</td><td className={`font-medium ${col}`}>{p.severity}</td></tr>
          {p.description && <tr><td className="pr-2 text-gray-500">Notes</td><td>{p.description}</td></tr>}
          <tr><td className="pr-2 text-gray-500">Reported</td><td>{p.reported_date}</td></tr>
          <tr><td className="pr-2 text-gray-500">Status</td><td>{p.status}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

function ReportedAccidentPopup({ a }: { a: DbAccident }) {
  const sevColor = a.severity === "CRITICAL" || a.severity === "HIGH" ? "text-red-700"
    : a.severity === "MEDIUM" ? "text-amber-700" : "text-gray-700";
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900">Reported Accident</p>
      <p className="text-gray-500 mb-1">{a.location_label}</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500">Mode</td><td>{a.report_mode}</td></tr>
          {a.severity && <tr><td className="pr-2 text-gray-500">Severity</td><td className={`font-medium ${sevColor}`}>{a.severity}</td></tr>}
          {a.description && <tr><td className="pr-2 text-gray-500">Notes</td><td className="break-words max-w-[160px]">{a.description}</td></tr>}
          {a.flags?.length > 0 && <tr><td className="pr-2 text-gray-500">Flags</td><td>{a.flags.join(", ")}</td></tr>}
          <tr><td className="pr-2 text-gray-500">Date</td><td>{a.reported_date}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

// Suraksha Mitra (community volunteer) popup — PRIVACY-GATED. The public sees
// only non-sensitive info (first name, coverage area, first-aid yes/no) and a
// generalised coverage location, never the phone number or precise home pin.
// Operators additionally see the full name, phone and registration detail.
// Framing stays honest: this is a registration, not a dispatch/activation.
const FIRST_AID_LABEL: Record<string, string> = {
  basic: "Basic first aid", cpr: "CPR trained", professional: "Professional (medic)",
};
function SurakshaMitraPopup({ m, isOperator }: { m: SurakshaMitra; isOperator: boolean }) {
  const firstName = m.name.trim().split(/\s+/)[0] || "Volunteer";
  const aidLevel = m.firstAidTrained && m.firstAidLevel && m.firstAidLevel !== "none"
    ? FIRST_AID_LABEL[m.firstAidLevel] ?? null : null;
  return (
    <div className="text-xs leading-relaxed min-w-[200px]">
      <p className="font-semibold text-sm text-gray-900 flex items-center gap-2">
        {isOperator ? m.name : firstName}
        <span style={{ fontSize: 9.5, fontWeight: 700, color: "#8A5A17", background: "#FBF1E4", border: "1px solid #EED9B8", borderRadius: 999, padding: "1px 6px", letterSpacing: ".02em" }}>
          SURAKSHA MITRA
        </span>
      </p>
      <p className="text-gray-500 mb-1">Community volunteer first-responder</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500 align-top">Coverage</td><td>{m.coverageRadiusKm} km radius{m.locationLabel ? ` · ${m.locationLabel}` : ""}</td></tr>
          <tr><td className="pr-2 text-gray-500 align-top">First aid</td><td>{m.firstAidTrained ? `Yes${isOperator && aidLevel ? ` · ${aidLevel}` : ""}` : "No"}</td></tr>
          {isOperator && (
            <>
              {m.phone && <tr><td className="pr-2 text-gray-500 align-top">Phone</td><td><a href={`tel:${m.phone}`} className="font-medium text-amber-800 hover:underline">{m.phone}</a></td></tr>}
              {m.occupation && <tr><td className="pr-2 text-gray-500 align-top">Background</td><td>{m.occupation}</td></tr>}
            </>
          )}
        </tbody>
      </table>
      <p className="text-gray-400 text-[10px] mt-2">Registration only — this volunteer has not been dispatched or activated.</p>
      {isOperator && <p className="text-amber-700 text-[10px] mt-0.5">Operator view — contact details are hidden from the public map.</p>}
    </div>
  );
}

// ── InfoWindow state type ─────────────────────────────────────────────────────

interface MarkerInfo {
  position: { lat: number; lng: number };
  content: React.ReactNode;
}

// Lifts the live google.maps.Map instance (and current centre) up to MapView so
// the floating panel's list rows can pan/zoom the map. Renders nothing.
function MapHandle({
  onMap,
  onCenter,
}: {
  onMap: (m: google.maps.Map | null) => void;
  onCenter: (c: { lat: number; lng: number }) => void;
}) {
  const map = useMap();
  useEffect(() => {
    onMap(map ?? null);
  }, [map, onMap]);
  useEffect(() => {
    if (!map) return;
    const listener = map.addListener("idle", () => {
      const c = map.getCenter();
      if (c) onCenter({ lat: c.lat(), lng: c.lng() });
    });
    return () => listener.remove();
  }, [map, onCenter]);
  return null;
}

// Coverage-zone circle for the selected volunteer (imperative google.maps.Circle
// via useMap — same pattern as AccidentDensityLayer). Saffron to match the
// volunteer layer; purely a visual of the registered coverage radius.
function VolunteerZoneCircle({ zone }: { zone: { lat: number; lng: number; radiusKm: number } }) {
  const map = useMap();
  const circleRef = useRef<google.maps.Circle | null>(null);
  useEffect(() => {
    if (!map) return;
    circleRef.current = new google.maps.Circle({
      map,
      center: { lat: zone.lat, lng: zone.lng },
      radius: zone.radiusKm * 1000,
      strokeColor: "#DD8A20",
      strokeOpacity: 0.7,
      strokeWeight: 1.5,
      fillColor: "#DD8A20",
      fillOpacity: 0.1,
      clickable: false,
      zIndex: 5,
    });
    return () => { circleRef.current?.setMap(null); circleRef.current = null; };
  }, [map, zone.lat, zone.lng, zone.radiusKm]);
  return null;
}

// Coarse relative-time label for report cards (date-granularity source data).
function relTime(dateStr: string): string {
  const then = new Date(dateStr).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} h`;
  return `${Math.floor(hrs / 24)} d`;
}

// ── Main component ────────────────────────────────────────────────────────────

type Tab = "SERVICES" | "ACCIDENTS" | "NETWORK";

export default function MapView() {
  const t = useT();
  const { showHindi } = useBilingual();
  const browserKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY ?? "";

  const [tab, setTab] = useState<Tab>("SERVICES");
  // Network tab is operator-only (Signals/Aggregator dispatch dashboard).
  const isOperator = useIsOperator();
  // Never let a non-operator sit on NETWORK (e.g. via stale state after sign-out
  // or a role change) — fall back to Services.
  useEffect(() => {
    if (tab === "NETWORK" && !isOperator) setTab("SERVICES");
  }, [tab, isOperator]);
  // Only Hospitals / Ambulance / Fire / Towing start ON (design default).
  const [activeServices, setActiveServices] = useState<Set<ServiceLayerType>>(
    new Set(SERVICE_LAYERS.filter((l) => l.defaultOn).map((l) => l.key))
  );
  const [activeAccidents, setActiveAccidents] = useState<Set<AccidentLayerType>>(
    new Set(ACCIDENT_LAYERS.map((l) => l.key))
  );

  // PWA launch intent — set by PWAHome before the app mounted. MapView is an
  // ssr:false dynamic import, so this reads on the client only; consume it into
  // initial state here (no setState-in-effect), then clear the store flag below.
  const [launchIntent] = useState(() => useAuthStore.getState().launchIntent);
  // "voice" (SOS) auto-starts the dispatcher; "report" opens the full report
  // sheet on its default (SOS) tab — both mirror the map's two report FABs.
  const [reportOpen, setReportOpen] = useState(launchIntent === "voice" || launchIntent === "report");
  // PWA launch OR the map SOS button: force the report panel into a mode /
  // auto-start the voice call in a chosen language. reportSession keys the panel
  // so these props re-seed its state on every open (it stays mounted otherwise).
  const [reportInitialMode, setReportInitialMode] = useState<ReportMode | undefined>(
    launchIntent === "voice" ? "DISPATCHER" : undefined
  );
  const [reportAutoStartVoice, setReportAutoStartVoice] = useState(launchIntent === "voice");
  const [reportVoiceLocale, setReportVoiceLocale] = useState<"en-IN" | "hi-IN" | null>(
    () => useAuthStore.getState().launchVoiceLocale
  );
  const [reportSession, setReportSession] = useState(0);
  const [sosLangChoice, setSosLangChoice] = useState(false);
  const [profileOpen, setProfileOpen] = useState(launchIntent === "profile");
  useEffect(() => {
    // Clear the one-shot intent once consumed (a store action, not a React
    // setState, so this never re-triggers a render loop).
    if (launchIntent) useAuthStore.getState().clearLaunchIntent();
  }, [launchIntent]);
  const [isPickingPin, setIsPickingPin] = useState(false);
  const [pinnedLocation, setPinnedLocation] = useState<GeoPoint | null>(null);
  const [pinnedLabel, setPinnedLabel] = useState("");
  const [openInfo, setOpenInfo] = useState<MarkerInfo | null>(null);
  // The selected volunteer's coverage zone (drawn as a circle while their popup
  // is open); cleared when the InfoWindow closes.
  const [selectedZone, setSelectedZone] = useState<{ lat: number; lng: number; radiusKm: number } | null>(null);
  // On mobile the floating panel becomes a bottom drawer — start it collapsed so
  // the map, FAB and timeline pill are visible; the ☰ button re-opens it. Safe to
  // read matchMedia in the initializer: MapView is a client-only (ssr:false)
  // dynamic import, so `window` always exists and there's no hydration mismatch.
  const [panelOpen, setPanelOpen] = useState(
    () => !(typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches)
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [mapInstance, setMapInstance] = useState<google.maps.Map | null>(null);
  const [mapCenter, setMapCenter] = useState<{ lat: number; lng: number }>(CORRIDOR_CENTER);

  const mapRoutes = useRoutingStore((s) => s.routes);
  const simulatedVehicles = useRoutingStore((s) => s.simulatedVehicles);
  const [simTick, setSimTick] = useState(() => Date.now());
  useEffect(() => {
    if (simulatedVehicles.length === 0) return;
    const id = setInterval(() => setSimTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, [simulatedVehicles.length]);
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [recordIncidentId, setRecordIncidentId] = useState<string | null>(null);
  const entries = useEventLog((s) => s.entries);
  const eventCount = entries.length;

  // Derive committed incident location from event log so the pin always appears —
  // including SOS mode, where geolocation runs inside ReportPanel and never
  // propagates back to MapView's pinnedLocation state.
  const activeIncident = useMemo(() => {
    for (let i = entries.length - 1; i >= 0; i--) {
      if (entries[i].type === "REPORT_CREATED") {
        const r = entries[i].payload as AccidentReport;
        return { location: r.location, label: r.locationLabel };
      }
    }
    return null;
  }, [entries]);

  // Show the user's live map-pin while picking; fall back to the committed incident.
  const incidentPinLocation: GeoPoint | null = pinnedLocation ?? activeIncident?.location ?? null;
  const incidentPinLabel = pinnedLabel || activeIncident?.label || "";

  // ── Google Places (live, server-fetched) ──────────────────────────────────
  // All responder/service layers come from the Aggregator DPG — curated,
  // synthetic and Google-synced alike. Google Places is ingestion-only.
  const { results: places, data: responders, loading: placesLoading, hasError: placesError } = useResponders();

  // ── Synthetic seed data (labelled as sample) ──────────────────────────────
  const ambulances = responders.ambulanceStations;
  const fireStations = responders.fireStations;
  const towingStations = responders.towingStations;
  // Community volunteers — REAL registrations mirrored from the responder
  // registry (not sample). Layer + popup are privacy-gated (Phase 4).
  const surakshaMitras = responders.surakshaMitras;
  const { potholes, loading: potholesLoading, error: potholesError, refetch: refetchPotholes } = usePotholes();
  const { accidents: reportedAccidents, refetch: refetchAccidents } = useAccidents();

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleMapClick = useCallback(async (lat: number, lng: number) => {
    setPinnedLocation({ lat, lng });
    setPinnedLabel(`${lat.toFixed(5)}, ${lng.toFixed(5)}`);
    setIsPickingPin(false);
    setReportOpen(true);
    try {
      const label = await reverseGeocode(lat, lng);
      setPinnedLabel(label);
    } catch {
      // keep coordinate label
    }
  }, []);

  function openReport() {
    setReportInitialMode(undefined); setReportAutoStartVoice(false); setReportVoiceLocale(null);
    setReportSession((s) => s + 1);
    setPinnedLocation(null); setPinnedLabel(""); setReportOpen(true);
  }
  function closeReport() {
    setReportOpen(false); setIsPickingPin(false);
    setReportInitialMode(undefined); setReportAutoStartVoice(false); setReportVoiceLocale(null);
  }
  // Map SOS → open the Voice dispatcher in the chosen language and auto-start.
  function startSosVoice(locale: "en-IN" | "hi-IN") {
    setSosLangChoice(false);
    setReportInitialMode("DISPATCHER"); setReportAutoStartVoice(true); setReportVoiceLocale(locale);
    setReportSession((s) => s + 1);
    setPinnedLocation(null); setPinnedLabel(""); setReportOpen(true);
  }
  function requestPin() { setIsPickingPin(true); setReportOpen(false); }

  function toggleService(key: ServiceLayerType) {
    setActiveServices((prev) => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  }
  function toggleAccident(key: AccidentLayerType) {
    setActiveAccidents((prev) => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  }

  function openPlaceInfo(p: GooglePlace, label: string) {
    setOpenInfo({ position: { lat: p.lat, lng: p.lng }, content: <GooglePlacePopup p={p} label={label} /> });
  }

  // Pan + zoom the live map to a coordinate (used by floating-panel list rows).
  const focusOn = useCallback((lat: number, lng: number) => {
    if (!mapInstance) return;
    mapInstance.panTo({ lat, lng });
    if ((mapInstance.getZoom() ?? 0) < 13) mapInstance.setZoom(14);
  }, [mapInstance]);

  const query = searchQuery.trim().toLowerCase();
  const matchesSearch = useCallback(
    (name: string) => query.length === 0 || name.toLowerCase().includes(query),
    [query]
  );

  // ── Floating-panel: service-layer rows (label, colour, count, toggle) ───────
  const serviceLayerRows: ServiceLayerRow[] = SERVICE_LAYERS.map((l) => {
    const pt = LAYER_TO_PLACE_TYPE[l.key];
    const isGoogle = l.source === "google" && pt;
    const count = isGoogle
      ? places[pt].length
      : l.key === "AMBULANCE_STATION"
      ? ambulances.length
      : l.key === "FIRE_STATION"
      ? fireStations.length
      : l.key === "TOWING_STATION"
      ? towingStations.length
      : l.key === "SURAKSHA_MITRA"
      ? surakshaMitras.length
      : null;
    return {
      key: l.key,
      en: t(l.labelKey),
      hi: l.hi,
      color: l.color,
      count,
      loading: isGoogle ? placesLoading[pt] : false,
      sample: l.source === "synthetic",
      active: activeServices.has(l.key),
      onToggle: () => toggleService(l.key),
    };
  });

  // ── Floating-panel: nearby facilities (active service layers, by distance) ──
  const nearbyFacilities: NearbyFacility[] = useMemo(() => {
    const items: (NearbyFacility & { lat: number; lng: number })[] = [];
    for (const l of SERVICE_LAYERS) {
      if (!activeServices.has(l.key)) continue;
      const pt = LAYER_TO_PLACE_TYPE[l.key];
      if (l.source === "google" && pt) {
        const label = singularLabel(t(l.labelKey));
        for (const p of places[pt]) {
          if (!matchesSearch(p.name)) continue;
          items.push({
            key: p.id, name: p.name, meta: label, color: l.color, lat: p.lat, lng: p.lng,
            distanceLabel: "",
            onSelect: () => { focusOn(p.lat, p.lng); openPlaceInfo(p, label); },
          });
        }
      } else if (l.key === "AMBULANCE_STATION") {
        for (const a of ambulances) {
          if (!matchesSearch(a.name)) continue;
          items.push({ key: a.id, name: a.name, meta: a.district, color: l.color, lat: a.lat, lng: a.lng, distanceLabel: "",
            onSelect: () => { focusOn(a.lat, a.lng); setOpenInfo({ position: { lat: a.lat, lng: a.lng }, content: <AmbulancePopup a={a} /> }); } });
        }
      } else if (l.key === "FIRE_STATION") {
        for (const f of fireStations) {
          if (!matchesSearch(f.name)) continue;
          items.push({ key: f.id, name: f.name, meta: f.district, color: l.color, lat: f.lat, lng: f.lng, distanceLabel: "",
            onSelect: () => { focusOn(f.lat, f.lng); setOpenInfo({ position: { lat: f.lat, lng: f.lng }, content: <FireStationPopup f={f} /> }); } });
        }
      } else if (l.key === "TOWING_STATION") {
        for (const w of towingStations) {
          if (!matchesSearch(w.name)) continue;
          items.push({ key: w.id, name: w.name, meta: w.district, color: l.color, lat: w.lat, lng: w.lng, distanceLabel: "",
            onSelect: () => { focusOn(w.lat, w.lng); setOpenInfo({ position: { lat: w.lat, lng: w.lng }, content: <TowingStationPopup w={w} /> }); } });
        }
      } else if (l.key === "SURAKSHA_MITRA") {
        for (const m of surakshaMitras) {
          // Privacy: non-operators see first name only, and search matches that
          // (never leak the full name via the list to the public).
          const displayName = isOperator ? m.name : (m.name.trim().split(/\s+/)[0] || "Volunteer");
          if (!matchesSearch(displayName)) continue;
          items.push({ key: m.id, name: displayName, meta: m.locationLabel || `${m.coverageRadiusKm} km zone`, color: l.color, lat: m.lat, lng: m.lng, distanceLabel: "",
            onSelect: () => { focusOn(m.lat, m.lng); setSelectedZone({ lat: m.lat, lng: m.lng, radiusKm: m.coverageRadiusKm }); setOpenInfo({ position: { lat: m.lat, lng: m.lng }, content: <SurakshaMitraPopup m={m} isOperator={isOperator} /> }); } });
        }
      }
    }
    items.sort(
      (a, b) => haversineKmLocal(mapCenter, { lat: a.lat, lng: a.lng }) - haversineKmLocal(mapCenter, { lat: b.lat, lng: b.lng })
    );
    return items.slice(0, 8).map((it) => ({
      ...it,
      distanceLabel: `${haversineKmLocal(mapCenter, { lat: it.lat, lng: it.lng }).toFixed(1)} km`,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [places, ambulances, fireStations, towingStations, surakshaMitras, isOperator, activeServices, query, mapCenter, focusOn]);

  // ── Floating-panel: report cards (accidents tab) ────────────────────────────
  const reportCards: ReportCardData[] = useMemo(() => {
    const cards: (ReportCardData & { ts: number })[] = [];
    // Individual accident report cards (with their detailed popups) are
    // operator-only. Citizens/guests never get per-report rows — they see the
    // aggregated density layer + legend instead.
    if (isOperator && activeAccidents.has("REPORTED_ACCIDENT")) {
      for (const a of reportedAccidents) {
        const sev: ReportCardData["sev"] =
          a.severity === "CRITICAL" || a.severity === "HIGH" ? "HIGH" : a.severity === "MEDIUM" ? "MED" : "LOW";
        const title = a.description?.trim().split(/[.\n]/)[0].slice(0, 42) || "Reported accident";
        const ignored = a.review_status === "ignored"; // B5: dim reviewed-duplicate rows
        cards.push({
          key: `acc-${a.id}`, sev, title, time: relTime(a.reported_date), loc: a.location_label,
          status: ignored ? "Ignored (dup)" : "Reported", statusHot: false, dimmed: ignored,
          ts: new Date(a.reported_date).getTime() || 0,
          onSelect: () => { focusOn(a.lat, a.lng); setOpenInfo({ position: { lat: a.lat, lng: a.lng }, content: <ReportedAccidentPopup a={a} /> }); },
        });
      }
    }
    if (activeAccidents.has("POTHOLE")) {
      for (const p of potholes) {
        const sev: ReportCardData["sev"] = p.severity === "HIGH" ? "HIGH" : p.severity === "MEDIUM" ? "MED" : "LOW";
        cards.push({
          key: `pot-${p.id}`, sev, title: "Road defect", time: relTime(p.reported_date), loc: p.road,
          status: p.status || "Reported", statusHot: false, ts: new Date(p.reported_date).getTime() || 0,
          onSelect: () => { focusOn(p.lat, p.lng); setOpenInfo({ position: { lat: p.lat, lng: p.lng }, content: <PotholePopup p={p} /> }); },
        });
      }
    }
    cards.sort((a, b) => b.ts - a.ts);
    return cards;
  }, [reportedAccidents, potholes, activeAccidents, focusOn, isOperator]);

  // Density-zone click (citizen/guest Accidents view): aggregate text ONLY —
  // never an individual report field. Stable identity so AccidentDensityLayer's
  // effect doesn't redraw circles on every render.
  const handleAccidentZoneClick = useCallback(
    (position: { lat: number; lng: number }, count: number) => {
      setOpenInfo({
        position,
        content: (
          <div className="text-xs leading-relaxed" style={{ minWidth: 190 }}>
            <p className="font-semibold text-sm text-gray-900">Accident-prone area</p>
            <p className="text-gray-700" style={{ marginTop: 2 }}>
              {count} reported accident{count === 1 ? "" : "s"} in this area — drive with caution.
            </p>
            <p className="text-gray-400" style={{ fontSize: 11, marginTop: 4 }}>
              दुर्घटना-संभावित क्षेत्र · reported density, not an official blackspot
            </p>
          </div>
        ),
      });
    },
    []
  );

  // ── Missing browser key guard ──────────────────────────────────────────────

  if (!browserKey) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-gray-100 p-8">
        <div className="bg-white rounded-xl border border-amber-300 shadow p-6 max-w-md text-center">
          <p className="text-sm font-semibold text-gray-900 mb-1">Google Maps key not configured</p>
          <p className="text-xs text-gray-500 mb-3">
            Set <code className="bg-gray-100 px-1 rounded">NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY</code> in{" "}
            <code className="bg-gray-100 px-1 rounded">.env.local</code>, then restart the dev server.
          </p>
          <p className="text-xs text-amber-700">See <strong>SETUP.md</strong> for key creation instructions.</p>
        </div>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col overflow-hidden" style={{ background: C.page, color: C.ink }}>
      {/* ── Header (60px navy) ─────────────────────────────────────────────── */}
      <header
        className="flex items-center gap-2 sm:gap-5 px-3 sm:px-4 flex-none"
        style={{ minHeight: 60, paddingTop: "env(safe-area-inset-top)", background: HEADER_GRADIENT, boxShadow: "0 1px 0 rgba(255,255,255,.06) inset, 0 2px 8px rgba(14,26,47,.25)" }}
      >
        {/* Brand */}
        <div className="flex items-center gap-[11px] min-w-0">
          <div
            className="flex items-center justify-center flex-none"
            style={{ width: 34, height: 34, borderRadius: 9, background: BRAND_GRADIENT }}
          >
            <ShieldCrossIcon size={19} style={{ color: "#fff" }} />
          </div>
          <div className="min-w-0" style={{ lineHeight: 1.15 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: "#fff" }} className="truncate">
              {t("appName")}
              {showHindi && <span style={{ fontWeight: 500, color: "#93A3BE", fontSize: 13 }}> · परिवहन सहायक</span>}
            </div>
            <div className="truncate hidden sm:block" style={{ fontSize: 11, color: C.onNavySub }}>
              Delhi–Dehradun Expressway — Road Accident First Response
            </div>
          </div>
        </div>

        {/* Segmented nav */}
        <nav
          className="ts-nav flex flex-none"
          style={{ marginLeft: "auto", gap: 4, background: "rgba(255,255,255,.07)", border: "1px solid rgba(255,255,255,.1)", borderRadius: 10, padding: 3 }}
        >
          {([
            { key: "SERVICES" as Tab, labelKey: "tabServices" as StringKey, hi: "सेवाएँ" },
            { key: "ACCIDENTS" as Tab, labelKey: "tabAccidents" as StringKey, hi: "दुर्घटनाएँ" },
            // Network is operator-only — citizens/guests never see this segment.
            ...(isOperator ? [{ key: "NETWORK" as Tab, labelKey: "tabNetwork" as StringKey, hi: "नेटवर्क" }] : []),
          ]).map((nt) => {
            const on = tab === nt.key;
            return (
              <button
                key={nt.key}
                onClick={() => setTab(nt.key)}
                className="flex flex-col items-center"
                style={{ padding: "5px 18px", border: "none", borderRadius: 8, cursor: "pointer", fontSize: 12.5, fontWeight: 600, lineHeight: 1.25, background: on ? "#fff" : "transparent", color: on ? C.navy800 : C.navInactive }}
              >
                <span>{t(nt.labelKey)}</span>
                {showHindi && (
                  <span style={{ fontSize: 10.5, fontWeight: 500, color: on ? C.muted : C.onNavySub }}>{nt.hi}</span>
                )}
              </button>
            );
          })}
        </nav>

        {/* Account + Language toggle + PWA install */}
        <div className="flex items-center gap-1.5 sm:gap-2 flex-none" style={{ marginLeft: "auto" }}>
          <InstallPWA />
          <AuthControl />
          <LanguageToggle />
        </div>
      </header>

      {/* ── Map / dashboard area ───────────────────────────────────────────── */}
      <main className="relative flex-1 overflow-hidden">
      <APIProvider apiKey={browserKey}>
        <Map
          mapId="DEMO_MAP_ID"
          defaultCenter={CORRIDOR_CENTER}
          defaultZoom={8}
          gestureHandling="greedy"
          disableDefaultUI
          className="absolute inset-0 w-full h-full"
          draggableCursor={isPickingPin ? "crosshair" : ""}
          onClick={(e) => {
            setOpenInfo(null);
            if (isPickingPin && e.detail.latLng) {
              handleMapClick(e.detail.latLng.lat, e.detail.latLng.lng);
            }
          }}
        >
          {/* ── Service layers — clustered, one clusterer per category ─────── */}
          {tab === "SERVICES" && (
            <>
              {SERVICE_LAYERS.filter((l) => l.source === "google").map((layer) => {
                const placeType = LAYER_TO_PLACE_TYPE[layer.key];
                if (!placeType || !activeServices.has(layer.key)) return null;
                const label = singularLabel(t(layer.labelKey));
                const items: ClusterItem[] = places[placeType]
                  .filter((p) => matchesSearch(p.name))
                  .map((p) => ({
                    key: p.id,
                    position: { lat: p.lat, lng: p.lng },
                    title: p.name,
                    onClick: () => openPlaceInfo(p, label),
                    pin: <LayerMarker layerKey={layer.key} color={layer.color} strokeColor={layer.strokeColor} />,
                  }));
                return <ClusteredLayer key={layer.key} items={items} color={layer.color} />;
              })}

              {/* Ambulance stations — synthetic */}
              {activeServices.has("AMBULANCE_STATION") && (
                <ClusteredLayer
                  color={LAYER_COLOR.AMBULANCE_STATION.color}
                  items={ambulances.filter((a) => matchesSearch(a.name)).map((a) => ({
                    key: a.id, position: { lat: a.lat, lng: a.lng }, title: a.name,
                    onClick: () => setOpenInfo({ position: { lat: a.lat, lng: a.lng }, content: <AmbulancePopup a={a} /> }),
                    pin: <LayerMarker layerKey="AMBULANCE_STATION" color={LAYER_COLOR.AMBULANCE_STATION.color} strokeColor={LAYER_COLOR.AMBULANCE_STATION.strokeColor} />,
                  }))}
                />
              )}

              {/* Fire stations — synthetic */}
              {activeServices.has("FIRE_STATION") && (
                <ClusteredLayer
                  color={LAYER_COLOR.FIRE_STATION.color}
                  items={fireStations.filter((f) => matchesSearch(f.name)).map((f) => ({
                    key: f.id, position: { lat: f.lat, lng: f.lng }, title: f.name,
                    onClick: () => setOpenInfo({ position: { lat: f.lat, lng: f.lng }, content: <FireStationPopup f={f} /> }),
                    pin: <LayerMarker layerKey="FIRE_STATION" color={LAYER_COLOR.FIRE_STATION.color} strokeColor={LAYER_COLOR.FIRE_STATION.strokeColor} />,
                  }))}
                />
              )}

              {/* Towing / recovery — synthetic */}
              {activeServices.has("TOWING_STATION") && (
                <ClusteredLayer
                  color={LAYER_COLOR.TOWING_STATION.color}
                  items={towingStations.filter((w) => matchesSearch(w.name)).map((w) => ({
                    key: w.id, position: { lat: w.lat, lng: w.lng }, title: w.name,
                    onClick: () => setOpenInfo({ position: { lat: w.lat, lng: w.lng }, content: <TowingStationPopup w={w} /> }),
                    pin: <LayerMarker layerKey="TOWING_STATION" color={LAYER_COLOR.TOWING_STATION.color} strokeColor={LAYER_COLOR.TOWING_STATION.strokeColor} />,
                  }))}
                />
              )}

              {/* Suraksha Mitra volunteers — real registrations, privacy-gated popup.
                  Marker title is first-name only so non-operators don't get the full
                  name via the native map tooltip. */}
              {activeServices.has("SURAKSHA_MITRA") && (
                <ClusteredLayer
                  color={LAYER_COLOR.SURAKSHA_MITRA.color}
                  items={surakshaMitras
                    .filter((m) => matchesSearch(isOperator ? m.name : (m.name.trim().split(/\s+/)[0] || "Volunteer")))
                    .map((m) => ({
                      key: m.id, position: { lat: m.lat, lng: m.lng },
                      title: isOperator ? m.name : (m.name.trim().split(/\s+/)[0] || "Suraksha Mitra"),
                      onClick: () => { setSelectedZone({ lat: m.lat, lng: m.lng, radiusKm: m.coverageRadiusKm }); setOpenInfo({ position: { lat: m.lat, lng: m.lng }, content: <SurakshaMitraPopup m={m} isOperator={isOperator} /> }); },
                      pin: <LayerMarker layerKey="SURAKSHA_MITRA" color={LAYER_COLOR.SURAKSHA_MITRA.color} strokeColor={LAYER_COLOR.SURAKSHA_MITRA.strokeColor} />,
                    }))}
                />
              )}

              {/* Selected volunteer's coverage-radius zone. */}
              {selectedZone && <VolunteerZoneCircle zone={selectedZone} />}
            </>
          )}

          {/* ── Accident layers — clustered ─────────────────────────────── */}
          {tab === "ACCIDENTS" && (
            <>
              {/* Road defects (potholes) — brown diamonds */}
              {activeAccidents.has("POTHOLE") && !potholesLoading && (
                <ClusteredLayer
                  color={LAYER_COLOR.POTHOLE.color}
                  items={potholes.map((p) => ({
                    key: p.id, position: { lat: p.lat, lng: p.lng },
                    onClick: () => setOpenInfo({ position: { lat: p.lat, lng: p.lng }, content: <PotholePopup p={p} /> }),
                    pin: <LayerMarker layerKey="POTHOLE" color={LAYER_COLOR.POTHOLE.color} strokeColor={LAYER_COLOR.POTHOLE.strokeColor} />,
                  }))}
                />
              )}

              {/* Reported accidents — ROLE-BRANCHED. Operator: individual
                  clustered markers + detailed popups (unchanged). Citizen/guest:
                  aggregated density heat zones only — no individual markers,
                  cards, or per-report detail ever reaches them. */}
              {activeAccidents.has("REPORTED_ACCIDENT") && (
                isOperator ? (
                  <ClusteredLayer
                    color={LAYER_COLOR.REPORTED_ACCIDENT.color}
                    items={reportedAccidents.map((a) => ({
                      key: a.id, position: { lat: a.lat, lng: a.lng }, title: a.location_label,
                      onClick: () => setOpenInfo({ position: { lat: a.lat, lng: a.lng }, content: <ReportedAccidentPopup a={a} /> }),
                      pin: <LayerMarker layerKey="REPORTED_ACCIDENT" color={LAYER_COLOR.REPORTED_ACCIDENT.color} strokeColor={LAYER_COLOR.REPORTED_ACCIDENT.strokeColor} />,
                    }))}
                  />
                ) : (
                  <AccidentDensityLayer accidents={reportedAccidents} onZoneClick={handleAccidentZoneClick} />
                )
              )}
            </>
          )}

          {/* ── Incident location pin ────────────────────────────────────── */}
          {incidentPinLocation && (
            <AdvancedMarker
              position={incidentPinLocation}
              title="Incident location"
              onClick={() =>
                setOpenInfo({
                  position: incidentPinLocation,
                  content: (
                    <div className="text-xs">
                      <p className="font-semibold text-gray-900">Incident location</p>
                      <p className="text-gray-500 break-words max-w-[200px]">{incidentPinLabel}</p>
                    </div>
                  ),
                })
              }
            >
              <IncidentPin />
            </AdvancedMarker>
          )}

          {/* ── Route polylines ───────────────────────────────────────────── */}
          {mapRoutes.map((route) => {
            // Police route has dashArray set — render thinner + semi-transparent
            // to distinguish from the solid hospital route without relying on
            // the `icons` prop (which @vis.gl/react-google-maps silently ignores).
            const isSecondary = Boolean(route.dashArray);
            return (
              <Polyline
                key={route.id}
                path={route.coords.map(([lat, lng]) => ({ lat, lng }))}
                strokeColor={route.color}
                strokeWeight={isSecondary ? 3 : 4}
                strokeOpacity={isSecondary ? 0.6 : 0.85}
              />
            );
          })}

          {/* ── Simulated vehicle markers — cosmetic demo animation along each
              highlighted route; not a real position feed (see CLAUDE.md).
              One per emergency service the engine actually recommended for
              this incident (ambulance / fire / towing), each on its own clock. ── */}
          {simulatedVehicles.map((v) => {
            const startedAtMs = new Date(v.startedAt).getTime();
            const elapsedMin = (simTick - startedAtMs) / 60000;
            const fraction = v.durationMin > 0 ? elapsedMin / v.durationMin : 1;
            const pos = interpolateAlongPath(v.coords, fraction);
            const label = SIM_VEHICLE_STYLE[v.kind].label;
            return (
              <AdvancedMarker
                key={v.id}
                position={pos}
                title={`${label} — demonstration only, not live tracking`}
                zIndex={999}
                onClick={() =>
                  setOpenInfo({
                    position: pos,
                    content: (
                      <div className="text-xs max-w-[220px]">
                        <p className="font-semibold text-gray-900">{label}</p>
                        <p className="text-gray-500 mt-0.5 leading-relaxed">
                          Demonstration animation along the calculated route — not a live GPS
                          position. We do not track vehicles.
                        </p>
                      </div>
                    ),
                  })
                }
              >
                <SimulatedVehicleMarker kind={v.kind} />
              </AdvancedMarker>
            );
          })}

          {/* ── InfoWindow ────────────────────────────────────────────────── */}
          {openInfo && (
            <InfoWindow position={openInfo.position} onClose={() => { setOpenInfo(null); setSelectedZone(null); }} shouldFocus={false}>
              {openInfo.content}
            </InfoWindow>
          )}
        </Map>
        <MapHandle onMap={setMapInstance} onCenter={setMapCenter} />
        {tab !== "NETWORK" && <MapControls />}
      </APIProvider>

      {/* ── Floating left panel (Services / Accidents) ─────────────────────── */}
      {tab !== "NETWORK" && (
        <FloatingPanel
          tab={tab}
          open={panelOpen}
          onToggle={() => setPanelOpen((v) => !v)}
          showHindi={showHindi}
          search={searchQuery}
          onSearch={setSearchQuery}
          serviceLayers={serviceLayerRows}
          nearby={nearbyFacilities}
          showDefects={activeAccidents.has("POTHOLE")}
          showAccidents={activeAccidents.has("REPORTED_ACCIDENT")}
          onToggleDefects={() => toggleAccident("POTHOLE")}
          onToggleAccidents={() => toggleAccident("REPORTED_ACCIDENT")}
          reports={reportCards}
          isOperator={isOperator}
        />
      )}

      {/* Places error notice — only shown when server key is missing/broken */}
      {placesError && tab === "SERVICES" && panelOpen && (
        <div className="absolute z-[500]" style={{ left: 14, bottom: 14, width: 342 }}>
          <p className="text-[10px] px-3" style={{ color: C.red }}>{t("placesLoadError")}</p>
        </div>
      )}

      {/* ── Operator dashboard (full-page overlay) — operator-only ─────────── */}
      {/* Sub-tabs: Network (Signals) · Call Analytics · Ambiguity review. */}
      {tab === "NETWORK" && isOperator && (
        <div className="absolute inset-0 z-[600] overflow-y-auto" style={{ background: C.page }}>
          <OperatorDashboard accidents={reportedAccidents} onReviewsChanged={refetchAccidents} />
        </div>
      )}

      {/* ── Pin-picking hint ─────────────────────────────────────────────────── */}
      {isPickingPin && (
        <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 z-[1001] flex justify-center pointer-events-none">
          <div
            className="text-white text-xs font-semibold px-5 py-2.5 rounded-full shadow-xl flex items-center gap-2"
            style={{ background: C.navy800 }}
          >
            <svg className="w-4 h-4 flex-shrink-0" aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            Tap anywhere on map to set incident location
            <button
              onClick={() => setIsPickingPin(false)}
              className="pointer-events-auto ml-2 hover:text-white font-normal text-[11px] underline"
              style={{ color: C.navInactive }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Timeline pill (bottom-left) ────────────────────────────────────── */}
      {tab !== "NETWORK" && !isPickingPin && (
        <button
          onClick={() => setTimelineOpen(true)}
          className="absolute z-[500] flex items-center gap-[7px]"
          style={{
            left: panelOpen ? 368 : 16,
            bottom: 14,
            background: "#fff",
            border: `1px solid ${C.border}`,
            borderRadius: 99,
            padding: "8px 14px",
            fontSize: 12.5,
            fontWeight: 500,
            color: C.body,
            cursor: "pointer",
            boxShadow: SHADOW.mapControl,
          }}
        >
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: C.blue, display: "inline-block" }} />
          Timeline
          {showHindi && <span style={{ color: C.muted }}>· समयरेखा</span>}
          {eventCount > 0 && (
            <span
              className="inline-flex items-center justify-center"
              style={{ width: 16, height: 16, borderRadius: "50%", background: C.navy800, color: "#fff", fontSize: 9, fontWeight: 700 }}
            >
              {eventCount > 9 ? "9+" : eventCount}
            </span>
          )}
        </button>
      )}

      {/* ── Bottom-right action stack: SOS (voice, red pulsing) above Report ── */}
      {!reportOpen && !isPickingPin && (
        <div
          className="absolute z-[500] flex flex-col items-end"
          style={{ right: 22, bottom: "calc(22px + env(safe-area-inset-bottom))", gap: 12 }}
        >
          {/* SOS — emergency voice dispatch. The red pulsing CTA (now the priority). */}
          <button
            onClick={() => setSosLangChoice(true)}
            aria-label="SOS — talk to the voice dispatcher"
            className="flex items-center gap-2"
            style={{
              background: CTA_GRADIENT, color: "#fff", border: "none", borderRadius: 99,
              padding: "13px 22px", fontSize: 15, fontWeight: 800, letterSpacing: ".02em",
              cursor: "pointer", boxShadow: SHADOW.fab, animation: "tsPulse 2.6s infinite",
            }}
          >
            <MicIcon size={18} />
            SOS{showHindi && <span style={{ fontWeight: 600, opacity: 0.9 }}> · एसओएस</span>}
          </button>

          {/* Report Incident — the full report sheet. Secondary (navy, no pulse). */}
          <button
            onClick={openReport}
            className="flex items-center gap-2.5"
            style={{
              background: C.navy800, color: "#fff", border: "none", borderRadius: 99,
              padding: "12px 20px", fontSize: 13.5, fontWeight: 700, cursor: "pointer",
              boxShadow: SHADOW.floatBtn,
            }}
          >
            <span
              className="inline-flex items-center justify-center"
              style={{ width: 19, height: 19, borderRadius: "50%", background: "rgba(255,255,255,.18)", fontSize: 14, fontWeight: 600 }}
            >
              +
            </span>
            {t("reportTitle")}
            {showHindi && <span style={{ fontWeight: 500, opacity: 0.8 }}> · रिपोर्ट करें</span>}
          </button>
        </div>
      )}

      {isPickingPin && pinnedLocation && (
        <button
          onClick={() => { setIsPickingPin(false); setReportOpen(true); }}
          className="absolute z-[1001] text-white text-sm font-semibold px-4 py-2.5 rounded-full shadow-lg"
          style={{ right: 22, bottom: 22, background: CTA_GRADIENT }}
        >
          Use this location
        </button>
      )}
      </main>

      {/* ── Report panel ─────────────────────────────────────────────────────── */}
      <ReportPanel
        key={reportSession}
        open={reportOpen}
        initialMode={reportInitialMode}
        autoStartVoice={reportAutoStartVoice}
        initialVoiceLocale={reportVoiceLocale ?? undefined}
        pinnedLocation={pinnedLocation}
        pinnedLabel={pinnedLabel}
        onRequestPin={requestPin}
        onClose={closeReport}
        onPotholeSubmitted={async (p) => {
          try {
            await fetch("/api/potholes", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                id: p.id,
                lat: p.lat,
                lng: p.lng,
                road: p.road,
                severity: p.severity,
                description: p.description ?? null,
                reported_date: p.reportedDate,
              }),
            });
          } catch { /* non-fatal — marker will appear on next refetch */ }
          setTab("ACCIDENTS");
          closeReport();
          refetchPotholes();
        }}
        onAccidentSubmitted={async (r) => {
          try {
            await fetch("/api/accidents", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                id: r.id,
                lat: r.location.lat,
                lng: r.location.lng,
                location_label: r.locationLabel,
                description: r.description || null,
                severity: r.severity || null,
                report_mode: r.reportMode,
                flags: r.flags ?? [],
                reported_date: r.timestamp.slice(0, 10),
              }),
            });
          } catch { /* non-fatal */ }
          refetchAccidents();
        }}
      />

      {/* ── Timeline panel ────────────────────────────────────────────────────── */}
      <TimelinePanel
        open={timelineOpen}
        onClose={() => setTimelineOpen(false)}
        onViewRecord={(id) => { setTimelineOpen(false); setRecordIncidentId(id); }}
      />

      {/* ── Incident Record overlay ───────────────────────────────────────────── */}
      <IncidentRecord
        incidentId={recordIncidentId}
        onClose={() => setRecordIncidentId(null)}
      />

      {/* ── Safety profile — opened by the PWA "View dashboard" (signed in) or
          the home-screen name chip. AuthControl also owns its own instance for
          the header dropdown; this one is driven by the launch intent. ── */}
      {profileOpen && <SafetyProfileSheet showHindi={showHindi} onClose={() => setProfileOpen(false)} />}

      {/* ── SOS: quick language chooser → voice dispatcher (auto-start) ── */}
      {sosLangChoice && (
        <>
          <div className="fixed inset-0 z-[2099]" style={{ background: "rgba(14,26,47,.45)" }} onClick={() => setSosLangChoice(false)} />
          <div
            className="fixed z-[2100] flex flex-col bg-white"
            style={{ left: "50%", top: "50%", transform: "translate(-50%,-50%)", width: "min(360px, 92vw)", borderRadius: RADIUS.card, boxShadow: "0 20px 60px rgba(14,26,47,.4)", overflow: "hidden" }}
          >
            <div style={{ padding: "16px 18px 2px" }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: C.ink }}>
                Choose a language{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 13 }}> · भाषा चुनें</span>}
              </div>
              <p style={{ fontSize: 12, color: C.secondary, marginTop: 2 }}>The voice dispatcher will speak this language, then take your report.</p>
            </div>
            <div className="flex flex-col" style={{ gap: 10, padding: "12px 18px 6px" }}>
              <button
                onClick={() => startSosVoice("en-IN")}
                style={{ padding: "13px 16px", border: "none", borderRadius: 12, background: CTA_GRADIENT, color: "#fff", fontSize: 14.5, fontWeight: 700, cursor: "pointer", textAlign: "left", boxShadow: "0 6px 18px rgba(198,54,44,.3)" }}
              >
                English<span style={{ fontWeight: 500, opacity: 0.85 }}> · Talk to the dispatcher in English</span>
              </button>
              <button
                onClick={() => startSosVoice("hi-IN")}
                style={{ padding: "13px 16px", border: `1px solid ${C.border}`, borderRadius: 12, background: "#fff", color: C.ink, fontSize: 14.5, fontWeight: 700, cursor: "pointer", textAlign: "left" }}
              >
                हिंदी<span style={{ fontWeight: 500, color: C.muted }}> · वॉइस डिस्पैचर से हिंदी में बात करें</span>
              </button>
            </div>
            <button
              onClick={() => setSosLangChoice(false)}
              style={{ padding: "11px", border: "none", borderTop: `1px solid ${C.hairline}`, background: "transparent", color: C.secondary, fontSize: 13, fontWeight: 600, cursor: "pointer" }}
            >
              Cancel{showHindi && " · रद्द करें"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
