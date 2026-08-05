"use client";
import { create } from "zustand";
import type { Session, User } from "@supabase/supabase-js";
import { supabaseBrowser } from "@/lib/supabaseBrowser";

// Phase-1 auth state. Mirrors the localeStore Zustand pattern: a plain store,
// hydrated once at module load. Auth is CLIENT-ONLY (browser Supabase client +
// RLS) — there is no server session, no middleware. With email confirmation
// disabled for the prototype, signUp returns a live session immediately.
//
// The app is fronted by a full-screen AuthGate (src/components/auth/AuthGate):
// a user must sign in, OR continue as a GUEST, before the app mounts. Guest is
// a client-only bypass (persisted in sessionStorage so a mid-emergency refresh
// doesn't re-gate) and is cleared the moment a real session appears or on
// sign-out. needsOnboarding drives the one-time post-SIGNUP flow (profile →
// Suraksha Mitra), never a returning sign-in.

export interface SignUpMeta {
  fullName?: string;
}

interface AuthResult {
  error: string | null;
  // True when the project still has email confirmation ON (signUp returned no
  // session). The prototype expects this to be OFF; surfaced so the UI can tell
  // the user to confirm rather than silently appearing to do nothing.
  needsEmailConfirm?: boolean;
}

interface AuthState {
  user: User | null;
  session: Session | null;
  loading: boolean; // initial session hydration in flight
  ready: boolean; // hydration finished at least once
  configured: boolean; // Supabase env keys present
  isGuest: boolean; // client-only emergency bypass of the gate
  needsOnboarding: boolean; // post-signup guided flow is active
  gateInitialTab: "signup" | "signin"; // which tab the full-screen gate opens on
  // ── PWA launch home (standalone only) ──────────────────────────────────────
  // launchIntent = the action tapped on the standalone home screen (PWAHome),
  // consumed once by MapView on mount then cleared. pwaEntered flips true the
  // moment the user leaves the home screen so the gate stops re-showing it;
  // it's in-memory only, so a fresh cold launch of the PWA resets to the home.
  launchIntent: "voice" | "dashboard" | "profile" | null;
  pwaEntered: boolean;
  signUp: (email: string, password: string, meta?: SignUpMeta) => Promise<AuthResult>;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<void>;
  continueAsGuest: () => void;
  exitGuest: (tab?: "signup" | "signin") => void;
  beginOnboarding: () => void;
  finishOnboarding: () => void;
  setLaunchIntent: (intent: "voice" | "dashboard" | "profile") => void;
  clearLaunchIntent: () => void;
  enterPwa: () => void;
}

const GUEST_KEY = "ts_guest";

function getInitialGuest(): boolean {
  if (typeof window === "undefined") return false;
  return sessionStorage.getItem(GUEST_KEY) === "1";
}

// ── Operator allowlist ────────────────────────────────────────────────────────
// The operator ("admin") is a real Supabase account whose email is on this
// hardcoded allowlist. isOperator is DERIVED from the signed-in user's email —
// never a settable flag — so it can't be granted by signing up, and the
// operator's PASSWORD lives only in Supabase (never in this bundle). The
// allowlisted email is not a secret. Override via NEXT_PUBLIC_OPERATOR_EMAILS
// (comma-separated).
const OPERATOR_EMAILS = (process.env.NEXT_PUBLIC_OPERATOR_EMAILS ?? "yashsingh1105@gmail.com")
  .split(",")
  .map((e) => e.trim().toLowerCase())
  .filter(Boolean);

export function isOperatorEmail(email?: string | null): boolean {
  return !!email && OPERATOR_EMAILS.includes(email.trim().toLowerCase());
}

