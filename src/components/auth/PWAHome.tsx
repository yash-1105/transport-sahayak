"use client";
import React, { useState } from "react";
import { C, RADIUS, CTA_GRADIENT, BRAND_GRADIENT, SHADOW } from "@/lib/design";
import { ShieldCrossIcon, UserIcon, MicIcon } from "@/components/ui/icons";
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
  const [chooseLang, setChooseLang] = useState(false);

  const displayName =
    ((user?.user_metadata?.full_name as string | undefined)?.trim() || user?.email || "").trim();
  const initial = (displayName || "?").charAt(0).toUpperCase();

  // SOS → first ask which language the voice dispatcher should speak.
  function handleSos() {
    setChooseLang(true);
  }
  // Report Incident → open the full report sheet (manual/describe form) in the
  // app, mirroring the map's navy "Report Incident" FAB. Emergencies are never
  // blocked, so a signed-out user is dropped into guest mode first.
  function startReport() {
    if (!user) continueAsGuest();
    setLaunchIntent("report");
    enterPwa();
  }
  // Language chosen → into the voice dispatcher in that language. Emergencies
  // must never be blocked, so a signed-out user is dropped into guest mode first.
  function startVoice(locale: "en-IN" | "hi-IN") {
    if (!user) continueAsGuest();
    setLaunchIntent("voice", locale);
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
      style={{ background: C.page, color: C.ink }}
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
            <div style={{ fontSize: 15, fontWeight: 700, color: C.ink }} className="truncate">Transport Sahayak</div>
            {showHindi && <div style={{ fontSize: 12, color: C.muted }}>परिवहन सहायक</div>}
          </div>
        </div>

        {user ? (
          <button
            onClick={handleProfile}
            className="flex items-center flex-none"
            style={{ gap: 7, padding: "4px 10px 4px 5px", borderRadius: RADIUS.pill, border: `1px solid ${C.border}`, background: "#fff", color: C.ink, cursor: "pointer", maxWidth: 170 }}
          >
            <span className="flex items-center justify-center flex-none" style={{ width: 24, height: 24, borderRadius: "50%", background: "linear-gradient(135deg,#2456A6,#173B77)", fontSize: 12, fontWeight: 700, color: "#fff" }}>{initial}</span>
            <span className="truncate" style={{ fontSize: 12.5, fontWeight: 600, maxWidth: 120 }}>{displayName}</span>
          </button>
        ) : (
          <button
            onClick={() => setShowAuth(true)}
            className="flex items-center flex-none"
            style={{ gap: 6, padding: "6px 12px", borderRadius: RADIUS.pill, border: `1px solid ${C.border}`, background: "#fff", color: C.body, fontSize: 12.5, fontWeight: 600, cursor: "pointer" }}
          >
            <UserIcon size={15} />
            <span>Sign in{showHindi && <span style={{ color: C.muted, fontWeight: 500 }}> · साइन इन</span>}</span>
          </button>
        )}
      </div>

      {/* ── Center: Report button, or the language choice once tapped ── */}
      <div className="flex flex-col items-center justify-center flex-1" style={{ gap: 20, padding: "12px 24px", overflowY: "auto" }}>
        {chooseLang ? (
          <>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.ink }}>
                Choose a language{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 15 }}> · भाषा चुनें</span>}
              </div>
              <p style={{ fontSize: 12.5, color: C.secondary, marginTop: 4 }}>Which language should the voice dispatcher speak?</p>
            </div>

            <div className="flex flex-col" style={{ gap: 12, width: "min(86vw, 320px)" }}>
              <LangButton primary onClick={() => startVoice("en-IN")} title="English" sub="Talk to the dispatcher in English" />
              <LangButton onClick={() => startVoice("hi-IN")} title="हिंदी" sub="वॉइस डिस्पैचर से हिंदी में बात करें" />
            </div>

            <button
              onClick={() => setChooseLang(false)}
              style={{ background: "transparent", border: "none", color: C.secondary, fontSize: 13, fontWeight: 600, cursor: "pointer", padding: "6px 14px" }}
            >
              ‹ Back
            </button>
          </>
        ) : (
          <>
            <div style={{ fontSize: 12, letterSpacing: ".14em", textTransform: "uppercase", color: C.muted, fontWeight: 600, textAlign: "center" }}>
              Road accident first response
            </div>

            {/* SOS — emergency voice dispatch. The big red pulsing CTA (primary),
                matching the map's SOS FAB. */}
            <button
              onClick={handleSos}
              aria-label="SOS — talk to the voice dispatcher"
              className="flex flex-col items-center justify-center active:scale-95 transition-transform"
              style={{
                width: "min(64vw, 224px)", aspectRatio: "1", borderRadius: "50%",
                border: "none", cursor: "pointer", color: "#fff", gap: 8,
                background: CTA_GRADIENT, boxShadow: "0 18px 44px rgba(198,54,44,.34)",
                animation: "tsPulse 2.6s infinite",
              }}
            >
              <MicIcon size={40} />
              <span style={{ fontSize: 27, fontWeight: 800, letterSpacing: "-.01em" }}>
                SOS{showHindi && <span style={{ fontSize: 17, fontWeight: 600, opacity: 0.92 }}> · एसओएस</span>}
              </span>
            </button>

            <p style={{ fontSize: 12.5, color: C.secondary, textAlign: "center", maxWidth: 300, lineHeight: 1.5 }}>
              Tap to talk to the AI dispatcher — it takes your report in English or Hindi.
            </p>

            {/* Report Incident — the full report sheet (secondary, navy), matching
                the map's second FAB. */}
            <button
              onClick={startReport}
              className="flex items-center justify-center active:scale-95 transition-transform"
              style={{
                gap: 9, width: "min(86vw, 320px)", padding: "13px 20px", borderRadius: 14,
                background: C.navy800, color: "#fff", border: "none", cursor: "pointer",
                fontSize: 15, fontWeight: 700, boxShadow: SHADOW.floatBtn,
              }}
            >
              <span className="inline-flex items-center justify-center flex-none" style={{ width: 21, height: 21, borderRadius: "50%", background: "rgba(255,255,255,.18)", fontSize: 15, fontWeight: 600 }}>+</span>
              Report Incident{showHindi && <span style={{ fontWeight: 500, opacity: 0.82 }}> · रिपोर्ट करें</span>}
            </button>

            <button
              onClick={handleDashboard}
              style={{ background: "transparent", border: "none", color: C.blue, fontSize: 14, fontWeight: 600, cursor: "pointer", padding: "6px 14px" }}
            >
              View dashboard{showHindi && <span style={{ color: C.muted, fontWeight: 500 }}> · डैशबोर्ड देखें</span>} ›
            </button>
          </>
        )}
      </div>

      {/* ── Footer (safe-area bottom) ── */}
      <div style={{ padding: "10px 20px calc(14px + env(safe-area-inset-bottom))", textAlign: "center", fontSize: 11, color: C.faint, letterSpacing: ".1em", textTransform: "uppercase", fontVariantNumeric: "tabular-nums" }}>
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

// Language choice tile (English / हिंदी) for the voice dispatcher.
function LangButton({ title, sub, onClick, primary }: { title: string; sub: string; onClick: () => void; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      className="flex flex-col active:scale-95 transition-transform"
      style={{
        textAlign: "left", padding: "14px 18px", borderRadius: 14, cursor: "pointer", gap: 2,
        color: primary ? "#fff" : C.ink,
        border: primary ? "none" : `1px solid ${C.border}`,
        background: primary ? CTA_GRADIENT : "#fff",
        boxShadow: primary ? "0 8px 24px rgba(198,54,44,.32)" : SHADOW.floatBtn,
      }}
    >
      <span style={{ fontSize: 17, fontWeight: 700 }}>{title}</span>
      <span style={{ fontSize: 12, color: primary ? "rgba(255,255,255,.85)" : C.muted, fontWeight: 500 }}>{sub}</span>
    </button>
  );
}
