"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoPoint } from "@/lib/types";
import type { VoiceLocale } from "@/hooks/useVoiceInput";
import type { useVoiceDispatcher } from "@/hooks/useVoiceDispatcher";

// Conversational AI dispatcher tab (Gemini Live via Vertex AI). Separate,
// new file from VoiceSection (the existing Chirp speech-to-text tab) — no
// shared state, no shared component. Receives the useVoiceDispatcher() hook
// result plus the shared form values as props (same callback-into-shared-
// state convention VoiceSection already uses), so this component's only job
// is to render the call UI and a live-mirrored summary of what's been
// collected so far; ReportPanel.tsx owns all the actual form state.

const STATUS_LABEL: Record<string, string> = {
  idle: "Tap to start a conversation",
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
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
  dispatcherLocation,
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
}: DispatcherSectionProps) {
  const isActive = !["idle", "ended", "error"].includes(dispatcher.status);
  const locationLabel = dispatcherLocation?.label || (pinnedLocation ? pinnedLabel : null);
  const flagsLabel = selectedFlags.size ? Array.from(selectedFlags).join(", ") : null;

  if (!dispatcher.supported) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs text-gray-500">
        The voice dispatcher is not supported in this browser. Use the Text tab instead.
        <br />
        <span className="text-gray-400">Supported: Chrome / Edge on desktop and Android.</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="block text-xs font-semibold text-gray-600 mb-1.5">Conversation Language</label>
        <div className="flex gap-2">
          {(["en-IN", "hi-IN"] as VoiceLocale[]).map((l) => (
            <button
              key={l}
              onClick={() => { if (isActive) dispatcher.stop(); onLocaleChange(l); }}
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
      </div>

      <div className="flex flex-col items-center gap-2 py-1">
        <button
          onClick={() => (isActive ? dispatcher.stop() : dispatcher.start(locale))}
          className={`w-20 h-20 rounded-full border-2 flex items-center justify-center transition-all ${STATUS_RING[dispatcher.status] ?? STATUS_RING.idle}`}
        >
          {dispatcher.status === "speaking" ? (
            <WaveformBars active />
          ) : (
            <svg
              className={`w-8 h-8 ${isActive ? "text-white" : "text-gray-500"}`}
              fill="currentColor"
              viewBox="0 0 24 24"
            >
              {isActive ? (
                <rect x="6" y="6" width="12" height="12" rx="2" />
              ) : (
                <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4zm0 2a2 2 0 0 0-2 2v6a2 2 0 0 0 4 0V5a2 2 0 0 0-2-2zm7 8a1 1 0 0 1 1 1 8 8 0 0 1-7 7.938V21h2a1 1 0 0 1 0 2H9a1 1 0 0 1 0-2h2v-1.062A8 8 0 0 1 4 12a1 1 0 0 1 2 0 6 6 0 0 0 12 0 1 1 0 0 1 1-1z" />
              )}
            </svg>
          )}
        </button>
        <p className="text-xs font-medium text-gray-600">
          {isActive ? STATUS_LABEL[dispatcher.status] : "Start Conversation"}
        </p>
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
        <button onClick={onRequestPin} className="text-xs text-[#0f2044] underline self-start">
          Or set location manually on the map
        </button>
      )}
    </div>
  );
}