// Map Supabase's auth error messages to short, friendly inline copy.
function friendlyAuthError(message: string): string {
  const m = message.toLowerCase();
  if (m.includes("already registered") || m.includes("already been registered"))
    return "That email is already registered — try signing in instead.";
  if (m.includes("invalid login")) return "Incorrect email or password.";
  if (m.includes("password should be") || m.includes("password"))
    return "Password is too weak — use at least 6 characters.";
  if (m.includes("unable to validate email") || m.includes("invalid email"))
    return "That doesn't look like a valid email address.";
  if (m.includes("email not confirmed"))
    return "This email hasn't been confirmed yet.";
  return message;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  session: null,
  loading: true,
  ready: false,
  configured: !!supabaseBrowser,
  isGuest: getInitialGuest(),
  needsOnboarding: false,
  gateInitialTab: "signup",
  launchIntent: null,
  pwaEntered: false,

  async signUp(email, password, meta) {
    if (!supabaseBrowser) return { error: "Accounts are not configured on this deployment." };
    const { data, error } = await supabaseBrowser.auth.signUp({
      email: email.trim(),
      password,
      options: { data: meta?.fullName ? { full_name: meta.fullName } : undefined },
    });
    if (error) return { error: friendlyAuthError(error.message) };

    // With confirmation OFF, a session is returned immediately. Seed a profile
    // shell so the user always has a row to fill in (upsert is idempotent, so a
    // later profile save just updates it). If confirmation is still ON, there is
    // no session yet and RLS would reject the insert — skip and flag it.
    if (data.session && data.user) {
      await supabaseBrowser
        .from("profiles")
        .upsert(
          {
            id: data.user.id,
            full_name: meta?.fullName ?? null,
            updated_at: new Date().toISOString(),
          },
          { onConflict: "id" }
        );
      return { error: null };
    }
    return { error: null, needsEmailConfirm: true };
  },

  async signIn(email, password) {
    if (!supabaseBrowser) return { error: "Accounts are not configured on this deployment." };
    const { error } = await supabaseBrowser.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    if (error) return { error: friendlyAuthError(error.message) };
    return { error: null };
  },

  async signOut() {
    // Clear the guest bypass and any in-flight onboarding first, so signing out
    // always returns to the gate (never leaves a stale guest/onboarding state).
    if (typeof window !== "undefined") sessionStorage.removeItem(GUEST_KEY);
    // Also drop the PWA launch state so signing out returns to the home screen
    // (in a browser these are inert — pwaEntered is only ever set in standalone).
    set({ isGuest: false, needsOnboarding: false, pwaEntered: false, launchIntent: null });
    if (!supabaseBrowser) return;
    await supabaseBrowser.auth.signOut();
  },

  continueAsGuest() {
    if (typeof window !== "undefined") sessionStorage.setItem(GUEST_KEY, "1");
    set({ isGuest: true });
  },

  // Leave guest mode WITHOUT signing out (there is no session) — returns the
  // guest to the full-screen gate so they can create/sign in to an account.
  // `tab` selects which tab the gate opens on (the header "Sign in" pill passes
  // "signin"). Used instead of a header modal so a guest sees the same
  // redesigned auth screen as a fresh visitor.
  exitGuest(tab = "signup") {
    if (typeof window !== "undefined") sessionStorage.removeItem(GUEST_KEY);
    set({ isGuest: false, gateInitialTab: tab });
  },

  beginOnboarding() {
    set({ needsOnboarding: true });
  },

  finishOnboarding() {
    set({ needsOnboarding: false });
  },

  // PWA launch home → app. setLaunchIntent records the tapped action; enterPwa
  // dismisses the home screen so the app (children) mounts; MapView reads the
  // intent on mount and calls clearLaunchIntent once consumed.
  setLaunchIntent(intent) {
    set({ launchIntent: intent });
  },
  clearLaunchIntent() {
    set({ launchIntent: null });
  },
  enterPwa() {
    set({ pwaEntered: true });
  },
}));

// ── Hydrate once at module load (browser only) ────────────────────────────────
// getSession() restores a persisted session; onAuthStateChange keeps the store
// in sync for sign-in / sign-out / token refresh for the life of the tab. A real
// session always supersedes guest mode (clear the flag + its sessionStorage key).
function applySession(session: Session | null) {
  if (session && typeof window !== "undefined") sessionStorage.removeItem(GUEST_KEY);
  useAuthStore.setState({
    session,
    user: session?.user ?? null,
    loading: false,
    ready: true,
    ...(session ? { isGuest: false } : null),
  });
}

if (supabaseBrowser) {
  supabaseBrowser.auth.getSession().then(({ data }) => applySession(data.session));
  supabaseBrowser.auth.onAuthStateChange((_event, session) => applySession(session));
} else {
  useAuthStore.setState({ loading: false, ready: true });
}

// Derived operator flag — true ONLY for a signed-in Supabase user whose email is
// allowlisted. Recomputed from `user`; there is no way to set it directly, and
// no signup path can produce it (a fresh signup's email won't be on the list).
export function useIsOperator(): boolean {
  return useAuthStore((s) => isOperatorEmail(s.user?.email));
}
