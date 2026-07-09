"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useVoiceInput, type VoiceLocale } from "@/hooks/useVoiceInput";
import {
  useVoiceDispatcher,
  type DispatcherSubmitPayload,
  type DispatchBriefingServices,
} from "@/hooks/useVoiceDispatcher";
import { DispatcherSection } from "@/components/report/DispatcherSection";
import { useEventLog } from "@/store/eventLog";
import { reverseGeocode } from "@/lib/geocode";
import { checkDuplicate, type DuplicateMatch } from "@/lib/dedup";
import MatchingPanel from "@/components/report/MatchingPanel";
import { useRoutingStore } from "@/store/routingStore";
import { useLocaleStore } from "@/store/localeStore";
import hospitalsRaw from "../../../data/hospitals.json";
import policeRaw from "../../../data/police-stations.json";
import ambulanceRaw from "../../../data/ambulance-stations.json";
import fireStationsRaw from "../../../data/fire-stations.json";
import towingStationsRaw from "../../../data/towing-stations.json";
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

const HOSPITALS = hospitalsRaw.hospitals as unknown as Hospital[];
const POLICE_STATIONS = policeRaw.policeStations as unknown as PoliceStation[];
const AMBULANCE_STATIONS = ambulanceRaw.ambulanceStations as unknown as AmbulanceStation[];
const FIRE_STATIONS = fireStationsRaw.fireStations as unknown as FireStation[];
const TOWING_STATIONS = towingStationsRaw.towingStations as unknown as TowingStation[];

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
  const sev = SEV[score] ?? SEV[1];
  const [modOpen, setModOpen] = useState(false);
  const classLabel = CLASSIFIED_LABEL[result.classifiedBy] ?? result.classifiedBy;

  return (
    <div className="flex flex-col gap-4">
      {/* Incident created confirmation */}
      <div className="flex items-center gap-2 px-1">
        <div className="w-5 h-5 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
          <svg className="w-3 h-3 text-green-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-xs text-gray-500">
          Incident <span className="font-mono font-semibold text-gray-800">{incidentId}</span> created and logged
        </p>
      </div>

      {/* Assessment card */}
      <div className="rounded-xl border-2 overflow-hidden" style={{ borderColor: sev.border, background: sev.bg }}>
        {/* Header */}
        <div className="px-4 py-2 flex items-center justify-between" style={{ background: sev.track }}>
          <p className="text-[11px] font-black tracking-widest text-white uppercase">Severity Assessment</p>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-white/20 text-white">
              {classLabel}
            </span>
            {result.lowConfidence && (
              <span className="text-[10px] text-white/80 italic">low-confidence input</span>
            )}
          </div>
        </div>

        {/* SubType */}
        {result.subType && (
          <div className="px-4 pt-3">
            <p className="text-[10px] font-black tracking-widest uppercase mb-0.5" style={{ color: sev.text }}>Incident Type</p>
            <p className="text-sm font-semibold text-gray-900">{result.subType}</p>
          </div>
        )}

        {/* Score circle + label + track */}
        <div className="pt-4 pb-3 flex flex-col items-center gap-2">
          <div className="w-24 h-24 rounded-full border-4 flex items-center justify-center"
            style={{ borderColor: sev.border, background: "#fff" }}>
            <span className="text-6xl font-black leading-none tabular-nums" style={{ color: sev.text }}>
              {score}
            </span>
          </div>
          <p className="text-xl font-black tracking-wide uppercase" style={{ color: sev.text }}>
            {result.severity}
          </p>
          <div className="flex gap-1 mt-1">
            {([1, 2, 3, 4] as AssessmentSeverity[]).map((n) => (
              <div key={n} className="w-8 h-2 rounded-full transition-all"
                style={{ background: n <= score ? SEV[n].track : "#e5e7eb" }} />
            ))}
          </div>
        </div>

        <div className="px-4 pb-4 flex flex-col gap-3">
          {/* Impact note */}
          <div>
            <p className="text-[10px] font-black tracking-widest uppercase mb-1" style={{ color: sev.text }}>
              Impact Assessment
            </p>
            <p className="text-sm text-gray-800 leading-relaxed">{result.impactNote}</p>
          </div>

          {/* Agency chips */}
          {result.agencies.length > 0 && (
            <div className="border-t pt-3" style={{ borderColor: sev.border }}>
              <p className="text-[10px] font-black tracking-widest uppercase mb-2" style={{ color: sev.text }}>
                Agencies to Notify
              </p>
              <div className="flex flex-wrap gap-1.5">
                {result.agencies.map((a) => (
                  <span key={a.code}
                    className="text-xs font-semibold px-2.5 py-1 rounded-full border"
                    style={{ borderColor: sev.border, color: sev.text, background: "#fff" }}>
                    {a.label}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Why this rating */}
          {result.appliedModifiers.length > 0 && (
            <div className="border-t pt-3" style={{ borderColor: sev.border }}>
              <button type="button" onClick={() => setModOpen((v) => !v)}
                className="flex items-center gap-1 text-[11px] font-semibold text-gray-500 hover:text-gray-800">
                <svg className={`w-3 h-3 transition-transform ${modOpen ? "rotate-90" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
                Why this rating
              </button>
              {modOpen && (
                <ul className="mt-2 flex flex-col gap-1">
                  {result.appliedModifiers.map((m, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-gray-700">
                      <span className="text-gray-400 flex-shrink-0 mt-0.5">+</span>{m}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Ask-next checklist */}
          {result.dataGaps.length > 0 && (
            <div className="border-t pt-3" style={{ borderColor: sev.border }}>
              <p className="text-[10px] font-black tracking-widest uppercase mb-2" style={{ color: sev.text }}>
                Ask Next
              </p>
              <ol className="flex flex-col gap-1.5">
                {result.dataGaps.map((gap, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-gray-700">
                    <span className="w-4 text-gray-400 font-semibold flex-shrink-0">{i + 1}.</span>{gap}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Source footer */}
          <div className="border-t pt-3" style={{ borderColor: sev.border }}>
            <p className="text-[11px] text-gray-400 leading-relaxed">
              Assessed by: <span className="font-semibold text-gray-600">{classLabel}</span>
              {result.llmUsed && " · LLM used for type classification only"}
              <span className="block mt-0.5">Operator should verify before acting.</span>
            </p>
          </div>
        </div>
      </div>

      <p className="text-[11px] text-gray-400 text-center px-1">
        Next step: review and send dispatch notification from the incident detail view.
      </p>
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
  return (
    <div className="p-5 flex flex-col gap-4">
      <div className="bg-red-50 border border-red-200 rounded-lg p-3">
        <p className="text-xs font-semibold text-red-800 mb-1.5">What SOS does</p>
        <ul className="text-xs text-red-700 space-y-1 list-disc list-inside">
          <li>Requests your GPS coordinates from the browser</li>
          <li>Creates an incident flagged as high priority</li>
          <li>Triggers automatic severity assessment</li>
          <li>Appends an entry to the session event log</li>
        </ul>
      </div>
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
        <p className="text-xs font-semibold text-gray-500 mb-1.5">What SOS does NOT do</p>
        <ul className="text-xs text-gray-400 space-y-1 list-disc list-inside">
          <li>Does not automatically call or alert emergency services</li>
          <li>Does not transmit to any external system in real time</li>
          <li>Dispatch is a separate, manual step</li>
        </ul>
      </div>

      {status === "IDLE" && (
        <button
          onClick={onSend}
          className="w-full py-4 bg-red-600 hover:bg-red-700 active:bg-red-800 text-white font-black text-base rounded-xl tracking-widest uppercase shadow transition-colors"
        >
          Send SOS
        </button>
      )}

      {status === "BUSY" && (
        <div className="flex flex-col items-center gap-2 py-4">
          <div className="w-6 h-6 border-2 border-[#0f2044] border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-500">Acquiring GPS location…</p>
        </div>
      )}

      {status === "ERROR" && error && (
        <div className="flex flex-col gap-3">
          <div className="bg-red-50 border border-red-300 rounded-lg p-3 text-xs text-red-800">
            {error}
          </div>
          <button
            onClick={onSend}
            className="w-full py-3 bg-red-600 text-white font-bold rounded-xl text-sm"
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
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs text-gray-500">
        Voice input is not supported in this browser. Use the Text tab instead.
        <br />
        <span className="text-gray-400">Supported: Chrome / Edge on desktop and Android.</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          Voice Language
        </label>
        <div className="flex gap-2">
          {(["en-IN", "hi-IN"] as VoiceLocale[]).map((l) => (
            <button
              key={l}
              onClick={() => { if (voice.listening) voice.stop(); onLocaleChange(l); }}
              className={`flex-1 py-2 rounded-lg border text-sm font-semibold transition-colors ${
                locale === l
                  ? "bg-[#0f2044] text-white border-[#0f2044]"
                  : "bg-white text-gray-500 border-gray-200 hover:border-gray-400"
              }`}
            >
              {l === "en-IN" ? "English" : "हिंदी"}
            </button>
          ))}
        </div>
        {locale === "hi-IN" && (
          <p className="text-[11px] text-indigo-700 bg-indigo-50 rounded px-2 py-1 mt-1.5">
            हिंदी में बोलें — text will appear in देवनागरी script
          </p>
        )}
      </div>

      <div className="flex flex-col items-center gap-2">
        <button
          onClick={() => (voice.listening ? voice.stop() : voice.start(locale))}
          className={`w-16 h-16 rounded-full border-2 flex items-center justify-center transition-all ${
            voice.listening
              ? "bg-red-600 border-red-700 shadow-lg shadow-red-200 scale-105"
              : "bg-white border-gray-300 hover:border-[#0f2044] hover:shadow"
          }`}
        >
          <svg
            className={`w-7 h-7 ${voice.listening ? "text-white" : "text-gray-500"}`}
            fill="currentColor"
            viewBox="0 0 24 24"
          >
            <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm0 2a2 2 0 0 0-2 2v6a2 2 0 0 0 4 0V5a2 2 0 0 0-2-2zm7 8a1 1 0 0 1 1 1 8 8 0 0 1-7 7.938V21h2a1 1 0 0 1 0 2H9a1 1 0 0 1 0-2h2v-1.062A8 8 0 0 1 4 12a1 1 0 0 1 2 0 6 6 0 0 0 12 0 1 1 0 0 1 1-1z" />
          </svg>
        </button>
        <p className="text-xs text-gray-500">
          {voice.listening ? "Recording — tap to stop" : "Tap to start recording"}
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
    <div className="p-4 flex flex-col gap-4">
      {mode === "VOICE" && (
        <VoiceSection
          voice={voice}
          locale={locale}
          onLocaleChange={onLocaleChange}
          onTranscriptReady={handleTranscript}
        />
      )}

      {/* Incident type — auto-detect + two-tier browse */}
      <IncidentTypePicker description={description} value={selectedSubType || selectedCategory} onChange={onSubType} />

      {/* Location */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          Incident Location <span className="text-red-600">*</span>
        </label>
        {pinnedLocation ? (
          <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg p-2.5">
            <svg className="w-4 h-4 text-blue-600 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            <div className="flex-1 min-w-0">
              <p className="text-xs text-blue-900 break-words">{pinnedLabel}</p>
              <p className="text-[10px] text-blue-400 mt-0.5">
                {pinnedLocation.lat.toFixed(5)}, {pinnedLocation.lng.toFixed(5)}
              </p>
            </div>
            <button
              onClick={onRequestPin}
              className="text-[11px] text-blue-600 underline flex-shrink-0 hover:text-blue-800"
            >
              Change
            </button>
          </div>
        ) : (
          <button
            onClick={onRequestPin}
            className="w-full border-2 border-dashed border-gray-300 rounded-lg py-3 px-3 text-sm text-gray-400 hover:border-[#0f2044] hover:text-[#0f2044] transition-colors flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            Tap here, then tap map to set location
          </button>
        )}
      </div>

      {/* Description */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          Description
          {mode === "VOICE" && (
            <span className="font-normal text-gray-400"> — from transcript, editable</span>
          )}
        </label>
        <textarea
          rows={3}
          value={description}
          onChange={(e) => onDescription(e.target.value)}
          placeholder="Describe the incident — vehicles, visible injuries, road conditions…"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#0f2044]/30 resize-none"
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
          <label className="block text-xs font-semibold text-gray-600 mb-1.5">
            Vehicles Involved
          </label>
          <input
            type="number"
            min="0"
            max="999"
            value={vehiclesInvolved}
            onChange={(e) => onVehiclesInvolved(e.target.value)}
            placeholder="0"
            className="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-[#0f2044]/30"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1.5">
            Casualties / Injured
          </label>
          <input
            type="number"
            min="0"
            max="999"
            value={casualties}
            onChange={(e) => onCasualties(e.target.value)}
            placeholder="0"
            className="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-[#0f2044]/30"
          />
        </div>
      </div>

      {/* Quick flags */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-2">
          Observed Conditions
        </label>
        <div className="grid grid-cols-2 gap-2">
          {QUICK_FLAGS.map((flag) => {
            const active = selectedFlags.has(flag);
            return (
              <button
                key={flag}
                onClick={() => onToggleFlag(flag)}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-medium transition-all text-left ${
                  active
                    ? "bg-[#0f2044] border-[#0f2044] text-white"
                    : "bg-white border-gray-200 text-gray-600 hover:border-gray-400"
                }`}
              >
                <span
                  className={`w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center ${
                    active ? "bg-white border-transparent" : "border-gray-300"
                  }`}
                >
                  {active && (
                    <svg className="w-2.5 h-2.5 text-[#0f2044]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>
                {flag}
              </button>
            );
          })}
        </div>
      </div>

      {/* Submit */}
      <button
        onClick={onSubmit}
        disabled={!canSubmit}
        className={`w-full py-3 rounded-lg text-sm font-bold transition-colors ${
          canSubmit
            ? "bg-[#0f2044] text-white hover:bg-[#1a3567]"
            : "bg-gray-100 text-gray-400 cursor-not-allowed"
        }`}
      >
        {canSubmit ? "Submit & Assess Severity" : "Set location to submit"}
      </button>
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
  return (
    <div className="p-4 flex flex-col gap-4">
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
        <p className="text-xs font-semibold text-amber-800 mb-1">Reporting a road defect</p>
        <p className="text-xs text-amber-700">
          Pin the location, describe the defect, and select severity. The pothole will appear on the Accidents tab.
        </p>
      </div>

      {/* Location */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          Location <span className="text-red-600">*</span>
        </label>
        {pinnedLocation ? (
          <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg p-2.5">
            <svg className="w-4 h-4 text-blue-600 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            <div className="flex-1 min-w-0">
              <p className="text-xs text-blue-900 break-words">{pinnedLabel}</p>
              <p className="text-[10px] text-blue-400 mt-0.5">
                {pinnedLocation.lat.toFixed(5)}, {pinnedLocation.lng.toFixed(5)}
              </p>
            </div>
            <button
              onClick={onRequestPin}
              className="text-[11px] text-blue-600 underline flex-shrink-0 hover:text-blue-800"
            >
              Change
            </button>
          </div>
        ) : (
          <button
            onClick={onRequestPin}
            className="w-full border-2 border-dashed border-gray-300 rounded-lg py-3 px-3 text-sm text-gray-400 hover:border-amber-500 hover:text-amber-600 transition-colors flex items-center justify-center gap-2"
          >
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            Tap here, then tap map to set location
          </button>
        )}
      </div>

      {/* Description */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">Description</label>
        <textarea
          rows={3}
          value={description}
          onChange={(e) => onDescription(e.target.value)}
          placeholder="Describe the defect — size, depth, road name, near landmark…"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-amber-500/30 resize-none"
        />
      </div>

      {/* Severity */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-2">Severity</label>
        <div className="flex gap-2">
          {(["LOW", "MEDIUM", "HIGH"] as const).map((s) => (
            <button
              key={s}
              onClick={() => onSeverity(s)}
              className={`flex-1 py-2 rounded-lg border text-xs font-bold transition-all ${
                severity === s
                  ? s === "HIGH"
                    ? "bg-red-600 border-red-700 text-white"
                    : s === "MEDIUM"
                    ? "bg-amber-500 border-amber-600 text-white"
                    : "bg-gray-500 border-gray-600 text-white"
                  : "bg-white border-gray-200 text-gray-500 hover:border-gray-400"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Submit */}
      <button
        onClick={onSubmit}
        disabled={!canSubmit}
        className={`w-full py-3 rounded-lg text-sm font-bold transition-colors ${
          canSubmit
            ? "bg-amber-600 text-white hover:bg-amber-700"
            : "bg-gray-100 text-gray-400 cursor-not-allowed"
        }`}
      >
        {canSubmit ? "Report Pothole" : "Set location to submit"}
      </button>
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
}

type ReportMode = "SOS" | "TEXT" | "VOICE" | "DISPATCHER" | "POTHOLE";
type PanelStatus = "IDLE" | "BUSY" | "ASSESSING" | "MATCHING" | "COMPLETE" | "ERROR" | "POTHOLE_DONE";

export default function ReportPanel({
  open,
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
  onClose,
  onPotholeSubmitted,
  onAccidentSubmitted,
}: ReportPanelProps) {
  const [mode, setMode] = useState<ReportMode>("SOS");
  const [panelStatus, setPanelStatus] = useState<PanelStatus>("IDLE");
  const [sosError, setSosError] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [vehiclesInvolved, setVehiclesInvolved] = useState("");
  const [casualties, setCasualties] = useState("");
  const [selectedFlags, setSelectedFlags] = useState<Set<string>>(new Set());
  const [selectedSubType, setSelectedSubType] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const appLocale = useLocaleStore((s) => s.locale);
  const [locale, setLocale] = useState<VoiceLocale>(appLocale === "HI" ? "hi-IN" : "en-IN");
  const [createdIncident, setCreatedIncident] = useState<AccidentReport | null>(null);
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResult | null>(null);
  const [dupMatch, setDupMatch] = useState<DuplicateMatch | null>(null);
  const [pendingIncident, setPendingIncident] = useState<AccidentReport | null>(null);
  const [dispatcherLocation, setDispatcherLocation] = useState<{ point: GeoPoint; label: string } | null>(null);

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

  const appendReport = useEventLog((s) => s.appendReport);
  const appendAssessment = useEventLog((s) => s.appendAssessment);
  const appendDuplicateFlagged = useEventLog((s) => s.appendDuplicateFlagged);
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
  useEffect(() => {
    if (!createdIncident || createdIncident.reportMode !== "DISPATCHER") return;
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
      sendDispatchBriefing(services);
    }, 2500);
    return () => clearTimeout(t);
  }, [entries, createdIncident, sendDispatchBriefing]);

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
    } finally {
      setPanelStatus("MATCHING");
    }
  }

  function proceedWithIncident(incident: AccidentReport) {
    appendReport(incident);
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
        className="fixed inset-0 z-[1999] bg-black/30 backdrop-blur-[2px]"
        onClick={isBusy ? undefined : onClose}
      />

      {/* Sheet */}
      <div
        className="fixed bottom-0 left-0 right-0 z-[2000] flex flex-col bg-white rounded-t-2xl shadow-2xl"
        style={{ maxHeight: "80vh" }}
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
        {/* Handle */}
        <div className="flex justify-center pt-2.5 pb-1 flex-shrink-0">
          <div className="w-10 h-1 bg-gray-200 rounded-full" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-4 pb-2 flex-shrink-0">
          <p className="text-sm font-bold text-gray-900">Report Incident</p>
          {!isBusy && (
            <button
              onClick={onClose}
              aria-label="Close report panel"
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400"
            >
              <svg className="w-4 h-4" aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Mode tabs — hidden while processing */}
        {panelStatus === "IDLE" || panelStatus === "ERROR" ? (
          <div className="flex border-b border-gray-100 mx-1 flex-shrink-0">
            {(["SOS", "TEXT", "VOICE", "DISPATCHER", "POTHOLE"] as ReportMode[]).map((m) => (
              <button
                key={m}
                onClick={() => switchMode(m)}
                className={`flex-1 py-2.5 text-xs font-bold tracking-widest uppercase border-b-2 transition-colors ${
                  mode === m
                    ? m === "SOS"
                      ? "border-red-600 text-red-700 bg-red-50/50"
                      : m === "POTHOLE"
                      ? "border-amber-600 text-amber-700 bg-amber-50/50"
                      : "border-[#0f2044] text-[#0f2044]"
                    : "border-transparent text-gray-400 hover:text-gray-600"
                }`}
              >
                <span className="flex flex-col items-center gap-1">
                  {m === "SOS" && (
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="8" x2="12" y2="12" />
                      <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                  )}
                  {m === "TEXT" && (
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                  )}
                  {m === "VOICE" && (
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                      <line x1="12" y1="19" x2="12" y2="23" />
                      <line x1="8" y1="23" x2="16" y2="23" />
                    </svg>
                  )}
                  {m === "DISPATCHER" && (
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15a2 2 0 0 1-2 2H8l-4 3v-3H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                      <line x1="7" y1="8.5" x2="17" y2="8.5" />
                      <line x1="7" y1="12" x2="13" y2="12" />
                    </svg>
                  )}
                  {m === "POTHOLE" && (
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 20h20" />
                      <path d="M2 4h20" />
                      <path d="M7 20v-4" />
                      <path d="M17 20v-4" />
                      <ellipse cx="12" cy="16" rx="4" ry="2" />
                      <path d="M10 16l1-4 2 2 1-4" />
                    </svg>
                  )}
                  <span>
                    {m === "POTHOLE" ? "Pothole"
                      : m === "DISPATCHER" ? "Voice"
                      : m === "VOICE" ? "Speech-to-Text"
                      : m === "TEXT" ? "Text" : "SOS"}
                  </span>
                </span>
              </button>
            ))}
          </div>
        ) : null}

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
          {panelStatus === "ASSESSING" && createdIncident ? (
            <AssessingView incidentId={createdIncident.id} />
          ) : panelStatus === "MATCHING" && createdIncident && !assessmentResult ? (
            <div className="flex flex-col items-center gap-3 p-8">
              <div className="w-8 h-8 border-[3px] border-[#0f2044] border-t-transparent rounded-full animate-spin" />
              <p className="text-sm text-gray-500">Matching nearest services…</p>
            </div>
          ) : (panelStatus === "MATCHING" || panelStatus === "COMPLETE") &&
            createdIncident &&
            assessmentResult ? (
            <div className="p-4 flex flex-col gap-5">
              {/* Assessment result — always shown */}
              <AssessmentCard result={assessmentResult} incidentId={createdIncident.id} />

              {/* Divider */}
              <div className="flex items-center gap-3">
                <div className="flex-1 h-px bg-gray-200" />
                <span className="text-[10px] font-black tracking-widest text-gray-400 uppercase">
                  Nearest Services
                </span>
                <div className="flex-1 h-px bg-gray-200" />
              </div>

              {/* Matching + routing — MatchingPanel fetches Places + Routes API internally */}
              <MatchingPanel
                hospitals={HOSPITALS}
                policeStations={POLICE_STATIONS}
                ambulanceStations={AMBULANCE_STATIONS}
                fireStations={FIRE_STATIONS}
                towingStations={TOWING_STATIONS}
                incident={createdIncident}
                assessment={assessmentResult}
                onReady={() => setPanelStatus("COMPLETE")}
              />

              <button
                onClick={onClose}
                className="w-full bg-[#0f2044] text-white py-3 rounded-lg text-sm font-bold hover:bg-[#1a3567] transition-colors"
              >
                Close
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
            <div className="p-4">
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
