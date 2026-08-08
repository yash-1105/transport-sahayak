"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoPoint } from "@/lib/types";
import type { VoiceLocale } from "@/hooks/useVoiceInput";
import type { useVoiceDispatcher } from "@/hooks/useVoiceDispatcher";
import type { InterimDispatch } from "@/components/report/ReportPanel";
import { C, RADIUS } from "@/lib/design";
import { MicIcon } from "@/components/ui/icons";
import { useBilingual } from "@/hooks/useI18n";

// Conversational AI dispatcher tab (Gemini Live via Vertex AI). Separate,
// new file from VoiceSection (the existing Chirp speech-to-text tab) — no
// shared state, no shared component. Receives the useVoiceDispatcher() hook
// result plus the shared form values as props (same callback-into-shared-
// state convention VoiceSection already uses), so this component's only job
// is to render the call UI and a live-mirrored summary of what's been
// collected so far; ReportPanel.tsx owns all the actual form state.

// #4 (staged dispatch): bilingual chip labels per interim service key.
const INTERIM_LABEL: Record<string, { en: string; hi: string }> = {
  ambulance: { en: "Ambulance", hi: "एम्बुलेंस" },
  fire: { en: "Fire service", hi: "दमकल" },
  towing: { en: "Towing", hi: "टोइंग" },
};

const STATUS_LABEL: Record<string, string> = {
  idle: "Tap to start a conversation",
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
  briefing: "Emergency briefing…",
  reconnecting: "Reconnecting…",
  error: "Something went wrong",
  ended: "Conversation ended",
};

const STATUS_RING: Record<string, string> = {
  idle: "bg-white border-gray-300",
  connecting: "bg-gray-100 border-gray-400",
  listening: "bg-emerald-600 border-emerald-700 shadow-lg shadow-emerald-200",
  thinking: "bg-amber-500 border-amber-600 shadow-lg shadow-amber-200",
  speaking: "bg-[#0f2044] border-[#0f2044] shadow-lg shadow-blue-200",
  briefing: "bg-[#0f2044] border-[#0f2044] shadow-lg shadow-blue-200",
  reconnecting: "bg-amber-500 border-amber-600",
  error: "bg-red-600 border-red-700",
  ended: "bg-white border-gray-300",
};

function WaveformBars({ active }: { active: boolean }) {
  return (
    <div className="flex items-end gap-0.5 h-4">
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className={`w-0.5 rounded-full bg-white ${active ? "animate-pulse" : ""}`}
          style={{
            height: active ? `${6 + (i % 3) * 4}px` : "4px",
            animationDelay: `${i * 100}ms`,
            animationDuration: "700ms",
          }}
        />
      ))}
    </div>
  );
}

function useHighlight(value: unknown): boolean {
  const [flash, setFlash] = useState(false);
  const prev = useRef(value);
  useEffect(() => {
    if (prev.current !== value) {
      prev.current = value;
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 1200);
      return () => clearTimeout(t);
    }
  }, [value]);
  return flash;
}

function SummaryRow({ label, value }: { label: string; value: string | null }) {
  const flash = useHighlight(value);
  return (
    <div
      className={`flex items-baseline justify-between gap-2 px-2 py-1 rounded transition-colors duration-500 ${
        flash ? "bg-emerald-50" : ""
      }`}
    >
      <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide shrink-0">{label}</span>
      <span className={`text-xs text-right ${value ? "text-gray-800" : "text-gray-300 italic"}`}>
        {value || "—"}
      </span>
    </div>
  );
}

export interface DispatcherSectionProps {
  dispatcher: ReturnType<typeof useVoiceDispatcher>;
  locale: VoiceLocale;
  onLocaleChange: (l: VoiceLocale) => void;
  selectedSubType: string;
  selectedCategory: string;
  description: string;
  vehiclesInvolved: string;
  casualties: string;
  selectedFlags: Set<string>;
  interimDispatches: InterimDispatch[];
  dispatcherLocation: { point: GeoPoint; label: string } | null;
  pinnedLocation: GeoPoint | null;
  pinnedLabel: string;
  onRequestPin: () => void;
}

