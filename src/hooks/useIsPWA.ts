"use client";
import { useSyncExternalStore } from "react";

// True when the app is running as an INSTALLED PWA — standalone display mode
// (Android/desktop) or iOS home-screen (`navigator.standalone`). NOT true in a
// normal browser tab.
//
// Implemented with useSyncExternalStore so it's SSR-safe with no hydration
// mismatch and no setState-in-effect: the server + first hydration render get
// `null` (unresolved), then it switches to the real boolean on the client.
// Callers keep showing the splash while it's `null`.

// NOTE: only `display-mode: standalone` (the manifest's display mode) and iOS
// `navigator.standalone` count. Deliberately NOT `fullscreen` — a normal browser
// in fullscreen (F11 / kiosk) is not an installed PWA and must NOT get the home.
function isStandalone(): boolean {
  return !!(
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (window.navigator as { standalone?: boolean }).standalone === true
  );
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const mql = window.matchMedia("(display-mode: standalone)");
  mql.addEventListener?.("change", onChange);
  return () => mql.removeEventListener?.("change", onChange);
}

// Primitive snapshots are compared by value, so returning a fresh boolean each
// call is safe (no infinite re-render).
function getSnapshot(): boolean | null {
  return isStandalone();
}
function getServerSnapshot(): boolean | null {
  return null;
}

export function useIsPWA(): boolean | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
