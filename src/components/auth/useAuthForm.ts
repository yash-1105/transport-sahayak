"use client";
import { useState } from "react";
import { useAuthStore } from "@/store/authStore";

// Single source of truth for the auth form's state + submit logic. BOTH the
// redesigned full-screen landing (AuthLanding) and the header modal (AuthForm →
// AuthModal) drive their fields from this hook, so the sign-up/sign-in behaviour,
// validation, and error/needsEmailConfirm handling exist in exactly one place —
// the redesign is presentation-only and never forks this logic.
export type AuthTab = "signup" | "signin";

export interface UseAuthFormOpts {
  onSignedUp: () => void;
  onSignedIn: () => void;
  onTabChange?: (tab: AuthTab) => void;
  onBusyChange?: (busy: boolean) => void;
  initialTab?: AuthTab;
}

export function useAuthForm({ onSignedUp, onSignedIn, onTabChange, onBusyChange, initialTab = "signup" }: UseAuthFormOpts) {
  const { signUp, signIn } = useAuthStore();
  const [tab, setTab] = useState<AuthTab>(initialTab);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusyState] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  function setBusy(b: boolean) {
    setBusyState(b);
    onBusyChange?.(b);
  }

  function switchTab(t: AuthTab) {
    if (t === tab) return;
    setTab(t);
    setError(null);
    setInfo(null);
    onTabChange?.(t);
  }

  function toggleTab() {
    switchTab(tab === "signup" ? "signin" : "signup");
  }

  async function submit() {
    setError(null);
    setInfo(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    if (tab === "signup" && password.length < 6) {
      setError("Password is too weak — use at least 6 characters.");
      return;
    }
    setBusy(true);
    if (tab === "signup") {
      const res = await signUp(email, password, { fullName: fullName.trim() || undefined });
      setBusy(false);
      if (res.error) return setError(res.error);
      if (res.needsEmailConfirm) {
        // The project still has email confirmation ON — the prototype expects it
        // OFF. Be honest rather than pretending the user is signed in.
        setInfo(
          "Account created, but this project still requires email confirmation. Check your inbox to confirm, then sign in."
        );
        return;
      }
      onSignedUp();
    } else {
      const res = await signIn(email, password);
      setBusy(false);
      if (res.error) return setError(res.error);
      onSignedIn();
    }
  }

  return {
    tab,
    isSignup: tab === "signup",
    switchTab,
    toggleTab,
    fullName, setFullName,
    email, setEmail,
    password, setPassword,
    showPw, setShowPw,
    busy,
    error,
    info,
    submit,
  };
}
