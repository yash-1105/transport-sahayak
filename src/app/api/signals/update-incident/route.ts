// Mirror a completed severity assessment onto the incident's Signals item.
// Sends the FULL merged item_state (not a partial patch) so the result is
// correct regardless of PATCH merge semantics, and always passes the
// additionalProperties:false schema check.

import { NextRequest, NextResponse } from "next/server";
import { incidentToItemState, signalsFetch } from "@/lib/signalsClient";
import type { AccidentReport, AssessmentResult } from "@/lib/types";

export async function POST(req: NextRequest) {
  const { itemId, incident, assessment } = (await req.json()) as {
    itemId: string;
    incident: AccidentReport;
    assessment: AssessmentResult;
  };
  if (!itemId || !incident?.id) {
    return NextResponse.json({ source: "invalid", error: "itemId and incident required" });
  }

  const result = await signalsFetch<{ item: { item_id: string } }>(
    `/api/v1/item/${itemId}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        item_state: incidentToItemState(incident, assessment),
      }),
    },
  );

  if (!result.ok) {
    return NextResponse.json({ source: result.reason, detail: result.detail ?? null });
  }
  return NextResponse.json({ source: "signals", itemId });
}
