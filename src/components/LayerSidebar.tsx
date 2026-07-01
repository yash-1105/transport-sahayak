"use client";

import React from "react";
import { LAYER_TO_PLACE_TYPE } from "@/hooks/usePlaces";
import type {
  ServiceLayerType,
  AccidentLayerType,
  GooglePlaceType,
  GooglePlace,
} from "@/lib/types";

// ── Layer config (mirrors MapView constants) ──────────────────────────────────

type LayerShape = "square" | "circle" | "triangle" | "diamond";

const SERVICE_LAYER_CONFIG: {
  key: ServiceLayerType;
  label: string;
  color: string;
  shape: LayerShape;
  source: "google" | "synthetic";
}[] = [
  { key: "HOSPITAL",          label: "Hospitals",       color: "#2563eb", shape: "square",   source: "google" },
  { key: "AMBULANCE_STATION", label: "Ambulances",      color: "#16a34a", shape: "circle",   source: "synthetic" },
  { key: "FIRE_STATION",      label: "Fire Stations",   color: "#dc2626", shape: "circle",   source: "synthetic" },
  { key: "TOWING_STATION",    label: "Towing / Recovery", color: "#57534e", shape: "circle",  source: "synthetic" },
  { key: "MECHANIC",          label: "Mechanics",       color: "#6b7280", shape: "square",   source: "google" },
  { key: "POLICE",            label: "Police Stations", color: "#1e3a8a", shape: "square",   source: "google" },
  { key: "PHARMACY",          label: "Pharmacies",      color: "#7c3aed", shape: "square",   source: "google" },
  { key: "GAS_STATION",       label: "Fuel Stations",   color: "#0891b2", shape: "square",   source: "google" },
];

const ACCIDENT_LAYER_CONFIG: {
  key: AccidentLayerType;
  label: string;
  color: string;
  shape: LayerShape;
}[] = [
  { key: "POTHOLE",           label: "Road Defects",       color: "#78350f", shape: "diamond" },
  { key: "REPORTED_ACCIDENT", label: "Reported Accidents", color: "#ea580c", shape: "circle"  },
];

// ── Mini shape indicator ──────────────────────────────────────────────────────

function MiniShape({ shape, color }: { shape: LayerShape; color: string }) {
  if (shape === "triangle") {
    return (
      <svg width="12" height="11" viewBox="0 0 12 11" style={{ flexShrink: 0 }}>
        <path d="M6 1L11 10H1L6 1z" fill={color} />
      </svg>
    );
  }
  if (shape === "diamond") {
    return (
      <span style={{
        display: "inline-block", flexShrink: 0,
        width: 10, height: 10,
        background: color, borderRadius: 2,
        transform: "rotate(45deg)",
      }} />
    );
  }
  if (shape === "circle") {
    return (
      <span style={{
        display: "inline-block", flexShrink: 0,
        width: 10, height: 10,
        background: color, borderRadius: "50%",
      }} />
    );
  }
  return (
    <span style={{
      display: "inline-block", flexShrink: 0,
      width: 10, height: 10,
      background: color, borderRadius: 2.5,
    }} />
  );
}

// ── Props ─────────────────────────────────────────────────────────────────────

type Tab = "SERVICES" | "ACCIDENTS";

