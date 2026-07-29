"use client";
import React, { useState } from "react";
import { C } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import { useAuthStore } from "@/store/authStore";
import { Sheet, InfoBanner, PrimaryButton } from "@/components/auth/ui";
import SafetyProfileSheet from "@/components/auth/SafetyProfileSheet";
import SurakshaMitraSheet from "@/components/auth/SurakshaMitraSheet";

// One-time post-SIGNUP guided flow. Rendered by AuthGate (only for real users)
// and active only while needsOnboarding is true — a returning sign-in never
// triggers it. It ORCHESTRATES the existing sheets (SafetyProfileSheet /
// SurakshaMitraSheet) as-is; it does not reimplement them.
type Step = "welcome" | "profile" | "mitraOffer" | "mitra";

// A muted text button used for the "skip / maybe later" secondary actions.
function SkipButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{ fontSize: 12.5, fontWeight: 600, color: C.secondary, cursor: "pointer", textAlign: "center", width: "100%", padding: "4px 0" }}
    >
      {children}
    </button>
  );
}

export default function OnboardingFlow() {
  const { showHindi } = useBilingual();
  const needsOnboarding = useAuthStore((s) => s.needsOnboarding);
  const finishOnboarding = useAuthStore((s) => s.finishOnboarding);
  const [step, setStep] = useState<Step>("welcome");

  if (!needsOnboarding) return null;

  if (step === "welcome") {
    return (
      <Sheet title="Welcome" hiTitle="स्वागत है" showHindi={showHindi} onClose={finishOnboarding} maxWidth={460}>
        <div className="flex flex-col" style={{ gap: 14, padding: "16px 20px 20px" }}>
          <InfoBanner tone="green">
            You&apos;re all set and signed in. Let&apos;s quickly set up your <b>safety profile</b> — blood group,
            emergency contacts and medical notes — so responders can help you faster in the golden hour.
            {showHindi && (
              <span style={{ display: "block", marginTop: 3, opacity: 0.9 }}>
                आइए आपकी सुरक्षा प्रोफ़ाइल तैयार करें — रक्त समूह, आपातकालीन संपर्क।
              </span>
            )}
          </InfoBanner>
          <PrimaryButton onClick={() => setStep("profile")}>
            Set up safety profile · जारी रखें
          </PrimaryButton>
          <SkipButton onClick={finishOnboarding}>Skip for now · अभी नहीं</SkipButton>
        </div>
      </Sheet>
    );
  }

  if (step === "profile") {
    // Reuse the existing sheet as-is; closing it (save or skip via ✕) advances
    // to the Suraksha Mitra offer.
    return <SafetyProfileSheet showHindi={showHindi} onClose={() => setStep("mitraOffer")} />;
  }

  if (step === "mitraOffer") {
    return (
      <Sheet title="Become a Suraksha Mitra?" hiTitle="सुरक्षा मित्र बनें?" showHindi={showHindi} onClose={finishOnboarding} maxWidth={460}>
        <div className="flex flex-col" style={{ gap: 14, padding: "16px 20px 20px" }}>
          <InfoBanner tone="saffron">
            In the golden hour after a crash, a trained neighbour who reaches the scene first can save lives.
            Register as a <b>community volunteer first-responder</b> — India&apos;s Good Samaritan law protects those
            who help. This is a <b>registration only</b>; it never dispatches or activates you.
            {showHindi && (
              <span style={{ display: "block", marginTop: 3, opacity: 0.9 }}>
                सामुदायिक स्वयंसेवक के रूप में पंजीकरण करें — यह केवल एक रिकॉर्ड है।
              </span>
            )}
          </InfoBanner>
          <PrimaryButton onClick={() => setStep("mitra")}>
            Register as Suraksha Mitra · पंजीकरण करें
          </PrimaryButton>
          <SkipButton onClick={finishOnboarding}>Maybe later · बाद में</SkipButton>
        </div>
      </Sheet>
    );
  }

  // step === "mitra": reuse the registration sheet; closing it finishes onboarding.
  return <SurakshaMitraSheet showHindi={showHindi} onClose={finishOnboarding} />;
}
