import { NextResponse } from "next/server";

const SYSTEM_PROMPT = `You are cleaning up a voice-transcribed road accident report.
Fix punctuation, capitalise sentences, and correct obvious misrecognitions of accident vocabulary
(e.g. "am balance" → "ambulance", "mota cycle" → "motorcycle").
Do NOT add or remove any facts. Do NOT translate between languages.
Return ONLY the corrected text with no commentary or explanation.`;

export async function POST(req: Request) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return NextResponse.json({ error: "AI not configured" }, { status: 503 });

  const { transcript, locale } = await req.json();
  if (!transcript || typeof transcript !== "string") {
    return NextResponse.json({ error: "Missing transcript" }, { status: 400 });
  }

  const langHint = locale === "hi-IN" ? " The text is in Hindi (Devanagari script)." : "";

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
        max_tokens: 512,
        system: SYSTEM_PROMPT + langHint,
        messages: [{ role: "user", content: transcript }],
      }),
    });

    if (!res.ok) return NextResponse.json({ error: "Upstream error" }, { status: 500 });
    const data = await res.json();
    const cleaned: string = data.content?.[0]?.text?.trim() ?? transcript;
    return NextResponse.json({ cleaned });
  } catch {
    return NextResponse.json({ error: "Cleanup failed" }, { status: 500 });
  }
}
