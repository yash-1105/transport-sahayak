"use client";
import React, { useState } from "react";
import { C, RADIUS, SHADOW } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import { useAuthStore, useIsOperator, useIsAdmin } from "@/store/authStore";
import { UserIcon, ChevronDownIcon, LogOutIcon, ShieldCrossIcon } from "@/components/ui/icons";
import SafetyProfileSheet from "@/components/auth/SafetyProfileSheet";
import SurakshaMitraSheet from "@/components/auth/SurakshaMitraSheet";

type Overlay = null | "profile" | "mitra";

// Header auth control (lives next to the EN/हिं toggle in MapView's navy
// header). Signed-out → "Sign in" pill; signed-in → a compact name/email chip
// with a dropdown menu (safety profile / Suraksha Mitra / sign out). It also
// owns the auth modal + the two profile sheets so MapView only mounts one node.
export default function AuthControl() {
  const { showHindi } = useBilingual();
  const user = useAuthStore((s) => s.user);
  const configured = useAuthStore((s) => s.configured);
  const ready = useAuthStore((s) => s.ready);
  const signOut = useAuthStore((s) => s.signOut);
  const exitGuest = useAuthStore((s) => s.exitGuest);
  const isOperator = useIsOperator();
  const isAdmin = useIsAdmin();
  const staffLabel = isAdmin ? "Administrator" : "Operator";

  const [overlay, setOverlay] = useState<Overlay>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  // If Supabase isn't configured, don't render an auth affordance at all — the
  // rest of the app is unaffected.
  if (!configured) return null;

  const displayName =
    (user?.user_metadata?.full_name as string | undefined)?.trim() ||
    user?.email ||
    "";
  const initial = (displayName || "?").charAt(0).toUpperCase();

  function closeMenu() {
    setMenuOpen(false);
  }

  return (
    <>
      {!user ? (
        // ── Guest: "Sign in" pill → leave guest mode and return to the
        //    full-screen auth screen (on the sign-in tab), not a modal. ──
        <button
          onClick={() => exitGuest("signin")}
          aria-label="Sign in"
          title="Sign in"
          // Labelled pill on all sizes — with the app name + language toggle
          // gone from the mobile header there's room, and a labelled control
          // reads better than a bare icon.
          className="flex items-center justify-center py-1.5 px-3"
          style={{
            gap: 6,
            borderRadius: RADIUS.pill,
            border: "1px solid rgba(255,255,255,.18)",
            background: "rgba(255,255,255,.12)",
            color: "#fff",
            fontSize: 12.5,
            fontWeight: 600,
            cursor: "pointer",
            lineHeight: 1,
            opacity: ready ? 1 : 0.7,
          }}
        >
          <UserIcon size={15} />
          <span>Sign in{showHindi && <span style={{ color: "#C6D0E2", fontWeight: 500 }}> · साइन इन</span>}</span>
        </button>
      ) : (
        // ── Signed in: chip + dropdown ──
        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center"
            style={{
              gap: 7,
              padding: "4px 8px 4px 5px",
              borderRadius: RADIUS.pill,
              border: "1px solid rgba(255,255,255,.14)",
              background: "rgba(255,255,255,.08)",
              color: "#fff",
              cursor: "pointer",
              maxWidth: 190,
            }}
          >
            <span
              className="flex items-center justify-center flex-none"
              style={{ width: 24, height: 24, borderRadius: "50%", background: isOperator ? "linear-gradient(135deg,#E8862B,#C6362C)" : "linear-gradient(135deg,#2456A6,#173B77)", fontSize: 12, fontWeight: 700, color: "#fff" }}
            >
              {isOperator ? <ShieldCrossIcon size={14} /> : initial}
            </span>
            <span className="truncate hidden sm:block" style={{ fontSize: 12.5, fontWeight: 600, maxWidth: 140 }}>
              {isOperator ? staffLabel : displayName}
            </span>
            <ChevronDownIcon size={14} style={{ opacity: 0.75, flex: "none" }} />
          </button>

          {menuOpen && (
            <>
              {/* click-away layer */}
              <div className="fixed inset-0 z-[2040]" onClick={closeMenu} />
              <div
                className="absolute z-[2050]"
                style={{
                  top: "calc(100% + 8px)",
                  right: 0,
                  minWidth: 220,
                  background: "#fff",
                  border: `1px solid ${C.border}`,
                  borderRadius: RADIUS.card,
                  boxShadow: SHADOW.panel,
                  overflow: "hidden",
                }}
              >
                <div style={{ padding: "11px 14px", borderBottom: `1px solid ${C.hairline}` }}>
                  <div style={{ fontSize: 12.5, fontWeight: 700, color: C.ink }} className="truncate">
                    {isOperator ? staffLabel : displayName}
                  </div>
                  {isOperator && (
                    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.saffron, marginTop: 1 }}>
                      {isAdmin ? "Network-wide oversight" : "Dispatch console access"}
                    </div>
                  )}
                  {user.email && (isOperator || displayName !== user.email) && (
                    <div style={{ fontSize: 11, color: C.muted }} className="truncate">{user.email}</div>
                  )}
                </div>
                {/* Citizen account items are hidden for operators (staff, not a
                    citizen) — an operator only gets Sign out. */}
                {!isOperator && (
                  <>
                    <MenuItem icon={<ShieldCrossIcon size={16} />} label="My safety profile" hi="मेरी सुरक्षा प्रोफ़ाइल" showHindi={showHindi}
                      onClick={() => { closeMenu(); setOverlay("profile"); }} />
                    <MenuItem icon={<UserIcon size={16} />} label="Suraksha Mitra" hi="सुरक्षा मित्र" showHindi={showHindi}
                      onClick={() => { closeMenu(); setOverlay("mitra"); }} />
                    <div style={{ height: 1, background: C.hairline }} />
                  </>
                )}
                <MenuItem icon={<LogOutIcon size={16} />} label="Sign out" hi="साइन आउट" showHindi={showHindi} danger
                  onClick={async () => { closeMenu(); await signOut(); }} />
              </div>
            </>
          )}
        </div>
      )}

      {/* Overlays — signed-in account sheets only (guest sign-in goes to the
          full-screen gate; new-account onboarding is driven there by the gate). */}
      {overlay === "profile" && <SafetyProfileSheet showHindi={showHindi} onClose={() => setOverlay(null)} />}
      {overlay === "mitra" && <SurakshaMitraSheet showHindi={showHindi} onClose={() => setOverlay(null)} />}
    </>
  );
}

function MenuItem({
  icon, label, hi, showHindi, onClick, danger,
}: {
  icon: React.ReactNode;
  label: string;
  hi: string;
  showHindi: boolean;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className="ts-row flex items-center w-full"
      style={{ gap: 10, padding: "11px 14px", background: "transparent", cursor: "pointer", textAlign: "left", color: danger ? C.red : C.body }}
    >
      <span style={{ flex: "none", color: danger ? C.red : C.secondary, display: "flex" }}>{icon}</span>
      <span style={{ fontSize: 13, fontWeight: 500 }}>
        {label}
        {showHindi && <span style={{ color: danger ? "#D98B85" : C.muted, fontWeight: 400 }}> · {hi}</span>}
      </span>
    </button>
  );
}
