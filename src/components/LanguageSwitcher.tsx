"use client";
import { useEffect, useRef, useState } from "react";
import { useLocaleStore } from "@/store/localeStore";
import type { Locale } from "@/i18n/strings";
import { C } from "@/lib/design";

// Compact UI-language switcher (globe icon + dropdown): English / हिंदी / অসমীয়া.
// Sets useLocaleStore's locale (persisted to sessionStorage by the store). It is a
// pure UI-locale control, independent of auth — rendered in the app header (every
// signed-in role) and on the pre-login landing + operator sign-in. Assamese is
// UI-only: the voice dispatcher stays English/Hindi (see the voice flow), so this
// switcher never implies an Assamese conversational agent.

const OPTIONS: { code: Locale; label: string; short: string }[] = [
  { code: "EN", label: "English",  short: "EN" },
  { code: "HI", label: "हिंदी",     short: "हिं" },
  { code: "AS", label: "অসমীয়া",   short: "অস" },
];

// Globe glyph — inline SVG so it needs no icon dependency and inherits currentColor.
function GlobeIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.5 2.5 3.8 5.7 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.7-3.8-9s1.3-6.5 3.8-9z" />
    </svg>
  );
}

export default function LanguageSwitcher({ tone = "light" }: { tone?: "light" | "dark" }) {
  const { locale, setLocale } = useLocaleStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const current = OPTIONS.find((o) => o.code === locale) ?? OPTIONS[0];

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Button colours: "light" = light glyph on a dark/navy surface (the app header,
  // navy landing, operator sign-in); "dark" = dark glyph on a light surface.
  const fg = tone === "light" ? "#C7D2E6" : C.secondary;
  const bg = tone === "light" ? "rgba(255,255,255,.07)" : C.inset;
  const bd = tone === "light" ? "rgba(255,255,255,.14)" : C.border;

  return (
    <div ref={ref} style={{ position: "relative", flex: "none" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Change language"
        title="Language"
        style={{
          display: "flex", alignItems: "center", gap: 5, cursor: "pointer",
          padding: "5px 9px", borderRadius: 8, border: `1px solid ${bd}`,
          background: bg, color: fg, fontSize: 12, fontWeight: 600, lineHeight: 1,
        }}
      >
        <GlobeIcon />
        <span style={{ minWidth: 16, textAlign: "center" }}>{current.short}</span>
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 3000,
            minWidth: 160, background: "#fff", borderRadius: 12,
            border: `1px solid ${C.border}`, boxShadow: "0 12px 32px rgba(14,26,47,.22)",
            padding: 5, overflow: "hidden",
          }}
        >
          {OPTIONS.map((o) => {
            const on = o.code === locale;
            return (
              <button
                key={o.code}
                role="menuitemradio"
                aria-checked={on}
                onClick={() => { setLocale(o.code); setOpen(false); }}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  width: "100%", gap: 12, padding: "9px 11px", borderRadius: 8,
                  border: "none", cursor: "pointer", textAlign: "left",
                  background: on ? C.inset : "transparent",
                  color: C.ink, fontSize: 14, fontWeight: on ? 700 : 500,
                }}
                onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = C.hairline; }}
                onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = "transparent"; }}
              >
                <span>{o.label}</span>
                <span style={{ color: on ? "#2E7D57" : "transparent", fontSize: 13, fontWeight: 700 }}>✓</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
