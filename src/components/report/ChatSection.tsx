"use client";
import React, { useEffect, useRef, useState } from "react";
import { C, RADIUS, BRAND_GRADIENT } from "@/lib/design";
import { ShieldCrossIcon } from "@/components/ui/icons";
import type { UseTextChat } from "@/hooks/useTextChat";

// AI TEXT-CHAT channel UI — a modern chatbot thread (assistant/user bubbles, a
// typing indicator, a pill input with a Send icon) styled with the app's design
// tokens so it looks native to the report sheet. TEXT-ONLY — no mic / audio /
// playback. The incident form is filled in the BACKGROUND (via the useTextChat
// callbacks wired in ReportPanel) and is deliberately NOT shown here; the same
// submit → matching → closing-briefing flow runs unchanged, with the greeting
// and closing briefing rendered as ordinary assistant bubbles.

// User-bubble accent — the app's blue → navy, matching the other primary CTAs.
const USER_BUBBLE = `linear-gradient(135deg,${C.blue},${C.navy700})`;

function statusLine(status: string): { text: string; dot?: string } {
  switch (status) {
    case "connecting": return { text: "Connecting…" };
    case "thinking": return { text: "typing…", dot: C.green };
    case "waiting": return { text: "Online", dot: C.green };
    case "submitted": return { text: "Finding nearest help…", dot: C.saffron };
    case "complete": return { text: "Chat ended" };
    case "error": return { text: "Connection problem", dot: C.red };
    case "offline": return { text: "Unavailable" };
    default: return { text: "Online", dot: C.green };
  }
}

function OperatorAvatar({ size = 26 }: { size?: number }) {
  return (
    <span
      className="flex-none inline-flex items-center justify-center"
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.3), background: BRAND_GRADIENT }}
    >
      <ShieldCrossIcon size={Math.round(size * 0.54)} style={{ color: "#fff" }} />
    </span>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center" style={{ gap: 4, padding: "11px 14px" }}>
      {[0, 1, 2].map((i) => (
        <span key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: C.muted, display: "inline-block", animation: "tsTyping 1.1s infinite", animationDelay: `${i * 0.18}s` }} />
      ))}
    </div>
  );
}

function Notice({ children, tone }: { children: React.ReactNode; tone?: "error" }) {
  const err = tone === "error";
  return (
    <div style={{ margin: "4px 6px", padding: "9px 12px", borderRadius: RADIUS.input, fontSize: 12, lineHeight: 1.45, background: err ? C.redSoftBg : C.saffronSoftBg, border: `1px solid ${err ? C.redSoftBorder : C.saffronSoftBorder}`, color: err ? C.redSoftText : C.saffronSoftText }}>
      {children}
    </div>
  );
}

export interface ChatSectionProps {
  chat: UseTextChat;
}

