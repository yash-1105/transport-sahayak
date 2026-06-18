// Builds the hybrid hospital candidate list and ranks by Routes API traffic times.
// Rules:
//   - Curated entries (seed data) keep their trauma level + specialties.
//   - Google Places entries are deduplicated against curated by proximity (≤500 m)
//     or significant name overlap; surviving Google entries are labelled
//     capabilitySource:"unverified".
//   - Sort all candidates by straight-line distance to the incident, keep nearest 10.
//   - Rank the shortlist by traffic-aware drive time, with a capability bonus.

import type {
  Hospital,
  HospitalCandidate,
  GooglePlace,
  RankedHospital,
  AccidentReport,
  AssessmentResult,
  AssessmentSeverity,
} from "./types";
import { haversineKm } from "./matching";

// ── Specialty relevance table ─────────────────────────────────────────────────

const RELEVANT_SPECIALTIES: Record<number, string[]> = {
  5: ["Trauma", "Multi-speciality", "Neurosurgery", "Burns", "Cardiology"],
  4: ["Trauma", "Multi-speciality", "Neurosurgery", "Burns", "Cardiology", "ICU"],
  3: ["Trauma", "General Surgery", "Orthopaedics", "ICU", "Multi-speciality"],
  2: ["General Medicine", "General Surgery", "Paediatrics", "Obstetrics"],
  1: ["General Medicine", "General Surgery"],
};

// ── Curated → candidate conversion ───────────────────────────────────────────

export function hospitalToCandidate(h: Hospital): HospitalCandidate {
  return {
    id: h.id,
    name: h.name,
    shortName: h.shortName,
    lat: h.lat,
    lng: h.lng,
    district: h.district,
    type: h.type,
    traumaCapable: h.traumaCapable,
    traumaLevel: h.traumaLevel,
    specialty: h.specialty,
    capabilitySource: "curated",
  };
}

// ── Name-similarity check (token overlap) ────────────────────────────────────

function isSameFacility(a: string, b: string): boolean {
  const tokens = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, "")
      .split(/\s+/)
      .filter((w) => w.length > 3 && !["hospital", "medical", "centre", "center", "district"].includes(w));

  const ta = tokens(a);
  const tb = tokens(b);
  const overlap = ta.filter((w) => tb.includes(w));
  // Two-word match, or exact match of a single distinctive word
  return overlap.length >= 2 || (ta.length === 1 && tb.length === 1 && overlap.length === 1);
}

// ── Merge curated + Google Places, dedup ─────────────────────────────────────

export function buildCandidates(
  curated: Hospital[],
  googlePlaces: GooglePlace[]
): HospitalCandidate[] {
  const base = curated.map(hospitalToCandidate);

  const newFromGoogle = googlePlaces.filter((gp) => {
    if (!gp.lat || !gp.lng) return false;
    return !base.some((c) => {
      const distKm = haversineKm({ lat: gp.lat, lng: gp.lng }, { lat: c.lat, lng: c.lng });
      return distKm < 0.5 || isSameFacility(gp.name, c.name);
    });
  });

  const googleCandidates: HospitalCandidate[] = newFromGoogle.map((gp) => ({
    id: `gp-${gp.id}`,
    name: gp.name,
    shortName: gp.name.length > 25 ? gp.name.slice(0, 23) + "…" : gp.name,
    lat: gp.lat,
    lng: gp.lng,
    district: "",
    type: "Hospital",
    traumaCapable: false,
    traumaLevel: null,
    specialty: [],
    capabilitySource: "unverified",
    placeId: gp.id,
  }));

  return [...base, ...googleCandidates];
}

// ── Shortlist: nearest 10 by straight line ────────────────────────────────────

export function shortlistByDistance(
  candidates: HospitalCandidate[],
  incident: AccidentReport,
  limit = 10
): { candidate: HospitalCandidate; straightLineKm: number }[] {
  return candidates
    .map((c) => ({
      candidate: c,
      straightLineKm: haversineKm(incident.location, { lat: c.lat, lng: c.lng }),
    }))
    .sort((a, b) => a.straightLineKm - b.straightLineKm)
    .slice(0, limit);
}

// ── Rank by traffic time + capability ────────────────────────────────────────

export interface TrafficResult {
  originIndex: number;
  distanceMeters: number;
  durationSec: number;
}

function capabilityBonus(c: HospitalCandidate, sev: AssessmentSeverity): number {
  if (c.capabilitySource === "unverified") return -50;
  if (!c.traumaCapable) return sev >= 3 ? -20 : 0;
  const levelMap: Record<number, number> = { 1: 200, 2: 100, 3: 50 };
  const base = levelMap[c.traumaLevel ?? 3] ?? 0;
  return sev >= 4 ? base : sev >= 3 ? Math.round(base * 0.5) : Math.round(base * 0.25);
}

