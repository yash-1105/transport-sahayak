"use client";
import { useLocaleStore } from "@/store/localeStore";
import type { Locale } from "@/i18n/strings";

const LOCALES: { code: Locale; label: string }[] = [
  { code: "EN", label: "EN" },
  { code: "HI", label: "हि" },
  { code: "AS", label: "অ" },
];

export default function LanguageToggle() {
  const { locale, setLocale } = useLocaleStore();
  return (
    <div className="flex items-center gap-0.5 bg-white/95 rounded border border-gray-300 p-0.5 shadow-sm">
      {LOCALES.map(({ code, label }) => (
        <button
          key={code}
          onClick={() => setLocale(code)}
          aria-pressed={locale === code}
          className={`px-2 py-0.5 text-[11px] font-bold rounded transition-colors leading-none ${
            locale === code
              ? "bg-[#0f2044] text-white"
              : "text-gray-500 hover:text-gray-800 hover:bg-gray-100"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
