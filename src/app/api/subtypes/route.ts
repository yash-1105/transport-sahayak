import { NextResponse } from "next/server";
import { getSeverityEngineUrl } from "@/lib/severityEngineUrl";

export async function GET() {
  try {
    const res = await fetch(`${getSeverityEngineUrl()}/subtypes`, { cache: "no-store" });
    if (!res.ok) throw new Error(`engine ${res.status}`);
    return NextResponse.json(await res.json());
  } catch (e) {
    return NextResponse.json(
      { error: "subtypes unavailable", detail: String(e) },
      { status: 503 }
    );
  }
}
