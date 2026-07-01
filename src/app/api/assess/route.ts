// Rule-first severity assessment proxy.
// POSTs {incident, signals, location} to the local Python engine and returns its JSON verbatim.
// Returns HTTP 503 if the engine is unreachable — never fabricates a result.

import { NextRequest, NextResponse } from "next/server";
import { getSeverityEngineUrl, describeFetchError } from "@/lib/severityEngineUrl";

export async function POST(req: NextRequest) {
  const body = await req.json();
  // body = { incident:{subType?,description?,language?}, signals:{...}, location:{km?,latlng?} }
  const engineUrl = getSeverityEngineUrl();
  try {
    const res = await fetch(`${engineUrl}/assess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`engine ${res.status}`);
    const assessment = await res.json();
    return NextResponse.json(assessment);
  } catch (e) {
    // Engine unreachable — fail safe, never fabricate
    return NextResponse.json(
      { error: "severity engine unavailable", detail: describeFetchError(e), engineUrl },
      { status: 503 }
    );
  }
}
