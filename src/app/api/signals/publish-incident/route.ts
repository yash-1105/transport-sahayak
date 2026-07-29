// Publish a newly reported incident to the local Signals DPG as an
// incident_1.0 item. Fire-and-forget from the UI's perspective: always
// responds 200 with a `source` discriminator so a Signals outage can never
// surface as an error in the incident flow.

import { NextRequest, NextResponse } from "next/server";
import {
  SIGNALS_INCIDENT_DOMAIN,
  SIGNALS_INCIDENT_TYPE,
  SIGNALS_NETWORK,
  incidentToItemState,
  signalsFetch,
} from "@/lib/signalsClient";
import type { AccidentReport } from "@/lib/types";

export async function POST(req: NextRequest) {
  const { incident } = (await req.json()) as { incident: AccidentReport };
  if (!incident?.id) {
    return NextResponse.json({ source: "invalid", error: "incident required" });
  }

  const result = await signalsFetch<{ item_type: string; item_id: string }>(
    "/api/v1/item/create",
    {
      method: "POST",
      body: JSON.stringify({
        item_network: SIGNALS_NETWORK,
        item_domain: SIGNALS_INCIDENT_DOMAIN,
        item_type: SIGNALS_INCIDENT_TYPE,
        item_state: incidentToItemState(incident),
        ...(incident.location
          ? {
              item_locations: [
                {
                  lat: incident.location.lat,
                  lng: incident.location.lng,
                  label: incident.locationLabel || "Incident location",
                },
              ],
            }
          : {}),
      }),
    },
  );

  if (!result.ok) {
    return NextResponse.json({ source: result.reason, detail: result.detail ?? null });
  }
  return NextResponse.json({ source: "signals", itemId: result.data.item_id });
}
