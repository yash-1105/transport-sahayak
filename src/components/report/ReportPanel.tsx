"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useVoiceInput, type VoiceLocale } from "@/hooks/useVoiceInput";
import {
  useVoiceDispatcher,
  type DispatcherSubmitPayload,
  type DispatchBriefingServices,
  type HelplineFacility,
} from "@/hooks/useVoiceDispatcher";
import { DispatcherSection } from "@/components/report/DispatcherSection";
import { ChatSection, ChatLocationGate } from "@/components/report/ChatSection";
import { useTextChat } from "@/hooks/useTextChat";
import { useEventLog } from "@/store/eventLog";
import { reverseGeocode } from "@/lib/geocode";
import { checkDuplicate, type DuplicateMatch } from "@/lib/dedup";
import { publishIncident, publishAssessment } from "@/lib/signalsPublisher";
import MatchingPanel from "@/components/report/MatchingPanel";
import { useRoutingStore } from "@/store/routingStore";
import { useLocaleStore } from "@/store/localeStore";
import { useBilingual } from "@/hooks/useI18n";
import { useResponders } from "@/hooks/useResponders";
import { haversineKm, haversineEtaMinutes } from "@/lib/matching";
import type {
  AccidentReport,
  AssessmentResult,
  AssessmentSeverity,
  GeoPoint,
  Hospital,
  PoliceStation,
  AmbulanceStation,
  FireStation,
  TowingStation,
  UserReportedPothole,
  RouteEstimatedPayload,
  HospitalMatchedPayload,
} from "@/lib/types";
import { C, CTA_GRADIENT, RADIUS } from "@/lib/design";
import { MapPinIcon, MicIcon } from "@/components/ui/icons";

// ── Shared sheet-UI helpers (UI redesign) ─────────────────────────────────────

// Location picker button. Unset → dashed saffron outline; set → solid green,
// 600-weight green text with the coordinates. Behaviour is unchanged.
function LocationField({
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
}: {
  pinnedLocation: GeoPoint | null;
  pinnedLabel: string;
  onRequestPin: () => void;
}) {
  const set = !!pinnedLocation;
  return (
    <button
      onClick={onRequestPin}
      style={{
        width: "100%",
        boxSizing: "border-box",
        padding: "12px 13px",
        borderRadius: RADIUS.input,
        fontSize: 13,
        cursor: "pointer",
        textAlign: "left",
        fontWeight: set ? 600 : 400,
        border: set ? `1px solid ${C.greenSoftBorder}` : "1.5px dashed #C9B98F",
        background: set ? C.greenSoftBg : "#FDFBF6",
        color: set ? C.greenSoftText : C.muted,
      }}
    >
      <span className="flex items-center gap-2">
        <MapPinIcon size={15} style={{ flex: "none" }} />
        {set
          ? `${pinnedLabel || `${pinnedLocation!.lat.toFixed(5)}, ${pinnedLocation!.lng.toFixed(5)}`}`
          : "Tap here, then tap map to set location"}
      </span>
    </button>
  );
}

// Red-gradient submit button with location gating. Disabled state is grey with
// "Set location to submit"; enabled uses the CTA gradient.
function SubmitButton({ canSubmit, onClick, label }: { canSubmit: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      disabled={!canSubmit}
      style={{
        marginTop: 2,
        width: "100%",
        padding: 14,
        border: "none",
        borderRadius: 12,
        fontSize: 14,
        fontWeight: 700,
        cursor: canSubmit ? "pointer" : "default",
        background: canSubmit ? CTA_GRADIENT : "#EFECE4",
        color: canSubmit ? "#fff" : "#A9A395",
        boxShadow: canSubmit ? "0 6px 20px rgba(198,54,44,.3)" : "none",
      }}
    >
      {canSubmit ? label : "Set location to submit"}
    </button>
  );
}

// Field label (12.5/600) with an optional muted Hindi/hint suffix.
function FieldLabel({ children, suffix }: { children: React.ReactNode; suffix?: React.ReactNode }) {
  return (
    <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6, color: C.ink }}>
      {children}
      {suffix && <span style={{ fontWeight: 400, color: C.muted }}> {suffix}</span>}
    </div>
  );
}

