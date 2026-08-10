"use client";
import React, { useState } from "react";
import { C } from "@/lib/design";
import { Sheet, Field, TextInput, PrimaryButton, ErrorBanner } from "@/components/auth/ui";
import { useAuthStore, isOperatorEmail } from "@/store/authStore";
import LanguageSwitcher from "@/components/LanguageSwitcher";

// Compact, deliberately spare operator ("admin") sign-in — reached only via the
// quiet corner entry on the gate. Visually distinct from the citizen form (navy
// header strip, no bilingual flourish, NO "sign up" tab — operators can't
// self-register). Uses the SAME store signIn as everyone else; operator status
// is derived from the email allowlist, not granted here.
export default function OperatorSignIn({ onClose }: { onClose: () => void }) {
  const signIn = useAuthStore((s) => s.signIn);
  const signOut = useAuthStore((s) => s.signOut);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    setErr(null);
    const em = email.trim();
    if (!em || !password) {
      setErr("Enter the operator email and password.");
      return;
    }
    // The operator door only admits allowlisted emails. Checking BEFORE sign-in
    // means a citizen's credentials never create a session here (and there is no
    // flash of the app before we could sign them back out).
    if (!isOperatorEmail(em)) {
      setErr("This account is not authorised for operator access.");
      return;
    }
    setBusy(true);
    const res = await signIn(em, password);
    if (res.error) {
      setBusy(false);
      setErr(res.error);
      return;
    }
    // Defense-in-depth: if the session that actually landed is somehow not an
    // operator, sign it straight back out (belt-and-suspenders with the check
    // above). On success the gate re-renders into the app on its own.
    const signedInEmail = useAuthStore.getState().user?.email ?? null;
    if (signedInEmail && !isOperatorEmail(signedInEmail)) {
      await signOut();
      setBusy(false);
      setErr("This account is not authorised for operator access.");
      return;
    }
    setBusy(false);
  }

  return (
    <Sheet title="Operator sign in" showHindi={false} onClose={onClose} busy={busy} maxWidth={400}>
      {/* Distinct navy strip so this never looks like the citizen form */}
      <div style={{ background: C.navy800, color: "#fff", padding: "12px 20px", display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 22, height: 22, borderRadius: 6, background: "rgba(255,255,255,.12)", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="11" width="18" height="10" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        </span>
        <div style={{ fontSize: 12, lineHeight: 1.35 }}>
          <div style={{ fontWeight: 600 }}>Restricted — operator access</div>
          <div style={{ color: "#93A3BE", fontSize: 11 }}>Network dispatch dashboard. Staff only.</div>
        </div>
        <div style={{ marginLeft: "auto", flex: "none" }}>
          <LanguageSwitcher tone="light" />
        </div>
      </div>

      <div className="flex flex-col" style={{ gap: 14, padding: "16px 20px 20px" }}>
        <Field label="Operator email" required>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="operator@transport.gov.in"
            autoComplete="username"
            inputMode="email"
          />
        </Field>
        <Field label="Password" required>
          <TextInput
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Operator password"
            autoComplete="current-password"
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
        </Field>

        {err && <ErrorBanner>{err}</ErrorBanner>}

        <PrimaryButton onClick={submit} busy={busy}>Sign in as operator</PrimaryButton>

        <p style={{ fontSize: 11, color: C.muted, textAlign: "center", lineHeight: 1.5 }}>
          Operator accounts are provisioned by the administrator — there is no self-registration.
        </p>
      </div>
    </Sheet>
  );
}
