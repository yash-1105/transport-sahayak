"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useVoiceInput, type VoiceLocale } from "@/hooks/useVoiceInput";
import { useEventLog } from "@/store/eventLog";
import { reverseGeocode } from "@/lib/geocode";
import { heuristicAssess } from "@/lib/heuristic";
import { checkDuplicate, type DuplicateMatch } from "@/lib/dedup";
import MatchingPanel from "@/components/report/MatchingPanel";
import { useRoutingStore } from "@/store/routingStore";
import hospitalsRaw from "../../../data/hospitals.json";
import policeRaw from "../../../data/police-stations.json";
import type {
  AccidentReport,
  AssessmentResult,
  AssessmentSeverity,
  AssessmentPriority,
  GeoPoint,
  Hospital,
  PoliceStation,
} from "@/lib/types";

const HOSPITALS = hospitalsRaw.hospitals as unknown as Hospital[];
const POLICE_STATIONS = policeRaw.policeStations as unknown as PoliceStation[];

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

const QUICK_FLAGS = ["Conscious", "Breathing", "Trapped", "Heavy bleeding"] as const;

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

  const FIRE    = ["fire", "burning", "flames", "caught fire", "fuel leak", "ignit", "explod"];
  const INJURY  = ["injur", "bleed", "blood", "unconscious", "unresponsive", "fracture",
                   "hurt", "casualt", "critical", "serious", "victim", "fatal", "dead",
                   "wounded", "person down", "people hurt", "someone hurt"];
  const CRASH   = ["crash", "collision", "accident", "overturned", "rollover", "head-on",
                   "rear-end", "hit", "struck", "smash", "vehicle crash", "car crash",
                   "bike accident", "truck", "lorry", "bus hit", "tempo", "auto rickshaw"];
  const MECH    = ["breakdown", "broken down", "flat tyre", "flat tire", "tyre burst",
                   "tire burst", "puncture", "engine fail", "engine stop", "stalled",
                   "car stopped", "vehicle stopped", "won't start", "wont start",
                   "mechanical", "brake fail", "oil leak", "overheating", "tow"];
  const HAZARD  = ["pothole", "debris", "fallen tree", "tree fallen", "rock fall",
                   "obstacle on road", "road blocked", "flooded road", "oil spill",
                   "road damage", "landslide", "mud on road"];

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

// ── Severity visual config ────────────────────────────────────────────────────

const SEV: Record<
  AssessmentSeverity,
  { label: string; bg: string; text: string; border: string; track: string }
> = {
  1: { label: "Minor",    bg: "#f0fdf4", text: "#15803d", border: "#86efac", track: "#22c55e" },
  2: { label: "Low",      bg: "#f7fee7", text: "#4d7c0f", border: "#bef264", track: "#84cc16" },
  3: { label: "Moderate", bg: "#fffbeb", text: "#b45309", border: "#fcd34d", track: "#f59e0b" },
  4: { label: "Serious",  bg: "#fff7ed", text: "#c2410c", border: "#fdba74", track: "#f97316" },
  5: { label: "Critical", bg: "#fef2f2", text: "#b91c1c", border: "#fca5a5", track: "#ef4444" },
};

const PRI: Record<AssessmentPriority, { label: string; bg: string; text: string }> = {
  low:      { label: "LOW PRIORITY",      bg: "#f3f4f6", text: "#6b7280" },
  medium:   { label: "MEDIUM PRIORITY",   bg: "#dbeafe", text: "#1d4ed8" },
  high:     { label: "HIGH PRIORITY",     bg: "#ffedd5", text: "#c2410c" },
  critical: { label: "CRITICAL PRIORITY", bg: "#fee2e2", text: "#b91c1c" },
};

// ── Assessment result card (the centrepiece) ──────────────────────────────────

