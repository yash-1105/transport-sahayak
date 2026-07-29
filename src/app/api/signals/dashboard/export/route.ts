// Streams the Signals aggregator dashboard CSV export through the server
// (key + acting-org stay server-side).

import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const key = process.env.SIGNALS_API_KEY;
  const orgId = process.env.SIGNALS_AGG_ORG_ID;
  if (!key || !orgId) {
    return NextResponse.json({ source: "no_key" }, { status: 404 });
  }

  const params = new URLSearchParams();
  for (const k of ["status", "domain", "refresh"]) {
    const v = req.nextUrl.searchParams.get(k);
    if (v) params.set(k, v);
  }
  const qs = params.toString();
  const apiUrl = process.env.SIGNALS_API_URL ?? "http://localhost:2742";

  try {
    const res = await fetch(`${apiUrl}/api/v1/aggregator/dashboard/export${qs ? `?${qs}` : ""}`, {
      headers: { "x-api-key": key, "x-acting-org-id": orgId },
      cache: "no-store",
    });
    if (!res.ok || !res.body) {
      return NextResponse.json({ source: "unavailable" }, { status: 503 });
    }
    return new NextResponse(res.body, {
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": 'attachment; filename="signals-dashboard.csv"',
      },
    });
  } catch {
    return NextResponse.json({ source: "unavailable" }, { status: 503 });
  }
}
