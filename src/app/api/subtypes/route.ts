import { NextResponse } from "next/server";
import { getSeverityEngineUrl, describeFetchError } from "@/lib/severityEngineUrl";

export async function GET() {
  const engineUrl = getSeverityEngineUrl();
  try {
    const res = await fetch(`${engineUrl}/subtypes`, { cache: "no-store" });
    if (!res.ok) throw new Error(`engine ${res.status}`);
    return NextResponse.json(await res.json());
  } catch (e) {
    return NextResponse.json(
      { error: "subtypes unavailable", detail: describeFetchError(e), engineUrl },
      { status: 503 }
    );
  }
}
