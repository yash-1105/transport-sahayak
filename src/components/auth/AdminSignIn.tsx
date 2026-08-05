"use client";
import React, { useState } from "react";
import { C } from "@/lib/design";
import { Sheet, Field, TextInput, PrimaryButton, ErrorBanner } from "@/components/auth/ui";
import { useAuthStore, isAdminEmail } from "@/store/authStore";

// Administrator sign-in — the super-role above operator. Reached only via the
// quietest corner entry on the gate. Same model as OperatorSignIn: SAME store
// signIn, admin status DERIVED from the email allowlist (never granted here),
// no self-registration. Distinct saffron strip so it reads as the top tier.
export default function AdminSignIn({ onClose }: { onClose: () => void }) {
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
      setErr("Enter the administrator email and password.");
      return;
    }
    // Admin door only admits allowlisted admin emails — checked BEFORE sign-in so
    // a non-admin's credentials never create a session here.
    if (!isAdminEmail(em)) {
      setErr("This account is not authorised for administrator access.");
      return;
    }
    setBusy(true);
    const res = await signIn(em, password);
    if (res.error) {
      setBusy(false);
      setErr(res.error);
      return;
    }
    // Defense-in-depth: sign back out if the landed session isn't an admin.
    const signedInEmail = useAuthStore.getState().user?.email ?? null;
    if (signedInEmail && !isAdminEmail(signedInEmail)) {
      await signOut();
      setBusy(false);
      setErr("This account is not authorised for administrator access.");
      return;
    }
    setBusy(false);
  }

  return (
    <Sheet title="Administrator sign in" showHindi={false} onClose={onClose} busy={busy} maxWidth={400}>
      {/* Saffron strip — the top oversight tier, distinct from operator navy */}
      <div style={{ background: "linear-gradient(135deg,#E8862B,#C6362C)", color: "#fff", padding: "12px 20px", display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 22, height: 22, borderRadius: 6, background: "rgba(255,255,255,.18)", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9.5 12l1.8 1.8L15 10" />
          </svg>
        </span>
        <div style={{ fontSize: 12, lineHeight: 1.35 }}>
          <div style={{ fontWeight: 600 }}>Restricted — administrator access</div>
          <div style={{ color: "#FBE7D2", fontSize: 11 }}>Network-wide oversight. Administrators only.</div>
        </div>
      </div>

      <div className="flex flex-col" style={{ gap: 14, padding: "16px 20px 20px" }}>
        <Field label="Administrator email" required>
          <TextInput
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="admin@transport.gov.in"
            autoComplete="username"
            inputMode="email"
          />
        </Field>
        <Field label="Password" required>
          <TextInput
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Administrator password"
            autoComplete="current-password"
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
        </Field>

        {err && <ErrorBanner>{err}</ErrorBanner>}

        <PrimaryButton onClick={submit} busy={busy}>Sign in as administrator</PrimaryButton>

        <p style={{ fontSize: 11, color: C.muted, textAlign: "center", lineHeight: 1.5 }}>
          Administrator accounts are provisioned centrally — there is no self-registration.
        </p>
      </div>
    </Sheet>
  );
}
