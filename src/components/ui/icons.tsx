import React from "react";

// Minimal feather-style line icons for the UI redesign — 24×24 viewBox,
// stroke: currentColor, stroke-width 2, round caps/joins. No emoji anywhere.

type IconProps = {
  size?: number;
  className?: string;
  strokeWidth?: number;
  style?: React.CSSProperties;
};

function base(size: number, className?: string, style?: React.CSSProperties) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    style,
  };
}

/** Shield + cross — the brand mark glyph (drawn white on the brand gradient). */
export function ShieldCrossIcon({ size = 19, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <line x1="12" y1="8.5" x2="12" y2="13.5" />
      <line x1="9.5" y1="11" x2="14.5" y2="11" />
    </svg>
  );
}

export function MapPinIcon({ size = 15, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" />
      <circle cx="12" cy="10" r="3" />
    </svg>
  );
}

export function MicIcon({ size = 24, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10v1a7 7 0 0 0 14 0v-1" />
      <line x1="12" y1="18" x2="12" y2="22" />
    </svg>
  );
}

export function ClockIcon({ size = 16, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4l3 3" />
    </svg>
  );
}

export function PhoneIcon({ size = 15, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
    </svg>
  );
}

export function UserIcon({ size = 16, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

export function ChevronDownIcon({ size = 16, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

export function LogOutIcon({ size = 16, className, strokeWidth = 2, style }: IconProps) {
  return (
    <svg {...base(size, className, style)} strokeWidth={strokeWidth}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}