export function DispatcherSection({
  dispatcher,
  locale,
  onLocaleChange,
  selectedSubType,
  description,
  vehiclesInvolved,
  casualties,
  selectedFlags,
  interimDispatches,
  dispatcherLocation,
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
}: DispatcherSectionProps) {
  const isActive = !["idle", "ended", "error"].includes(dispatcher.status);
  const locationLabel = dispatcherLocation?.label || (pinnedLocation ? pinnedLabel : null);
  const flagsLabel = selectedFlags.size ? Array.from(selectedFlags).join(", ") : null;
  const { showHindi } = useBilingual();

  if (!dispatcher.supported) {
    return (
      <div style={{ background: C.inset, border: `1px solid ${C.border}`, borderRadius: RADIUS.input, padding: 12, fontSize: 12.5, color: C.secondary }}>
        The voice dispatcher is not supported in this browser. Use the Text tab instead.
        <br />
        <span style={{ color: C.muted }}>Supported: Chrome / Edge on desktop and Android.</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3.5">
      <div>
        <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6, color: C.ink }}>
          Conversation language{showHindi && <span style={{ fontWeight: 400, color: C.muted }}> · भाषा</span>}
        </div>
        <div className="flex" style={{ gap: 6, background: C.page, borderRadius: RADIUS.input, padding: 4 }}>
          {(["en-IN", "hi-IN"] as VoiceLocale[]).map((l) => {
            const on = locale === l;
            return (
              <button
                key={l}
                // English (dispatcher_live.py / Gemini Live) and Hindi
                // (dispatcher_hindi.py) are entirely separate backend pipelines, so
                // a language switch cleanly TEARS DOWN the current session and starts
                // the other — never a mid-session hot-swap or a half-open socket.
                // stop() drains + closes the current WS (detaches handlers, ws.close);
                // start(l) then opens a fresh WS on the other locale.
                onClick={() => {
                  if (l === locale) return;            // already on this language
                  const wasActive = isActive;
                  if (wasActive) dispatcher.stop();     // clean teardown of the current pipeline
                  onLocaleChange(l);
                  if (wasActive) dispatcher.start(l);   // reopen on the OTHER pipeline, auto-connect
                }}
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
                {l === "en-IN" ? "English" : "हिंदी"}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col items-center" style={{ gap: 10, padding: "22px 0" }}>
        {isActive ? (
          <button
            onClick={() => dispatcher.stop()}
            className={`w-20 h-20 rounded-full border-2 flex items-center justify-center transition-all ${STATUS_RING[dispatcher.status] ?? STATUS_RING.idle}`}
          >
            {dispatcher.status === "speaking" || dispatcher.status === "briefing" ? (
              <WaveformBars active />
            ) : (
              <svg className="w-8 h-8 text-white" fill="currentColor" viewBox="0 0 24 24">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            )}
          </button>
        ) : (
          <button
            onClick={() => dispatcher.start(locale)}
            className="flex items-center justify-center"
            style={{ width: 84, height: 84, borderRadius: "50%", border: "none", background: "linear-gradient(135deg,#2456A6,#173B77)", color: "#fff", cursor: "pointer", boxShadow: "0 8px 24px rgba(36,86,166,.35)" }}
          >
            <MicIcon size={32} />
          </button>
        )}
        <div style={{ fontSize: 13.5, fontWeight: 600, color: C.ink }}>
          {isActive ? STATUS_LABEL[dispatcher.status] : "Start conversation"}
          {!isActive && showHindi && <span style={{ fontWeight: 500, color: C.muted }}> · बातचीत शुरू करें</span>}
        </div>
        {!isActive && (
          <div style={{ fontSize: 12, color: C.muted, maxWidth: 420, textAlign: "center", lineHeight: 1.5 }}>
            A guided voice assistant asks about location, vehicles and injuries, then files the report for you.
          </div>
        )}
        {isActive && dispatcher.status === "listening" && (
          <span className="flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-2 w-2 rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
        )}
      </div>

      {dispatcher.offline && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2.5">
          You appear to be offline. The conversation will resume once your connection is back.
        </p>
      )}

      {dispatcher.error && (
        <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5">
          {dispatcher.error}
        </p>
      )}

      {/* Fallback only: shown when speech synthesis failed server-side and
          the agent's reply arrived as text instead of audio (Hindi path). */}
      {dispatcher.agentText && (
        <p className="text-xs text-gray-700 bg-gray-50 border border-gray-200 rounded-lg p-2.5">
          {dispatcher.agentText}
        </p>
      )}

      {/* #4 (staged dispatch): responders notified early, mid-call. Each is a
          notification record only — "being arranged", never a tracked vehicle
          or a fabricated ETA (the real ETA is read at the close of the call). */}
      {interimDispatches.length > 0 && (
        <div style={{ background: "#FEF6EC", border: `1px solid ${C.saffronSoftBorder}`, borderRadius: RADIUS.input, padding: "10px 12px" }}>
          <p className="uppercase" style={{ fontSize: 10, fontWeight: 800, letterSpacing: ".08em", color: "#8A5A17", marginBottom: 6 }}>
            Help being arranged{showHindi && <span style={{ fontWeight: 600 }}> · मदद भेजी जा रही है</span>}
          </p>
          <div className="flex flex-col" style={{ gap: 5 }}>
            {interimDispatches.map((d) => {
              const m = INTERIM_LABEL[d.service] ?? { en: d.service, hi: d.service };
              return (
                <div key={d.service} className="flex items-center" style={{ gap: 7, fontSize: 12 }}>
                  <span className="flex h-2 w-2 flex-none">
                    <span className="animate-ping absolute inline-flex h-2 w-2 rounded-full opacity-60" style={{ background: C.saffron }} />
                    <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: C.saffron }} />
                  </span>
                  <span style={{ color: C.ink, fontWeight: 600 }}>
                    {m.en} notified{showHindi && <span style={{ fontWeight: 500, color: C.muted }}> · {m.hi} सूचित</span>}
                  </span>
                  {d.name && (
                    <span className="truncate" style={{ color: C.secondary }}>
                      — {d.name}{d.distanceKm != null && ` (~${d.distanceKm} km)`}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <p style={{ fontSize: 10.5, color: C.muted, marginTop: 6, lineHeight: 1.4 }}>
            Notification records only — being arranged now. Final estimated arrival times are confirmed at the end of the call. We do not track vehicles.
          </p>
        </div>
      )}

      {(isActive || selectedSubType || description) && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg divide-y divide-gray-100">
          <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase px-2 pt-2 pb-1">
            Report — filling live
          </p>
          <SummaryRow label="Incident Type" value={selectedSubType || null} />
          <SummaryRow label="Description" value={description || null} />
          <SummaryRow label="Vehicles" value={vehiclesInvolved || null} />
          <SummaryRow label="Casualties" value={casualties || null} />
          <SummaryRow label="Conditions" value={flagsLabel} />
          <SummaryRow label="Location" value={locationLabel} />
        </div>
      )}

      {!dispatcherLocation && !pinnedLocation && (
        <button onClick={onRequestPin} className="self-center" style={{ fontSize: 12.5, color: C.blue }}>
          Or set location manually on the map
        </button>
      )}
    </div>
  );
}
