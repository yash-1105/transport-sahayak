"use client";

import React, { useState, useMemo, useCallback, useEffect, type ReactNode } from "react";
import {
  APIProvider,
  Map,
  AdvancedMarker,
  InfoWindow,
  Polyline,
} from "@vis.gl/react-google-maps";
import { useRoutingStore, type SimulatedVehicleKind } from "@/store/routingStore";
import { useEventLog } from "@/store/eventLog";
import TimelinePanel from "@/components/TimelinePanel";
import LanguageToggle from "@/components/LanguageToggle";
import InstallPWA from "@/components/InstallPWA";
import IncidentRecord from "@/components/IncidentRecord";
import { useT } from "@/hooks/useI18n";
import { usePlaces, LAYER_TO_PLACE_TYPE } from "@/hooks/usePlaces";
import { usePotholes } from "@/hooks/usePotholes";
import { useAccidents } from "@/hooks/useAccidents";
import { CORRIDOR_WAYPOINTS, CORRIDOR_CENTER, CORRIDOR_WAYPOINT_RADIUS_M } from "@/lib/corridorWaypoints";
import type { StringKey } from "@/i18n/strings";
import type { GooglePlace } from "@/lib/types";

// Synthetic-only layers still loaded from seed files
import ambulanceData from "../../data/ambulance-stations.json";
import fireStationData from "../../data/fire-stations.json";
import towingStationData from "../../data/towing-stations.json";
import blackspotsData from "../../data/blackspots.json";

import type {
  AmbulanceStation,
  FireStation,
  TowingStation,
  AccidentReport,
  Blackspot,
  DbPothole,
  DbAccident,
  ServiceLayerType,
  AccidentLayerType,
  GeoPoint,
} from "@/lib/types";
import { reverseGeocode } from "@/lib/geocode";
import ReportPanel from "@/components/report/ReportPanel";
import LayerSidebar from "@/components/LayerSidebar";

// ── Constants ─────────────────────────────────────────────────────────────────

// Corridor constants imported from @/lib/corridorWaypoints

// ── Layer config ──────────────────────────────────────────────────────────────

const SERVICE_LAYERS: {
  key: ServiceLayerType;
  labelKey: StringKey;
  color: string;
  strokeColor: string;
  source: "google" | "synthetic";
}[] = [
  { key: "HOSPITAL",          labelKey: "layerHospitals", color: "#2563eb", strokeColor: "#1d4ed8", source: "google" },
  { key: "AMBULANCE_STATION", labelKey: "layerAmbulance", color: "#16a34a", strokeColor: "#15803d", source: "synthetic" },
  { key: "FIRE_STATION",      labelKey: "layerFire",      color: "#dc2626", strokeColor: "#b91c1c", source: "synthetic" },
  { key: "TOWING_STATION",    labelKey: "layerTowing",    color: "#57534e", strokeColor: "#3f3c3a", source: "synthetic" },
  { key: "MECHANIC",          labelKey: "layerMechanics", color: "#6b7280", strokeColor: "#4b5563", source: "google" },
  { key: "POLICE",            labelKey: "layerPolice",    color: "#1e3a8a", strokeColor: "#1e3069", source: "google" },
  { key: "PHARMACY",          labelKey: "layerPharmacy",  color: "#7c3aed", strokeColor: "#6d28d9", source: "google" },
  { key: "GAS_STATION",       labelKey: "layerFuel",      color: "#0891b2", strokeColor: "#0e7490", source: "google" },
];