export function rankCandidatesByTraffic(
  shortlisted: { candidate: HospitalCandidate; straightLineKm: number }[],
  trafficResults: TrafficResult[],
  incident: AccidentReport,
  assessment: AssessmentResult
): RankedHospital[] {
  const sev = assessment.severity as AssessmentSeverity;
  const relevant = RELEVANT_SPECIALTIES[sev] ?? RELEVANT_SPECIALTIES[1];

  // Index traffic results by originIndex for fast lookup
  const byOrigin = new Map<number, TrafficResult>();
  trafficResults.forEach((r) => byOrigin.set(r.originIndex, r));

  const scored: {
    candidate: HospitalCandidate;
    straightLineKm: number;
    trafficResult: TrafficResult;
    specialtyMatches: string[];
    score: number;
  }[] = [];

  shortlisted.forEach(({ candidate: c, straightLineKm }, idx) => {
    const tr = byOrigin.get(idx);
    if (!tr) return; // Routes API couldn't find a route — drop this candidate

    const durationMin = tr.durationSec / 60;
    const specialtyMatches = c.specialty.filter((s) => relevant.includes(s));
    const score =
      1000 -
      durationMin + // shorter drive = higher score
      capabilityBonus(c, sev) +
      Math.min(specialtyMatches.length, 3) * 30;

    scored.push({ candidate: c, straightLineKm, trafficResult: tr, specialtyMatches, score });
  });

  scored.sort((a, b) => b.score - a.score);

  return scored.slice(0, 3).map((item, i) => {
    const rank = (i + 1) as 1 | 2 | 3;
    const roadKm = item.trafficResult.distanceMeters / 1000;
    const roadMin = item.trafficResult.durationSec / 60;
    return {
      hospital: item.candidate,
      rank,
      straightLineKm: item.straightLineKm,
      roadDistanceKm: roadKm,
      roadDurationMin: roadMin,
      matchScore: item.score,
      specialtyMatches: item.specialtyMatches,
      routeCoords: null,
      reasoning: generateCandidateReasoning(item.candidate, rank, item.straightLineKm, roadKm, roadMin, sev, item.specialtyMatches),
    };
  });
}

// ── Fallback ranking (no Routes API results) ──────────────────────────────────

export function rankCandidatesByDistance(
  shortlisted: { candidate: HospitalCandidate; straightLineKm: number }[],
  incident: AccidentReport,
  assessment: AssessmentResult
): RankedHospital[] {
  const sev = assessment.severity as AssessmentSeverity;
  const relevant = RELEVANT_SPECIALTIES[sev] ?? RELEVANT_SPECIALTIES[1];

  const scored = shortlisted.map(({ candidate: c, straightLineKm }) => {
    const specialtyMatches = c.specialty.filter((s) => relevant.includes(s));
    const score =
      40 / (straightLineKm + 1) +
      capabilityBonus(c, sev) / 5 +
      Math.min(specialtyMatches.length, 3) * 5;
    return { candidate: c, straightLineKm, specialtyMatches, score };
  });

  scored.sort((a, b) => b.score - a.score);

  return scored.slice(0, 3).map((item, i) => {
    const rank = (i + 1) as 1 | 2 | 3;
    return {
      hospital: item.candidate,
      rank,
      straightLineKm: item.straightLineKm,
      roadDistanceKm: null,
      roadDurationMin: null,
      matchScore: item.score,
      specialtyMatches: item.specialtyMatches,
      routeCoords: null,
      reasoning: generateCandidateReasoning(item.candidate, rank, item.straightLineKm, null, null, sev, item.specialtyMatches),
    };
  });
}

// ── Reasoning text ────────────────────────────────────────────────────────────

export function generateCandidateReasoning(
  h: HospitalCandidate,
  rank: number,
  straightKm: number,
  roadKm: number | null,
  roadMin: number | null,
  severity: AssessmentSeverity,
  specialtyMatches: string[]
): string {
  const distText =
    roadKm !== null && roadMin !== null
      ? `${roadKm.toFixed(1)} km by road (~${Math.round(roadMin)} min, current traffic)`
      : `${straightKm.toFixed(1)} km straight-line`;

  const parts: string[] = [];

  if (h.capabilitySource === "unverified") {
    parts.push(
      rank === 1
        ? `Nearest option at ${distText}. ⚠ Capability unverified — no trauma or specialty data from Google Places.`
        : `Option at ${distText}. ⚠ Capability unverified — no trauma or specialty data.`
    );
    return parts.join(" ");
  }

  if (rank === 1) {
    if (h.traumaCapable && h.traumaLevel === 1) {
      parts.push(`Top match: nearest Level-1 trauma centre at ${distText}.`);
    } else if (h.traumaCapable) {
      parts.push(`Top match at ${distText} — Level-${h.traumaLevel} trauma capability.`);
    } else {
      parts.push(`Closest hospital at ${distText}, selected on proximity.`);
    }
  } else {
    const ordinal = rank === 2 ? "Second" : "Third";
    parts.push(`${ordinal} option at ${distText}.`);
    if (h.traumaCapable) {
      parts.push(`Level-${h.traumaLevel} trauma.`);
    } else {
      parts.push(`No declared trauma capability.`);
    }
  }

  if (specialtyMatches.length >= 2) {
    parts.push(`Specialties relevant to this incident: ${specialtyMatches.slice(0, 3).join(", ")}.`);
  } else if (specialtyMatches.length === 1) {
    parts.push(`One relevant specialty: ${specialtyMatches[0]}.`);
  } else if (severity >= 3) {
    parts.push(`No declared specialties matched this incident type.`);
  }

  if (!h.traumaCapable && severity >= 4) {
    parts.push(`⚠ Stabilisation only — arrange transfer to a Level-1 centre.`);
  }

  return parts.join(" ");
}
