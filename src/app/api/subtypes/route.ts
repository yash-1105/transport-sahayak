import { NextResponse } from "next/server";

const ENGINE_URL = process.env.SEVERITY_ENGINE_URL ?? "http://localhost:8000";

export async function GET() {
  try {
    const res = await fetch(`${ENGINE_URL}/subtypes`, { cache: "no-store" });
    if (!res.ok) throw new Error(`engine ${res.status}`);
    return NextResponse.json(await res.json());
  } catch (e) {
    return NextResponse.json(
      { error: "subtypes unavailable", detail: String(e) },
      { status: 503 }
    );
  }
}
