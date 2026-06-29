// Generates the exact notification text that would be sent via SMS or push.
// This is a record of what WAS sent — not a live status feed.

import type { AccidentReport, AssessmentResult } from "./types";

const SEV_LABEL: Record<number, string> = {
  1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL",
};

function toIST(iso: string): string {
  return (
    new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(iso)) + " IST"
  );
}

function fmtVictims(n: number | null): string {
  return n !== null ? `${n} estimated` : "Not reported";
}

function fmtDist(km: number | null, min: number | null): string {
  if (km !== null && min !== null) {
    return (
      `${km.toFixed(1)} km by road (~${Math.round(min)} min, current traffic)\n` +
      `(Est. drive time from facility — vehicle leaving now. We do not track ambulances.)`
    );
  }
  return "Road distance unavailable — straight-line proximity used for matching.";
}

// ── Hospital alert ────────────────────────────────────────────────────────────

export function generateHospitalAlert(
  incident: AccidentReport,
  assessment: AssessmentResult,
  hospitalName: string,
  roadKm: number | null,
  roadMin: number | null
): string {
  const sev = assessment.severityScore;
  const victims = fmtVictims(
    incident.estimatedCasualties ?? incident.vehiclesInvolved
  );

  return [
    "TRANSPORT SAHAYAK — ACCIDENT ALERT",
    "─".repeat(40),
    `Ref      : ${incident.id}`,
    `Reported : ${toIST(incident.timestamp)}`,
    `Mode     : ${incident.reportMode}`,
    "─".repeat(40),
    `Severity : ${assessment.severity} (${sev}/4)  ${SEV_LABEL[sev]}`,
    ...(assessment.subType ? [`Type     : ${assessment.subType}`] : []),
    "",
    `Location : ${incident.locationLabel}`,
    `GPS      : ${incident.location.lat.toFixed(5)} N, ${incident.location.lng.toFixed(5)} E`,
    `Persons  : ${victims}`,
    ...(incident.flags.length ? [`Flags    : ${incident.flags.join(", ")}`] : []),
    "",
    `Assessment: ${assessment.impactNote}`,
    ...(assessment.agencies.length ? [`Agencies : ${assessment.agencies.map((a) => a.label).join(", ")}`] : []),
    "─".repeat(40),
    `To       : ${hospitalName} (Emergency Dept.)`,
    `Distance : ${fmtDist(roadKm, roadMin)}`,
    "",
    "Please prepare emergency bay and confirm receipt via the",
    "Transport Sahayak dispatch system.",
    "─".repeat(40),
    "Acknowledgement: Open field — recorded by the deployed",
    "production system. No en-route or arrival status is implied",
    "by this notification.",
    "",
    "Delivery channel: SMS / Push Notification",
    "Transport Sahayak  |  Delhi–Dehradun Corridor",
  ].join("\n");
}

// ── Police alert ──────────────────────────────────────────────────────────────

export function generatePoliceAlert(
  incident: AccidentReport,
  assessment: AssessmentResult,
  stationName: string,
  roadKm: number | null,
  roadMin: number | null
): string {
  const sev = assessment.severityScore;
  const victims = fmtVictims(
    incident.estimatedCasualties ?? incident.vehiclesInvolved
  );

  return [
    "TRANSPORT SAHAYAK — ACCIDENT ALERT",
    "─".repeat(40),
    `Ref      : ${incident.id}`,
    `Reported : ${toIST(incident.timestamp)}`,
    `Mode     : ${incident.reportMode}`,
    "─".repeat(40),
    `Severity : ${assessment.severity} (${sev}/4)  ${SEV_LABEL[sev]}`,
    ...(assessment.subType ? [`Type     : ${assessment.subType}`] : []),
    "",
    `Location : ${incident.locationLabel}`,
    `GPS      : ${incident.location.lat.toFixed(5)} N, ${incident.location.lng.toFixed(5)} E`,
    `Persons  : ${victims}`,
    ...(incident.flags.length ? [`Flags    : ${incident.flags.join(", ")}`] : []),
    "",
    `Assessment: ${assessment.impactNote}`,
    "─".repeat(40),
    `To       : ${stationName}`,
    `Distance : ${fmtDist(roadKm, roadMin)}`,
    "",
    "Please attend the incident scene, establish traffic control,",
    "and coordinate with attending medical team.",
    "Confirm receipt via the Transport Sahayak dispatch system.",
    "─".repeat(40),
    "Acknowledgement: Open field — recorded by the deployed",
    "production system. No en-route or arrival status is implied",
    "by this notification.",
    "",
    "Delivery channel: SMS / Push Notification",
    "Transport Sahayak  |  Delhi–Dehradun Corridor",
  ].join("\n");
}
