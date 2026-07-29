// Server-side proxy for Signals' aggregator dashboard (the Aggregator DPG
// view). Keeps the API key + acting-org id server-side; degrades to a
// `source`-tagged empty payload instead of erroring (existing convention).

import { NextRequest, NextResponse } from "next/server";
import { signalsFetch } from "@/lib/signalsClient";

export async function GET(req: NextRequest) {
  const orgId = process.env.SIGNALS_AGG_ORG_ID;
  if (!orgId || !process.env.SIGNALS_API_KEY) {
    return NextResponse.json({ source: "no_key" });
  }

  const params = new URLSearchParams();
  for (const key of ["page", "limit", "status", "domain", "refresh"]) {
    const v = req.nextUrl.searchParams.get(key);
    if (v) params.set(key, v);
  }
  const qs = params.toString();

  const result = await signalsFetch<Record<string, unknown>>(
    `/api/v1/aggregator/dashboard${qs ? `?${qs}` : ""}`,
    { headers: { "x-acting-org-id": orgId } },
  );

  if (!result.ok) {
    return NextResponse.json({ source: result.reason, detail: result.detail ?? null });
  }
  return NextResponse.json({ source: "signals", ...result.data });
}