interface LayerSidebarProps {
  open: boolean;
  onClose: () => void;
  tab: Tab;
  onTabChange: (t: Tab) => void;
  activeServices: Set<ServiceLayerType>;
  activeAccidents: Set<AccidentLayerType>;
  onToggleService: (k: ServiceLayerType) => void;
  onToggleAccident: (k: AccidentLayerType) => void;
  places: Record<GooglePlaceType, GooglePlace[]>;
  placesLoading: Record<GooglePlaceType, boolean>;
  placesError: boolean;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function LayerSidebar({
  open,
  onClose,
  tab,
  onTabChange,
  activeServices,
  activeAccidents,
  onToggleService,
  onToggleAccident,
  places,
  placesLoading,
}: LayerSidebarProps) {
  return (
    <>
      {/* Scrim — only visible when open */}
      <div
        className={`fixed inset-0 z-[1498] bg-black/30 backdrop-blur-[2px] transition-opacity duration-300 ${
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
      />

      {/* Sidebar panel */}
      <div
        className="fixed top-0 left-0 bottom-0 z-[1499] w-72 bg-white shadow-2xl flex flex-col"
        style={{
          transform: open ? "translateX(0)" : "translateX(-100%)",
          transition: "transform 300ms ease-in-out",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-[#0f2044] flex-shrink-0">
          <p className="text-sm font-bold text-white">Map Layers</p>
          <button
            onClick={onClose}
            aria-label="Close layer panel"
            className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-white/10 text-white/70 hover:text-white"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Tab selector */}
        <div className="flex border-b border-gray-200 flex-shrink-0">
          <button
            onClick={() => onTabChange("SERVICES")}
            className={`flex-1 py-2.5 text-xs font-semibold tracking-wide uppercase border-b-2 transition-colors ${
              tab === "SERVICES" ? "border-[#0f2044] text-[#0f2044]" : "border-transparent text-gray-500"
            }`}
          >
            Services
          </button>
          <button
            onClick={() => onTabChange("ACCIDENTS")}
            className={`flex-1 py-2.5 text-xs font-semibold tracking-wide uppercase border-b-2 transition-colors ${
              tab === "ACCIDENTS" ? "border-red-700 text-red-700" : "border-transparent text-gray-500"
            }`}
          >
            Accidents
          </button>
        </div>

        {/* Layer list */}
        <div className="flex-1 overflow-y-auto py-2">
          {tab === "SERVICES" && SERVICE_LAYER_CONFIG.map((layer) => {
            const active = activeServices.has(layer.key);
            const placeType = LAYER_TO_PLACE_TYPE[layer.key];
            const count = placeType ? places[placeType]?.length ?? null : null;
            const loading = placeType ? placesLoading[placeType] : false;

            return (
              <button
                key={layer.key}
                onClick={() => onToggleService(layer.key)}
                className={`w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors border-b border-gray-100 last:border-0 ${
                  active ? "" : "opacity-50"
                }`}
              >
                <MiniShape shape={layer.shape} color={active ? layer.color : "#d1d5db"} />
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-medium ${active ? "text-gray-900" : "text-gray-400"}`}>
                    {layer.label}
                  </p>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    {layer.source === "synthetic" && (
                      <span className="text-[10px] text-amber-600 font-medium">sample data</span>
                    )}
                    {layer.source === "google" && (
                      <span className="text-[10px] text-blue-500 font-medium">Google Places</span>
                    )}
                    {loading && (
                      <span className="inline-block w-3 h-3 border-[1.5px] border-gray-300 border-t-gray-600 rounded-full animate-spin" />
                    )}
                    {!loading && count !== null && (
                      <span className="text-[10px] text-gray-400">({count})</span>
                    )}
                  </div>
                </div>
                <div className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 ${
                  active ? "bg-[#0f2044] border-[#0f2044]" : "border-gray-300"
                }`}>
                  {active && (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
              </button>
            );
          })}

          {tab === "ACCIDENTS" && ACCIDENT_LAYER_CONFIG.map((layer) => {
            const active = activeAccidents.has(layer.key);
            return (
              <button
                key={layer.key}
                onClick={() => onToggleAccident(layer.key)}
                className={`w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors border-b border-gray-100 last:border-0 ${
                  active ? "" : "opacity-50"
                }`}
              >
                <MiniShape shape={layer.shape} color={active ? layer.color : "#d1d5db"} />
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-medium ${active ? "text-gray-900" : "text-gray-400"}`}>
                    {layer.label}
                  </p>
                  <span className="text-[10px] text-amber-600 font-medium">sample data</span>
                </div>
                <div className={`w-4 h-4 rounded border-2 flex items-center justify-center flex-shrink-0 ${
                  active ? "bg-[#0f2044] border-[#0f2044]" : "border-gray-300"
                }`}>
                  {active && (
                    <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
              </button>
            );
          })}
        </div>

        {/* Footer note */}
        <div className="px-4 py-3 border-t border-gray-100 flex-shrink-0">
          <p className="text-[10px] text-gray-400 leading-relaxed">
            Tap a layer to toggle visibility on the map.
            <br />
            Live data from Google Places · Synthetic layers are labelled.
          </p>
        </div>
      </div>
    </>
  );
}
