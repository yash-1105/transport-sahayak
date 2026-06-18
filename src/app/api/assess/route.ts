// Severity assessment route handler.
// Always returns HTTP 200 with a valid AssessmentResult — either from Gemini or
// the rule-based heuristic fallback. The client can rely on 200 = usable result.

import { NextRequest, NextResponse } from "next/server";
import type { AccidentReport, AssessmentResult } from "@/lib/types";
import { heuristicAssess } from "@/lib/heuristic";

const SYSTEM_PROMPT = `You are a road accident severity assessor for the Assam Transport Department emergency operations centre.

Given an incident report, return ONLY valid JSON — no markdown, no prose, no code fences, nothing outside the JSON object.

Required output format (all fields mandatory):
{
  "severity": <integer 1 to 5>,
  "rationale": "<one to two sentences explaining the score and the key risk factors>",
  "recommendedResponse": "<concrete action steps for on-ground responders, written as clear instructions>",
  "priority": "<exactly one of: low | medium | high | critical>"
}

Severity scale:
1 — Minor: No injuries apparent, vehicle assistance only, no emergency services required
2 — Low: Possible minor injuries, BLS ambulance advisable, police notification recommended
3 — Moderate: Confirmed injuries, ALS ambulance required, district hospital alert, police response
4 — Serious: Multiple casualties or possible fatalities, all emergency services, trauma centre notification
5 — Critical: Confirmed fatalities or mass casualties, all services on maximum alert, district-level coordination

Priority mapping:
low → severity 1
medium → severity 2
high → severity 3–4
critical → severity 4–5

SOS with no detail: assign severity 4, priority critical — unknown information is a risk factor, not a reason to downgrade.
Absence of details should increase, not decrease, the assessed risk.`;

function safeParseAssessment(text: string): Partial<AssessmentResult> {
  const stripped = text
    .replace(/^```(?:json)?\s*/im, "")
    .replace(/\s*```\s*$/m, "")
    .trim();
  return JSON.parse(stripped) as Partial<AssessmentResult>;
}

function isValidResult(obj: Partial<AssessmentResult>): obj is AssessmentResult {
  return (
    typeof obj.severity === "number" &&
    obj.severity >= 1 &&
    obj.severity <= 5 &&
    typeof obj.rationale === "string" &&
    obj.rationale.length > 0 &&
    typeof obj.recommendedResponse === "string" &&
    obj.recommendedResponse.length > 0 &&
    ["low", "medium", "high", "critical"].includes(obj.priority as string)
  );
}

export async function POST(req: NextRequest) {
  const incident: AccidentReport = await req.json();
  const now = new Date().toISOString();

  const apiKey = process.env.GEMINI_API_KEY;

  if (!apiKey) {
    const result = heuristicAssess(incident);
    result.fallbackReason = "no API key";
    return NextResponse.json(result);
  }

  try {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI = new GoogleGenerativeAI(apiKey);
    const model = genAI.getGenerativeModel({
      model: "gemini-2.0-flash",
      systemInstruction: SYSTEM_PROMPT,
    });

    const userContent = [
      `Incident ID: ${incident.id}`,
      `Mode: ${incident.reportMode}`,
      `Location: ${incident.locationLabel}`,
      `Persons involved: ${incident.vehiclesInvolved ?? "unknown"}`,
      `Flags: ${incident.flags.length > 0 ? incident.flags.join(", ") : "none"}`,
      `Description: ${incident.description || "(none provided)"}`,
    ].join("\n");

    const response = await model.generateContent({
      contents: [{ role: "user", parts: [{ text: userContent }] }],
      generationConfig: { maxOutputTokens: 512 },
    });

    const text = response.response.text();
    const parsed = safeParseAssessment(text);

    if (!isValidResult(parsed)) {
      throw new Error(`Invalid assessment shape from model: ${text.slice(0, 200)}`);
    }

    const result: AssessmentResult = {
      ...parsed,
      source: "AI",
      assessedAt: now,
    };

    return NextResponse.json(result);
  } catch (err) {
    const reason =
      err instanceof Error ? err.message.slice(0, 120) : "Unknown error";
    const result = heuristicAssess(incident);
    result.fallbackReason = reason;
    return NextResponse.json(result);
  }
}
