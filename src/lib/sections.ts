// Corridor sections — seed config for the Administrator oversight view. The
// monitored Delhi–Dehradun corridor is split into sections, each with an
// assigned monitor (an operator/person). Incidents are bucketed into a section
// by their position ALONG the corridor (km marker), and operators are scoped to
// the section they monitor; the administrator sees every section.

import { CORRIDOR_POLYLINE } from "./corridorWaypoints";
import { haversineKm } from "./corridorGeometry";

export interface CorridorSection {
  id: string;
  name: string;
  hi: string;
  fromKm: number;
  toKm: number;
  monitorName: string;
  // The operator account (allowlist email) assigned to this section, or null for
  // a monitor without an app account. Operator scoping keys off this.
  monitorEmail: string | null;
}

// ── Corridor km geometry (cumulative distance along the polyline) ──────────────
const CUM_KM: number[] = (() => {
  const cum = [0];
  for (let i = 1; i < CORRIDOR_POLYLINE.length; i++) {
    cum.push(cum[i - 1] + haversineKm(CORRIDOR_POLYLINE[i - 1], CORRIDOR_POLYLINE[i]));
  }
  return cum;
})();

export const CORRIDOR_LENGTH_KM = CUM_KM[CUM_KM.length - 1];

// Project p onto segment a–b (equirectangular approx — fine over short segments);
// returns the clamped parameter t∈[0,1] and the perpendicular distance in km.
function projectOnSegment(
  p: { lat: number; lng: number },
  a: { lat: number; lng: number },
  b: { lat: number; lng: number },
): { t: number; distKm: number } {
  const latRef = ((a.lat + b.lat) / 2) * (Math.PI / 180);
  const kx = 111.32 * Math.cos(latRef); // km per deg lng at this latitude
  const ky = 110.57; // km per deg lat
  const ax = a.lng * kx, ay = a.lat * ky;
  const bx = b.lng * kx, by = b.lat * ky;
  const px = p.lng * kx, py = p.lat * ky;
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx, cy = ay + t * dy;
  const distKm = Math.hypot(px - cx, py - cy);
  return { t, distKm };
}

// The km marker along the corridor of the point nearest to p.
export function corridorKmOf(p: { lat: number; lng: number }): number {
  let best = { distKm: Infinity, km: 0 };
  for (let i = 1; i < CORRIDOR_POLYLINE.length; i++) {
    const a = CORRIDOR_POLYLINE[i - 1], b = CORRIDOR_POLYLINE[i];
    const { t, distKm } = projectOnSegment(p, a, b);
    if (distKm < best.distKm) {
      const segLen = CUM_KM[i] - CUM_KM[i - 1];
      best = { distKm, km: CUM_KM[i - 1] + t * segLen };
    }
  }
  return best.km;
}

// ── Sections (4 equal stretches of the corridor, named + assigned) ────────────
const L = CORRIDOR_LENGTH_KM;
export const CORRIDOR_SECTIONS: CorridorSection[] = [
  { id: "S1", name: "Delhi approach",     hi: "दिल्ली छोर",      fromKm: 0,        toKm: L * 0.25, monitorName: "R. Verma",              monitorEmail: null },
  { id: "S2", name: "Baghpat – Shamli",   hi: "बागपत – शामली",  fromKm: L * 0.25, toKm: L * 0.5,  monitorName: "Operator (Y. Singh)",  monitorEmail: "yashsingh1105@gmail.com" },
  { id: "S3", name: "Saharanpur belt",    hi: "सहारनपुर पट्टी", fromKm: L * 0.5,  toKm: L * 0.75, monitorName: "S. Kaur",              monitorEmail: null },
  { id: "S4", name: "Dehradun approach",  hi: "देहरादून छोर",   fromKm: L * 0.75, toKm: L,        monitorName: "A. Rawat",             monitorEmail: null },
];

// Which section a corridor km falls in (last section is inclusive of the end).
export function sectionOfKm(km: number): CorridorSection {
  for (const s of CORRIDOR_SECTIONS) {
    if (km >= s.fromKm && km < s.toKm) return s;
  }
  return CORRIDOR_SECTIONS[CORRIDOR_SECTIONS.length - 1];
}

// Which section a lat/lng incident belongs to (by its km marker).
export function sectionOfPoint(p: { lat: number; lng: number }): CorridorSection {
  return sectionOfKm(corridorKmOf(p));
}

// The section an operator monitors (by their allowlist email), or null.
export function sectionOfEmail(email?: string | null): CorridorSection | null {
  if (!email) return null;
  const e = email.trim().toLowerCase();
  return CORRIDOR_SECTIONS.find((s) => s.monitorEmail?.toLowerCase() === e) ?? null;
}

export function fmtKm(km: number): string {
  return `${Math.round(km)} km`;
}
