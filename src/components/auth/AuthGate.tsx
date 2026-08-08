"use client";
import React from "react";
import { SHADOW, HEADER_GRADIENT, BRAND_GRADIENT } from "@/lib/design";
import { ShieldCrossIcon } from "@/components/ui/icons";
import { useBilingual } from "@/hooks/useI18n";
import { useAuthStore } from "@/store/authStore";
import AuthLanding from "@/components/auth/AuthLanding";
import PWAHome from "@/components/auth/PWAHome";
import OnboardingFlow from "@/components/auth/OnboardingFlow";

// Full-screen sign-in gate in front of the app. The children (MapView, mounted
// via MapLoader's ssr:false dynamic import) only render once the user is signed
// in OR has chosen to continue as a guest — so the map / responder fetches never
// start behind the gate.
//
// Degraded mode: if Supabase isn't configured, there is NO gate — the app
// renders exactly as before (accounts are simply unavailable).
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { showHindi } = useBilingual();
  const configured = useAuthStore((s) => s.configured);
  const ready = useAuthStore((s) => s.ready);
  const user = useAuthStore((s) => s.user);
  const isGuest = useAuthStore((s) => s.isGuest);
  const continueAsGuest = useAuthStore((s) => s.continueAsGuest);
  const beginOnboarding = useAuthStore((s) => s.beginOnboarding);
  const gateInitialTab = useAuthStore((s) => s.gateInitialTab);
  const pwaEntered = useAuthStore((s) => s.pwaEntered);

  // No Supabase → never gate (accounts unavailable — app renders as-is, and no
  // launch home either, preserving the unconfigured behaviour).
  if (!configured) return <>{children}</>;

  // Hydrating a persisted session → branded splash, so a signed-in user never
  // flashes the gate. (Checked BEFORE user/guest so server + first client render
  // agree — avoids a hydration mismatch on the sessionStorage-backed guest flag.)
  if (!ready) return <Splash />;

  // Universal entry: the SOS launch home is the landing on ALL form factors —
  // desktop web, mobile web, AND installed PWA — not just standalone display mode.
  // pwaEntered flips once the user leaves it (in-memory, so a fresh load returns
  // here). From here they start an SOS call, open the report, view the dashboard
  // (guest), or sign in (AuthLanding overlay, incl. operator access) — every
  // gate/guest/operator flow preserved.
  if (!pwaEntered) return <PWAHome />;

  // Signed in or guest → the app. Onboarding overlay only for real users.
  if (user || isGuest) {
    return (
      <>
        {children}
        {user && <OnboardingFlow />}
      </>
    );
  }

  // Otherwise → the redesigned split-screen landing / auth screen.
  return (
    <AuthLanding
      showHindi={showHindi}
      onGuest={continueAsGuest}
      onSignedUp={beginOnboarding}
      onSignedIn={() => { /* the app reveals itself once the session lands */ }}
      initialTab={gateInitialTab}
    />
  );
}

function Splash() {
  return (
    <div
      className="fixed inset-0 z-[3000] flex flex-col items-center justify-center"
      style={{ background: HEADER_GRADIENT, gap: 16 }}
    >
      <div
        className="inline-flex items-center justify-center"
        style={{ width: 56, height: 56, borderRadius: 14, background: BRAND_GRADIENT, boxShadow: SHADOW.floatBtn }}
      >
        <ShieldCrossIcon size={30} style={{ color: "#fff" }} />
      </div>
      <div style={{ fontSize: 17, fontWeight: 600, color: "#fff" }}>Transport Sahayak</div>
      <div
        className="rounded-full animate-spin"
        style={{ width: 22, height: 22, border: "2.5px solid rgba(255,255,255,.25)", borderTopColor: "#fff" }}
      />
    </div>
  );
}
