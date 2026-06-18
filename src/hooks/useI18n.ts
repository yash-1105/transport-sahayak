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
