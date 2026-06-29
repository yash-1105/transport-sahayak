// Rule-based severity scoring — retained for reference / future re-wiring.
// No longer called from the UI since the Python engine took over /api/assess.

import type { AccidentReport, AssessmentResult, AssessmentSeverity } from "./types";

const IMPACT_NOTES: Record<AssessmentSeverity, string> = {
  1: "Minor incident — no confirmed casualties. Basic road clearance response.",
  2: "Possible minor injuries requiring medical evaluation. BLS ambulance advisable.",
  3: "Confirmed casualties requiring immediate ambulance and police response.",
  4: "Multiple casualties or critical life-threatening conditions. Full emergency response required.",
};

const AGENCY_SETS: Record<AssessmentSeverity, { code: string; label: string }[]> = {
  1: [{ code: "TRAFFIC", label: "Traffic Police" }],
  2: [{ code: "AMBULANCE", label: "Ambulance (108)" }, { code: "POLICE", label: "Police" }],
  3: [{ code: "AMBULANCE", label: "Ambulance (108)" }, { code: "POLICE", label: "Police" }, { code: "HOSPITAL", label: "District Hospital" }],
  4: [{ code: "AMBULANCE", label: "Ambulance (108)" }, { code: "POLICE", label: "Police" }, { code: "HOSPITAL", label: "Level-1 Trauma Centre" }, { code: "FIRE", label: "Fire & Rescue" }],
};

export function heuristicAssess(incident: AccidentReport): AssessmentResult {
  let score = 0;
  const modifiers: string[] = [];

  if (incident.flags.includes("SOS")) { score += 4; modifiers.push("SOS alert — unknown detail treated as high risk"); }
  if (incident.flags.includes("Heavy bleeding")) { score += 3; modifiers.push("heavy bleeding reported"); }
  if (incident.flags.includes("Trapped")) { score += 3; modifiers.push("person(s) trapped"); }

  const persons = incident.vehiclesInvolved ?? incident.estimatedCasualties ?? 0;
  if (persons >= 5) { score += 3; modifiers.push(`${persons} persons involved`); }
  else if (persons >= 2) { score += 1; modifiers.push(`${persons} persons involved`); }

  const desc = (incident.description ?? "").toLowerCase();
  if (/fatal|dead\b|died|death|killed/.test(desc)) { score += 4; modifiers.push("fatality language in description"); }
  if (/unconscious|unresponsive|not breathing/.test(desc)) { score += 3; modifiers.push("unconscious/unresponsive mentioned"); }
  if (/fire|burn|explos/.test(desc)) { score += 2; modifiers.push("fire or explosion mentioned"); }

  const severityScore: AssessmentSeverity =
    score >= 8 ? 4 : score >= 5 ? 3 : score >= 2 ? 2 : 1;

  const severityLabel = (["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const)[severityScore - 1];

  const dataGaps: string[] = [];
  if (!incident.estimatedCasualties) dataGaps.push("How many persons are injured?");
  if (!incident.flags.length) dataGaps.push("Are there visible injuries or trapped persons?");

  return {
    severity: severityLabel,
    severityScore,
    impactNote: IMPACT_NOTES[severityScore],
    appliedModifiers: modifiers,
    agencies: AGENCY_SETS[severityScore],
    dataGaps,
    classifiedBy: "rules",
    llmUsed: false,
    lowConfidence: modifiers.length === 0,
  };
}
