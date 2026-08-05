"use client";
import React, { useState } from "react";
import { C, RADIUS, CTA_GRADIENT, BRAND_GRADIENT, HEADER_GRADIENT } from "@/lib/design";
import { ShieldCrossIcon, UserIcon } from "@/components/ui/icons";
import { useBilingual } from "@/hooks/useI18n";
import { useAuthStore } from "@/store/authStore";
import AuthLanding from "@/components/auth/AuthLanding";

// The PWA-ONLY launch home screen. Shown by AuthGate when the app is opened as
// an installed PWA (standalone) — a simple, emergency-first home instead of the
// full sign-in gate. From here the user either taps the big Report Incident
// button (→ voice dispatcher, never blocked by auth) or View dashboard. A
// normal browser tab never sees this screen.
export default function PWAHome() {
  const { showHindi } = useBilingual();
  const user = useAuthStore((s) => s.user);
  const continueAsGuest = useAuthStore((s) => s.continueAsGuest);
  const setLaunchIntent = useAuthStore((s) => s.setLaunchIntent);
  const enterPwa = useAuthStore((s) => s.enterPwa);
  const beginOnboarding = useAuthStore((s) => s.beginOnboarding);
  const gateInitialTab = useAuthStore((s) => s.gateInitialTab);

  const [showAuth, setShowAuth] = useState(false);

  const displayName =
    ((user?.user_metadata?.full_name as string | undefined)?.trim() || user?.email || "").trim();
  const initial = (displayName || "?").charAt(0).toUpperCase();

  // Report Incident → straight into the voice dispatcher. Emergencies must never
  // be blocked, so a signed-out user is dropped into guest mode first.
  function handleReport() {
    if (!user) continueAsGuest();
    setLaunchIntent("voice");
    enterPwa();
  }
  // View dashboard → signed in: their own profile; signed out: the guest app
  // (with a visible "Sign in" in the header via AuthControl).
  function handleDashboard() {
    if (user) setLaunchIntent("profile");
    else { continueAsGuest(); setLaunchIntent("dashboard"); }
    enterPwa();
  }
  // Signed-in name chip → their profile.
  function handleProfile() {
    setLaunchIntent("profile");
    enterPwa();
  }

  return (
    <div
      className="fixed inset-0 z-[2500] flex flex-col"
      style={{ background: HEADER_GRADIENT, color: "#fff" }}
    >
      {/* ── Top bar: brand + auth affordance (safe-area top) ── */}
      <div
        className="flex items-center"
        style={{ justifyContent: "space-between", padding: "calc(14px + env(safe-area-inset-top)) 18px 8px" }}
      >
        <div className="flex items-center" style={{ gap: 10, minWidth: 0 }}>
          <div style={{ width: 34, height: 34, borderRadius: 9, background: BRAND_GRADIENT, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>
            <ShieldCrossIcon size={19} style={{ color: "#fff" }} />
          </div>
          <div style={{ lineHeight: 1.15, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700 }} className="truncate">Transport Sahayak</div>
            {showHindi && <div style={{ fontSize: 12, color: "#93A3BE" }}>परिवहन सहायक</div>}
          </div>
        </div>

        {user ? (
          <button
            onClick={handleProfile}
            className="flex items-center flex-none"
            style={{ gap: 7, padding: "4px 10px 4px 5px", borderRadius: RADIUS.pill, border: "1px solid rgba(255,255,255,.16)", background: "rgba(255,255,255,.1)", color: "#fff", cursor: "pointer", maxWidth: 170 }}
          >
            <span className="flex items-center justify-center flex-none" style={{ width: 24, height: 24, borderRadius: "50%", background: "linear-gradient(135deg,#2456A6,#173B77)", fontSize: 12, fontWeight: 700 }}>{initial}</span>
            <span className="truncate" style={{ fontSize: 12.5, fontWeight: 600, maxWidth: 120 }}>{displayName}</span>
          </button>
        ) : (
          <button
            onClick={() => setShowAuth(true)}
            className="flex items-center flex-none"
            style={{ gap: 6, padding: "6px 12px", borderRadius: RADIUS.pill, border: "1px solid rgba(255,255,255,.18)", background: "rgba(255,255,255,.12)", color: "#fff", fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}
          >
            <UserIcon size={15} />
            <span>Sign in{showHindi && <span style={{ color: "#C6D0E2", fontWeight: 500 }}> · साइन इन</span>}</span>
          </button>
        )}
      </div>

      {/* ── Center: big Report button ── */}
      <div className="flex flex-col items-center justify-center flex-1" style={{ gap: 20, padding: "12px 24px", overflowY: "auto" }}>
        <div style={{ fontSize: 12, letterSpacing: ".14em", textTransform: "uppercase", color: "#7D8DA9", fontWeight: 600, textAlign: "center" }}>
          Road accident first response
        </div>

        <button
          onClick={handleReport}
          aria-label="Report Incident"
          className="flex flex-col items-center justify-center active:scale-95 transition-transform"
          style={{
            width: "min(72vw, 258px)", aspectRatio: "1", borderRadius: "50%",
            border: "none", cursor: "pointer", color: "#fff", gap: 8,
            background: CTA_GRADIENT, boxShadow: "0 16px 44px rgba(198,54,44,.5)",
          }}
        >
          <AlertGlyph />
          <span style={{ fontSize: 21, fontWeight: 800, letterSpacing: "-.01em" }}>Report Incident</span>
          {showHindi && <span style={{ fontSize: 14, fontWeight: 600, opacity: 0.92 }}>रिपोर्ट करें</span>}
        </button>

        <p style={{ fontSize: 12.5, color: "#93A3BE", textAlign: "center", maxWidth: 300, lineHeight: 1.5 }}>
          Tap to talk to the AI dispatcher — it takes your report in English or Hindi.
        </p>

        <button
          onClick={handleDashboard}
          style={{ background: "transparent", border: "none", color: "#C6D0E2", fontSize: 14, fontWeight: 600, cursor: "pointer", padding: "8px 14px" }}
        >
          View dashboard{showHindi && <span style={{ color: "#7D8DA9", fontWeight: 500 }}> · डैशबोर्ड देखें</span>} ›
        </button>
      </div>

      {/* ── Footer (safe-area bottom) ── */}
      <div style={{ padding: "10px 20px calc(14px + env(safe-area-inset-bottom))", textAlign: "center", fontSize: 11, color: "#5F7093", letterSpacing: ".1em", textTransform: "uppercase", fontVariantNumeric: "tabular-nums" }}>
        Highway helpline 1033 · 210 km corridor
      </div>

      {/* ── Sign-in overlay — reuses the full auth screen (now portrait-safe) ── */}
      {showAuth && (
        <div className="fixed inset-0 z-[2600]" style={{ background: C.card }}>
          <AuthLanding
            showHindi={showHindi}
            initialTab={gateInitialTab}
            onGuest={() => { setShowAuth(false); handleDashboard(); }}
            onSignedUp={() => { setShowAuth(false); enterPwa(); beginOnboarding(); }}
            onSignedIn={() => setShowAuth(false)}
          />
        </div>
      )}
    </div>
  );
}

// White alert glyph for the report button (matches the incident-pin symbol).
function AlertGlyph() {
  return (
    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 3.2 22 20.5H2L12 3.2z" fill="rgba(255,255,255,.16)" stroke="#fff" strokeWidth={1.8} strokeLinejoin="round" />
      <path d="M12 9.5v4.5" stroke="#fff" strokeWidth={2.2} strokeLinecap="round" />
      <circle cx="12" cy="17" r="1.3" fill="#fff" />
    </svg>
  );
}
