import { NextRequest, NextResponse } from "next/server";

const ENGINE_URL = process.env.SEVERITY_ENGINE_URL ?? "http://localhost:8000";

export async function GET(req: NextRequest) {
  const name = new URL(req.url).searchParams.get("name") ?? "";
  if (!name) {
    return NextResponse.json({ error: "name param required" }, { status: 400 });
  }
  try {
    const res = await fetch(
      `${ENGINE_URL}/categories/subtypes?name=${encodeURIComponent(name)}`,
      { cache: "no-store" }
    );
    if (!res.ok) throw new Error(`engine ${res.status}`);
    return NextResponse.json(await res.json());
  } catch (e) {
    return NextResponse.json(
      { error: "subtypes unavailable", detail: String(e) },
      { status: 503 }
    );
  }
}
