// Rule-based severity scoring — used when AI assessment is unavailable.
// Importable in both server (route handler) and client (network-error fallback).

import type { AccidentReport, AssessmentResult, AssessmentSeverity, AssessmentPriority } from "./types";

const RESPONSE_TEMPLATES: Record<AssessmentSeverity, string> = {
  1: "Send nearest patrol vehicle. No immediate medical response required. Clear road if obstructed.",
  2: "Dispatch BLS ambulance. Notify nearest police station. Document for traffic management.",
  3: "Dispatch ALS ambulance and police. Alert district hospital emergency unit. Establish traffic control point.",
  4: "Dispatch multiple ambulances (ALS preferred). Notify trauma centre. Request police and fire services. Set up on-scene incident command.",
  5: "Mass casualty protocol — all available emergency services. Immediate notification to GMC/nearest Level-1 trauma centre. Request ambulances from adjacent districts. Activate district disaster management cell.",
};

const SEVERITY_LABELS: Record<AssessmentSeverity, string> = {
  1: "minor road incident with no apparent casualties",
  2: "possible minor injuries requiring medical evaluation",
  3: "confirmed casualties requiring immediate ambulance response",
  4: "serious casualties or confirmed entrapment requiring full emergency response",
  5: "mass casualty event or critical life-threatening conditions",
};

export function heuristicAssess(incident: AccidentReport): AssessmentResult {
  let score = 0;
  const reasons: string[] = [];

  // SOS — unknown detail is a risk, not a reason to downgrade
  if (incident.flags.includes("SOS")) {
    score += 5;
    reasons.push("SOS alert with no additional detail");
  }

  // Observed condition flags
  if (incident.flags.includes("Heavy bleeding")) {
    score += 3;
    reasons.push("heavy bleeding reported");
  }
  if (incident.flags.includes("Trapped")) {
    score += 3;
    reasons.push("person(s) trapped");
  }
  // Breathing not confirmed is a passive risk signal only when other flags are present
  if (
    incident.flags.length > 0 &&
    !incident.flags.includes("SOS") &&
    !incident.flags.includes("Breathing") &&
    incident.flags.includes("Conscious")
  ) {
    score += 1;
    reasons.push("conscious but breathing not confirmed");
  }

  // Persons involved (stored in vehiclesInvolved field from the form)
  const persons = incident.vehiclesInvolved ?? incident.estimatedCasualties ?? 0;
  if (persons >= 5) {
    score += 3;
    reasons.push(`${persons} persons involved`);
  } else if (persons >= 3) {
    score += 2;
    reasons.push(`${persons} persons involved`);
  } else if (persons >= 1) {
    score += 1;
    reasons.push(`${persons} person(s) involved`);
  }

  // Description keyword signals
  const desc = (incident.description ?? "").toLowerCase();
  if (/fatal|dead\b|died|death|killed/.test(desc)) {
    score += 4;
    reasons.push("fatality language in description");
  }
  if (/unconscious|unresponsive|not breathing/.test(desc)) {
    score += 3;
    reasons.push("unconscious/unresponsive mentioned");
  }
  if (/fire|burn|explos/.test(desc)) {
    score += 3;
    reasons.push("fire or explosion mentioned");
  }
  if (/multiple|several|many|overturned|rollover/.test(desc)) {
    score += 1;
    reasons.push("multiple vehicles or rollovers mentioned");
  }
  if (/truck|lorry|bus|tanker|heavy vehicle/.test(desc)) {
    score += 1;
    reasons.push("heavy vehicle involved");
  }

  // Map score → severity
  const severity: AssessmentSeverity =
    score >= 9 ? 5 : score >= 6 ? 4 : score >= 4 ? 3 : score >= 2 ? 2 : 1;

  const priority: AssessmentPriority =
    severity >= 4 ? "critical" : severity === 3 ? "high" : severity === 2 ? "medium" : "low";

  const rationale =
    reasons.length > 0
      ? `Heuristic indicators: ${reasons.join("; ")}. Score ${score} maps to ${SEVERITY_LABELS[severity]}.`
      : `No high-risk indicators in flags or description. Score ${score} — ${SEVERITY_LABELS[severity]}.`;

  return {
    severity,
    rationale,
    recommendedResponse: RESPONSE_TEMPLATES[severity],
    priority,
    source: "HEURISTIC",
    assessedAt: new Date().toISOString(),
  };
}
