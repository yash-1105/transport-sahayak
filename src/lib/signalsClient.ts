// Server-only client for the local Signals DPG instance (road_safety network).
//
// Publish/mirror integration: transport-new remains fully functional without
// Signals — every function here degrades to a typed "no_key"/"unavailable"
// result instead of throwing into caller flow. Only route handlers under
// /api/signals/* import this module; the key never reaches the browser.

import type { AccidentReport, AssessmentResult, DispatchRecord } from "@/lib/types";

export const SIGNALS_NETWORK = "road_safety";
export const SIGNALS_INCIDENT_DOMAIN = "control_room";
export const SIGNALS_INCIDENT_TYPE = "incident_1.0";
export const SIGNALS_RESPONDER_DOMAIN = "responder";
export const SIGNALS_RESPONDER_TYPE = "responder_facility_1.0";

const TIMEOUT_MS = 3000;

const apiUrl = () => process.env.SIGNALS_API_URL ?? "http://localhost:2742";

export function signalsEnabled(): boolean {
  return Boolean(process.env.SIGNALS_API_KEY);
}

export type SignalsResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "no_key" | "unavailable"; detail?: string };

export async function signalsFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<SignalsResult<T>> {
  const key = process.env.SIGNALS_API_KEY;
  if (!key) return { ok: false, reason: "no_key" };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${apiUrl()}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        ...(init.headers ?? {}),
      },
      cache: "no-store",
      signal: controller.signal,
    });
    const body = (await res.json().catch(() => ({}))) as T & { error?: string; message?: string };
    if (!res.ok) {
      return {
        ok: false,
        reason: "unavailable",
        detail: `${res.status} ${body?.error ?? ""} ${body?.message ?? ""}`.trim(),
      };
    }
    return { ok: true, data: body };
  } catch (e) {
    return {
      ok: false,
      reason: "unavailable",
      detail: e instanceof Error ? e.message : String(e),
    };
  } finally {
    clearTimeout(timer);
  }
}

// ── Mappers ───────────────────────────────────────────────────────────────────
// The incident_1.0 schema is additionalProperties:false and the instance runs
// ALLOW_EXTRA_SCHEMA_DATA=false — emit exactly the schema's fields and OMIT
// nulls (a null fails "type": "integer"/"string" checks; absence does not).

export function incidentToItemState(
  incident: AccidentReport,
  assessment?: AssessmentResult | null,
): Record<string, unknown> {
  return {
    "Incident ID": incident.id,
    "Reported At": incident.timestamp,
    "Report Mode": incident.reportMode,
    "Location Label": incident.locationLabel || "Unknown location",
    "Description": incident.description || "No description",
    "Severity": incident.severity,
    ...(incident.vehiclesInvolved != null ? { "Vehicles Involved": incident.vehiclesInvolved } : {}),
    ...(incident.estimatedCasualties != null ? { "Estimated Casualties": incident.estimatedCasualties } : {}),
    ...(incident.flags.length ? { "Flags": incident.flags } : {}),
    ...(incident.severitySource ? { "Severity Source": incident.severitySource } : {}),
    ...(assessment
      ? {
          "Severity Score": assessment.severityScore,
          "Assessed Severity": assessment.severity,
          ...(assessment.category ? { "Severity Category": assessment.category } : {}),
          ...(assessment.subType ? { "Sub Type": assessment.subType } : {}),
          ...(assessment.impactNote ? { "Impact Note": assessment.impactNote } : {}),
          ...(assessment.agencies.length
            ? { "Recommended Agencies": assessment.agencies.map((a) => a.label) }
            : {}),
          "Classified By": assessment.classifiedBy,
          "Low Confidence": assessment.lowConfidence,
        }
      : {}),
  };
}

export function dispatchToSnapshot(
  dispatch: DispatchRecord,
  assessedSeverity?: string | null,
  googlePlaceId?: string | null,
): Record<string, unknown> {
  return {
    "Incident ID": dispatch.reportId,
    "Dispatched To": dispatch.dispatchedTo,
    "Message Text": dispatch.messageText,
    ...(dispatch.routePlanningEstimateKm != null
      ? { "Route Planning Estimate Km": dispatch.routePlanningEstimateKm }
      : {}),
    ...(assessedSeverity ? { "Assessed Severity": assessedSeverity } : {}),
    ...(googlePlaceId ? { "Google Place ID": googlePlaceId } : {}),
  };
}
