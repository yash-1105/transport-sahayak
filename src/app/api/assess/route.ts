// Severity assessment route handler.
// Always returns HTTP 200 with a valid AssessmentResult — either from Gemini or
// the rule-based heuristic fallback. The client can rely on 200 = usable result.

import { NextRequest, NextResponse } from "next/server";
import { SchemaType, type ResponseSchema } from "@google/generative-ai";
import type { AccidentReport, AssessmentResult } from "@/lib/types";
import { heuristicAssess } from "@/lib/heuristic";

// Forces Gemini to emit complete, well-formed JSON in this exact shape.
const RESPONSE_SCHEMA: ResponseSchema = {
  type: SchemaType.OBJECT,
  properties: {
    severity: { type: SchemaType.INTEGER },
    rationale: { type: SchemaType.STRING },
    recommendedResponse: { type: SchemaType.STRING },
    priority: {
      type: SchemaType.STRING,
      format: "enum",
      enum: ["low", "medium", "high", "critical"],
    },
  },
  required: ["severity", "rationale", "recommendedResponse", "priority"],
};

const SYSTEM_PROMPT = `You are a road accident severity assessor for the Assam Transport Department emergency operations centre.

The incident description may be in Hindi, Assamese, or any other language. Always respond in English only.

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
  // Extract the first {...} block in case the model wraps it in stray prose.
  const start = stripped.indexOf("{");
  const end = stripped.lastIndexOf("}");
  const json = start !== -1 && end > start ? stripped.slice(start, end + 1) : stripped;
  return JSON.parse(json) as Partial<AssessmentResult>;
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

// Models tried in order. The free tier throttles aggressively (503 "high demand"
// / 429), so if the primary is overloaded we fall through to lighter models that
// are usually less congested.
const MODEL_CHAIN = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"];

// Transient, worth-retrying conditions: capacity (503), rate limit (429), or a
// raw network/fetch hiccup. A hard 400/401/403 is NOT retried.
function isTransient(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /\b(429|503|500|UNAVAILABLE|RESOURCE_EXHAUSTED|overloaded|high demand|fetch failed|ECONNRESET|ETIMEDOUT)\b/i.test(
    msg,
  );
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function POST(req: NextRequest) {
  const incident: AccidentReport = await req.json();
  const now = new Date().toISOString();

  const apiKey = process.env.GEMINI_API_KEY;

  if (!apiKey) {
    const result = heuristicAssess(incident);
    result.fallbackReason = "no API key";
    return NextResponse.json(result);
  }

  const userContent = [
    `Incident ID: ${incident.id}`,
    `Mode: ${incident.reportMode}`,
    `Location: ${incident.locationLabel}`,
    `Persons involved: ${incident.vehiclesInvolved ?? "unknown"}`,
    `Flags: ${incident.flags.length > 0 ? incident.flags.join(", ") : "none"}`,
    `Description: ${incident.description || "(none provided)"}`,
  ].join("\n");

  try {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI = new GoogleGenerativeAI(apiKey);

    let text: string | null = null;
    let lastErr: unknown = null;

    // Try each model; retry transient failures with exponential backoff before
    // moving to the next model. ~6 total attempts before giving up to heuristic.
    outer: for (const modelName of MODEL_CHAIN) {
      const model = genAI.getGenerativeModel({
        model: modelName,
        systemInstruction: SYSTEM_PROMPT,
      });

      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const response = await model.generateContent({
            contents: [{ role: "user", parts: [{ text: userContent }] }],
            // gemini-2.5-flash is a thinking model: reasoning tokens are drawn from
            // the same output budget, so a small cap truncates the JSON mid-string.
            // Give it ample room and force a strict JSON schema so the body is complete.
            generationConfig: {
              maxOutputTokens: 4096,
              responseMimeType: "application/json",
              responseSchema: RESPONSE_SCHEMA,
            },
          });
          text = response.response.text();
          break outer;
        } catch (e) {
          lastErr = e;
          if (!isTransient(e)) break; // hard error → try next model immediately
          await sleep(600 * (attempt + 1)); // 600ms, then 1200ms
        }
      }
    }

    if (text === null) {
      throw lastErr instanceof Error ? lastErr : new Error("All models unavailable");
    }

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
