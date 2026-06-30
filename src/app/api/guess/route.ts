import { NextRequest, NextResponse } from "next/server";
import { guess } from "@/lib/incidentClassifier";
import { aiClassify } from "@/lib/aiClassify";

export async function POST(req: NextRequest) {
  const { description } = await req.json();
  if (!description || typeof description !== "string") {
    return NextResponse.json({ error: "description required" }, { status: 400 });
  }

  // Fast path: English-indexed keyword matcher.
  const local = guess(description);
  if (local.subType && !local.lowConfidence) {
    return NextResponse.json(local);
  }

  // Fallback: the keyword matcher missed or was unsure — common for Hindi /
  // Hinglish transcripts whose words don't overlap the English taxonomy. Let the
  // LLM map the description onto the same taxonomy. Degrades to `local` with no
  // API key or on any failure (never fabricates a match).
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (apiKey) {
    const ai = await aiClassify(description, apiKey);
    if (ai && ai.confidence >= local.confidence) {
      return NextResponse.json(ai);
    }
  }

  return NextResponse.json(local);
}
