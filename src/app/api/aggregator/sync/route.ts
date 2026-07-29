// POST: trigger a Google Places → Aggregator DPG ingestion run.
// Google Places is only an ingestion source — the frontend never consumes
// its responses; it reads /api/aggregator/responders, which serves what
// this sync stored. Also runs automatically when the responders route
// detects stale data (see that route), so manual calls are optional ops.

import { NextResponse } from "next/server";
import { runGoogleSync } from "@/lib/aggregator/googleSync";

export async function POST() {
  try {
    const summary = await runGoogleSync();
    return NextResponse.json(summary);
  } catch (e) {
    return NextResponse.json({
      ran: false,
      reason: e instanceof Error ? e.message : "sync failed",
      created: 0,
      updated: 0,
      unchanged: 0,
    });
  }
}