export function ChatSection({ chat }: ChatSectionProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  const isActive = chat.status !== "idle" && chat.status !== "offline";
  const ended = chat.status === "complete";
  const canType = chat.status === "waiting" || chat.status === "thinking";
  const sline = statusLine(chat.status);

  // Auto-scroll to the newest message / indicator.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.messages, chat.status]);

  // Auto-grow the input up to ~4 lines (Shift+Enter newlines).
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 96)}px`;
  }, [draft]);

  function submitDraft() {
    const t = draft.trim();
    if (!t || !canType) return;
    chat.send(t);
    setDraft("");
    requestAnimationFrame(() => taRef.current?.focus());
  }

  return (
    <div className="flex flex-col h-full" style={{ minHeight: 0 }}>
      {/* Header — operator identity + live status, matching the sheet treatment */}
      <div className="flex items-center flex-none" style={{ gap: 10, paddingBottom: 12, borderBottom: `1px solid ${C.hairline}` }}>
        <OperatorAvatar size={34} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: C.ink, lineHeight: 1.2 }}>Chat with the dispatcher</div>
          <div className="flex items-center" style={{ gap: 5, fontSize: 11.5, color: C.muted, marginTop: 1 }}>
            {sline.dot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: sline.dot, display: "inline-block" }} />}
            {sline.text}
          </div>
        </div>
        {isActive && !ended && (
          <button
            onClick={() => chat.stop()}
            className="flex-none"
            style={{ marginLeft: "auto", fontSize: 12, color: C.muted, background: "none", border: "none", cursor: "pointer", padding: "4px 2px" }}
          >
            End chat
          </button>
        )}
      </div>

      {/* Message thread */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto"
        style={{ minHeight: 0, padding: "14px 2px", display: "flex", flexDirection: "column", gap: 2 }}
      >
        {chat.offline && <Notice>The chat dispatcher is not configured on this deployment.</Notice>}
        {chat.error && <Notice tone="error">{chat.error}</Notice>}
        {chat.messages.length === 0 && !chat.offline && !chat.error && (
          <div className="flex-1 flex items-center justify-center" style={{ color: C.faint, fontSize: 12.5 }}>
            Connecting to the dispatcher…
          </div>
        )}

        {chat.messages.map((m, i) => {
          const prev = chat.messages[i - 1];
          const startGroup = !prev || prev.role !== m.role;
          const isUser = m.role === "user";
          return (
            <div
              key={i}
              className="flex"
              style={{ justifyContent: isUser ? "flex-end" : "flex-start", gap: 8, alignItems: "flex-end", marginTop: startGroup ? 8 : 2, padding: "0 4px" }}
            >
              {!isUser && (
                startGroup ? <OperatorAvatar /> : <span className="flex-none" style={{ width: 26 }} aria-hidden />
              )}
              <div
                style={{
                  maxWidth: "78%",
                  padding: "9px 13px",
                  fontSize: 13.5,
                  lineHeight: 1.5,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  background: isUser ? USER_BUBBLE : C.card,
                  color: isUser ? "#fff" : C.ink,
                  border: isUser ? "none" : `1px solid ${C.border}`,
                  boxShadow: isUser ? "none" : "0 1px 2px rgba(14,26,47,.04)",
                  borderRadius: 16,
                  borderBottomRightRadius: isUser ? 5 : 16,
                  borderBottomLeftRadius: isUser ? 16 : 5,
                }}
              >
                {m.text}
              </div>
            </div>
          );
        })}

        {chat.status === "thinking" && (
          <div className="flex" style={{ justifyContent: "flex-start", gap: 8, alignItems: "flex-end", marginTop: 8, padding: "0 4px" }}>
            <OperatorAvatar />
            <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 16, borderBottomLeftRadius: 5 }}>
              <TypingDots />
            </div>
          </div>
        )}

        {chat.status === "submitted" && (
          <div className="self-center" style={{ margin: "10px 0", padding: "5px 12px", borderRadius: RADIUS.pill, background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, fontSize: 11.5, fontWeight: 600, color: C.saffronSoftText }}>
            Finding nearest help…
          </div>
        )}
        {ended && (
          <div className="self-center" style={{ margin: "10px 0 2px", fontSize: 11.5, color: C.muted }}>
            Chat ended · your report has been filed
          </div>
        )}
      </div>

      {/* Input bar — pill with a Send icon, or a quiet close link once ended */}
      {!ended ? (
        <div className="flex-none" style={{ paddingTop: 10 }}>
          <div className="flex items-end" style={{ gap: 8, background: C.card, border: `1px solid ${C.border}`, borderRadius: 22, padding: "6px 6px 6px 14px", boxShadow: "0 1px 2px rgba(14,26,47,.04)" }}>
            <textarea
              ref={taRef}
              value={draft}
              rows={1}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitDraft(); } }}
              placeholder={canType ? "Type your message…" : "Please wait…"}
              disabled={!canType}
              style={{ flex: 1, resize: "none", border: "none", outline: "none", background: "transparent", fontSize: 13.5, lineHeight: 1.4, color: C.ink, maxHeight: 96, padding: "6px 0", fontFamily: "inherit" }}
            />
            <button
              onClick={submitDraft}
              disabled={!canType || !draft.trim()}
              aria-label="Send message"
              className="flex-none inline-flex items-center justify-center"
              style={{ width: 34, height: 34, borderRadius: "50%", border: "none", background: canType && draft.trim() ? USER_BUBBLE : C.hairline, color: "#fff", cursor: canType && draft.trim() ? "pointer" : "not-allowed", transition: "background .15s" }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 2 11 13" />
                <path d="M22 2 15 22l-4-9-9-4 20-7z" />
              </svg>
            </button>
          </div>
        </div>
      ) : (
        <div className="flex-none flex justify-center" style={{ paddingTop: 10 }}>
          <button onClick={() => chat.stop()} style={{ fontSize: 12.5, color: C.muted, background: "none", border: "none", cursor: "pointer", padding: "4px 8px" }}>
            Close chat
          </button>
        </div>
      )}
    </div>
  );
}
