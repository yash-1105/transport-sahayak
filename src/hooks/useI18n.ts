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

// Bilingual helper — RETIRED to single-language rendering (2026-08). The UI used to
// show a lighter Hindi sub-label beside the English label in EN mode. Now that there
// is an explicit EN / हिंदी / অসমীয়া switcher (LanguageSwitcher), that dual-label is
// redundant and, with three languages, risks a mismatched second language (e.g. a
// Hindi sub-label while Assamese is selected). So `showHindi` is now always false:
// every `showHindi && <Hindi>` secondary collapses to nothing, and each surface
// renders in exactly the selected language (useT) or its own single base label. The
// hook is kept (not deleted) so the many call sites need no churn.
export function useBilingual() {
  return {
    showHindi: false,
    hi(key: StringKey): string {
      const entry = strings[key] as Record<string, string>;
      return entry["HI"] ?? "";
    },
  };
}
