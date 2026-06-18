import type {
  EventLogEntry,
  AccidentReport,
  AssessmentResult,
  RankedHospital,
  NearestPolice,
  RouteEstimatedPayload,
  DispatchRecord,
  SeverityAssessedPayload,
  HospitalMatchedPayload,
  DuplicateFlaggedPayload,
} from "@/lib/types";

export interface IncidentRecordData {
  id: string;
  report: AccidentReport | null;
  assessment: AssessmentResult | null;
  topHospital: RankedHospital | null;
  allRankedHospitals: RankedHospital[];
  nearestPolice: NearestPolice | null;
  routes: RouteEstimatedPayload[];
  dispatches: DispatchRecord[];
  duplicateFlags: DuplicateFlaggedPayload[];
  rawEntries: EventLogEntry[];
  generatedAt: string; // ISO 8601
}

function incidentIdOf(entry: EventLogEntry): string | null {
  const p = entry.payload;
  if (entry.type === "REPORT_CREATED") return (p as unknown as AccidentReport).id;
  if (typeof p === "object" && p !== null && "incidentId" in p)
    return (p as { incidentId: string }).incidentId;
  if (entry.type === "DISPATCH_SENT") return (p as unknown as DispatchRecord).reportId;
  if (entry.type === "DUPLICATE_FLAGGED") {
    const dup = p as unknown as DuplicateFlaggedPayload;
    return dup.newIncidentId ?? dup.existingIncidentId;
  }
  return null;
}

export function buildIncidentRecord(
  incidentId: string,
  entries: EventLogEntry[]
): IncidentRecordData {
  const rel = entries.filter((e) => incidentIdOf(e) === incidentId);

  const reportEntry = rel.find((e) => e.type === "REPORT_CREATED");
  const assessEntry = rel.find((e) => e.type === "SEVERITY_ASSESSED");
  const matchEntry = rel.find((e) => e.type === "HOSPITAL_MATCHED");
  const routes = rel
    .filter((e) => e.type === "ROUTE_ESTIMATED")
    .map((e) => e.payload as RouteEstimatedPayload);
  const dispatches = rel
    .filter((e) => e.type === "DISPATCH_SENT")
    .map((e) => e.payload as DispatchRecord);
  const duplicateFlags = rel
    .filter((e) => e.type === "DUPLICATE_FLAGGED")
    .map((e) => e.payload as DuplicateFlaggedPayload);

  const rankedHospitals = matchEntry
    ? (matchEntry.payload as HospitalMatchedPayload).rankedHospitals
    : [];

  return {
    id: incidentId,
    report: reportEntry ? (reportEntry.payload as AccidentReport) : null,
    assessment: assessEntry
      ? (assessEntry.payload as SeverityAssessedPayload).assessment
      : null,
    topHospital: rankedHospitals[0] ?? null,
    allRankedHospitals: rankedHospitals,
    nearestPolice: matchEntry
      ? (matchEntry.payload as HospitalMatchedPayload).nearestPolice
      : null,
    routes,
    dispatches,
    duplicateFlags,
    rawEntries: rel,
    generatedAt: new Date().toISOString(),
  };
}

// ── Plain-text export ─────────────────────────────────────────────────────────

