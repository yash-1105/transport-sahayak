"use client";
import React from "react";
import { C, CTA_GRADIENT, RADIUS } from "@/lib/design";

// Shared overlay UI for the account screens (auth modal, safety profile,
// Suraksha Mitra). These mirror the sheet + input styling from
// ReportPanel.tsx so the account screens look native to the redesign — same
// scrim, same bottom-sheet, same field styling, same CTA gradient. Kept in a
// small local module rather than importing ReportPanel's internal helpers
// (which aren't exported).

// ── Bottom sheet (scrim + slide-up), matching ReportPanel's ts-report-sheet ───
export function Sheet({
  title,
  hiTitle,
  showHindi,
  onClose,
  children,
  busy = false,
  maxWidth = 560,
}: {
  title: string;
  hiTitle?: string;
  showHindi: boolean;
  onClose: () => void;
  children: React.ReactNode;
  busy?: boolean;
  maxWidth?: number;
}) {
  return (
    <>
      {/* Scrim */}
      <div
        className="fixed inset-0 z-[2099]"
        style={{ background: "rgba(14,26,47,.4)", backdropFilter: "blur(2px)" }}
        onClick={busy ? undefined : onClose}
      />
      {/* Sheet — margin-auto centering so the tsUp translateY animation is clean */}
      <div
        className="ts-report-sheet fixed bottom-0 z-[2100] flex flex-col bg-white"
        style={{
          left: 0,
          right: 0,
          margin: "0 auto",
          width: `min(${maxWidth}px, 96vw)`,
          maxHeight: "88%",
          borderRadius: "18px 18px 0 0",
          boxShadow: "0 -12px 48px rgba(14,26,47,.3)",
          animation: "tsUp .28s ease",
        }}
      >
        {/* Header + drag handle */}
        <div className="relative flex items-center flex-shrink-0" style={{ gap: 12, padding: "14px 20px 12px", borderBottom: `1px solid ${C.hairline}` }}>
          <div style={{ position: "absolute", left: "50%", top: 7, transform: "translateX(-50%)", width: 44, height: 4, borderRadius: 2, background: C.border }} />
          <div className="flex-1 min-w-0">
            <span style={{ fontSize: 15, fontWeight: 700, color: C.ink }}>{title}</span>
            {showHindi && hiTitle && (
              <span style={{ fontSize: 13, color: C.muted, fontWeight: 500 }}> · {hiTitle}</span>
            )}
          </div>
          {!busy && (
            <button
              onClick={onClose}
              aria-label="Close"
              style={{ width: 30, height: 30, border: `1px solid ${C.border}`, borderRadius: 8, background: C.inset, color: C.secondary, fontSize: 14, cursor: "pointer", flex: "none" }}
            >
              ✕
            </button>
          )}
        </div>
        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
          {children}
        </div>
      </div>
    </>
  );
}

// ── Field label (12.5/600) with optional muted Hindi/hint suffix ──────────────
export function FieldLabel({ children, suffix, required }: { children: React.ReactNode; suffix?: React.ReactNode; required?: boolean }) {
  return (
    <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6, color: C.ink }}>
      {children}
      {required && <span style={{ color: C.red }}> *</span>}
      {suffix && <span style={{ fontWeight: 400, color: C.muted }}> {suffix}</span>}
    </div>
  );
}

const inputBase: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "11px 13px",
  border: `1px solid ${C.border}`,
  borderRadius: RADIUS.input,
  fontSize: 13.5,
  background: C.inset,
  outline: "none",
  color: C.ink,
};

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const { style, ...rest } = props;
  return <input {...rest} style={{ ...inputBase, ...style }} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { style, ...rest } = props;
  return <textarea {...rest} style={{ ...inputBase, resize: "vertical", minHeight: 64, ...style }} />;
}

export function Select({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ ...inputBase, cursor: "pointer", color: value ? C.ink : C.muted }}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value} style={{ color: C.ink }}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

// Field wrapper: label + control, stacked.
export function Field({
  label,
  suffix,
  required,
  children,
}: {
  label: React.ReactNode;
  suffix?: React.ReactNode;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <FieldLabel suffix={suffix} required={required}>
        {label}
      </FieldLabel>
      {children}
    </div>
  );
}

