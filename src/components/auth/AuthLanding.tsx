"use client";
import React, { useState } from "react";
import { ShieldCrossIcon } from "@/components/ui/icons";
import { ErrorBanner, InfoBanner } from "@/components/auth/ui";
import { useAuthForm } from "@/components/auth/useAuthForm";
import OperatorSignIn from "@/components/auth/OperatorSignIn";
import AdminSignIn from "@/components/auth/AdminSignIn";

// Redesigned full-screen auth screen (design handoff: design_handoff_login).
// Split layout — animated navy brand panel (left) + light form panel (right).
// Presentation only: every field / action is driven by the shared useAuthForm
// hook, so sign-up, sign-in and the guest bypass behave exactly as before.

const ROUTE_PATH = "M40 320 C 120 300, 90 220, 150 190 C 215 158, 180 90, 240 44";

export default function AuthLanding({
  showHindi,
  onGuest,
  onSignedUp,
  onSignedIn,
  initialTab = "signup",
}: {
  showHindi: boolean;
  onGuest: () => void;
  onSignedUp: () => void;
  onSignedIn: () => void;
  initialTab?: "signup" | "signin";
}) {
  const f = useAuthForm({ onSignedUp, onSignedIn, initialTab });
  const [operatorOpen, setOperatorOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);

  const kickerEn = f.isSignup ? "Citizen safety profile" : "Registered citizen";
  const kickerHi = f.isSignup ? "नागरिक सुरक्षा प्रोफ़ाइल" : "पंजीकृत नागरिक";
  const titleEn = f.isSignup ? "Create your account" : "Welcome back";
  const subEn = f.isSignup ? "A short register — three lines, no OTP." : "Sign in to your safety profile.";
  const ctaEn = f.isSignup ? "Create account" : "Sign in";
  const ctaHi = f.isSignup ? "खाता बनाएँ" : "साइन इन";
  const swapPromptEn = f.isSignup ? "Already have an account?" : "New to Transport Sahayak?";
  const swapLinkEn = f.isSignup ? "Sign in" : "Create an account";

  const seg = (on: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "9px 6px",
    border: "none",
    borderRadius: 8,
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 600,
    transition: "background .15s,color .15s,box-shadow .15s",
    background: on ? "#fff" : "transparent",
    color: on ? "#14243E" : "#8A8578",
    boxShadow: on ? "0 1px 4px rgba(28,36,52,.12)" : "none",
  });

  const inputStyle: React.CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    padding: "11px 13px",
    border: "1px solid #E4E1D8",
    borderRadius: 10,
    fontSize: 13.5,
    background: "#fff",
    color: "#1C2434",
    transition: "border-color .15s,box-shadow .15s",
  };

  return (
    <>
    <div className="lg-auth">
      {/* ══ Navy brand panel ══ */}
      <aside className="lg-brand">
        {/* blueprint grid */}
        <div className="lg-grid" aria-hidden="true" />
        {/* ghost numeral */}
        <div className="lg-ghost" aria-hidden="true">1033</div>

        {/* brand block */}
        <div style={{ position: "relative", animation: "lgUp .7s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div
              className="lg-shield"
              style={{
                width: 46, height: 46, borderRadius: 12, flex: "none",
                background: "linear-gradient(135deg,#E8862B,#C6362C)",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              <ShieldCrossIcon size={24} style={{ color: "#fff" }} />
            </div>
            <div>
              <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-.01em" }}>Transport Sahayak</div>
              <div style={{ fontSize: 14, color: "#93A3BE" }}>परिवहन सहायक</div>
            </div>
          </div>
          <div
            className="lg-rule"
            style={{
              width: 64, height: 2, borderRadius: 1, margin: "24px 0 18px",
              background: "linear-gradient(90deg,#E8862B,#C6362C)",
              transformOrigin: "left", animation: "lgRule 1s .5s ease both",
            }}
          />
          <p className="lg-tagline" style={{ fontSize: 14, maxWidth: "36ch", color: "#B9C4D8", lineHeight: 1.7, margin: 0 }}>
            Every minute of the golden hour, accounted for — from the first report on the Delhi–Dehradun
            Expressway to the nearest ready hospital.
          </p>
        </div>

        {/* animated route + chips (hidden on mobile) */}
        <div className="lg-brand-visual" style={{ position: "relative", flex: 1, display: "flex", alignItems: "flex-end", minHeight: 240 }}>
          <svg viewBox="0 0 300 340" style={{ width: "min(310px,84%)", height: "auto", overflow: "visible" }} aria-hidden="true">
            {/* road bed */}
            <path d={ROUTE_PATH} fill="none" stroke="rgba(255,255,255,.12)" strokeWidth={5} strokeLinecap="round" />
            {/* saffron draw-in */}
            <path d={ROUTE_PATH} fill="none" stroke="#E8862B" strokeWidth={1.8} strokeDasharray={980} style={{ animation: "lgDraw 2.8s .3s cubic-bezier(.5,0,.2,1) both" }} />
            {/* flowing traffic dashes */}
            <path className="lg-loop" d={ROUTE_PATH} fill="none" stroke="rgba(255,255,255,.5)" strokeWidth={1.4} strokeDasharray="3 41" style={{ animation: "lgFlow 1.6s 3.2s linear infinite" }} />
            {/* city markers */}
            <g fontFamily="'IBM Plex Sans', sans-serif" fontSize={12} fontWeight={600} fill="#B9C4D8">
              <g style={{ animation: "lgCity .6s 1s ease both" }}>
                <circle cx={40} cy={320} r={4} fill="#14243E" stroke="#E8862B" strokeWidth={1.6} />
                <text x={56} y={325}>Delhi</text>
              </g>
              <g style={{ animation: "lgCity .6s 1.6s ease both" }}>
                <circle cx={108} cy={243} r={3} fill="#14243E" stroke="rgba(255,255,255,.5)" strokeWidth={1.2} />
                <text x={122} y={248} fill="rgba(185,196,216,.75)" fontWeight={500}>Baraut</text>
              </g>
              <g style={{ animation: "lgCity .6s 2.2s ease both" }}>
                <circle cx={195} cy={130} r={3} fill="#14243E" stroke="rgba(255,255,255,.5)" strokeWidth={1.2} />
                <text x={209} y={135} fill="rgba(185,196,216,.75)" fontWeight={500}>Saharanpur</text>
              </g>
              <g style={{ animation: "lgCity .6s 2.8s ease both" }}>
                <circle cx={240} cy={44} r={4} fill="#14243E" stroke="#E8862B" strokeWidth={1.6} />
                <text x={254} y={49}>Dehradun</text>
              </g>
            </g>
            {/* red beacon at Baraut */}
            <circle className="lg-loop" cx={108} cy={243} r={5} fill="none" stroke="#C6362C" strokeWidth={1.4} style={{ animation: "lgBeacon 2.2s 3.4s ease-out infinite" }} />
            {/* traveling dot */}
            <circle className="lg-loop" r={3.4} fill="#E8862B" style={{ offsetPath: `path('${ROUTE_PATH}')`, animation: "lgTravel 7s 3.2s linear infinite" } as React.CSSProperties} />
          </svg>

          {/* floating stat chips */}
          <div style={{ position: "absolute", right: 0, top: 24, display: "flex", flexDirection: "column", gap: 10, animation: "lgUp .7s 1.2s ease both" }}>
            <Chip dot="#4CAF7D" delay="0s">Highway helpline <b style={{ fontVariantNumeric: "tabular-nums" }}>1033</b></Chip>
            <Chip dot="#E8862B" delay="1.4s">Severity assessed in seconds</Chip>
            <Chip dot="#6E9BE0" delay="2.8s">EN · हिंदी voice reporting</Chip>
          </div>
        </div>

        {/* footer caption */}
        <div className="lg-brand-footer" style={{ position: "relative", fontSize: 11, letterSpacing: ".14em", textTransform: "uppercase", color: "#5F7093", animation: "lgUp .7s 1.5s ease both", fontVariantNumeric: "tabular-nums" }}>
          Road accident first response · 210 km corridor
        </div>
      </aside>

      {/* ══ Form panel ══ */}
      <main className="lg-form">
        <div style={{ width: "min(400px,100%)" }}>
          {/* kicker / title / sub */}
          <div style={{ animation: "lgUp .6s .15s ease both" }}>
            <div style={{ fontSize: 10.5, letterSpacing: ".16em", textTransform: "uppercase", fontWeight: 600, color: "#8A5A17", marginBottom: 8 }}>
              {kickerEn}
              {showHindi && <> · <span style={{ letterSpacing: ".04em" }}>{kickerHi}</span></>}
            </div>
            <h2 style={{ fontSize: 27, fontWeight: 700, letterSpacing: "-.01em", margin: "0 0 4px", color: "#1C2434" }}>{titleEn}</h2>
            <p style={{ fontSize: 13.5, color: "#8A8578", margin: "0 0 22px" }}>{subEn}</p>
          </div>

          {/* segmented toggle */}
          <div style={{ display: "flex", gap: 4, background: "#EAE7DF", borderRadius: 11, padding: 4, marginBottom: 20, animation: "lgUp .6s .25s ease both" }}>
            <button className="lg-seg" onClick={() => f.switchTab("signup")} style={seg(f.isSignup)}>
              Sign up <span style={{ fontSize: 11, fontWeight: 500, opacity: 0.65 }}>· साइन अप</span>
            </button>
            <button className="lg-seg" onClick={() => f.switchTab("signin")} style={seg(!f.isSignup)}>
              Sign in <span style={{ fontSize: 11, fontWeight: 500, opacity: 0.65 }}>· साइन इन</span>
            </button>
          </div>

          {/* form — re-animates (short fade+rise) on every mode switch via the
              changing animation-name (lgUp <-> lgUpB) */}
          <div style={{ animation: `${f.isSignup ? "lgUp" : "lgUpB"} .45s ease both` }}>
            {f.isSignup && (
              <div style={{ marginBottom: 16 }}>
                <label style={labelStyle}>
                  Full name{showHindi && <span style={sufStyle}> · पूरा नाम</span>}
                </label>
                <input
                  value={f.fullName}
                  onChange={(e) => f.setFullName(e.target.value)}
                  placeholder="e.g. Anjali Sharma"
                  autoComplete="name"
                  style={inputStyle}
                />
              </div>
            )}

            <div style={{ marginBottom: 16 }}>
              <label style={labelStyle}>
                Email <span style={{ color: "#C6362C" }}>*</span>{showHindi && <span style={sufStyle}> · ईमेल</span>}
              </label>
              <input
                type="email"
                value={f.email}
                onChange={(e) => f.setEmail(e.target.value)}
                placeholder="you@example.com"
                autoComplete="email"
                inputMode="email"
                style={inputStyle}
              />
            </div>

            <div style={{ marginBottom: 20 }}>
              <label style={{ ...labelStyle, display: "flex", justifyContent: "space-between" }}>
                <span>
                  Password <span style={{ color: "#C6362C" }}>*</span>{showHindi && <span style={sufStyle}> · पासवर्ड</span>}
                </span>
                <button
                  type="button"
                  onClick={() => f.setShowPw(!f.showPw)}
                  style={{ fontSize: 12, fontWeight: 500, color: "#2456A6", cursor: "pointer" }}
                >
                  {f.showPw ? "Hide" : "Show"}
                </button>
              </label>
              <input
                type={f.showPw ? "text" : "password"}
                value={f.password}
                onChange={(e) => f.setPassword(e.target.value)}
                placeholder={f.isSignup ? "At least 6 characters" : "Your password"}
                autoComplete={f.isSignup ? "new-password" : "current-password"}
                onKeyDown={(e) => { if (e.key === "Enter") f.submit(); }}
                style={inputStyle}
              />
            </div>

            {f.error && <div style={{ marginBottom: 14 }}><ErrorBanner>{f.error}</ErrorBanner></div>}
            {f.info && <div style={{ marginBottom: 14 }}><InfoBanner tone="saffron">{f.info}</InfoBanner></div>}

            <button
              className="lg-cta"
              onClick={f.submit}
              disabled={f.busy}
              style={{
                width: "100%", padding: 14, border: "none", borderRadius: 12,
                background: "linear-gradient(135deg,#D14036,#B92C22)", color: "#fff",
                fontSize: 14.5, fontWeight: 700, cursor: f.busy ? "default" : "pointer",
                boxShadow: "0 6px 20px rgba(198,54,44,.3)", opacity: f.busy ? 0.85 : 1,
              }}
            >
              {f.busy ? "Working…" : (<>{ctaEn} <span style={{ fontWeight: 500, opacity: 0.85 }}>· {ctaHi}</span></>)}
            </button>

            <p style={{ fontSize: 13, color: "#8A8578", textAlign: "center", margin: "14px 0 0" }}>
              {swapPromptEn}{" "}
              <button onClick={f.toggleTab} style={{ fontWeight: 600, color: "#2456A6", cursor: "pointer" }}>{swapLinkEn}</button>
            </p>
          </div>

          {/* emergency divider + guest */}
          <div style={{ animation: "lgUp .6s .45s ease both" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "22px 0 16px" }}>
              <span style={{ flex: 1, height: 1, background: "#E4E1D8" }} />
              <span style={{ fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: "#8A8578", fontWeight: 600 }}>In an emergency</span>
              <span style={{ flex: 1, height: 1, background: "#E4E1D8" }} />
            </div>
            <button
              className="lg-guest"
              onClick={onGuest}
              style={{
                display: "flex", alignItems: "center", justifyContent: "center", width: "100%",
                boxSizing: "border-box", padding: 12, border: "1.5px solid #14243E", borderRadius: 12,
                color: "#14243E", fontSize: 13.5, fontWeight: 600, cursor: "pointer", background: "transparent",
              }}
            >
              <span style={{ textAlign: "center" }}>
                Continue as guest — report an emergency
                {showHindi && <span style={{ fontWeight: 400, color: "#5B6572" }}> · अतिथि के रूप में जारी रखें</span>}
              </span>
            </button>
            <p style={{ fontSize: 12, color: "#8A8578", textAlign: "center", margin: "10px 0 0" }}>
              You can report incidents without an account.
            </p>
            <p style={{ fontSize: 11, color: "#B4AFA2", textAlign: "center", margin: "22px 0 0", lineHeight: 1.5 }}>
              Prototype — accounts store a private safety profile behind sign-in. Emergency reporting never requires an account.
            </p>
          </div>
        </div>
      </main>

      {/* Quiet operator ("admin") entry — deliberately low-visibility, corner. */}
      <button
        className="lg-operator-link"
        onClick={() => setOperatorOpen(true)}
        title="Operator access"
        style={{
          position: "fixed", right: 16, bottom: "max(12px, env(safe-area-inset-bottom))", zIndex: 5,
          display: "flex", alignItems: "center", gap: 6,
          background: "transparent", color: "#B4AFA2", fontSize: 11, fontWeight: 500,
          cursor: "pointer", padding: 6, lineHeight: 1,
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="11" width="18" height="10" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
        Operator access
      </button>

      {/* Even quieter administrator entry (bottom-left corner). */}
      <button
        className="lg-operator-link"
        onClick={() => setAdminOpen(true)}
        title="Administrator access"
        style={{
          position: "fixed", left: 16, bottom: "max(12px, env(safe-area-inset-bottom))", zIndex: 5,
          display: "flex", alignItems: "center", gap: 6,
          background: "transparent", color: "#B4AFA2", fontSize: 11, fontWeight: 500,
          cursor: "pointer", padding: 6, lineHeight: 1,
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
        Administrator access
      </button>
    </div>

    {operatorOpen && <OperatorSignIn onClose={() => setOperatorOpen(false)} />}
    {adminOpen && <AdminSignIn onClose={() => setAdminOpen(false)} />}
    </>
  );
}

const labelStyle: React.CSSProperties = { display: "block", fontSize: 12.5, fontWeight: 600, marginBottom: 6, color: "#1C2434" };
const sufStyle: React.CSSProperties = { fontWeight: 400, color: "#8A8578" };

function Chip({ dot, delay, children }: { dot: string; delay: string; children: React.ReactNode }) {
  return (
    <div
      className="lg-loop"
      style={{
        display: "flex", alignItems: "center", gap: 9,
        background: "rgba(255,255,255,.06)", border: "1px solid rgba(255,255,255,.1)",
        borderRadius: 10, padding: "9px 14px", animation: `lgFloat 5s ${delay} ease-in-out infinite`,
      }}
    >
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: dot, flex: "none" }} />
      <span style={{ fontSize: 12, color: "#D5DCE8" }}>{children}</span>
    </div>
  );
}