function toIST(iso: string): string {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

const HR = "─".repeat(52);

export function recordToText(data: IncidentRecordData): string {
  const lines: string[] = [];
  const push = (...s: string[]) => lines.push(...s);

  push(
    "TRANSPORT SAHAYAK — INCIDENT RECORD",
    "Assam Transport Department — Road Safety Operations",
    HR,
    `Ref: ${data.id}`,
    `Generated: ${toIST(data.generatedAt)} IST`,
    HR,
    ""
  );

  // Location
  push("LOCATION", HR.slice(0, 10));
  if (data.report) {
    push(
      `Address : ${data.report.locationLabel || "Not available"}`,
      `GPS     : ${data.report.location.lat.toFixed(5)}°N, ${data.report.location.lng.toFixed(5)}°E`
    );
  } else {
    push("Not available");
  }
  push("");

  // Report
  push("REPORT", HR.slice(0, 10));
  if (data.report) {
    push(
      `Mode       : ${data.report.reportMode}`,
      `Reported   : ${toIST(data.report.timestamp)} IST`,
      `Description: ${data.report.description || "(none)"}`,
      `Persons    : ${data.report.estimatedCasualties ?? "Unknown"}`,
      `Vehicles   : ${data.report.vehiclesInvolved ?? "Unknown"}`,
      `Flags      : ${data.report.flags.length ? data.report.flags.join(", ") : "None"}`
    );
  } else {
    push("Not available");
  }
  push("");

  // Assessment
  push("SEVERITY ASSESSMENT", HR.slice(0, 10));
  if (data.assessment) {
    const a = data.assessment;
    push(
      `Severity : ${a.severity}/5`,
      `Priority : ${a.priority.toUpperCase()}`,
      `Source   : ${a.source === "AI" ? "AI assessment (claude-sonnet-4-6)" : "Heuristic fallback (rule-based)"}`,
      ...(a.fallbackReason ? [`Fallback : ${a.fallbackReason}`] : []),
      `Rationale: ${a.rationale}`,
      `Recommended response: ${a.recommendedResponse}`
    );
  } else {
    push("Not yet assessed");
  }
  push("");

  // Hospital
  push("MATCHED HOSPITAL", HR.slice(0, 10));
  if (data.topHospital) {
    const h = data.topHospital.hospital;
    const rh = data.topHospital;
    push(
      `#1 : ${h.name}`,
      `     Type: ${h.type}${h.capabilitySource === "unverified" ? " (Google Places — capability unverified)" : ""}`,
      `     Trauma Level: ${h.traumaLevel != null ? `Level ${h.traumaLevel}` : "Unverified"}`,
      `     Est. road distance: ${rh.roadDistanceKm != null ? `${rh.roadDistanceKm.toFixed(1)} km` : `~${rh.straightLineKm.toFixed(1)} km (straight-line)`}`,
      `     Est. drive time: ${rh.roadDurationMin != null ? `${Math.round(rh.roadDurationMin)} min, current traffic — vehicle leaving now` : "not available"}`,
      `     Specialties: ${h.specialty.join(", ") || "Not specified"}`,
      `     Bed count: Not shown (requires hospital capacity feed)`
    );
  } else {
    push("Not yet matched");
  }
  push("");

  // Police
  push("NEAREST POLICE STATION", HR.slice(0, 10));
  if (data.nearestPolice) {
    const ps = data.nearestPolice.station;
    const np = data.nearestPolice;
    push(
      `Station : ${ps.name}`,
      `District: ${ps.district}`,
      `Est. road distance: ${np.roadDistanceKm != null ? `${np.roadDistanceKm.toFixed(1)} km` : `~${np.straightLineKm.toFixed(1)} km (straight-line)`}`,
      `Est. drive time   : ${np.roadDurationMin != null ? `${Math.round(np.roadDurationMin)} min, current traffic — vehicle leaving now` : "not available"}`,
      `Emergency: ${ps.emergency}`
    );
  } else {
    push("Not yet matched");
  }
  push("");

  // Routes
  push("ROUTE ESTIMATES", HR.slice(0, 10));
  push("Est. drive times from facility, current traffic — vehicle leaving now. We do not track ambulances.");
  if (data.routes.length) {
    data.routes.forEach((r) => {
      push(
        `${r.entityName} (${r.entityType}): ${r.roadDistanceKm.toFixed(1)} km / ${Math.round(r.roadDurationMin)} min`
      );
    });
  } else {
    push("Not yet estimated");
  }
  push("");

  // Alerts
  push("ALERTS SENT", HR.slice(0, 10));
  if (data.dispatches.length) {
    data.dispatches.forEach((d, i) => {
      push(
        `[${i + 1}] ${d.entityName}`,
        `     Type: ${d.dispatchedTo}`,
        `     Sent: ${toIST(d.timestamp)} IST`,
        `     Status: ${d.status} — Awaiting acknowledgement`,
        `     Message:`
      );
      d.messageText.split("\n").forEach((line) => push(`       ${line}`));
      push("");
    });
  } else {
    push("No alerts sent yet");
    push("");
  }

  // Event log
  push("EVENT LOG", HR.slice(0, 10));
  if (data.rawEntries.length) {
    data.rawEntries.forEach((e) => {
      push(`${toIST(e.timestamp)} IST | ${e.type}`);
    });
  } else {
    push("(empty)");
  }
  push("");

  // Notes
  push(HR);
  push("SYSTEM NOTES");
  push(
    "Generated by Transport Sahayak (Proof of Concept).",
    "Drive times: traffic-aware estimates from Google Routes API (vehicle leaving facility now).",
    "  NOT a tracked ETA. We do not track ambulances or police vehicles.",
    "Bed availability: requires a live hospital capacity feed (not yet in place).",
    "Acknowledgement status: open field, filled by the production system on receipt.",
    "Field steps (crew movement, on-scene arrival) are not tracked —",
    "  requires GPS-equipped vehicle infrastructure not yet deployed.",
    "",
    "Sample data — replace with official dataset.",
    HR
  );

  return lines.join("\n");
}
