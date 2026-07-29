"use client";
import { useLocaleStore } from "@/store/localeStore";
import { strings, type StringKey } from "@/i18n/strings";

export function useT() {
  const locale = useLocaleStore((s) => s.locale);
  return function t(key: StringKey): string {
    const entry = strings[key] as Record<string, string>;
    return entry[locale] ?? entry["EN"] ?? key;
  };
}

// Bilingual helper for the UI redesign. The language toggle still switches the
// whole UI (and the voice-recognition locale) — that behaviour is unchanged.
// On top of that, the design shows a lighter Hindi sub-label beside the English
// label. We surface that sub-label only when English is the active primary
// (showHindi), so Hindi text is never duplicated once the toggle is on Hindi.
export function useBilingual() {
  const locale = useLocaleStore((s) => s.locale);
  return {
    showHindi: locale === "EN",
    hi(key: StringKey): string {
      const entry = strings[key] as Record<string, string>;
      return entry["HI"] ?? "";
    },
  };
}
