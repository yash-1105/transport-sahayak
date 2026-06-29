// Hospital ranking and nearest-police logic.
// Pure functions — no I/O, importable on client or server.

import type {
  Hospital,
  HospitalCandidate,
  PoliceStation,
  AccidentReport,
  AssessmentResult,
  AssessmentSeverity,
  RankedHospital,
  NearestPolice,
} from "./types";

// ── Distance ──────────────────────────────────────────────────────────────────

export function haversineKm(
  a: { lat: number; lng: number },
  b: { lat: number; lng: number }
): number {
  const R = 6371;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) *
      Math.cos((b.lat * Math.PI) / 180) *
      Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.asin(Math.sqrt(h));
}

// ── Specialty relevance by severity ──────────────────────────────────────────

const RELEVANT_SPECIALTIES: Record<number, string[]> = {
  4: ["Trauma", "Multi-speciality", "Neurosurgery", "Burns", "Cardiology", "ICU"], // CRITICAL
  3: ["Trauma", "General Surgery", "Orthopaedics", "ICU", "Multi-speciality"],      // HIGH
  2: ["General Medicine", "General Surgery", "Paediatrics", "Obstetrics"],           // MEDIUM
  1: ["General Medicine", "General Surgery"],                                         // LOW
};

function relevantSpecialties(severity: AssessmentSeverity): string[] {
  return RELEVANT_SPECIALTIES[severity] ?? RELEVANT_SPECIALTIES[1];
}

// ── Scoring ───────────────────────────────────────────────────────────────────

function score(
  hospital: Hospital,
  distKm: number,
  severity: AssessmentSeverity,
  specialtyMatches: string[]
): number {
  // Trauma weight rises steeply with severity so Level-1 centres float to top
  const traumaWeight = severity >= 3 ? 3.5 : severity === 2 ? 2 : 1;

  let s = 0;

  if (hospital.traumaCapable) {
    // Level 1 = 30 pts, Level 2 = 20 pts, Level 3 = 10 pts — then multiplied
    const baseTrauma = Math.max(0, (4 - hospital.traumaLevel) * 10);
    s += baseTrauma * traumaWeight;
  } else if (severity >= 2) {
    // Non-trauma hospital is a penalty at medium-to-high severity
    s -= 15;
  }

  // Up to 3 specialty matches, 9 pts each
  s += Math.min(specialtyMatches.length, 3) * 9;

  // Distance: 40 pts at collocated, diminishing hyperbolically
  s += 40 / (distKm + 1);

  return s;
}

// ── Plain-language reasoning ──────────────────────────────────────────────────

export function generateReasoning(
  hospital: Hospital | HospitalCandidate,
  rank: number,
  straightKm: number,
  roadKm: number | null,
  roadMin: number | null,
  severity: AssessmentSeverity,
  specialtyMatches: string[]
): string {
  const dist =
    roadKm !== null
      ? `${roadKm.toFixed(1)} km by road (~${Math.round(roadMin!)} min, current traffic)`
      : `${straightKm.toFixed(1)} km straight-line`;

  const parts: string[] = [];

  // Opening — what makes this the #N pick
  if (rank === 1) {
    if (hospital.traumaCapable && hospital.traumaLevel === 1) {
      parts.push(`Top match: nearest Level-1 trauma centre at ${dist}.`);
    } else if (hospital.traumaCapable) {
      parts.push(`Top match at ${dist} — Level-${hospital.traumaLevel} trauma capability.`);
    } else {
      parts.push(`Closest hospital at ${dist}, selected on proximity.`);
    }
  } else {
    const ordinal = rank === 2 ? "Second" : "Third";
    parts.push(`${ordinal} option at ${dist}.`);
    if (hospital.traumaCapable) {
      parts.push(`Level-${hospital.traumaLevel} trauma.`);
    } else {
      parts.push(`No declared trauma capability.`);
    }
  }

  // Specialty match narrative
  if (specialtyMatches.length >= 2) {
    parts.push(`Specialties relevant to this incident: ${specialtyMatches.slice(0, 3).join(", ")}.`);
  } else if (specialtyMatches.length === 1) {
    parts.push(`One relevant specialty: ${specialtyMatches[0]}.`);
  } else if (severity >= 2) {
    parts.push(`No declared specialties directly matched to this incident type.`);
  }

  // Clinical guidance for high severity + limited capability
  if (!hospital.traumaCapable && severity >= 3) {
    parts.push(`⚠ Stabilisation only — arrange transfer to a Level-1 centre.`);
  } else if (hospital.traumaCapable && hospital.traumaLevel === 1 && severity >= 3) {
    parts.push(`Suitable for definitive care at this severity level.`);
  }

  return parts.join(" ");
}

// ── Public API ────────────────────────────────────────────────────────────────

// Converts a curated Hospital to a HospitalCandidate for internal use.
function toCandidate(h: Hospital): HospitalCandidate {
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

export function rankHospitals(
  hospitals: Hospital[],
  incident: AccidentReport,
  assessment: AssessmentResult
): RankedHospital[] {
  const sev = assessment.severityScore as AssessmentSeverity;
  const relevant = relevantSpecialties(sev);

  const scored = hospitals.map((hospital) => {
    const candidate = toCandidate(hospital);
    const distKm = haversineKm(incident.location, {
      lat: hospital.lat,
      lng: hospital.lng,
    });
    const specialtyMatches = hospital.specialty.filter((s) =>
      relevant.includes(s)
    );
    return {
      candidate,
      distKm,
      specialtyMatches,
      score: score(hospital, distKm, sev, specialtyMatches),
    };
  });

  scored.sort((a, b) => b.score - a.score);

  return scored.slice(0, 3).map((item, i) => {
    const rank = (i + 1) as 1 | 2 | 3;
    return {
      hospital: item.candidate,
      rank,
      straightLineKm: item.distKm,
      roadDistanceKm: null,
      roadDurationMin: null,
      matchScore: item.score,
      specialtyMatches: item.specialtyMatches,
      routeCoords: null,
      reasoning: generateReasoning(
        item.candidate,
        rank,
        item.distKm,
        null,
        null,
        sev,
        item.specialtyMatches
      ),
    };
  });
}

export function findNearestPolice(
  stations: PoliceStation[],
  incident: AccidentReport
): NearestPolice {
  const withDist = stations
    .map((s) => ({
      station: s,
      distKm: haversineKm(incident.location, { lat: s.lat, lng: s.lng }),
    }))
    .sort((a, b) => a.distKm - b.distKm);

  const nearest = withDist[0];
  return {
    station: nearest.station,
    straightLineKm: nearest.distKm,
    roadDistanceKm: null,
    roadDurationMin: null,
    routeCoords: null,
  };
}
