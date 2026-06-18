// Severity assessment route handler
// Calls Claude (claude-sonnet-4-6) with a rule-based fallback.
// NEVER called from client-side code — API key stays server-side.

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

// Rule-based fallback — used when AI is unavailable or key is missing
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

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(ruleBased(body));
  }

  try {
    // Lazy import to keep the bundle clean — only runs server-side
    const Anthropic = (await import("@anthropic-ai/sdk")).default;
    const client = new Anthropic({ apiKey });

    const message = await client.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 256,
      messages: [
        {
          role: "user",
          content: `You are a road accident severity classifier for the Assam Transport Department.
Classify the accident severity as one of: CRITICAL, SERIOUS, MINOR, or UNKNOWN.

Respond with JSON only, no prose:
{"severity": "<LEVEL>", "reasoning": "<one sentence>"}

Accident details:
- Vehicles involved: ${body.vehiclesInvolved}
- Estimated casualties: ${body.estimatedCasualties}
- Description: ${body.description}`,
        },
      ],
    });

    const text = message.content[0].type === "text" ? message.content[0].text : "";
    const parsed = JSON.parse(text) as { severity: SeverityLevel; reasoning: string };

    return NextResponse.json({
      severity: parsed.severity,
      source: "AI",
      reasoning: parsed.reasoning,
    } satisfies SeverityResponse);
  } catch {
    // AI failed — fall back to rules transparently
    return NextResponse.json(ruleBased(body));
  }
}
