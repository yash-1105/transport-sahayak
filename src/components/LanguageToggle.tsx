"use client";
import { useLocaleStore } from "@/store/localeStore";
import type { Locale } from "@/i18n/strings";

const LOCALES: { code: Locale; label: string }[] = [
  { code: "EN", label: "EN" },
  { code: "HI", label: "हिं" },
];

export default function LanguageToggle() {
  const { locale, setLocale } = useLocaleStore();
  return (
    <div
      className="flex items-center"
      style={{ gap: 2, background: "rgba(255,255,255,.07)", border: "1px solid rgba(255,255,255,.1)", borderRadius: 8, padding: 2 }}
    >
      {LOCALES.map(({ code, label }) => {
        const on = locale === code;
        return (
          <button
            key={code}
            onClick={() => setLocale(code)}
            aria-pressed={on}
            style={{
              padding: "4px 10px",
              border: "none",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 11.5,
              fontWeight: 600,
              lineHeight: 1,
              background: on ? "#fff" : "transparent",
              color: on ? "#14243E" : "#B9C4D8",
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
