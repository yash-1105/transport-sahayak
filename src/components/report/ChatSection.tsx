"use client";
import React, { useEffect, useRef, useState } from "react";
import { C, RADIUS } from "@/lib/design";
import type { UseTextChat } from "@/hooks/useTextChat";
import type { GeoPoint } from "@/lib/types";

// AI TEXT-CHAT channel UI — a message-bubble transcript + a text input, mirroring
// DispatcherSection's state machine but TEXT-ONLY (no mic, no audio, no
// playback). English only. Shows the incident form filling live as the bot
// extracts fields, exactly like the voice panel. Receives the useTextChat() hook
// from ReportPanel (which wires the SAME assess → MatchingPanel → dispatch_update
// flow the voice dispatcher uses).

const STATUS_LABEL: Record<string, string> = {
  idle: "Start chat",
  connecting: "Connecting…",
  thinking: "The operator is typing…",
  waiting: "Your turn",
  submitted: "Report submitted — checking responding services…",
  complete: "Chat complete",
  error: "Connection problem",
  offline: "Chat unavailable",
};

function SummaryRow({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="flex items-start justify-between gap-3 px-2 py-1.5">
      <span style={{ fontSize: 11.5, fontWeight: 600, color: C.muted, flex: "none" }}>{label}</span>
      <span className="text-right" style={{ fontSize: 12, color: value ? C.ink : C.faint, fontWeight: value ? 600 : 400 }}>
        {value || "—"}
      </span>
    </div>
  );
}

export interface ChatSectionProps {
  chat: UseTextChat;
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

export function ChatSection({
  chat,
  selectedSubType,
  description,
  vehiclesInvolved,
  casualties,
  selectedFlags,
  dispatcherLocation,
  pinnedLocation,
  pinnedLabel,
  onRequestPin,
}: ChatSectionProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const isActive = chat.status !== "idle" && chat.status !== "offline";
  const ended = chat.status === "complete";
  const canType = isActive && !ended && (chat.status === "waiting" || chat.status === "thinking" || chat.status === "connecting");

  // Auto-scroll to the latest message.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages, chat.status]);

  const flagsLabel = selectedFlags.size ? Array.from(selectedFlags).join(", ") : null;
  const locationLabel =
    dispatcherLocation?.label || (pinnedLocation ? pinnedLabel || `${pinnedLocation.lat.toFixed(4)}, ${pinnedLocation.lng.toFixed(4)}` : null);

  function submitDraft() {
    const t = draft.trim();
    if (!t || !canType) return;
    chat.send(t);
    setDraft("");
    inputRef.current?.focus();
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Header */}
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: C.ink }}>Chat with the dispatcher</div>
        <div style={{ fontSize: 12, color: C.muted, marginTop: 2, lineHeight: 1.5 }}>
          Type to an AI operator (English). It asks about location, vehicles and injuries, then files the report for you — the same flow as a call, without the audio.
        </div>
      </div>

      {chat.offline && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2.5">
          The chat dispatcher is not configured on this deployment.
        </p>
      )}
      {chat.error && (
        <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg p-2.5">{chat.error}</p>
      )}

      {!isActive ? (
        <button
          onClick={() => chat.start()}
          disabled={!chat.supported || chat.offline}
          className="self-stretch flex items-center justify-center"
          style={{
            gap: 8, padding: "13px 20px", borderRadius: 12,
            background: chat.offline ? C.hairline : "linear-gradient(135deg,#2456A6,#173B77)",
            color: "#fff", border: "none", cursor: chat.offline ? "not-allowed" : "pointer",
            fontSize: 14.5, fontWeight: 700, opacity: chat.offline ? 0.6 : 1,
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          Start chat
        </button>
      ) : (
        <>
          {/* Message transcript */}
          <div
            ref={scrollRef}
            className="flex flex-col gap-2 overflow-y-auto"
            style={{ maxHeight: 320, minHeight: 160, padding: "6px 2px", background: C.page, borderRadius: RADIUS.input }}
          >
            {chat.messages.map((m, i) => (
              <div key={i} className="flex" style={{ justifyContent: m.role === "user" ? "flex-end" : "flex-start", padding: "0 8px" }}>
                <div
                  style={{
                    maxWidth: "82%",
                    padding: "8px 12px",
                    borderRadius: 14,
                    fontSize: 13,
                    lineHeight: 1.45,
                    whiteSpace: "pre-wrap",
                    background: m.role === "user" ? "linear-gradient(135deg,#2456A6,#173B77)" : "#fff",
                    color: m.role === "user" ? "#fff" : C.ink,
                    border: m.role === "user" ? "none" : `1px solid ${C.border}`,
                    borderBottomRightRadius: m.role === "user" ? 4 : 14,
                    borderBottomLeftRadius: m.role === "user" ? 14 : 4,
                  }}
                >
                  {m.text}
                </div>
              </div>
            ))}
            {chat.status === "thinking" && (
              <div className="flex" style={{ justifyContent: "flex-start", padding: "0 8px" }}>
                <div style={{ padding: "8px 12px", borderRadius: 14, background: "#fff", border: `1px solid ${C.border}`, color: C.muted, fontSize: 12 }}>
                  typing…
                </div>
              </div>
            )}
          </div>

          {/* Status line */}
          <div style={{ fontSize: 11.5, color: C.muted, textAlign: "center" }}>
            {STATUS_LABEL[chat.status] ?? chat.status}
          </div>

          {/* Input row (hidden once the chat is complete) */}
          {!ended && (
            <div className="flex" style={{ gap: 8 }}>
              <input
                ref={inputRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitDraft(); } }}
                placeholder={canType ? "Type your message…" : "Please wait…"}
                disabled={!canType}
                style={{
                  flex: 1, padding: "11px 14px", borderRadius: RADIUS.input,
                  border: `1px solid ${C.border}`, background: canType ? "#fff" : C.page,
                  fontSize: 13.5, color: C.ink, outline: "none",
                }}
              />
              <button
                onClick={submitDraft}
                disabled={!canType || !draft.trim()}
                style={{
                  flex: "none", padding: "0 16px", borderRadius: RADIUS.input, border: "none",
                  background: canType && draft.trim() ? C.navy800 : C.hairline,
                  color: "#fff", fontSize: 13.5, fontWeight: 700,
                  cursor: canType && draft.trim() ? "pointer" : "not-allowed",
                }}
              >
                Send
              </button>
            </div>
          )}

          <button onClick={() => chat.stop()} className="self-center" style={{ fontSize: 12, color: C.muted }}>
            End chat
          </button>
        </>
      )}

      {/* Live incident form (same "filling live" display the voice panel shows) */}
      {(isActive || selectedSubType || description) && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg divide-y divide-gray-100">
          <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase px-2 pt-2 pb-1">Report — filling live</p>
          <SummaryRow label="Incident Type" value={selectedSubType || null} />
          <SummaryRow label="Description" value={description || null} />
          <SummaryRow label="Vehicles" value={vehiclesInvolved || null} />
          <SummaryRow label="Casualties" value={casualties || null} />
          <SummaryRow label="Conditions" value={flagsLabel} />
          <SummaryRow label="Location" value={locationLabel} />
        </div>
      )}

      {isActive && !dispatcherLocation && !pinnedLocation && (
        <button onClick={onRequestPin} className="self-center" style={{ fontSize: 12.5, color: C.blue }}>
          Or set location manually on the map
        </button>
      )}
    </div>
  );
}