// Segmented control (2-option) used for the language toggles.
function Segmented({
  options,
  value,
  onChange,
}: {
  options: { key: string; label: string }[];
  value: string;
  onChange: (k: string) => void;
}) {
  return (
    <div className="flex" style={{ gap: 6, background: C.page, borderRadius: RADIUS.input, padding: 4 }}>
      {options.map((o) => {
        const on = o.key === value;
        return (
          <button
            key={o.key}
            onClick={() => onChange(o.key)}
            style={{
              flex: 1,
              padding: "8px 0",
              border: "none",
              borderRadius: 7,
              cursor: "pointer",
              fontSize: 13,
              fontWeight: on ? 600 : 500,
              background: on ? "#fff" : "transparent",
              color: on ? C.ink : C.secondary,
              boxShadow: on ? "0 1px 2px rgba(0,0,0,.08)" : "none",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// Responder lists come from the Aggregator DPG (useResponders inside the
// component) — the app no longer bundles any responder dataset.

// #4 (Hindi staged dispatch): one interim responder notification shown as a
// chip while the call is still in progress. `name`/`distanceKm` are null if no
// responder of that type is loaded yet — the chip still honestly says the
// service was notified.
export interface InterimDispatch {
  service: string; // "ambulance" | "fire" | "towing"
  name: string | null;
  distanceKm: number | null;
  dispatchedAt: number; // ms epoch — when this responder was notified mid-call
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeIncidentId(): string {
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const rand = Math.random().toString(16).slice(2, 6).toUpperCase();
  return `INC-${date}-${rand}`;
}

const GEO_ERRORS: Record<number, string> = {
  1: "Location access denied. Enable GPS / location permission in browser settings.",
  2: "Location unavailable. Check that GPS is enabled on this device.",
  3: "Location request timed out. Move to better GPS coverage and try again.",
};

const QUICK_FLAGS = ["Conscious", "Breathing", "Trapped", "Heavy bleeding", "Fire", "Hazardous material"] as const;

// ── Real-time incident classification ────────────────────────────────────────

type IncidentClass =
  | { type: "medical";    label: string; detail: string }
  | { type: "collision";  label: string; detail: string }
  | { type: "mechanical"; label: string; detail: string }
  | { type: "fire";       label: string; detail: string }
  | { type: "hazard";     label: string; detail: string };

function classifyIncident(
  desc: string,
  flags: ReadonlySet<string>
): IncidentClass | null {
  if (desc.trim().length < 8) return null;
  const t = desc.toLowerCase();

  const FIRE    = ["fire", "burning", "flames", "caught fire", "fuel leak", "ignit", "explod",
                   "आग", "जल रही", "जलना", "विस्फोट", "ईंधन रिसाव", "जल गई", "आग लगी"];
  const INJURY  = ["injur", "bleed", "blood", "unconscious", "unresponsive", "fracture",
                   "hurt", "casualt", "critical", "serious", "victim", "fatal", "dead",
                   "wounded", "person down", "people hurt", "someone hurt",
                   "घायल", "चोट", "खून", "बेहोश", "फँसा", "हताहत", "जख्मी", "मृत", "पीड़ित", "गंभीर", "फंसा"];
  const CRASH   = ["crash", "collision", "accident", "overturned", "rollover", "head-on",
                   "rear-end", "hit", "struck", "smash", "vehicle crash", "car crash",
                   "bike accident", "truck", "lorry", "bus hit", "tempo", "auto rickshaw",
                   "टक्कर", "दुर्घटना", "हादसा", "पलट", "उलट", "टकरा", "वाहन दुर्घटना", "गाड़ी टकर", "पलटी"];
  const MECH    = ["breakdown", "broken down", "flat tyre", "flat tire", "tyre burst",
                   "tire burst", "puncture", "engine fail", "engine stop", "stalled",
                   "car stopped", "vehicle stopped", "won't start", "wont start",
                   "mechanical", "brake fail", "oil leak", "overheating", "tow",
                   "खराब", "पंचर", "टायर फटा", "इंजन बंद", "ब्रेक फेल", "गाड़ी बंद", "गाड़ी खराब"];
  const HAZARD  = ["pothole", "debris", "fallen tree", "tree fallen", "rock fall",
                   "obstacle on road", "road blocked", "flooded road", "oil spill",
                   "road damage", "landslide", "mud on road",
                   "गड्ढा", "सड़क बंद", "बाढ़", "भूस्खलन", "पत्थर गिरा", "पेड़ गिरा", "कीचड़"];

  const has = (words: string[]) => words.some((w) => t.includes(w));
  const flagInjury = flags.has("Heavy bleeding") || flags.has("Trapped");

  if (has(FIRE))
    return { type: "fire", label: "Fire / fuel hazard",
      detail: "Emergency response units alerted · hospital on standby" };

  if ((has(INJURY) || flagInjury) && has(CRASH))
    return { type: "medical", label: "Injury crash",
      detail: "Hospital + ambulance dispatch prioritised · police alerted" };

  if (has(INJURY) || flagInjury)
    return { type: "medical", label: "Medical emergency",
      detail: "Nearest hospital + ambulance being matched" };

  if (has(CRASH))
    return { type: "collision", label: "Road collision (no injuries reported)",
      detail: "Police dispatch + hospital on standby" };

  if (has(MECH))
    return { type: "mechanical", label: "Vehicle breakdown / damage",
      detail: "Mechanic stations highlighted on map · tow assistance flagged" };

  if (has(HAZARD))
    return { type: "hazard", label: "Road hazard",
      detail: "Traffic police + road authority notified" };

  return null;
}

const HINT_STYLE: Record<
  IncidentClass["type"],
  { border: string; bg: string; text: string; sub: string; icon: React.ReactNode }
> = {
  medical: {
    border: "#dc2626", bg: "#fef2f2", text: "#991b1b", sub: "#b91c1c",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <rect x="5.5" y="2" width="4" height="11" rx="1.5" fill="#dc2626"/>
        <rect x="2" y="5.5" width="11" height="4" rx="1.5" fill="#dc2626"/>
      </svg>
    ),
  },
  collision: {
    border: "#1e3a8a", bg: "#eff6ff", text: "#1e40af", sub: "#1d4ed8",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 1L2 4v4.5c0 3 2.4 5.5 5.5 6 3.1-.5 5.5-3 5.5-6V4L7.5 1z"
          fill="none" stroke="#1e3a8a" strokeWidth="1.5" strokeLinejoin="round"/>
        <path d="M5.3 7.8l1.8 1.8 3.1-3.5" stroke="#1e3a8a"
          strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
    ),
  },
  mechanical: {
    border: "#d97706", bg: "#fffbeb", text: "#92400e", sub: "#b45309",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <circle cx="7.5" cy="7.5" r="2" fill="#d97706"/>
        <circle cx="7.5" cy="7.5" r="5" stroke="#d97706" strokeWidth="1.3"
          fill="none" strokeDasharray="2.5 2"/>
      </svg>
    ),
  },
  fire: {
    border: "#ea580c", bg: "#fff7ed", text: "#9a3412", sub: "#c2410c",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 2c0 2-2 3-2 5a2.5 2.5 0 005 0c0-1.5-1-2.5-1-4 0 0-1 1.5-2 1.5z"
          fill="#ea580c"/>
        <circle cx="7.5" cy="12" r="1.5" fill="#ea580c"/>
      </svg>
    ),
  },
  hazard: {
    border: "#ca8a04", bg: "#fefce8", text: "#854d0e", sub: "#92400e",
    icon: (
      <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
        <path d="M7.5 1.5L13.5 12.5H1.5L7.5 1.5z"
          fill="none" stroke="#ca8a04" strokeWidth="1.5" strokeLinejoin="round"/>
        <path d="M7.5 6v3.5" stroke="#ca8a04" strokeWidth="1.8" strokeLinecap="round"/>
        <circle cx="7.5" cy="11.2" r="0.9" fill="#ca8a04"/>
      </svg>
    ),
  },
};

function IncidentHintCard({ hint }: { hint: IncidentClass }) {
  const s = HINT_STYLE[hint.type];
  return (
    <div
      style={{
        borderLeft: `3px solid ${s.border}`,
        background: s.bg,
        borderRadius: "0 6px 6px 0",
        padding: "8px 12px",
        marginTop: 8,
        display: "flex",
        gap: 8,
        alignItems: "flex-start",
      }}
    >
      <span style={{ flexShrink: 0, marginTop: 1 }}>{s.icon}</span>
      <div>
        <p style={{ color: s.text, fontWeight: 700, fontSize: 11, lineHeight: 1.3 }}>
          {hint.label}
        </p>
        <p style={{ color: s.sub, fontSize: 11, marginTop: 2, opacity: 0.85 }}>
          {hint.detail}
        </p>
      </div>
    </div>
  );
}

// ── Severity visual config (4-level: 1=LOW 2=MEDIUM 3=HIGH 4=CRITICAL) ────────

const SEV: Record<AssessmentSeverity, { bg: string; text: string; border: string; track: string }> = {
  1: { bg: "#f0fdf4", text: "#15803d", border: "#86efac", track: "#22c55e" },
  2: { bg: "#fffbeb", text: "#b45309", border: "#fcd34d", track: "#f59e0b" },
  3: { bg: "#fff7ed", text: "#c2410c", border: "#fdba74", track: "#f97316" },
  4: { bg: "#fef2f2", text: "#b91c1c", border: "#fca5a5", track: "#ef4444" },
};

const CLASSIFIED_LABEL: Record<string, string> = {
  operator: "Operator selected",
  rules:    "Rule engine",
  llm:      "AI-assisted",
};

// ── Incident type picker — two-tier + auto-detect ─────────────────────────────

type GuessResult = {
  subType: string | null;
  category: string | null;
  confidence: number;
  lowConfidence: boolean;
  candidates: Array<{ subType: string; category: string }>;
};

type CategoryItem = { category: string; count: number };

type PickerPhase =
  | { kind: "idle" }
  | { kind: "guessing" }
  | { kind: "confirm"; guess: GuessResult }
  | { kind: "browse"; categories: CategoryItem[] | null; catError: boolean }
  | { kind: "subtypes"; category: string; subtypes: string[]; filter: string }
  | { kind: "done"; subType: string; category: string };

function IncidentTypePicker({
  description,
  value,
  onChange,
}: {
  description: string;
  value: string;
  onChange: (subType: string, category: string) => void;
}) {
  const [phase, setPhase] = useState<PickerPhase>({ kind: "idle" });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phaseRef = useRef<PickerPhase["kind"]>("idle");
  phaseRef.current = phase.kind;

  // If parent clears value (form reset), go back to idle
  useEffect(() => {
    if (!value) setPhase({ kind: "idle" });
  }, [value]);

  // Auto-detect: debounce description → POST /api/guess
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    const pk = phaseRef.current;
    // Don't interrupt manual browsing or a confirmed selection
    if (pk === "done" || pk === "browse" || pk === "subtypes") return;

    if (description.trim().length < 8) {
      if (pk === "guessing" || pk === "confirm") setPhase({ kind: "idle" });
      return;
    }

    setPhase({ kind: "guessing" });
    timerRef.current = setTimeout(async () => {
      try {
        const res = await fetch("/api/guess", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: description.trim() }),
          cache: "no-store",
        });
        if (!res.ok) { setPhase({ kind: "idle" }); return; }
        const g: GuessResult = await res.json();
        if (!g.subType) { setPhase({ kind: "idle" }); return; }
        setPhase({ kind: "confirm", guess: g });
      } catch {
        setPhase({ kind: "idle" });
      }
    }, 400);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [description]); // eslint-disable-line react-hooks/exhaustive-deps

  function confirm(subType: string, category: string) {
    onChange(subType, category);
    setPhase({ kind: "done", subType, category });
  }

  function clear() {
    onChange("", "");
    setPhase({ kind: "idle" });
  }

  async function startBrowse() {
    setPhase({ kind: "browse", categories: null, catError: false });
    try {
      const res = await fetch("/api/categories", { cache: "no-store" });
      if (!res.ok) throw new Error();
      setPhase({ kind: "browse", categories: await res.json(), catError: false });
    } catch {
      setPhase({ kind: "browse", categories: null, catError: true });
    }
  }

  async function openCategory(cat: string) {
    setPhase({ kind: "subtypes", category: cat, subtypes: [], filter: "" });
    try {
      const res = await fetch(`/api/categories/subtypes?name=${encodeURIComponent(cat)}`, { cache: "no-store" });
      if (!res.ok) throw new Error();
      const subs: string[] = await res.json();
      setPhase({ kind: "subtypes", category: cat, subtypes: subs, filter: "" });
    } catch { /* leave empty — error visible via empty list */ }
  }

  // ── confirmed ──
  if (phase.kind === "done") {
    return (
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">Incident Type</label>
        <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <span className="flex-1 text-sm font-semibold text-green-900 truncate">{phase.subType}</span>
          <span className="text-[10px] text-green-600 flex-shrink-0">{phase.category}</span>
          <button onClick={clear} className="text-gray-400 hover:text-red-500 flex-shrink-0 ml-1 leading-none">✕</button>
        </div>
      </div>
    );
  }

  // ── subtype list (per category) ──
  if (phase.kind === "subtypes") {
    const filtered =
      phase.filter.length < 2
        ? phase.subtypes
        : phase.subtypes.filter((s) => s.toLowerCase().includes(phase.filter.toLowerCase()));
    return (
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          <button onClick={startBrowse} className="text-[#0f2044] underline">{phase.category}</button>
          {" "}→ pick sub-type
        </label>
        <div className="border border-gray-200 rounded-lg overflow-hidden">
          <div className="p-2 border-b border-gray-100 flex items-center gap-2">
            <button onClick={startBrowse} className="text-xs text-gray-400 hover:text-gray-700 flex-shrink-0">
              ← Back
            </button>
            <input
              autoFocus
              value={phase.filter}
              onChange={(e) => setPhase({ ...phase, filter: e.target.value })}
              placeholder={`Search ${phase.subtypes.length} types…`}
              className="flex-1 text-xs px-2 py-1 border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-[#0f2044]/30"
            />
          </div>
          {/* Allow submit at category level */}
          <button
            onClick={() => confirm("", phase.category)}
            className="w-full text-left px-3 py-2 text-xs border-b border-gray-100 hover:bg-[#0f2044]/5 flex items-center gap-2"
          >
            <span className="font-semibold text-[#0f2044]">{phase.category}</span>
            <span className="text-gray-400 text-[10px]">— use category only</span>
          </button>
          <div style={{ maxHeight: 200, overflowY: "auto" }}>
            {phase.subtypes.length === 0 && (
              <div className="p-3 text-xs text-gray-400 flex items-center gap-2">
                <div className="w-3 h-3 border-2 border-gray-300 border-t-[#0f2044] rounded-full animate-spin" />
                Loading…
              </div>
            )}
            {phase.subtypes.length > 0 && filtered.length === 0 && (
              <p className="text-xs text-gray-400 p-3">No matches</p>
            )}
            {filtered.map((s) => (
              <button
                key={s}
                onClick={() => confirm(s, phase.category)}
                className="w-full text-left px-3 py-2 text-xs font-medium text-gray-900 hover:bg-gray-50 border-b border-gray-100 last:border-0"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── browse: category tiles ──
  if (phase.kind === "browse") {
    return (
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          Incident Type{" "}
          <button onClick={clear} className="font-normal text-gray-400 underline text-[11px]">cancel</button>
        </label>
        {phase.catError ? (
          <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2.5 py-1.5">
            Engine unreachable — type list unavailable.
          </p>
        ) : !phase.categories ? (
          <div className="flex items-center gap-2 py-2 text-xs text-gray-400">
            <div className="w-3.5 h-3.5 border-2 border-gray-300 border-t-[#0f2044] rounded-full animate-spin" />
            Loading categories…
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-1.5">
            {phase.categories.map(({ category, count }) => (
              <button
                key={category}
                onClick={() => openCategory(category)}
                className="text-left px-3 py-2.5 border border-gray-200 rounded-lg hover:border-[#0f2044] hover:bg-[#0f2044]/5 text-xs font-medium text-gray-800 transition-colors"
              >
                <span className="block truncate">{category}</span>
                <span className="block text-[10px] text-gray-400 mt-0.5">{count} types</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── confirm card (auto-detected) ──
  if (phase.kind === "confirm") {
    const g = phase.guess;
    return (
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">Incident Type — detected</label>
        <div className="border border-[#0f2044]/20 rounded-lg overflow-hidden">
          <div className="px-3 py-2.5 flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-bold text-gray-900 leading-tight">{g.subType}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{g.category}</p>
            </div>
            <button
              onClick={() => confirm(g.subType!, g.category!)}
              className="flex-shrink-0 text-xs font-bold px-3 py-1.5 bg-[#0f2044] text-white rounded-md hover:bg-[#1a3567]"
            >
              Confirm
            </button>
          </div>
          {g.lowConfidence && g.candidates.length > 1 && (
            <div className="border-t border-gray-100 px-3 py-2">
              <p className="text-[10px] text-gray-400 mb-1.5">Or did you mean:</p>
              <div className="flex flex-wrap gap-1.5">
                {g.candidates.slice(0, 3).map((c) => (
                  <button
                    key={`${c.category}::${c.subType}`}
                    onClick={() => confirm(c.subType, c.category)}
                    className="text-[11px] px-2 py-1 border border-gray-200 rounded-full hover:border-[#0f2044] hover:text-[#0f2044] text-gray-600"
                  >
                    {c.subType}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="border-t border-gray-100 px-3 py-1.5 flex items-center justify-between">
            <button onClick={startBrowse} className="text-[11px] text-gray-400 hover:text-gray-700">
              Not right? Browse manually
            </button>
            <button onClick={clear} className="text-[11px] text-gray-400 hover:text-gray-600">Skip</button>
          </div>
        </div>
      </div>
    );
  }

  // ── guessing (debounce in flight) ──
  if (phase.kind === "guessing") {
    return (
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">Incident Type — detecting…</label>
        <div className="flex items-center gap-2 py-2 px-1 text-xs text-gray-400">
          <div className="w-3.5 h-3.5 border-2 border-gray-300 border-t-[#0f2044] rounded-full animate-spin" />
          Analysing description…
        </div>
      </div>
    );
  }

  // ── idle: auto-detects from description, or browse manually ──
  return (
    <div>
      <label className="block text-xs font-semibold text-gray-600 mb-1.5">
        Incident Type{" "}
        <span className="font-normal text-gray-400">— auto-detects as you type</span>
      </label>
      <button
        type="button"
        onClick={startBrowse}
        className="w-full border border-dashed border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-400 hover:border-[#0f2044] hover:text-[#0f2044] transition-colors flex items-center justify-center gap-2"
      >
        Browse incident types…
      </button>
    </div>
  );
}

// ── Assessment result card ────────────────────────────────────────────────────

function AssessmentCard({
  result,
  incidentId,
}: {
  result: AssessmentResult;
  incidentId: string;
}) {
  const score = (result.severityScore ?? 1) as AssessmentSeverity;
  const [modOpen, setModOpen] = useState(false);
  const classLabel = CLASSIFIED_LABEL[result.classifiedBy] ?? result.classifiedBy;

  // Severity → card accent (header tint, score ring, segment bar).
  const accent =
    score <= 1
      ? { bg: C.greenSoftBg, border: C.greenSoftBorder, text: C.greenSoftText, ring: C.green }
      : score === 2
      ? { bg: C.saffronSoftBg, border: C.saffronSoftBorder, text: "#B06712", ring: C.saffron }
      : score === 3
      ? { bg: "#FFF3EC", border: "#F5C9AE", text: "#C2410C", ring: "#EA580C" }
      : { bg: C.redSoftBg, border: C.redSoftBorder, text: C.redSoftText, ring: C.red };
  const SEG_COLORS = [C.green, C.saffron, "#EA580C", C.red];

  const agencyStyle = (code: string): { bg: string; bd: string; tx: string } => {
    if (code === "AMBULANCE") return { bg: C.greenSoftBg, bd: C.greenSoftBorder, tx: C.greenSoftText };
    if (code === "POLICE") return { bg: C.blueSoftBg, bd: C.blueSoftBorder, tx: C.blueSoftText };
    if (code === "FIRE") return { bg: C.redSoftBg, bd: C.redSoftBorder, tx: C.redSoftText };
    return { bg: C.saffronSoftBg, bd: C.saffronSoftBorder, tx: C.saffronSoftText };
  };

  const CAPS: React.CSSProperties = { fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted };

  return (
    <div className="flex flex-col gap-3">
      {/* Success strip */}
      <div className="flex items-center gap-2.5" style={{ background: C.greenSoftBg, border: `1px solid ${C.greenSoftBorder}`, borderRadius: 11, padding: "11px 15px" }}>
        <span className="inline-flex items-center justify-center flex-none" style={{ width: 22, height: 22, borderRadius: "50%", background: C.green, color: "#fff", fontSize: 12 }}>✓</span>
        <span style={{ fontSize: 13, color: C.greenSoftText }}>
          Incident <b style={{ fontFamily: "ui-monospace,Menlo,monospace" }}>{incidentId}</b> created and logged
        </span>
      </div>

      {/* Severity assessment card */}
      <section style={{ border: `1px solid ${accent.border}`, borderRadius: RADIUS.card, overflow: "hidden" }}>
        <div className="flex items-center gap-2.5" style={{ background: accent.bg, padding: "10px 16px" }}>
          <span style={{ ...CAPS, color: accent.text, flex: 1 }}>Severity assessment</span>
          <span style={{ fontSize: 11, fontWeight: 600, background: "#fff", border: `1px solid ${accent.border}`, color: accent.text, borderRadius: RADIUS.pill, padding: "3px 10px" }}>
            {classLabel}
          </span>
        </div>

        <div className="ts-assess-grid" style={{ display: "grid", gridTemplateColumns: "1fr 190px", gap: 16, padding: 16 }}>
          {/* Left column */}
          <div className="flex flex-col" style={{ gap: 12, minWidth: 0 }}>
            {result.subType && (
              <div>
                <div style={CAPS}>Incident type</div>
                <div style={{ fontSize: 14.5, fontWeight: 600, marginTop: 2, color: C.ink }}>{result.subType}</div>
              </div>
            )}
            <div>
              <div style={CAPS}>Impact assessment</div>
              <div style={{ fontSize: 13, color: C.body, marginTop: 2, lineHeight: 1.5 }}>{result.impactNote}</div>
            </div>
            {result.agencies.length > 0 && (
              <div>
                <div style={{ ...CAPS, marginBottom: 6 }}>Agencies to notify</div>
                <div className="flex flex-wrap" style={{ gap: 7 }}>
                  {result.agencies.map((a) => {
                    const s = agencyStyle(a.code);
                    return (
                      <span key={a.code} style={{ fontSize: 12, fontWeight: 600, border: `1px solid ${s.bd}`, background: s.bg, color: s.tx, borderRadius: RADIUS.pill, padding: "4px 11px" }}>
                        {a.label}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
            {result.dataGaps.length > 0 && (
              <div>
                <div style={{ ...CAPS, marginBottom: 5 }}>Ask next</div>
                <div style={{ fontSize: 12.5, color: C.body, lineHeight: 1.7 }}>
                  {result.dataGaps.map((gap, i) => (
                    <span key={i}>{i + 1}. {gap}{i < result.dataGaps.length - 1 ? "   " : ""}</span>
                  ))}
                </div>
              </div>
            )}
            {result.appliedModifiers.length > 0 && (
              <div>
                <button type="button" onClick={() => setModOpen((v) => !v)} className="flex items-center gap-1" style={{ fontSize: 11, fontWeight: 600, color: C.secondary }}>
                  <svg className={`w-3 h-3 transition-transform ${modOpen ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  Why this rating
                </button>
                {modOpen && (
                  <ul className="mt-1.5 flex flex-col gap-1">
                    {result.appliedModifiers.map((m, i) => (
                      <li key={i} className="flex items-start gap-1.5" style={{ fontSize: 12, color: C.body }}>
                        <span style={{ color: C.faint }}>+</span>{m}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          {/* Right column — score ring */}
          <div className="flex flex-col items-center justify-center" style={{ gap: 6, borderLeft: `1px solid ${C.hairline}`, paddingLeft: 16 }}>
            <div className="flex items-center justify-center" style={{ width: 74, height: 74, borderRadius: "50%", border: `4px solid ${accent.ring}`, fontSize: 30, fontWeight: 700, color: accent.text }}>
              {score}
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".1em", color: accent.text, textTransform: "uppercase" }}>{result.severity}</div>
            <div className="flex" style={{ gap: 4 }}>
              {[0, 1, 2, 3].map((i) => (
                <span key={i} style={{ width: 22, height: 6, borderRadius: 3, background: i < score ? SEG_COLORS[i] : C.border }} />
              ))}
            </div>
            <div style={{ fontSize: 10.5, color: C.muted, textAlign: "center", marginTop: 2 }}>
              {result.lowConfidence ? "Low-confidence — verify" : "Verify before acting"}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

// ── Assessing loader ──────────────────────────────────────────────────────────

function AssessingView({ incidentId }: { incidentId: string }) {
  return (
    <div className="p-6 flex flex-col items-center gap-4">
      <div className="w-10 h-10 border-[3px] border-[#0f2044] border-t-transparent rounded-full animate-spin" />
      <div className="text-center">
        <p className="text-sm font-semibold text-gray-800">Assessing severity…</p>
        <p className="text-xs text-gray-400 mt-1 font-mono">{incidentId}</p>
      </div>
      <p className="text-xs text-gray-400 text-center">
        Contacting severity engine.
      </p>
    </div>
  );
}

// ── SOS mode view ─────────────────────────────────────────────────────────────

function SOSView({
  status,
  error,
  onSend,
}: {
  status: "IDLE" | "BUSY" | "ERROR";
  error: string | null;
  onSend: () => void;
}) {
  const doesItems = [
    "Requests your GPS coordinates from the browser",
    "Creates an incident flagged as high priority",
    "Triggers automatic severity assessment",
    "Appends an entry to the session event log",
  ];
  const doesNotItems = [
    "Does not automatically call or alert emergency services",
    "Does not transmit to any external system in real time",
    "Dispatch is a separate, manual step",
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="ts-sos-grid grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div style={{ background: C.redSoftBg, border: `1px solid ${C.redSoftBorder}`, borderRadius: 12, padding: "14px 16px" }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: C.redSoftText, marginBottom: 8 }}>What SOS does</div>
          <div className="flex flex-col" style={{ gap: 6, fontSize: 12.5, color: "#7A2620", lineHeight: 1.45 }}>
            {doesItems.map((it) => <div key={it}>• {it}</div>)}
          </div>
        </div>
        <div style={{ background: C.inset, border: `1px solid ${C.hairline}`, borderRadius: 12, padding: "14px 16px" }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: C.secondary, marginBottom: 8 }}>What SOS does NOT do</div>
          <div className="flex flex-col" style={{ gap: 6, fontSize: 12.5, color: "#6B7480", lineHeight: 1.45 }}>
            {doesNotItems.map((it) => <div key={it}>• {it}</div>)}
          </div>
        </div>
      </div>

      {status === "IDLE" && (
        <button
          onClick={onSend}
          style={{ width: "100%", padding: 17, border: "none", borderRadius: 13, background: CTA_GRADIENT, color: "#fff", fontSize: 16, fontWeight: 700, letterSpacing: ".08em", cursor: "pointer", boxShadow: "0 6px 20px rgba(198,54,44,.35)" }}
        >
          SEND SOS
        </button>
      )}

      {status === "BUSY" && (
        <div className="flex flex-col items-center gap-2 py-4">
          <div className="w-6 h-6 rounded-full animate-spin" style={{ border: `2px solid ${C.navy800}`, borderTopColor: "transparent" }} />
          <p className="text-sm" style={{ color: C.secondary }}>Acquiring GPS location…</p>
        </div>
      )}

      {status === "ERROR" && error && (
        <div className="flex flex-col gap-3">
          <div style={{ background: C.redSoftBg, border: `1px solid ${C.redSoftBorder}`, borderRadius: 10, padding: 12, fontSize: 12.5, color: C.redSoftText }}>
            {error}
          </div>
          <button
            onClick={onSend}
            style={{ width: "100%", padding: 13, border: "none", borderRadius: 12, background: CTA_GRADIENT, color: "#fff", fontSize: 14, fontWeight: 700, cursor: "pointer" }}
          >
            Try Again
          </button>
        </div>
      )}
    </div>
  );
}

// ── Voice section ─────────────────────────────────────────────────────────────

function VoiceSection({
  voice,
  locale,
  onLocaleChange,
  onTranscriptReady,
}: {
  voice: ReturnType<typeof useVoiceInput>;
  locale: VoiceLocale;
  onLocaleChange: (l: VoiceLocale) => void;
  onTranscriptReady: (text: string) => void;
}) {
  const [polishing, setPolishing] = useState(false);
  const [polishedText, setPolishedText] = useState<string | null>(null);

  // The displayed transcript: polished version if available, else raw from hook
  const displayTranscript = polishedText ?? voice.transcript;

  useEffect(() => {
    onTranscriptReady(displayTranscript);
  }, [displayTranscript, onTranscriptReady]);

  // Reset polished text when transcript is cleared (new recording)
  useEffect(() => {
    if (!voice.transcript) setPolishedText(null);
  }, [voice.transcript]);

  async function handlePolish() {
    if (!voice.transcript || polishing) return;
    setPolishing(true);
    try {
      const res = await fetch("/api/voice-clean", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: voice.transcript, locale }),
      });
      if (res.ok) {
        const { cleaned } = await res.json();
        if (cleaned) setPolishedText(cleaned);
      }
    } catch { /* keep original on error */ }
    setPolishing(false);
  }

  if (!voice.supported) {
    return (
      <div style={{ background: C.inset, border: `1px solid ${C.border}`, borderRadius: RADIUS.input, padding: 12, fontSize: 12.5, color: C.secondary }}>
        Voice input is not supported in this browser. Use the Text tab instead.
        <br />
        <span style={{ color: C.muted }}>Supported: Chrome / Edge on desktop and Android.</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <FieldLabel>Voice language</FieldLabel>
        <Segmented
          options={[{ key: "en-IN", label: "English" }, { key: "hi-IN", label: "हिंदी" }]}
          value={locale}
          onChange={(k) => { if (voice.listening) voice.stop(); onLocaleChange(k as VoiceLocale); }}
        />
        {locale === "hi-IN" && (
          <p style={{ fontSize: 11, color: C.blueSoftText, background: C.blueSoftBg, borderRadius: 6, padding: "4px 8px", marginTop: 6 }}>
            हिंदी में बोलें — text will appear in देवनागरी script
          </p>
        )}
      </div>

      <div className="flex flex-col items-center" style={{ gap: 8, padding: "10px 0" }}>
        {/* Idle state matches the Voice tab's mic button exactly (same 84px
            navy-gradient circle + white MicIcon) so the two mic controls look
            identical; only the active/recording state differs (red). */}
        <button
          onClick={() => (voice.listening ? voice.stop() : voice.start(locale))}
          className="flex items-center justify-center transition-all"
          style={{
            width: 84,
            height: 84,
            borderRadius: "50%",
            border: voice.listening ? "2px solid #B92C22" : "none",
            background: voice.listening ? C.red : "linear-gradient(135deg,#2456A6,#173B77)",
            color: "#fff",
            cursor: "pointer",
            boxShadow: voice.listening ? "none" : "0 8px 24px rgba(36,86,166,.35)",
          }}
        >
          <MicIcon size={32} />
        </button>
        <p style={{ fontSize: 12.5, color: C.secondary }}>
          {voice.listening ? "Recording — tap to stop" : "Tap to start recording — transcript fills the form below"}
        </p>
      </div>

      {(displayTranscript || voice.interimTranscript || voice.listening) && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-2.5 text-xs min-h-[56px]">
          <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase mb-1">
            Live Transcript
          </p>
          <span className="text-gray-800">{displayTranscript}</span>
          {voice.interimTranscript && (
            <span className="text-gray-400 italic"> {voice.interimTranscript}</span>
          )}
          {voice.listening && !displayTranscript && !voice.interimTranscript && (
            <span className="text-gray-400 italic">Listening…</span>
          )}
        </div>
      )}

      {voice.transcript && (
        <div className="flex items-center gap-3 self-start">
          <button onClick={() => { voice.clearTranscript(); setPolishedText(null); }} className="text-xs text-gray-400 underline">
            Clear
          </button>
          {!polishedText && !voice.listening && (
            <button
              onClick={handlePolish}
              disabled={polishing}
              className="flex items-center gap-1 text-xs text-[#0f2044] font-medium border border-[#0f2044]/30 rounded px-2 py-0.5 hover:bg-[#0f2044]/5 disabled:opacity-50 transition-colors"
            >
              {polishing ? (
                <span className="inline-block w-3 h-3 border-[1.5px] border-[#0f2044]/40 border-t-[#0f2044] rounded-full animate-spin" />
              ) : (
                <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z" />
                </svg>
              )}
              {polishing ? "Polishing…" : "Polish"}
            </button>
          )}
          {polishedText && (
            <span className="text-xs text-green-700 font-medium">✓ Polished</span>
          )}
        </div>
      )}

      {voice.error && (
        <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5">
          {voice.error}
        </p>
      )}
    </div>
  );
}

// ── Shared form (TEXT + VOICE) ────────────────────────────────────────────────

interface FormViewProps {
  mode: "TEXT" | "VOICE";
  voice: ReturnType<typeof useVoiceInput>;
  locale: VoiceLocale;
  onLocaleChange: (l: VoiceLocale) => void;
  pinnedLocation: GeoPoint | null;
  pinnedLabel: string;
  onRequestPin: () => void;
  selectedSubType: string;
  selectedCategory: string;
  onSubType: (v: string, cat: string) => void;
  description: string;
  onDescription: (v: string) => void;
  vehiclesInvolved: string;
  onVehiclesInvolved: (v: string) => void;
  casualties: string;
  onCasualties: (v: string) => void;
  selectedFlags: Set<string>;
  onToggleFlag: (f: string) => void;
  onSubmit: () => void;
  canSubmit: boolean;
}

function FormView({
  mode, voice, locale, onLocaleChange,
  pinnedLocation, pinnedLabel, onRequestPin,
  selectedSubType, selectedCategory, onSubType,
  description, onDescription, vehiclesInvolved, onVehiclesInvolved, casualties, onCasualties,
  selectedFlags, onToggleFlag, onSubmit, canSubmit,
}: FormViewProps) {
  const handleTranscript = useCallback(
    (text: string) => onDescription(text),
    [onDescription]
  );

  return (
    <div className="flex flex-col" style={{ gap: 14, padding: "16px 20px 20px" }}>
      {/* Dictation mic — always available in the merged Describe tab; the user
          can type OR tap the mic to speak into the description. */}
      <VoiceSection
        voice={voice}
        locale={locale}
        onLocaleChange={onLocaleChange}
        onTranscriptReady={handleTranscript}
      />

      {/* Incident type — auto-detect + two-tier browse */}
      <IncidentTypePicker description={description} value={selectedSubType || selectedCategory} onChange={onSubType} />

      {/* Location */}
      <div>
        <FieldLabel suffix={<span style={{ color: C.red }}>*</span>}>Incident location</FieldLabel>
        <LocationField pinnedLocation={pinnedLocation} pinnedLabel={pinnedLabel} onRequestPin={onRequestPin} />
      </div>

      {/* Description */}
      <div>
        <FieldLabel suffix={mode === "VOICE" ? "— from transcript, editable" : undefined}>Description</FieldLabel>
        <textarea
          rows={3}
          value={description}
          onChange={(e) => onDescription(e.target.value)}
          placeholder="Describe the incident — vehicles, visible injuries, road conditions…"
          style={{ width: "100%", boxSizing: "border-box", minHeight: 74, padding: "11px 13px", border: `1px solid ${C.border}`, borderRadius: RADIUS.input, fontSize: 13.5, background: C.inset, outline: "none", resize: "vertical", color: C.ink }}
        />
        {(() => {
          const hint = classifyIncident(description, selectedFlags);
          return hint ? <IncidentHintCard hint={hint} /> : null;
        })()}
      </div>

      {/* Vehicles involved / casualties — kept as two separate fields since they
          feed different signals to the engine (vehicle count affects multi-vehicle
          dispatch logic; casualty count affects severity + ambulance dispatch) and
          conflating them previously sent whatever was typed here as vehiclesInvolved
          regardless of which the reporter meant. */}
      <div className="flex gap-3">
        <div>
          <FieldLabel>Vehicles involved</FieldLabel>
          <input
            type="number"
            min="0"
            max="999"
            value={vehiclesInvolved}
            onChange={(e) => onVehiclesInvolved(e.target.value)}
            placeholder="0"
            style={{ width: 90, padding: "10px 13px", border: `1px solid ${C.border}`, borderRadius: RADIUS.input, fontSize: 13.5, background: C.inset, outline: "none", color: C.ink }}
          />
        </div>
        <div>
          <FieldLabel>Casualties / injured</FieldLabel>
          <input
            type="number"
            min="0"
            max="999"
            value={casualties}
            onChange={(e) => onCasualties(e.target.value)}
            placeholder="0"
            style={{ width: 90, padding: "10px 13px", border: `1px solid ${C.border}`, borderRadius: RADIUS.input, fontSize: 13.5, background: C.inset, outline: "none", color: C.ink }}
          />
        </div>
      </div>

      {/* Observed conditions — toggle chips */}
      <div>
        <FieldLabel>Observed conditions</FieldLabel>
        <div className="flex flex-wrap" style={{ gap: 8 }}>
          {QUICK_FLAGS.map((flag) => {
            const active = selectedFlags.has(flag);
            return (
              <button
                key={flag}
                onClick={() => onToggleFlag(flag)}
                style={{
                  padding: "8px 14px",
                  border: `1px solid ${active ? C.blue : C.border}`,
                  borderRadius: RADIUS.pill,
                  background: active ? C.blueSoftBg : "#fff",
                  color: active ? C.blueSoftText : C.secondary,
                  fontSize: 12.5,
                  fontWeight: active ? 600 : 500,
                  cursor: "pointer",
                }}
              >
                {flag}
              </button>
            );
          })}
        </div>
      </div>

      {/* Submit */}
      <SubmitButton canSubmit={canSubmit} onClick={onSubmit} label="Submit report" />
    </div>
  );
}

// ── Pothole form ──────────────────────────────────────────────────────────────

interface PotholeFormViewProps {
  pinnedLocation: GeoPoint | null;
  pinnedLabel: string;
  onRequestPin: () => void;
  description: string;
  onDescription: (v: string) => void;
  severity: "HIGH" | "MEDIUM" | "LOW";
  onSeverity: (v: "HIGH" | "MEDIUM" | "LOW") => void;
  onSubmit: () => void;
  canSubmit: boolean;
}

function PotholeFormView({
  pinnedLocation, pinnedLabel, onRequestPin,
  description, onDescription,
  severity, onSeverity,
  onSubmit, canSubmit,
}: PotholeFormViewProps) {
  const SEV_ITEMS: { key: "LOW" | "MEDIUM" | "HIGH"; color: string }[] = [
    { key: "LOW", color: C.green },
    { key: "MEDIUM", color: C.saffron },
    { key: "HIGH", color: C.red },
  ];
  return (
    <div className="flex flex-col" style={{ gap: 14, padding: "16px 20px 20px" }}>
      <div style={{ background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 11, padding: "11px 15px", fontSize: 12.5, color: C.saffronSoftText, lineHeight: 1.5 }}>
        <b>Reporting a road defect</b> — pin the location, describe the defect, and select severity. It will appear on the Accidents tab.
      </div>

      {/* Location */}
      <div>
        <FieldLabel suffix={<span style={{ color: C.red }}>*</span>}>Location</FieldLabel>
        <LocationField pinnedLocation={pinnedLocation} pinnedLabel={pinnedLabel} onRequestPin={onRequestPin} />
      </div>

      {/* Description */}
      <div>
        <FieldLabel>Description</FieldLabel>
        <textarea
          rows={3}
          value={description}
          onChange={(e) => onDescription(e.target.value)}
          placeholder="Describe the defect — size, depth, road name, near landmark…"
          style={{ width: "100%", boxSizing: "border-box", minHeight: 64, padding: "11px 13px", border: `1px solid ${C.border}`, borderRadius: RADIUS.input, fontSize: 13.5, background: C.inset, outline: "none", resize: "vertical", color: C.ink }}
        />
      </div>

      {/* Severity */}
      <div>
        <FieldLabel>Severity</FieldLabel>
        <div className="flex" style={{ gap: 8 }}>
          {SEV_ITEMS.map(({ key, color }) => {
            const on = severity === key;
            return (
              <button
                key={key}
                onClick={() => onSeverity(key)}
                style={{
                  flex: 1,
                  padding: 11,
                  border: `1.5px solid ${on ? color : C.border}`,
                  borderRadius: RADIUS.input,
                  background: on ? color : "#fff",
                  color: on ? "#fff" : C.secondary,
                  fontSize: 12.5,
                  fontWeight: 700,
                  letterSpacing: ".06em",
                  cursor: "pointer",
                }}
              >
                {key}
              </button>
            );
          })}
        </div>
      </div>

      {/* Submit */}
      <SubmitButton canSubmit={canSubmit} onClick={onSubmit} label="Submit report" />
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export interface ReportPanelProps {
  open: boolean;
  pinnedLocation: GeoPoint | null;
  pinnedLabel: string;
  onRequestPin: () => void;
  onClose: () => void;
  onPotholeSubmitted: (p: UserReportedPothole) => void;
  onAccidentSubmitted?: (r: AccidentReport) => void;
  // When the panel is opened programmatically (e.g. the PWA "Report Incident"
  // launch), force this mode on open; with autoStartVoice, also start the
  // dispatcher call once. Undefined → normal behaviour (opens on the last mode).
  initialMode?: ReportMode;
  autoStartVoice?: boolean;
  autoStartChat?: boolean;
  // Voice-bot language chosen at launch (PWA). Seeds the dispatcher locale;
  // undefined → derive from the app locale as before. The user can still switch.
  initialVoiceLocale?: VoiceLocale;
}

export type ReportMode = "SOS" | "TEXT" | "VOICE" | "DISPATCHER" | "CHAT" | "POTHOLE";
type PanelStatus = "IDLE" | "BUSY" | "ASSESSING" | "MATCHING" | "COMPLETE" | "ERROR" | "POTHOLE_DONE";

export default function ReportPanel({
  open,
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
  onClose,
  onPotholeSubmitted,
  onAccidentSubmitted,
  initialMode,
  autoStartVoice,
  autoStartChat,
  initialVoiceLocale,
}: ReportPanelProps) {
  // Initialise from initialMode when provided (the PWA launch passes it from the
  // very first render, so no setState-in-effect is needed to apply it).
  const [mode, setMode] = useState<ReportMode>(() => initialMode ?? "SOS");
  const [panelStatus, setPanelStatus] = useState<PanelStatus>("IDLE");
  const [sosError, setSosError] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [vehiclesInvolved, setVehiclesInvolved] = useState("");
  const [casualties, setCasualties] = useState("");
  const [selectedFlags, setSelectedFlags] = useState<Set<string>>(new Set());
  const [selectedSubType, setSelectedSubType] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const appLocale = useLocaleStore((s) => s.locale);
  const { showHindi } = useBilingual();
  const [locale, setLocale] = useState<VoiceLocale>(initialVoiceLocale ?? (appLocale === "HI" ? "hi-IN" : "en-IN"));
  const [createdIncident, setCreatedIncident] = useState<AccidentReport | null>(null);
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResult | null>(null);
  const [dupMatch, setDupMatch] = useState<DuplicateMatch | null>(null);
  const [pendingIncident, setPendingIncident] = useState<AccidentReport | null>(null);
  const [dispatcherLocation, setDispatcherLocation] = useState<{ point: GeoPoint; label: string } | null>(null);
  // #4 (Hindi staged dispatch): responders the backend notified early, mid-call,
  // as interim notifications — shown as chips in DispatcherSection. Each is a
  // notification record only; the authoritative dispatch records + real ETAs are
  // produced by the normal matching flow after submit (never faked here).
  const [interimDispatches, setInterimDispatches] = useState<InterimDispatch[]>([]);

  const [potholeDescription, setPotholeDescription] = useState("");
  const [potholeSeverity, setPotholeSeverity] = useState<"HIGH" | "MEDIUM" | "LOW">("MEDIUM");

  const voice = useVoiceInput();

  function setFlag(f: string, active: boolean) {
    setSelectedFlags((prev) => {
      const next = new Set(prev);
      if (active) next.add(f); else next.delete(f);
      return next;
    });
  }

  const dispatcher = useVoiceDispatcher({
    onDescription: setDescription,
    onVehiclesInvolved: (n) => setVehiclesInvolved(String(n)),
    onCasualties: (n) => setCasualties(String(n)),
    onSetFlag: setFlag,
    onSubType: (v, cat) => { setSelectedSubType(v); setSelectedCategory(cat); },
    onLocationCaptured: (loc, label) => setDispatcherLocation({ point: loc, label }),
    // If the user dropped a map pin (manual location), use THAT for the
    // dispatcher instead of device GPS. Prefer an existing dispatcher-captured
    // pin, then the shared map pin; null → fall back to current GPS.
    getManualLocation: () => {
      const point = dispatcherLocation?.point ?? pinnedLocation;
      if (!point) return null;
      const label = dispatcherLocation?.label || pinnedLabel || `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
      return { lat: point.lat, lng: point.lng, label };
    },
    onInterimDispatch: (services, loc) => {
      const point: GeoPoint | null =
        (loc && typeof loc.lat === "number" && typeof loc.lng === "number"
          ? { lat: loc.lat, lng: loc.lng }
          : null) ?? dispatcherLocation?.point ?? pinnedLocation;
      handleInterimDispatch(services, point);
    },
    onFacilityQuery: (req) => handleFacilityQuery(req),
    onLodgeComplaint: (req) => handleLodgeComplaint(req),
    onSubmitReady: (payload: DispatcherSubmitPayload) => {
      const loc = dispatcherLocation?.point ?? payload.location ?? pinnedLocation;
      if (!loc) return;
      const label = dispatcherLocation?.label || pinnedLabel || `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)}`;
      commitIncident({
        id: makeIncidentId(),
        timestamp: new Date().toISOString(),
        location: loc,
        locationLabel: label,
        reportMode: "DISPATCHER",
        vehiclesInvolved: payload.vehiclesInvolved,
        estimatedCasualties: payload.casualties,
        description: payload.description.trim(),
        flags: payload.flags,
        severity: "UNKNOWN",
        severitySource: null,
      });
    },
  });

  // English-only TEXT-CHAT dispatcher (typed, no audio). Reuses the SAME field
  // setters + the SAME assess → MatchingPanel → dispatch_update flow as the voice
  // dispatcher; only submitted incidents get reportMode "CHAT" so the briefing
  // effect below routes dispatch_update to the chat socket. Additive — the voice
  // dispatcher above is untouched, and only one of the two is ever active (by
  // mode), so they safely share description/vehicles/casualties/flags/location.
  const chat = useTextChat({
    onDescription: setDescription,
    onVehiclesInvolved: (n) => setVehiclesInvolved(String(n)),
    onCasualties: (n) => setCasualties(String(n)),
    onSetFlag: setFlag,
    onSubType: (v, cat) => { setSelectedSubType(v); setSelectedCategory(cat); },
    onLocationCaptured: (loc, label) => setDispatcherLocation({ point: loc, label }),
    getManualLocation: () => {
      const point = dispatcherLocation?.point ?? pinnedLocation;
      if (!point) return null;
      const label = dispatcherLocation?.label || pinnedLabel || `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
      return { lat: point.lat, lng: point.lng, label };
    },
    // General helpline: nearest-facility answers reuse the SAME frontend compute
    // the voice dispatcher uses (real responder data + set location).
    onFacilityQuery: (req) => handleFacilityQuery(req),
    onSubmitReady: (payload: DispatcherSubmitPayload) => {
      const loc = dispatcherLocation?.point ?? payload.location ?? pinnedLocation;
      if (!loc) return;
      const label = dispatcherLocation?.label || pinnedLabel || `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)}`;
      commitIncident({
        id: makeIncidentId(),
        timestamp: new Date().toISOString(),
        location: loc,
        locationLabel: label,
        reportMode: "CHAT",
        vehiclesInvolved: payload.vehiclesInvolved,
        estimatedCasualties: payload.casualties,
        description: payload.description.trim(),
        flags: payload.flags,
        severity: "UNKNOWN",
        severitySource: null,
      });
    },
  });

  // Responder/service lists from the Aggregator DPG — fetched once at mount
  // so they're ready long before an incident reaches the matching stage.
  const { data: responders, results: respondersPlaces, allDone: respondersLoaded } = useResponders();
  const appendReport = useEventLog((s) => s.appendReport);
  const appendAssessment = useEventLog((s) => s.appendAssessment);
  const appendNote = useEventLog((s) => s.appendNote);
  const appendDuplicateFlagged = useEventLog((s) => s.appendDuplicateFlagged);

  // #4 (Hindi staged dispatch): the backend notified responder(s) early,
  // mid-call. Match the nearest one of each type from the in-memory Aggregator
  // lists (distance only — no route/ETA is computed here; that stays for the
  // post-submit matching flow), log an honest interim notification record in
  // the timeline, and surface a chip. Honesty (Hard Rules 1/2/5): "notified /
  // being arranged", never a dispatched/tracked claim or a fabricated ETA.
  const handleInterimDispatch = useCallback(
    (services: string[], point: GeoPoint | null) => {
      const LIST: Record<string, { lat: number; lng: number; name: string }[]> = {
        ambulance: responders.ambulanceStations,
        fire: responders.fireStations,
        towing: responders.towingStations,
      };
      const LABEL: Record<string, string> = { ambulance: "Ambulance", fire: "Fire service", towing: "Towing" };
      const dispatchedAt = Date.now();
      const picks: InterimDispatch[] = [];
      for (const svc of services) {
        const list = LIST[svc] ?? [];
        let best: { name: string; km: number } | null = null;
        if (point) {
          for (const r of list) {
            const km = haversineKm(point, { lat: r.lat, lng: r.lng });
            if (!best || km < best.km) best = { name: r.name, km };
          }
        }
        const pick: InterimDispatch = {
          service: svc,
          name: best?.name ?? null,
          distanceKm: best ? Math.round(best.km * 10) / 10 : null,
          dispatchedAt,
        };
        picks.push(pick);
        const label = LABEL[svc] ?? svc;
        appendNote(
          "",
          best
            ? `Interim dispatch — ${label} notified: ${best.name} (~${best.km.toFixed(1)} km, nearest post). Being arranged; final ETA confirmed at close of call.`
            : `Interim dispatch — ${label} notified. Being arranged; final ETA confirmed at close of call.`
        );
      }
      // Merge, keeping the latest entry per service (dedupe by service key).
      setInterimDispatches((prev) => {
        const byKey = new Map(prev.map((d) => [d.service, d]));
        for (const p of picks) byKey.set(p.service, p);
        return Array.from(byKey.values());
      });
    },
    [responders, appendNote]
  );

  // Phase 3 (multi-intent helpline): answer the backend's find_nearest_facility
  // tool from the in-memory responder data + Google POIs. ETA is a labelled
  // STRAIGHT-LINE estimate (haversineEtaMinutes) — honest, no live tracking, and
  // never an open/closed claim (that status isn't stored).
  const handleFacilityQuery = useCallback(
    (req: { facilityType: string; capability: string | null; location: { lat: number; lng: number; label?: string } | null }): HelplineFacility | null => {
      const point: GeoPoint | null =
        (req.location && typeof req.location.lat === "number" ? { lat: req.location.lat, lng: req.location.lng } : null)
        ?? dispatcherLocation?.point ?? pinnedLocation;
      if (!point) return null;
      type Cand = { name: string; lat: number; lng: number; contact: string | null; trauma?: boolean };
      const ft = req.facilityType;
      let cands: Cand[];
      switch (ft) {
        case "hospital":
          cands = responders.hospitals.map((h) => ({ name: h.name, lat: h.lat, lng: h.lng, contact: h.phone ?? null, trauma: h.traumaLevel != null }));
          break;
        case "ambulance":
          cands = responders.ambulanceStations.map((a) => ({ name: a.name, lat: a.lat, lng: a.lng, contact: a.contactNumber ?? null }));
          break;
        case "fire":
          cands = responders.fireStations.map((f) => ({ name: f.name, lat: f.lat, lng: f.lng, contact: f.contactNumber ?? null }));
          break;
        case "tow":
          cands = responders.towingStations.map((t) => ({ name: t.name, lat: t.lat, lng: t.lng, contact: t.contactNumber ?? null }));
          break;
        case "police":
          cands = responders.policeStations.map((p) => ({ name: p.name, lat: p.lat, lng: p.lng, contact: p.phone ?? null }));
          break;
        case "mechanic":
          cands = (respondersPlaces.car_repair ?? []).map((g) => ({ name: g.name, lat: g.lat, lng: g.lng, contact: g.phone }));
          break;
        case "fuel":
          cands = (respondersPlaces.gas_station ?? []).map((g) => ({ name: g.name, lat: g.lat, lng: g.lng, contact: g.phone }));
          break;
        default:
          return null;
      }
      if (cands.length === 0) return null;
      // Capability: for a hospital + a trauma/head-injury request, prefer a
      // trauma-capable facility; if none, return the nearest with an honest note.
      let note: string | null = null;
      let pool = cands;
      const wantsTrauma = ft === "hospital" && !!req.capability && /trauma|head|neuro|spinal|serious|गंभीर|सिर/i.test(req.capability);
      if (wantsTrauma) {
        const trauma = cands.filter((c) => c.trauma);
        if (trauma.length) { pool = trauma; note = "trauma-capable"; }
        else note = "nearest hospital; trauma capability not confirmed";
      }
      let best: Cand | null = null;
      let bestKm = Infinity;
      for (const c of pool) {
        const km = haversineKm(point, { lat: c.lat, lng: c.lng });
        if (km < bestKm) { bestKm = km; best = c; }
      }
      if (!best) return null;
      appendNote("", `Helpline lookup — nearest ${ft}: ${best.name} (~${bestKm.toFixed(1)} km). Estimate only; open/closed not tracked.`);
      return {
        name: best.name,
        contactNumber: best.contact,
        distanceKm: Math.round(bestKm * 10) / 10,
        etaMinutes: Math.round(haversineEtaMinutes(bestKm)),
        note,
      };
    },
    [responders, respondersPlaces, dispatcherLocation, pinnedLocation, appendNote]
  );

  // Phase 3: answer the backend's lodge_complaint tool by logging a road-defect
  // record (reusing the pothole POST path) and returning a REAL reference id.
  // Honest framing only — it's logged in the system; nothing is "escalated".
  const handleLodgeComplaint = useCallback(
    async (req: { description: string; complaintType: string; location: { lat: number; lng: number; label?: string } | null }): Promise<string | null> => {
      const desc = (req.description || "").trim();
      if (!desc) return null;
      const point: GeoPoint | null =
        (req.location && typeof req.location.lat === "number" ? { lat: req.location.lat, lng: req.location.lng } : null)
        ?? dispatcherLocation?.point ?? pinnedLocation;
      const label = req.location?.label || dispatcherLocation?.label || pinnedLabel || (point ? `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}` : "Unknown location");
      // Mostly-numeric, easy for the voice bot to read out digit-by-digit.
      const ref = `HD-${Math.floor(100000 + Math.random() * 900000)}`;
      // A logged record on the map only makes sense with coordinates; without
      // them we still keep a timeline record + return the reference.
      if (point) {
        try {
          await fetch("/api/potholes", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id: ref,
              lat: point.lat,
              lng: point.lng,
              road: label,
              severity: "MEDIUM",
              description: `[helpline:${req.complaintType}] ${desc}`.slice(0, 300),
              reported_date: new Date().toISOString().slice(0, 10),
            }),
          });
        } catch { /* best-effort: the timeline record + reference still stand */ }
      }
      appendNote("", `Helpline complaint logged (${req.complaintType}) — ref ${ref} at ${label}: ${desc}`);
      return ref;
    },
    [dispatcherLocation, pinnedLocation, pinnedLabel, appendNote]
  );

  const entries = useEventLog((s) => s.entries);
  const clearRoutes = useRoutingStore((s) => s.clearRoutes);

  // After a voice-dispatcher submission, feed the agent the SAME responder
  // ETAs the dashboard is displaying so it can announce them and close the
  // call — MatchingPanel already logs every one of them as a ROUTE_ESTIMATED
  // event (and the hospital/police match as HOSPITAL_MATCHED), so this only
  // reads the event log; nothing is recomputed and no extra API call is made.
  // Debounced: the route entries land in bursts as MatchingPanel's phases
  // complete, so wait for a quiet window before sending exactly once per
  // incident. If matching fails entirely and nothing is ever logged, the
  // backend's own timeout closes the call without ETAs — never with invented
  // ones.
  const briefingSentForRef = useRef<string | null>(null);
  const sendDispatchBriefing = dispatcher.sendDispatchBriefing;
  const chatSendDispatchBriefing = chat.sendDispatchBriefing;
  // Responder ETAs handed to the chat's closing so it can render interactive
  // countdown CARDS (not a text wall). Same values the briefing text/voice use.
  const [chatBriefingServices, setChatBriefingServices] = useState<DispatchBriefingServices | null>(null);
  const [chatBriefingAt, setChatBriefingAt] = useState<string | null>(null);

  // Link this dispatcher call's captured Post-Call Analytics to the committed
  // INC-… id (attachIncidentId is a stable ref-setter, so this fires once when
  // the dispatcher incident is created). CHAT incidents link to the chat hook.
  const attachIncidentId = dispatcher.attachIncidentId;
  const chatAttachIncidentId = chat.attachIncidentId;
  useEffect(() => {
    if (createdIncident?.reportMode === "DISPATCHER") attachIncidentId(createdIncident.id);
    else if (createdIncident?.reportMode === "CHAT") chatAttachIncidentId(createdIncident.id);
  }, [createdIncident, attachIncidentId, chatAttachIncidentId]);

  // ── PWA launch: auto-start the dispatcher ────────────────────────────────────
  // (initialMode is applied via the `mode` useState initializer above.)
  // autoStartVoice → start the dispatcher call automatically (one shot), as a
  // direct consequence of the launch tap. The browser prompts for mic; if it's
  // denied / unsupported / start fails, DispatcherSection falls back to its
  // normal mic button so the user can retry — never a dead screen.
  const autoStartedRef = useRef(false);
  const startDispatcher = dispatcher.start;
  const dispatcherSupported = dispatcher.supported;
  const dispatcherStatus = dispatcher.status;
  useEffect(() => {
    if (!open) { autoStartedRef.current = false; return; }
    if (!autoStartVoice || autoStartedRef.current) return;
    if (mode !== "DISPATCHER" || !dispatcherSupported) return;
    if (dispatcherStatus !== "idle" && dispatcherStatus !== "ended") return;
    autoStartedRef.current = true;
    startDispatcher(locale);
  }, [open, autoStartVoice, mode, dispatcherSupported, dispatcherStatus, locale, startDispatcher]);

  // Reset the one-shot guard on a REAL unmount so a mount → unmount → mount
  // cycle (React StrictMode's dev double-invoke, fast-refresh, or the SOS key
  // remount) re-fires the auto-start on the surviving mount instead of leaving
  // it disabled after the unmount tore the call's WebSocket down. Pairs with the
  // dispatcher hook's unmount-only teardown so exactly one live connection
  // survives the cycle. Empty deps → only on unmount, never on prop churn.
  useEffect(() => () => { autoStartedRef.current = false; }, []);

  // autoStartChat → open the text-chat dispatcher automatically (one shot), the
  // direct consequence of tapping the map's Chat FAB (mirrors autoStartVoice).
  const chatAutoStartedRef = useRef(false);
  const startChatSession = chat.start;
  const chatSupported = chat.supported;
  const chatStatus = chat.status;
  useEffect(() => {
    if (!open) { chatAutoStartedRef.current = false; return; }
    if (!autoStartChat || chatAutoStartedRef.current) return;
    if (mode !== "CHAT" || !chatSupported) return;
    // Wait for the location gate: the chat only auto-starts once the user has
    // set their location (so the bot greets with it already confirmed).
    if (!(dispatcherLocation?.point || pinnedLocation)) return;
    if (chatStatus !== "idle") return;
    chatAutoStartedRef.current = true;
    startChatSession();
  }, [open, autoStartChat, mode, chatSupported, chatStatus, startChatSession, dispatcherLocation, pinnedLocation]);
  useEffect(() => () => { chatAutoStartedRef.current = false; }, []);

  useEffect(() => {
    // The SAME briefing wiring the voice dispatcher uses, extended to CHAT: once
    // the matching flow has logged its ROUTE_ESTIMATED / HOSPITAL_MATCHED
    // entries, hand the responder ETAs back to whichever dispatcher is active so
    // it delivers the closing briefing (spoken for voice, a chat message for CHAT).
    const isVoice = createdIncident?.reportMode === "DISPATCHER";
    const isChat = createdIncident?.reportMode === "CHAT";
    if (!createdIncident || (!isVoice && !isChat)) return;
    if (briefingSentForRef.current === createdIncident.id) return;
    const incidentId = createdIncident.id;
    const routeEntries = entries.filter(
      (e) => e.type === "ROUTE_ESTIMATED" && (e.payload as RouteEstimatedPayload).incidentId === incidentId
    );
    const matched = entries.find(
      (e) => e.type === "HOSPITAL_MATCHED" && (e.payload as HospitalMatchedPayload).incidentId === incidentId
    );
    if (routeEntries.length === 0 && !matched) return;

    const t = setTimeout(() => {
      briefingSentForRef.current = incidentId;
      const services: DispatchBriefingServices = {};
      for (const e of routeEntries) {
        const p = e.payload as RouteEstimatedPayload;
        const key = p.entityType.toLowerCase() as keyof DispatchBriefingServices;
        if (!services[key]) {
          services[key] = {
            name: p.entityName,
            etaMinutes: Math.round(p.roadDurationMin),
            distanceKm: p.roadDistanceKm,
          };
        }
      }
      // Route polylines can fail while the matrix-based match still
      // succeeded — fall back to the HOSPITAL_MATCHED payload so the agent
      // can at least name the facility (with its traffic ETA when known).
      if (matched) {
        const p = matched.payload as HospitalMatchedPayload;
        const top = p.rankedHospitals[0];
        if (top && !services.hospital) {
          services.hospital = {
            name: top.hospital.name,
            etaMinutes: top.roadDurationMin != null ? Math.round(top.roadDurationMin) : null,
            distanceKm: top.roadDistanceKm,
          };
        }
        if (p.nearestPolice && !services.police) {
          services.police = {
            name: p.nearestPolice.station.name,
            etaMinutes: p.nearestPolice.roadDurationMin != null ? Math.round(p.nearestPolice.roadDurationMin) : null,
            distanceKm: p.nearestPolice.roadDistanceKm,
          };
        }
      }
      // Sanity-cap ETAs (the "1902 minutes" bug): a responder matched at an
      // implausible distance — bad live coordinates from the Guwahati migration —
      // yields a straight-line/road estimate of ~1900 min (≈31 h). Drop any
      // responder whose ETA exceeds a sane local ceiling so the briefing/cards
      // show only real local estimates (or "unavailable"), never a fabricated
      // 31-hour number. Applies to BOTH voice and chat (they share `services`).
      const MAX_SANE_ETA_MIN = 120;
      for (const k of Object.keys(services) as (keyof DispatchBriefingServices)[]) {
        const svc = services[k];
        if (svc && svc.etaMinutes != null && svc.etaMinutes > MAX_SANE_ETA_MIN) delete services[k];
      }
      if (isChat) {
        setChatBriefingServices(services);
        setChatBriefingAt(new Date().toISOString());
      }
      (isChat ? chatSendDispatchBriefing : sendDispatchBriefing)(services);
    }, 2500);
    return () => clearTimeout(t);
  }, [entries, createdIncident, sendDispatchBriefing, chatSendDispatchBriefing]);

  function resetForm() {
    setPanelStatus("IDLE");
    setSosError(null);
    setDescription("");
    setVehiclesInvolved("");
    setCasualties("");
    setSelectedFlags(new Set());
    setSelectedSubType("");
    setSelectedCategory("");
    setCreatedIncident(null);
    setAssessmentResult(null);
    setDupMatch(null);
    setPendingIncident(null);
    setDispatcherLocation(null);
    setInterimDispatches([]);
    setPotholeDescription("");
    setPotholeSeverity("MEDIUM");
    clearRoutes();
    voice.clearTranscript();
  }

  function handlePotholeSubmit() {
    if (!pinnedLocation) return;
    const today = new Date().toISOString().slice(0, 10);
    const rand = Math.random().toString(16).slice(2, 6).toUpperCase();
    const pothole: UserReportedPothole = {
      id: `rpot-${rand}`,
      lat: pinnedLocation.lat,
      lng: pinnedLocation.lng,
      road: pinnedLabel || `${pinnedLocation.lat.toFixed(5)}, ${pinnedLocation.lng.toFixed(5)}`,
      severity: potholeSeverity,
      reportedDate: today,
      description: potholeDescription.trim() || undefined,
    };
    onPotholeSubmitted(pothole);
    setPanelStatus("POTHOLE_DONE");
  }

  function switchMode(m: ReportMode) {
    if (voice.listening) voice.stop();
    if (dispatcher.status !== "idle" && dispatcher.status !== "ended") dispatcher.stop();
    setMode(m);
    resetForm();
  }

  async function runAssessment(incident: AccidentReport) {
    setPanelStatus("ASSESSING");
    const body = {
      incident: {
        ...(selectedSubType ? { subType: selectedSubType } : {}),
        ...(selectedCategory && !selectedSubType ? { category: selectedCategory } : {}),
        description: incident.description,
      },
      signals: {
        casualties: incident.estimatedCasualties ?? 0,
        vehiclesInvolved: incident.vehiclesInvolved ?? 1,
        entrapment: incident.flags.includes("Trapped"),
        vulnerableVictim: incident.flags.includes("Heavy bleeding"),
        // Explicit tap always wins — the engine also tries to auto-detect these
        // from the free-text description server-side, but a one-tap flag is
        // faster and unambiguous, and never gets overridden by that inference.
        fire: incident.flags.includes("Fire"),
        hazmat: incident.flags.includes("Hazardous material"),
      },
      ...(incident.location
        ? { location: { latlng: [incident.location.lat, incident.location.lng] } }
        : {}),
    };
    try {
      const res = await fetch("/api/assess", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({})) as { error?: string };
        throw new Error(err.error ?? `HTTP ${res.status}`);
      }
      const result: AssessmentResult = await res.json();
      setAssessmentResult(result);
      appendAssessment(incident.id, result);
      publishAssessment(incident, result);
    } catch (e) {
      // Engine unreachable — show honest error stub, never fabricate
      const stub: AssessmentResult = {
        severity: "HIGH",
        severityScore: 3,
        impactNote: `Severity engine unreachable — treat as HIGH. ${e instanceof Error ? e.message : ""}`.trim(),
        appliedModifiers: [],
        agencies: [],
        dataGaps: ["Retry assessment when engine is available"],
        classifiedBy: "rules",
        llmUsed: false,
        lowConfidence: true,
      };
      setAssessmentResult(stub);
      appendAssessment(incident.id, stub);
      publishAssessment(incident, stub); // honest stub is mirrored too (Classified By: rules)
    } finally {
      setPanelStatus("MATCHING");
    }
  }

  function proceedWithIncident(incident: AccidentReport) {
    appendReport(incident);
    publishIncident(incident); // fire-and-forget Signals DPG mirror — never blocks the flow
    setCreatedIncident(incident);
    onAccidentSubmitted?.(incident);
    runAssessment(incident);
  }

  function commitIncident(incident: AccidentReport) {
    const dup = checkDuplicate(incident.location, incident.timestamp, entries);
    if (dup) {
      setDupMatch(dup);
      setPendingIncident(incident);
      return;
    }
    proceedWithIncident(incident);
  }

  function handleDupSkip() {
    if (!dupMatch) return;
    appendDuplicateFlagged(null, dupMatch.existingIncident.id, dupMatch.distanceM, dupMatch.deltaMinutes, "SKIPPED");
    setDupMatch(null);
    setPendingIncident(null);
    // Leave panel open so user sees the existing incidents tab
  }

  function handleDupProceed() {
    if (!dupMatch || !pendingIncident) return;
    appendDuplicateFlagged(pendingIncident.id, dupMatch.existingIncident.id, dupMatch.distanceM, dupMatch.deltaMinutes, "PROCEEDED");
    const inc = pendingIncident;
    setDupMatch(null);
    setPendingIncident(null);
    proceedWithIncident(inc);
  }

  function handleSOS() {
    if (!navigator.geolocation) {
      setSosError("Geolocation is not supported by this browser.");
      setPanelStatus("ERROR");
      return;
    }
    setPanelStatus("BUSY");
    setSosError(null);

    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lat = pos.coords.latitude;
        const lng = pos.coords.longitude;
        let label = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
        try {
          label = await reverseGeocode(lat, lng);
        } catch { /* keep coordinate label */ }

        commitIncident({
          id: makeIncidentId(),
          timestamp: new Date().toISOString(),
          location: { lat, lng },
          locationLabel: label,
          reportMode: "SOS",
          vehiclesInvolved: null,
          estimatedCasualties: null,
          description: "SOS — details unknown, treat as high priority.",
          flags: ["SOS"],
          severity: "CRITICAL",
          severitySource: "RULE_BASED",
        });
      },
      (err) => {
        setSosError(GEO_ERRORS[err.code] ?? "Could not get location.");
        setPanelStatus("ERROR");
      },
      { timeout: 10000, maximumAge: 30000 }
    );
  }

  function handleFormSubmit() {
    if (!pinnedLocation) return;
    commitIncident({
      id: makeIncidentId(),
      timestamp: new Date().toISOString(),
      location: pinnedLocation,
      locationLabel:
        pinnedLabel ||
        `${pinnedLocation.lat.toFixed(5)}, ${pinnedLocation.lng.toFixed(5)}`,
      reportMode: mode === "VOICE" ? "VOICE" : "TEXT",
      vehiclesInvolved: vehiclesInvolved ? Number(vehiclesInvolved) : null,
      estimatedCasualties: casualties ? Number(casualties) : null,
      description: description.trim(),
      flags: Array.from(selectedFlags),
      severity: "UNKNOWN",
      severitySource: null,
    });
  }

  function toggleFlag(f: string) {
    setSelectedFlags((prev) => {
      const next = new Set(prev);
      next.has(f) ? next.delete(f) : next.add(f);
      return next;
    });
  }

  const isBusy = panelStatus === "BUSY" || panelStatus === "ASSESSING";

  if (!open) return null;

  return (
    <>
      {/* Scrim */}
      <div
        className="fixed inset-0 z-[1999]"
        style={{ background: "rgba(14,26,47,.4)", backdropFilter: "blur(2px)" }}
        onClick={isBusy ? undefined : onClose}
      />

      {/* Sheet */}
      <div
        className="ts-report-sheet fixed bottom-0 z-[2000] flex flex-col bg-white"
        style={{
          // Center horizontally with left:0/right:0 + margin auto rather than
          // transform: translateX(-50%). The tsUp entry animation animates
          // `transform` (translateY), which would OVERRIDE a translateX(-50%)
          // centering for the whole 0.28s -- the sheet rendered shifted right,
          // then snapped to center when the animation ended. margin-auto
          // centering is independent of transform, so the slide-up is clean.
          left: 0,
          right: 0,
          margin: "0 auto",
          width: "min(880px, 96vw)",
          maxHeight: "82%",
          borderRadius: "18px 18px 0 0",
          boxShadow: "0 -12px 48px rgba(14,26,47,.3)",
          animation: "tsUp .28s ease",
        }}
      >
        {/* ── Duplicate warning — overlays form content ── */}
        {dupMatch && pendingIncident && (
          <div className="absolute inset-0 z-10 bg-white rounded-t-2xl flex flex-col p-5 gap-4">
            <div className="flex items-start gap-3">
              <div className="w-9 h-9 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
                <svg className="w-5 h-5 text-amber-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <div>
                <p className="text-sm font-black text-gray-900">Possible duplicate</p>
                <p className="text-xs text-gray-500 mt-0.5">
                  A report was logged{" "}
                  <span className="font-semibold">{dupMatch.deltaMinutes} min ago</span> within{" "}
                  <span className="font-semibold">{dupMatch.distanceM} m</span> of this location.
                </p>
              </div>
            </div>

            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-[10px] font-black tracking-widest text-amber-700 uppercase mb-1">
                Existing incident
              </p>
              <p className="text-xs font-mono font-semibold text-gray-900">
                {dupMatch.existingIncident.id}
              </p>
              <p className="text-xs text-gray-600 mt-0.5">
                {dupMatch.existingIncident.locationLabel}
              </p>
            </div>

            <p className="text-xs text-gray-500 leading-relaxed">
              If this is the same accident, use the existing incident. If it is a
              separate event at the same location, proceed to log a new one.
            </p>

            <div className="flex flex-col gap-2 mt-auto">
              <button
                onClick={handleDupSkip}
                className="w-full py-3 bg-[#0f2044] text-white rounded-lg text-sm font-bold"
              >
                Use existing incident
              </button>
              <button
                onClick={handleDupProceed}
                className="w-full py-2.5 border border-gray-300 rounded-lg text-sm text-gray-600 font-semibold hover:bg-gray-50"
              >
                It&apos;s a different event — log separately
              </button>
            </div>
          </div>
        )}
        {/* Header + drag handle */}
        <div className="relative flex items-center flex-shrink-0" style={{ gap: 12, padding: "14px 20px 10px" }}>
          <div style={{ position: "absolute", left: "50%", top: 7, transform: "translateX(-50%)", width: 44, height: 4, borderRadius: 2, background: C.border }} />
          <div className="flex-1">
            <span style={{ fontSize: 15, fontWeight: 700, color: C.ink }}>Report Incident</span>
            {showHindi && <span style={{ fontSize: 13, color: C.muted, fontWeight: 500 }}> · घटना रिपोर्ट करें</span>}
          </div>
          {!isBusy && (
            <button
              onClick={onClose}
              aria-label="Close report panel"
              style={{ width: 30, height: 30, border: `1px solid ${C.border}`, borderRadius: 8, background: C.inset, color: C.secondary, fontSize: 14, cursor: "pointer", flex: "none" }}
            >
              ✕
            </button>
          )}
        </div>

        {/* Mode tabs — underline style, hidden while processing. Also hidden in
            CHAT mode: chat is a dedicated FAB-launched view, not a channel tab. */}
        {(panelStatus === "IDLE" || panelStatus === "ERROR") && mode !== "CHAT" ? (
          <div className="flex flex-shrink-0" style={{ gap: 4, padding: "0 20px 12px", borderBottom: `1px solid ${C.hairline}` }}>
            {([
              { key: "SOS" as ReportMode, en: "SOS", hi: "एसओएस", color: C.red },
              // "Describe" merges the old Text + Speech-to-text: type OR dictate
              // (the mic lives inside the form now).
              { key: "TEXT" as ReportMode, en: "Describe", hi: "लिखें / बोलें", color: C.blue },
              { key: "DISPATCHER" as ReportMode, en: "Voice", hi: "वॉइस", color: C.blue },
              { key: "POTHOLE" as ReportMode, en: "Pothole", hi: "गड्ढा", color: "#B06712" },
            ]).map((tab) => {
              const on = mode === tab.key;
              return (
                <button
                  key={tab.key}
                  onClick={() => switchMode(tab.key)}
                  className="flex flex-col items-center"
                  style={{
                    flex: 1,
                    padding: "8px 6px 10px",
                    border: "none",
                    borderBottom: `2.5px solid ${on ? tab.color : "transparent"}`,
                    background: "transparent",
                    cursor: "pointer",
                    fontSize: 12.5,
                    fontWeight: on ? 700 : 500,
                    color: on ? tab.color : C.muted,
                    lineHeight: 1.2,
                    gap: 1,
                  }}
                >
                  <span>{tab.en}</span>
                  {showHindi && <span style={{ fontSize: 10.5, fontWeight: 500, color: on ? tab.color : C.faint }}>{tab.hi}</span>}
                </button>
              );
            })}
          </div>
        ) : null}

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
          {mode === "CHAT" ? (
            // Definite height so the thread scrolls and the input pins to the
            // bottom (box-border: padding is inside the height). Fits within the
            // sheet's 82vh max on mobile and caps on desktop.
            <div style={{ height: "min(70vh, 540px)", padding: "16px 20px 18px" }}>
              {!(dispatcherLocation?.point || pinnedLocation) ? (
                // Location gate first — detect / set-on-map — before the chat begins.
                <ChatLocationGate
                  showHindi={showHindi}
                  onLocation={(p, label) => setDispatcherLocation({ point: p, label })}
                />
              ) : (
                <ChatSection chat={chat} services={chatBriefingServices} servicesAt={chatBriefingAt} />
              )}
              {/* Headless matching for a SUBMITTED chat incident: runs the same
                  assess + MatchingPanel routes work (logging ROUTE_ESTIMATED /
                  HOSPITAL_MATCHED, which triggers the closing-briefing effect
                  above) WITHOUT replacing the chat UI. The dashboard match view
                  itself belongs to the voice/operator paths; the chat conveys the
                  responder details as its closing message. */}
              {createdIncident?.reportMode === "CHAT" && assessmentResult && respondersLoaded && responders.policeStations.length > 0 && (
                <div style={{ display: "none" }} aria-hidden>
                  <MatchingPanel
                    hospitals={responders.hospitals}
                    policeStations={responders.policeStations}
                    ambulanceStations={responders.ambulanceStations}
                    fireStations={responders.fireStations}
                    towingStations={responders.towingStations}
                    incident={createdIncident}
                    assessment={assessmentResult}
                    ambulanceDispatchedAt={null}
                    onReady={() => setPanelStatus("COMPLETE")}
                  />
                </div>
              )}
            </div>
          ) : panelStatus === "ASSESSING" && createdIncident ? (
            <AssessingView incidentId={createdIncident.id} />
          ) : panelStatus === "MATCHING" && createdIncident && !assessmentResult ? (
            <div className="flex flex-col items-center gap-3 p-8">
              <div className="w-8 h-8 border-[3px] border-[#0f2044] border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-gray-500">Matching nearest services…</p>
            </div>
          ) : (panelStatus === "MATCHING" || panelStatus === "COMPLETE") &&
            createdIncident &&
            assessmentResult ? (
            <div className="flex flex-col" style={{ gap: 12, padding: "4px 20px 20px" }}>
              {/* Assessment result — always shown */}
              <AssessmentCard result={assessmentResult} incidentId={createdIncident.id} />

              {/* Matching + routing — responder lists come from the Aggregator DPG;
                  MatchingPanel fetches hospital candidates + Routes API internally */}
              {respondersLoaded && responders.policeStations.length > 0 ? (
                <MatchingPanel
                  hospitals={responders.hospitals}
                  policeStations={responders.policeStations}
                  ambulanceStations={responders.ambulanceStations}
                  fireStations={responders.fireStations}
                  towingStations={responders.towingStations}
                  incident={createdIncident}
                  assessment={assessmentResult}
                  ambulanceDispatchedAt={
                    interimDispatches.find((d) => d.service === "ambulance")?.dispatchedAt ?? null
                  }
                  onReady={() => setPanelStatus("COMPLETE")}
                />
              ) : respondersLoaded ? (
                <div style={{ background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 11, padding: "11px 15px" }}>
                  <p style={{ fontSize: 12.5, fontWeight: 600, color: C.saffronSoftText }}>Responder registry unavailable</p>
                  <p style={{ fontSize: 11.5, color: C.saffronSoftText, marginTop: 2 }}>
                    The Aggregator DPG could not be reached, so hospital/police matching cannot run.
                    The incident is logged — retry once the registry is back.
                  </p>
                </div>
              ) : (
                <div className="flex items-center gap-2" style={{ padding: "8px 4px" }}>
                  <span className="inline-block w-3.5 h-3.5 rounded-full animate-spin" style={{ border: "2px solid #d1d5db", borderTopColor: C.secondary }} />
                  <p style={{ fontSize: 12.5, color: C.secondary }}>Loading responder registry…</p>
                </div>
              )}

              <button
                onClick={onClose}
                className="w-full"
                style={{ marginTop: 2, padding: 14, border: "none", borderRadius: 12, background: C.navy800, color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer" }}
              >
                Close{showHindi && " · बंद करें"}
              </button>
            </div>
          ) : panelStatus === "POTHOLE_DONE" ? (
            <div className="p-6 flex flex-col items-center gap-4 text-center">
              <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center">
                <svg className="w-6 h-6 text-amber-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <div>
                <p className="text-sm font-bold text-gray-900">Pothole reported</p>
                <p className="text-xs text-gray-500 mt-1">Switch to the Accidents tab to see it on the map.</p>
              </div>
              <button
                onClick={onClose}
                className="w-full bg-[#0f2044] text-white py-3 rounded-lg text-sm font-bold hover:bg-[#1a3567] transition-colors"
              >
                Done
              </button>
            </div>
          ) : mode === "SOS" ? (
            <SOSView
              status={
                panelStatus === "BUSY" ? "BUSY"
                : panelStatus === "ERROR" ? "ERROR"
                : "IDLE"
              }
              error={sosError}
              onSend={handleSOS}
            />
          ) : mode === "DISPATCHER" ? (
            <div style={{ padding: "16px 20px 20px" }}>
              <DispatcherSection
                dispatcher={dispatcher}
                locale={locale}
                onLocaleChange={setLocale}
                selectedSubType={selectedSubType}
                selectedCategory={selectedCategory}
                description={description}
                vehiclesInvolved={vehiclesInvolved}
                casualties={casualties}
                selectedFlags={selectedFlags}
                interimDispatches={interimDispatches}
                dispatcherLocation={dispatcherLocation}
                pinnedLocation={pinnedLocation}
                pinnedLabel={pinnedLabel}
                onRequestPin={onRequestPin}
              />
            </div>
          ) : mode === "POTHOLE" ? (
            <PotholeFormView
              pinnedLocation={pinnedLocation}
              pinnedLabel={pinnedLabel}
              onRequestPin={onRequestPin}
              description={potholeDescription}
              onDescription={setPotholeDescription}
              severity={potholeSeverity}
              onSeverity={setPotholeSeverity}
              onSubmit={handlePotholeSubmit}
              canSubmit={!!pinnedLocation}
            />
          ) : (
            <FormView
              mode={mode as "TEXT" | "VOICE"}
              voice={voice}
              locale={locale}
              onLocaleChange={setLocale}
              pinnedLocation={pinnedLocation}
              pinnedLabel={pinnedLabel}
              onRequestPin={onRequestPin}
              selectedSubType={selectedSubType}
              selectedCategory={selectedCategory}
              onSubType={(v, cat) => { setSelectedSubType(v); setSelectedCategory(cat); }}
              description={description}
              onDescription={setDescription}
              vehiclesInvolved={vehiclesInvolved}
              onVehiclesInvolved={setVehiclesInvolved}
              casualties={casualties}
              onCasualties={setCasualties}
              selectedFlags={selectedFlags}
              onToggleFlag={toggleFlag}
              onSubmit={handleFormSubmit}
              canSubmit={!!pinnedLocation}
            />
          )}
        </div>
      </div>
    </>
  );
}
