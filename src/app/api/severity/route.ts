// Legacy severity endpoint — kept for backwards compatibility.
// New code should use /api/assess instead. This route uses Gemini with a rule-based fallback.

import { NextRequest, NextResponse } from "next/server";
import type { SeverityLevel } from "@/lib/types";

interface SeverityRequest {
  description: string;
  vehiclesInvolved: number;
  estimatedCasualties: number;
}

interface SeverityResponse {
  severity: SeverityLevel;
  source: "AI" | "RULE_BASED";
  reasoning: string;
}

function ruleBased(req: SeverityRequest): SeverityResponse {
  const { vehiclesInvolved, estimatedCasualties } = req;
  let severity: SeverityLevel = "MINOR";

  if (estimatedCasualties >= 5 || vehiclesInvolved >= 4) severity = "CRITICAL";
  else if (estimatedCasualties >= 2 || vehiclesInvolved >= 2) severity = "SERIOUS";
  else if (estimatedCasualties >= 1) severity = "MINOR";
  else severity = "UNKNOWN";

  return {
    severity,
    source: "RULE_BASED",
    reasoning: `Rule: ${vehiclesInvolved} vehicle(s), ${estimatedCasualties} estimated casualty(ies).`,
  };
}

export async function POST(req: NextRequest) {
  const body: SeverityRequest = await req.json();

  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return NextResponse.json(ruleBased(body));
  }

  try {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI = new GoogleGenerativeAI(apiKey);
    const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });

    const response = await model.generateContent({
      contents: [
        {
          role: "user",
          parts: [
            {
              text: `You are a road accident severity classifier for the Assam Transport Department.
Classify the accident severity as one of: CRITICAL, SERIOUS, MINOR, or UNKNOWN.

Respond with JSON only, no prose:
{"severity": "<LEVEL>", "reasoning": "<one sentence>"}

Accident details:
- Vehicles involved: ${body.vehiclesInvolved}
- Estimated casualties: ${body.estimatedCasualties}
- Description: ${body.description}`,
            },
          ],
        },
      ],
      generationConfig: { maxOutputTokens: 256 },
    });

    const text = response.response.text().replace(/^```(?:json)?\s*/im, "").replace(/\s*```\s*$/m, "").trim();
    const parsed = JSON.parse(text) as { severity: SeverityLevel; reasoning: string };

    return NextResponse.json({
      severity: parsed.severity,
      source: "AI",
      reasoning: parsed.reasoning,
    } satisfies SeverityResponse);
  } catch {
    return NextResponse.json(ruleBased(body));
  }
}
