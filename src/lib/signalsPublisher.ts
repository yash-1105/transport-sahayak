// Fire-and-forget publishing of incidents/assessments/dispatches to the
// Signals DPG mirror. Nothing here is ever awaited by UI code and every
// promise chain ends in .catch() — a Signals outage can only ever change a
// badge state, never throw into the incident flow (publish/mirror rule).

import { useSignalsSync } from "@/store/signalsSyncStore";
import type { AccidentReport, AssessmentResult, DispatchRecord } from "@/lib/types";

// incidentId → the in-flight/settled create call. Assessment and dispatch
// publishes chain on this internally (the assessment often finishes before
// the create round-trip returns the Signals item id).
const createCalls = new Map<string, Promise<string | null>>();

const setSync = (incidentId: string, state: "pending" | "published" | "unavailable" | "disabled", itemId?: string) =>
  useSignalsSync.getState().setSync(incidentId, { state, ...(itemId ? { itemId } : {}) });

// Tells the Network dashboard panel that Signals' data just changed, so it
// re-fetches a fresh rollup. Only called after a publish actually succeeded.
const bumpActivity = () => useSignalsSync.getState().bumpActivity();

async function postJson<T>(url: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// Volunteer ("Suraksha Mitra") registration mirror. Called after the Supabase
// save succeeds; never awaited by the form, always ends in .catch(). A Signals
// outage silently no-ops — the Supabase record is the source of truth. On a
// successful mirror, nudge the Network dashboard so the new responder shows up.
export interface VolunteerMirror {
  userId: string;
  name: string;
  phone?: string;
  lat: number;
  lng: number;
  locationLabel?: string;
  coverageRadiusKm?: number;
  occupation?: string;
  firstAidTrained?: boolean;
  firstAidLevel?: string;
}

export function publishVolunteer(volunteer: VolunteerMirror): void {
  void postJson<{ source: string }>("/api/signals/publish-volunteer", volunteer)
    .then((res) => {
      if (res?.source === "signals") bumpActivity();
    })
    .catch(() => {});
}

export function publishIncident(incident: AccidentReport): void {
  setSync(incident.id, "pending");
  const call = postJson<{ source: string; itemId?: string }>(
    "/api/signals/publish-incident",
    { incident },
  ).then((data) => {
    if (data?.source === "signals" && data.itemId) {
      setSync(incident.id, "published", data.itemId);
      bumpActivity();
      return data.itemId;
    }
    setSync(incident.id, data?.source === "no_key" ? "disabled" : "unavailable");
    return null;
  });
  createCalls.set(incident.id, call);
  void call.catch(() => setSync(incident.id, "unavailable"));
}

export function publishAssessment(incident: AccidentReport, assessment: AssessmentResult): void {
  const created = createCalls.get(incident.id);
  if (!created) return; // publishIncident never ran (e.g. feature untouched this session)
  void created
    .then(async (itemId) => {
      if (!itemId) return;
      const res = await postJson<{ source: string }>("/api/signals/update-incident", {
        itemId,
        incident,
        assessment,
      });
      if (res?.source === "signals") bumpActivity();
    })
    .catch(() => {});
}

export function publishDispatch(
  incident: AccidentReport,
  dispatch: DispatchRecord,
  assessedSeverity?: string | null,
  googlePlaceId?: string | null,
): void {
  const created = createCalls.get(incident.id);
  if (!created) return;
  void created
    .then(async (itemId) => {
      if (!itemId) return;
      const res = await postJson<{ source: string }>("/api/signals/publish-dispatch", {
        itemId,
        dispatch,
        assessedSeverity: assessedSeverity ?? null,
        googlePlaceId: googlePlaceId ?? null,
      });
      if (res?.source === "signals") bumpActivity();
    })
    .catch(() => {});
}
