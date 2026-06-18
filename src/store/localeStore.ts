"use client";
import { create } from "zustand";
import type { Locale } from "@/i18n/strings";

interface LocaleState {
  locale: Locale;
  setLocale: (l: Locale) => void;
}

const SESSION_KEY = "ts_locale";

function getInitialLocale(): Locale {
  if (typeof window === "undefined") return "EN";
  return (sessionStorage.getItem(SESSION_KEY) as Locale | null) ?? "EN";
}

export const useLocaleStore = create<LocaleState>((set) => ({
  locale: getInitialLocale(),
  setLocale: (locale) => {
    if (typeof window !== "undefined") sessionStorage.setItem(SESSION_KEY, locale);
    set({ locale });
  },
}));