function AssessmentCard({
  result,
  incidentId,
}: {
  result: AssessmentResult;
  incidentId: string;
}) {
  const sev = SEV[result.severity as AssessmentSeverity];
  const pri = PRI[result.priority as AssessmentPriority];
  const isAI = result.source === "AI";

  return (
    <div className="flex flex-col gap-4">
      {/* Incident created confirmation — compact */}
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

      {/* ── Assessment card ── */}
      <div
        className="rounded-xl border-2 overflow-hidden"
        style={{ borderColor: sev.border, background: sev.bg }}
      >
        {/* Card header */}
        <div
          className="px-4 py-2 flex items-center justify-between"
          style={{ background: sev.track }}
        >
          <p className="text-[11px] font-black tracking-widest text-white uppercase">
            Severity Assessment
          </p>
          <p className="text-[11px] text-white/80 font-medium">
            {isAI ? "claude-sonnet-4-6" : "Heuristic"}
          </p>
        </div>

        {/* Severity number + label */}
        <div className="pt-5 pb-4 flex flex-col items-center gap-2">
          {/* Big numbered circle */}
          <div
            className="w-24 h-24 rounded-full border-4 flex items-center justify-center"
            style={{
              borderColor: sev.border,
              background: "#fff",
            }}
          >
            <span
              className="text-6xl font-black leading-none tabular-nums"
              style={{ color: sev.text }}
            >
              {result.severity}
            </span>
          </div>

          {/* Severity label */}
          <p
            className="text-xl font-black tracking-wide uppercase"
            style={{ color: sev.text }}
          >
            {sev.label}
          </p>

          {/* Progress track — 5 segments */}
          <div className="flex gap-1 mt-1">
            {([1, 2, 3, 4, 5] as AssessmentSeverity[]).map((n) => (
              <div
                key={n}
                className="w-8 h-2 rounded-full transition-all"
                style={{
                  background: n <= result.severity ? SEV[n].track : "#e5e7eb",
                }}
              />
            ))}
          </div>

          {/* Priority badge */}
          <span
            className="mt-1 text-[11px] font-black tracking-widest px-3 py-1 rounded-full"
            style={{ background: pri.bg, color: pri.text }}
          >
            {pri.label}
          </span>
        </div>

        {/* Rationale + recommended response */}
        <div className="px-4 pb-4 flex flex-col gap-3">
          <div>
            <p
              className="text-[10px] font-black tracking-widest uppercase mb-1"
              style={{ color: sev.text }}
            >
              Rationale
            </p>
            <p className="text-sm text-gray-800 leading-relaxed">{result.rationale}</p>
          </div>

          <div
            className="border-t pt-3"
            style={{ borderColor: sev.border }}
          >
            <p
              className="text-[10px] font-black tracking-widest uppercase mb-1"
              style={{ color: sev.text }}
            >
              Recommended Response
            </p>
            <p className="text-sm text-gray-800 leading-relaxed">
              {result.recommendedResponse}
            </p>
          </div>

          {/* Source tag */}
          <div
            className="flex items-start gap-2 border-t pt-3"
            style={{ borderColor: sev.border }}
          >
            {isAI ? (
              <>
                <span className="text-base leading-none mt-0.5">🤖</span>
                <div>
                  <p className="text-xs font-semibold text-gray-700">AI assessment</p>
                  <p className="text-[11px] text-gray-400">Model: claude-sonnet-4-6 · Operator should verify before acting</p>
                </div>
              </>
            ) : (
              <>
                <span className="text-base leading-none mt-0.5">⚙️</span>
                <div>
                  <p className="text-xs font-semibold text-amber-700">Heuristic fallback</p>
                  <p className="text-[11px] text-amber-600">
                    {result.fallbackReason === "no API key"
                      ? "No API key configured — rule-based scoring used"
                      : `API call failed (${result.fallbackReason ?? "unknown"}) — rule-based scoring used`}
                  </p>
                  <p className="text-[11px] text-gray-400 mt-0.5">Operator should verify before acting</p>
                </div>
              </>
            )}
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
        Contacting AI assessment service.<br />
        A heuristic score is ready as fallback.
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
  useEffect(() => {
    onTranscriptReady(voice.transcript);
  }, [voice.transcript, onTranscriptReady]);

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
        <label className="block text-xs font-semibold text-gray-600 mb-1">
          Recognition Language
        </label>
        <select
          value={locale}
          onChange={(e) => {
            if (voice.listening) voice.stop();
            onLocaleChange(e.target.value as VoiceLocale);
          }}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-[#0f2044]/30"
        >
          <option value="en-IN">English — India (en-IN)</option>
          <option value="hi-IN">हिंदी — Hindi (hi-IN)</option>
          <option value="as-IN">অসমীয়া — Assamese (as-IN) ⚠ Experimental</option>
        </select>
        {locale === "as-IN" && (
          <p className="mt-1.5 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2.5 py-1.5">
            Experimental — browser support for as-IN is limited. Production deployments should
            integrate Bhashini or Google Cloud Speech-to-Text.
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

      {(voice.transcript || voice.interimTranscript || voice.listening) && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-2.5 text-xs min-h-[56px]">
          <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase mb-1">
            Live Transcript
          </p>
          <span className="text-gray-800">{voice.transcript}</span>
          {voice.interimTranscript && (
            <span className="text-gray-400 italic"> {voice.interimTranscript}</span>
          )}
          {voice.listening && !voice.transcript && !voice.interimTranscript && (
            <span className="text-gray-400 italic">Listening…</span>
          )}
        </div>
      )}

      {voice.transcript && (
        <button onClick={voice.clearTranscript} className="text-xs text-gray-400 underline self-start">
          Clear transcript
        </button>
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
  description: string;
  onDescription: (v: string) => void;
  victims: string;
  onVictims: (v: string) => void;
  selectedFlags: Set<string>;
  onToggleFlag: (f: string) => void;
  onSubmit: () => void;
  canSubmit: boolean;
}

function FormView({
  mode, voice, locale, onLocaleChange,
  pinnedLocation, pinnedLabel, onRequestPin,
  description, onDescription, victims, onVictims,
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

      {/* Persons involved */}
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">
          Estimated Persons Involved
        </label>
        <input
          type="number"
          min="0"
          max="999"
          value={victims}
          onChange={(e) => onVictims(e.target.value)}
          placeholder="0"
          className="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-[#0f2044]/30"
        />
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

// ── Main panel ────────────────────────────────────────────────────────────────

export interface ReportPanelProps {
  open: boolean;
  pinnedLocation: GeoPoint | null;
  pinnedLabel: string;
  onRequestPin: () => void;
  onClose: () => void;
}

type ReportMode = "SOS" | "TEXT" | "VOICE";
type PanelStatus = "IDLE" | "BUSY" | "ASSESSING" | "MATCHING" | "COMPLETE" | "ERROR";

export default function ReportPanel({
  open,
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
  onClose,
}: ReportPanelProps) {
  const [mode, setMode] = useState<ReportMode>("SOS");
  const [panelStatus, setPanelStatus] = useState<PanelStatus>("IDLE");
  const [sosError, setSosError] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [victims, setVictims] = useState("");
  const [selectedFlags, setSelectedFlags] = useState<Set<string>>(new Set());
  const [locale, setLocale] = useState<VoiceLocale>("en-IN");
  const [createdIncident, setCreatedIncident] = useState<AccidentReport | null>(null);
  const [assessmentResult, setAssessmentResult] = useState<AssessmentResult | null>(null);
  const [dupMatch, setDupMatch] = useState<DuplicateMatch | null>(null);
  const [pendingIncident, setPendingIncident] = useState<AccidentReport | null>(null);

  const voice = useVoiceInput();
  const appendReport = useEventLog((s) => s.appendReport);
  const appendAssessment = useEventLog((s) => s.appendAssessment);
  const appendDuplicateFlagged = useEventLog((s) => s.appendDuplicateFlagged);
  const entries = useEventLog((s) => s.entries);
  const clearRoutes = useRoutingStore((s) => s.clearRoutes);

  function resetForm() {
    setPanelStatus("IDLE");
    setSosError(null);
    setDescription("");
    setVictims("");
    setSelectedFlags(new Set());
    setCreatedIncident(null);
    setAssessmentResult(null);
    setDupMatch(null);
    setPendingIncident(null);
    clearRoutes();
    voice.clearTranscript();
  }

  function switchMode(m: ReportMode) {
    if (voice.listening) voice.stop();
    setMode(m);
    resetForm();
  }

  async function runAssessment(incident: AccidentReport) {
    setPanelStatus("ASSESSING");
    try {
      const res = await fetch("/api/assess", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(incident),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result: AssessmentResult = await res.json();
      setAssessmentResult(result);
      appendAssessment(incident.id, result);
    } catch {
      // Network is down — run heuristic client-side
      const result = heuristicAssess(incident);
      result.fallbackReason = "network error — assessed client-side";
      setAssessmentResult(result);
      appendAssessment(incident.id, result);
    } finally {
      setPanelStatus("MATCHING");
    }
  }

  function proceedWithIncident(incident: AccidentReport) {
    appendReport(incident);
    setCreatedIncident(incident);
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
      vehiclesInvolved: victims ? Number(victims) : null,
      estimatedCasualties: null,
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
            {(["SOS", "TEXT", "VOICE"] as ReportMode[]).map((m) => (
              <button
                key={m}
                onClick={() => switchMode(m)}
                className={`flex-1 py-2.5 text-xs font-bold tracking-widest uppercase border-b-2 transition-colors ${
                  mode === m
                    ? m === "SOS"
                      ? "border-red-600 text-red-700 bg-red-50/50"
                      : "border-[#0f2044] text-[#0f2044]"
                    : "border-transparent text-gray-400 hover:text-gray-600"
                }`}
              >
                {m === "SOS" ? "🚨 SOS" : m === "TEXT" ? "📝 Text" : "🎙 Voice"}
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
          ) : (
            <FormView
              mode={mode as "TEXT" | "VOICE"}
              voice={voice}
              locale={locale}
              onLocaleChange={setLocale}
              pinnedLocation={pinnedLocation}
              pinnedLabel={pinnedLabel}
              onRequestPin={onRequestPin}
              description={description}
              onDescription={setDescription}
              victims={victims}
              onVictims={setVictims}
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