const ACCIDENT_LAYERS: {
  key: AccidentLayerType;
  labelKey: StringKey;
  color: string;
  strokeColor: string;
  source: "synthetic" | "live";
}[] = [
  { key: "POTHOLE",           labelKey: "layerPotholes",           color: "#78350f", strokeColor: "#5c2a0b", source: "live" },
  { key: "REPORTED_ACCIDENT", labelKey: "layerReportedAccidents",  color: "#ea580c", strokeColor: "#c2410c", source: "live" },
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
function BlackspotIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M7 4v5" stroke="white" strokeWidth="2.2" strokeLinecap="round"/>
      <circle cx="7" cy="11.5" r="1.3" fill="white"/>
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
  BLACKSPOT:           { shape: "triangle", Icon: BlackspotIcon },
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

// Mini shape used in filter chips — mirrors the map marker shape at small scale
function ChipShape({ layerKey, color }: { layerKey: string; color: string }) {
  const shape = LAYER_MARKER[layerKey]?.shape ?? "circle";
  if (shape === "triangle") {
    return (
      <svg width="10" height="9" viewBox="0 0 10 9" style={{ flexShrink: 0, display: "inline-block" }}>
        <path d="M5 1L9.5 8.5H.5L5 1z" fill={color}/>
      </svg>
    );
  }
  if (shape === "diamond") {
    return (
      <span style={{
        display: "inline-block", flexShrink: 0,
        width: 9, height: 9,
        background: color, borderRadius: 1.5,
        transform: "rotate(45deg)",
      }}/>
    );
  }
  if (shape === "square") {
    return (
      <span style={{
        display: "inline-block", flexShrink: 0,
        width: 9, height: 9,
        background: color, borderRadius: 2,
      }}/>
    );
  }
  return (
    <span style={{
      display: "inline-block", flexShrink: 0,
      width: 9, height: 9,
      background: color, borderRadius: "50%",
    }}/>
  );
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
function interpolateAlongPath(coords: [number, number][], fraction: number): { lat: number; lng: number } {
  if (coords.length === 0) return { lat: 0, lng: 0 };
  const clamped = Math.min(1, Math.max(0, fraction));
  if (coords.length === 1 || clamped <= 0) return { lat: coords[0][0], lng: coords[0][1] };
  if (clamped >= 1) {
    const last = coords[coords.length - 1];
    return { lat: last[0], lng: last[1] };
  }
  const scaled = clamped * (coords.length - 1);
  const i = Math.floor(scaled);
  const t = scaled - i;
  const a = coords[i];
  const b = coords[Math.min(i + 1, coords.length - 1)];
  return { lat: a[0] + (b[0] - a[0]) * t, lng: a[1] + (b[1] - a[1]) * t };
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

function BlackspotPopup({ b }: { b: Blackspot }) {
  return (
    <div className="text-xs leading-relaxed min-w-[220px]">
      <p className="font-semibold text-sm text-gray-900">{b.name}</p>
      <p className="text-gray-500 mb-1">{b.highway} · {b.district}</p>
      <table className="w-full text-gray-700">
        <tbody>
          <tr><td className="pr-2 text-gray-500">Accidents (3 yr)</td><td className="font-medium text-red-700">{b.accidentsLast3Years}</td></tr>
          <tr><td className="pr-2 text-gray-500">Deaths (3 yr)</td><td className="font-medium text-red-900">{b.deathsLast3Years}</td></tr>
          <tr><td className="pr-2 text-gray-500">Hazard</td><td>{b.primaryHazard}</td></tr>
          <tr><td className="pr-2 text-gray-500">Peak period</td><td>{b.periodOfPeak}</td></tr>
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

// ── InfoWindow state type ─────────────────────────────────────────────────────

interface MarkerInfo {
  position: { lat: number; lng: number };
  content: React.ReactNode;
}

// ── Main component ────────────────────────────────────────────────────────────

type Tab = "SERVICES" | "ACCIDENTS";

export default function MapView() {
  const t = useT();
  const browserKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY ?? "";

  const [tab, setTab] = useState<Tab>("SERVICES");
  const [activeServices, setActiveServices] = useState<Set<ServiceLayerType>>(
    new Set(SERVICE_LAYERS.map((l) => l.key))
  );
  const [activeAccidents, setActiveAccidents] = useState<Set<AccidentLayerType>>(
    new Set(ACCIDENT_LAYERS.map((l) => l.key))
  );

  const [reportOpen, setReportOpen] = useState(false);
  const [isPickingPin, setIsPickingPin] = useState(false);
  const [pinnedLocation, setPinnedLocation] = useState<GeoPoint | null>(null);
  const [pinnedLabel, setPinnedLabel] = useState("");
  const [openInfo, setOpenInfo] = useState<MarkerInfo | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

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
  const { results: places, loading: placesLoading, hasError: placesError } = usePlaces(
    CORRIDOR_WAYPOINTS, CORRIDOR_WAYPOINT_RADIUS_M
  );

  // ── Synthetic seed data (labelled as sample) ──────────────────────────────
  const ambulances    = useMemo(() => ambulanceData.ambulanceStations as AmbulanceStation[], []);
  const fireStations  = useMemo(() => fireStationData.fireStations as FireStation[], []);
  const towingStations = useMemo(() => towingStationData.towingStations as TowingStation[], []);
  const blackspots    = useMemo(() => blackspotsData.blackspots as Blackspot[], []);
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

  function openReport() { setPinnedLocation(null); setPinnedLabel(""); setReportOpen(true); }
  function closeReport() { setReportOpen(false); setIsPickingPin(false); }
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

  // ── Missing browser key guard ──────────────────────────────────────────────

  if (!browserKey) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-100 p-8">
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
    <div className="relative w-full h-screen overflow-hidden bg-gray-100">
      <APIProvider apiKey={browserKey}>
        <Map
          mapId="DEMO_MAP_ID"
          defaultCenter={CORRIDOR_CENTER}
          defaultZoom={8}
          gestureHandling="greedy"
          className="w-full h-full"
          draggableCursor={isPickingPin ? "crosshair" : ""}
          onClick={(e) => {
            setOpenInfo(null);
            if (isPickingPin && e.detail.latLng) {
              handleMapClick(e.detail.latLng.lat, e.detail.latLng.lng);
            }
          }}
        >
          {/* ── Google Places: live service layers ────────────────────────── */}
          {tab === "SERVICES" && (
            <>
              {SERVICE_LAYERS.filter((l) => l.source === "google").map((layer) => {
                const placeType = LAYER_TO_PLACE_TYPE[layer.key];
                if (!placeType || !activeServices.has(layer.key)) return null;
                return places[placeType].map((p) => (
                  <AdvancedMarker
                    key={p.id}
                    position={{ lat: p.lat, lng: p.lng }}
                    title={p.name}
                    onClick={() => openPlaceInfo(p, singularLabel(t(layer.labelKey)))}
                  >
                    <LayerMarker layerKey={layer.key} color={layer.color} strokeColor={layer.strokeColor} />
                  </AdvancedMarker>
                ));
              })}

              {/* Ambulance stations — synthetic */}
              {activeServices.has("AMBULANCE_STATION") &&
                ambulances.map((a) => (
                  <AdvancedMarker
                    key={a.id}
                    position={{ lat: a.lat, lng: a.lng }}
                    title={a.name}
                    onClick={() => setOpenInfo({ position: { lat: a.lat, lng: a.lng }, content: <AmbulancePopup a={a} /> })}
                  >
                    <LayerMarker layerKey="AMBULANCE_STATION" color={LAYER_COLOR.AMBULANCE_STATION.color} strokeColor={LAYER_COLOR.AMBULANCE_STATION.strokeColor} />
                  </AdvancedMarker>
                ))}

              {/* Fire stations — synthetic */}
              {activeServices.has("FIRE_STATION") &&
                fireStations.map((f) => (
                  <AdvancedMarker
                    key={f.id}
                    position={{ lat: f.lat, lng: f.lng }}
                    title={f.name}
                    onClick={() => setOpenInfo({ position: { lat: f.lat, lng: f.lng }, content: <FireStationPopup f={f} /> })}
                  >
                    <LayerMarker layerKey="FIRE_STATION" color={LAYER_COLOR.FIRE_STATION.color} strokeColor={LAYER_COLOR.FIRE_STATION.strokeColor} />
                  </AdvancedMarker>
                ))}

              {/* Towing / recovery — synthetic */}
              {activeServices.has("TOWING_STATION") &&
                towingStations.map((w) => (
                  <AdvancedMarker
                    key={w.id}
                    position={{ lat: w.lat, lng: w.lng }}
                    title={w.name}
                    onClick={() => setOpenInfo({ position: { lat: w.lat, lng: w.lng }, content: <TowingStationPopup w={w} /> })}
                  >
                    <LayerMarker layerKey="TOWING_STATION" color={LAYER_COLOR.TOWING_STATION.color} strokeColor={LAYER_COLOR.TOWING_STATION.strokeColor} />
                  </AdvancedMarker>
                ))}

            </>
          )}

          {/* ── Accident layers — always synthetic ───────────────────────── */}
          {tab === "ACCIDENTS" && (
            <>
              {activeAccidents.has("BLACKSPOT") &&
                blackspots.map((b) => (
                  <AdvancedMarker
                    key={b.id}
                    position={{ lat: b.lat, lng: b.lng }}
                    title={b.name}
                    onClick={() => setOpenInfo({ position: { lat: b.lat, lng: b.lng }, content: <BlackspotPopup b={b} /> })}
                  >
                    <LayerMarker layerKey="BLACKSPOT" color={LAYER_COLOR.BLACKSPOT.color} strokeColor={LAYER_COLOR.BLACKSPOT.strokeColor} />
                  </AdvancedMarker>
                ))}

              {/* DB-backed potholes — loaded from Supabase via usePotholes hook */}
              {activeAccidents.has("POTHOLE") && !potholesLoading &&
                potholes.map((p) => (
                  <AdvancedMarker
                    key={p.id}
                    position={{ lat: p.lat, lng: p.lng }}
                    onClick={() => setOpenInfo({ position: { lat: p.lat, lng: p.lng }, content: <PotholePopup p={p} /> })}
                  >
                    <LayerMarker layerKey="POTHOLE" color={LAYER_COLOR.POTHOLE.color} strokeColor={LAYER_COLOR.POTHOLE.strokeColor} />
                  </AdvancedMarker>
                ))}

              {/* DB-backed reported accidents — loaded from Supabase via useAccidents hook */}
              {activeAccidents.has("REPORTED_ACCIDENT") &&
                reportedAccidents.map((a) => (
                  <AdvancedMarker
                    key={a.id}
                    position={{ lat: a.lat, lng: a.lng }}
                    title={a.location_label}
                    onClick={() => setOpenInfo({ position: { lat: a.lat, lng: a.lng }, content: <ReportedAccidentPopup a={a} /> })}
                  >
                    <LayerMarker layerKey="REPORTED_ACCIDENT" color={LAYER_COLOR.REPORTED_ACCIDENT.color} strokeColor={LAYER_COLOR.REPORTED_ACCIDENT.strokeColor} />
                  </AdvancedMarker>
                ))}
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
            <InfoWindow position={openInfo.position} onClose={() => setOpenInfo(null)} shouldFocus={false}>
              {openInfo.content}
            </InfoWindow>
          )}
        </Map>
      </APIProvider>

      {/* ── Controls overlay ─────────────────────────────────────────────────── */}
      <div className="absolute top-0 left-0 right-0 z-[1000] pointer-events-none">
        {/* Header */}
        <div className="pointer-events-auto bg-[#0f2044] text-white px-4 py-2.5 flex items-center gap-3 shadow-md">
          <button
            onClick={() => setSidebarOpen(true)}
            aria-label="Open layer controls"
            className="w-8 h-8 flex items-center justify-center rounded hover:bg-white/10 flex-shrink-0"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold tracking-wide truncate">{t("appName")}</p>
            <p className="text-[10px] text-blue-200 leading-tight truncate hidden sm:block">{t("appTagline")}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <InstallPWA />
            <LanguageToggle />
          </div>
        </div>

        {/* Tab toggle */}
        <div className="pointer-events-auto flex bg-white border-b border-gray-200 shadow-sm">
          <button
            onClick={() => setTab("SERVICES")}
            className={`flex-1 py-2 text-xs font-semibold tracking-wide uppercase border-b-2 transition-colors ${
              tab === "SERVICES" ? "border-[#0f2044] text-[#0f2044]" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t("tabServices")}
          </button>
          <button
            onClick={() => setTab("ACCIDENTS")}
            className={`flex-1 py-2 text-xs font-semibold tracking-wide uppercase border-b-2 transition-colors ${
              tab === "ACCIDENTS" ? "border-red-700 text-red-700" : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t("tabAccidents")}
          </button>
        </div>

        {/* Filter chips */}
        <div className="pointer-events-auto bg-white/95 backdrop-blur-sm border-b border-gray-200 shadow-sm">
          <div className="chips-row hidden sm:flex gap-2 overflow-x-auto px-3 py-2">
            {tab === "SERVICES" &&
              SERVICE_LAYERS.map((layer) => {
                const active = activeServices.has(layer.key);
                const placeType = LAYER_TO_PLACE_TYPE[layer.key];
                const isLoading = layer.source === "google" && placeType ? placesLoading[placeType] : false;
                const count = layer.source === "google" && placeType ? places[placeType].length : null;
                return (
                  <button
                    key={layer.key}
                    onClick={() => toggleService(layer.key)}
                    className={`flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-medium transition-all ${
                      active ? "border-gray-400 bg-white text-gray-800 shadow-sm" : "border-gray-200 bg-gray-50 text-gray-400"
                    }`}
                  >
                    <ChipShape layerKey={layer.key} color={active ? layer.color : "#d1d5db"} />
                    {t(layer.labelKey)}
                    {isLoading && (
                      <span className="inline-block w-3 h-3 border-[1.5px] border-gray-300 border-t-gray-600 rounded-full animate-spin" />
                    )}
                    {!isLoading && count !== null && (
                      <span className="text-[10px] text-gray-400 font-normal">({count})</span>
                    )}
                    {layer.source === "synthetic" && (
                      <span className="text-[9px] text-amber-600 font-normal ml-0.5">sample</span>
                    )}
                  </button>
                );
              })}

            {tab === "ACCIDENTS" &&
              ACCIDENT_LAYERS.map((layer) => {
                const active = activeAccidents.has(layer.key);
                return (
                  <button
                    key={layer.key}
                    onClick={() => toggleAccident(layer.key)}
                    className={`flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded border text-[11px] font-medium transition-all ${
                      active ? "border-gray-400 bg-white text-gray-800 shadow-sm" : "border-gray-200 bg-gray-50 text-gray-400"
                    }`}
                  >
                    <ChipShape layerKey={layer.key} color={active ? layer.color : "#d1d5db"} />
                    {t(layer.labelKey)}
                    {layer.source === "synthetic" && (
                      <span className="text-[9px] text-amber-600 font-normal ml-0.5">sample</span>
                    )}
                  </button>
                );
              })}
          </div>

          {/* Places error notice — only shown when server key is missing/broken */}
          {placesError && tab === "SERVICES" && (
            <div className="px-3 pb-1.5">
              <p className="text-[10px] text-red-600">{t("placesLoadError")}</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Pin-picking hint ─────────────────────────────────────────────────── */}
      {isPickingPin && (
        <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 z-[1001] flex justify-center pointer-events-none">
          <div className="bg-[#0f2044] text-white text-xs font-semibold px-5 py-2.5 rounded-full shadow-xl flex items-center gap-2">
            <svg className="w-4 h-4 flex-shrink-0" aria-hidden="true" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            Tap anywhere on map to set incident location
            <button
              onClick={() => setIsPickingPin(false)}
              className="pointer-events-auto ml-2 text-blue-200 hover:text-white font-normal text-[11px] underline"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Timeline button ───────────────────────────────────────────────────── */}
      {!isPickingPin && (
        <div className="absolute bottom-10 left-4 z-[1000]">
          <button
            onClick={() => setTimelineOpen(true)}
            className="bg-white hover:bg-gray-50 border border-gray-200 text-gray-700 text-xs font-bold px-3 py-2 rounded-lg shadow-md flex items-center gap-2 transition-colors"
          >
            <svg className="w-3.5 h-3.5 text-gray-500" aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Timeline
            {eventCount > 0 && (
              <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-[#0f2044] text-white text-[9px] font-black">
                {eventCount > 9 ? "9+" : eventCount}
              </span>
            )}
          </button>
        </div>
      )}

      {/* ── FAB ──────────────────────────────────────────────────────────────── */}
      {!reportOpen && !isPickingPin && (
        <div className="absolute bottom-10 right-4 z-[1000]">
          <button
            onClick={openReport}
            className="bg-red-600 hover:bg-red-700 active:bg-red-800 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow-lg flex items-center gap-2 transition-colors"
          >
            <svg className="w-4 h-4" aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            {t("reportTitle")}
          </button>
        </div>
      )}

      {isPickingPin && pinnedLocation && (
        <div className="absolute bottom-10 right-4 z-[1001]">
          <button
            onClick={() => { setIsPickingPin(false); setReportOpen(true); }}
            className="bg-amber-500 hover:bg-amber-600 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow-lg"
          >
            Use this location
          </button>
        </div>
      )}

      {/* ── Report panel ─────────────────────────────────────────────────────── */}
      <ReportPanel
        open={reportOpen}
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

      {/* ── Layer sidebar ─────────────────────────────────────────────────────── */}
      <LayerSidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        tab={tab}
        onTabChange={setTab}
        activeServices={activeServices}
        activeAccidents={activeAccidents}
        onToggleService={toggleService}
        onToggleAccident={toggleAccident}
        places={places}
        placesLoading={placesLoading}
        placesError={placesError}
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
    </div>
  );
}
