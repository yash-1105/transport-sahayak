// AI fallback for incident-type detection.
//
// The keyword matcher in incidentClassifier.ts is indexed on an English taxonomy
// with no stemming, so Hindi / Hinglish voice transcripts (and vague English)
// score zero — none of their tokens overlap the English keywords. Rather than
// hand-maintain an ever-growing Hindi→English dictionary, we let an LLM map the
// raw description (any language) onto the SAME taxonomy. The model must copy a
// subType verbatim from the list, which we then validate back to a real entry.
//
// Mirrors the existing AI-with-fallback pattern (see /api/voice-clean): if no
// ANTHROPIC_API_KEY is set, the caller keeps the keyword result — nothing fake.

import {
  getTaxonomy,
  resolveSubtype,
  type GuessResult,
} from "@/lib/incidentClassifier";

// Built once: the taxonomy the model must choose from.
let TAXONOMY_TEXT: string | null = null;
function taxonomyText(): string {
  if (TAXONOMY_TEXT === null) {
    TAXONOMY_TEXT = getTaxonomy()
      .map(({ category, subtypes }) => `## ${category}\n${subtypes.join("\n")}`)
      .join("\n\n");
  }
  return TAXONOMY_TEXT;
}

const SYSTEM_PROMPT = `You classify a road-accident first-response report into an official incident taxonomy.
The report may be in English, Hindi (Devanagari script), or Hinglish (Hindi written in Latin letters). Translate as needed.
Pick the SINGLE incident type that best matches what the reporter described.
You MUST copy the chosen value EXACTLY from the taxonomy below — never invent or reword a type.
Respond with ONLY minified JSON, no commentary:
{"subType":"<exact type from the list>","confidence":<0.0-1.0>}
If nothing in the taxonomy plausibly fits, respond: {"subType":null,"confidence":0}

TAXONOMY (grouped by category):
`;

function extractJson(text: string): unknown {
  // Models occasionally wrap JSON in ```json fences or prose — grab the object.
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    return JSON.parse(match[0]);
  } catch {
    return null;
  }
}

// Returns a GuessResult built from a validated taxonomy entry, or null when the
// AI is unavailable / unhelpful (caller falls back to the keyword guess).
export async function aiClassify(
  description: string,
  apiKey: string,
): Promise<GuessResult | null> {
  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 128,
        system: SYSTEM_PROMPT + taxonomyText(),
        messages: [{ role: "user", content: description }],
      }),
    });
    if (!res.ok) return null;

    const data = await res.json();
    const raw = data?.content?.[0]?.text;
    if (typeof raw !== "string") return null;

    const parsed = extractJson(raw) as { subType?: unknown; confidence?: unknown } | null;
    const subTypeRaw = parsed && typeof parsed.subType === "string" ? parsed.subType : null;
    if (!subTypeRaw) return null;

    const resolved = resolveSubtype(subTypeRaw);
    if (!resolved) return null;

    const confidence =
      typeof parsed?.confidence === "number"
        ? Math.max(0, Math.min(1, parsed.confidence))
        : 0.6;

    return {
      subType: resolved.subType,
      category: resolved.category,
      confidence: Math.round(confidence * 100) / 100,
      lowConfidence: confidence < 0.5,
      candidates: [{ subType: resolved.subType, category: resolved.category }],
    };
  } catch {
    return null;
  }
}
