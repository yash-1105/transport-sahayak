import { NextRequest, NextResponse } from "next/server";

const ENGINE_URL = process.env.SEVERITY_ENGINE_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const body = await req.json();
  try {
    const res = await fetch(`${ENGINE_URL}/guess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`engine ${res.status}`);
    return NextResponse.json(await res.json());
  } catch (e) {
    return NextResponse.json(
      { error: "guess unavailable", detail: String(e) },
      { status: 503 }
    );
  }
}