// Checkbox row — pill-ish inset card with a square check.
export function CheckboxRow({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        width: "100%",
        textAlign: "left",
        padding: "11px 13px",
        border: `1px solid ${checked ? C.blueSoftBorder : C.border}`,
        background: checked ? C.blueSoftBg : "#fff",
        borderRadius: RADIUS.input,
        cursor: "pointer",
      }}
    >
      <span
        style={{
          flex: "none",
          width: 18,
          height: 18,
          marginTop: 1,
          borderRadius: 5,
          border: `1.5px solid ${checked ? C.blue : C.faint}`,
          background: checked ? C.blue : "#fff",
          color: "#fff",
          fontSize: 12,
          lineHeight: "15px",
          textAlign: "center",
        }}
      >
        {checked ? "✓" : ""}
      </span>
      <span style={{ fontSize: 12.5, color: checked ? C.blueSoftText : C.body, lineHeight: 1.4 }}>{children}</span>
    </button>
  );
}

// Yes/no toggle (two-segment), styled like the report panel's Segmented.
export function Toggle({
  value,
  onChange,
  onLabel = "Yes",
  offLabel = "No",
}: {
  value: boolean;
  onChange: (v: boolean) => void;
  onLabel?: string;
  offLabel?: string;
}) {
  return (
    <div className="flex" style={{ gap: 6, background: C.page, borderRadius: RADIUS.input, padding: 4, maxWidth: 200 }}>
      {[
        { on: false, label: offLabel },
        { on: true, label: onLabel },
      ].map((opt) => {
        const active = opt.on === value;
        return (
          <button
            key={opt.label}
            type="button"
            onClick={() => onChange(opt.on)}
            style={{
              flex: 1,
              padding: "7px 0",
              border: "none",
              borderRadius: 7,
              cursor: "pointer",
              fontSize: 13,
              fontWeight: active ? 600 : 500,
              background: active ? "#fff" : "transparent",
              color: active ? C.ink : C.secondary,
              boxShadow: active ? "0 1px 2px rgba(0,0,0,.08)" : "none",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// Primary CTA (red gradient), full width. Not location-gated like ReportPanel's.
export function PrimaryButton({
  onClick,
  disabled,
  busy,
  children,
  type = "button",
}: {
  onClick?: () => void;
  disabled?: boolean;
  busy?: boolean;
  children: React.ReactNode;
  type?: "button" | "submit";
}) {
  const off = disabled || busy;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={off}
      style={{
        marginTop: 2,
        width: "100%",
        padding: 14,
        border: "none",
        borderRadius: 12,
        fontSize: 14,
        fontWeight: 700,
        cursor: off ? "default" : "pointer",
        background: off ? "#EFECE4" : CTA_GRADIENT,
        color: off ? "#A9A395" : "#fff",
        boxShadow: off ? "none" : "0 6px 20px rgba(198,54,44,.3)",
      }}
    >
      {busy ? "Saving…" : children}
    </button>
  );
}

// Inline error / info banners.
export function ErrorBanner({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ background: C.redSoftBg, border: `1px solid ${C.redSoftBorder}`, borderRadius: 10, padding: "10px 13px", fontSize: 12.5, color: C.redSoftText }}>
      {children}
    </div>
  );
}

export function InfoBanner({ children, tone = "blue" }: { children: React.ReactNode; tone?: "blue" | "saffron" | "green" }) {
  const map = {
    blue: { bg: C.blueSoftBg, bd: C.blueSoftBorder, tx: C.blueSoftText },
    saffron: { bg: C.saffronSoftBg, bd: C.saffronSoftBorder, tx: C.saffronSoftText },
    green: { bg: C.greenSoftBg, bd: C.greenSoftBorder, tx: C.greenSoftText },
  }[tone];
  return (
    <div style={{ background: map.bg, border: `1px solid ${map.bd}`, borderRadius: 11, padding: "11px 15px", fontSize: 12.5, color: map.tx, lineHeight: 1.5 }}>
      {children}
    </div>
  );
}

// Shared option lists.
export const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((g) => ({ value: g, label: g }));
