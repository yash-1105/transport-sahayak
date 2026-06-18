"use client";

import { useState } from "react";
import { useEventLog } from "@/store/eventLog";
import { useT } from "@/hooks/useI18n";
import type {
  EventLogEntry,
  AccidentReport,
  SeverityAssessedPayload,
  HospitalMatchedPayload,
  RouteEstimatedPayload,
  DispatchRecord,
  DuplicateFlaggedPayload,
  AssessmentSeverity,
} from "@/lib/types";
import type { StringKey } from "@/i18n/strings";

// ── Time helpers ──────────────────────────────────────────────────────────────

function toIST(iso: string) {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

// ── Domain labels ─────────────────────────────────────────────────────────────

const SEV_DOT: Record<number, string> = {
  1: "#22c55e", 2: "#84cc16", 3: "#f59e0b", 4: "#f97316", 5: "#ef4444",
};

const SEV_LABEL_KEY: Record<number, StringKey> = {
  1: "sev1", 2: "sev2", 3: "sev3", 4: "sev4", 5: "sev5",
};

// ── Incident-id extractor ─────────────────────────────────────────────────────

function incidentIdOf(entry: EventLogEntry): string | null {
  switch (entry.type) {
    case "REPORT_CREATED":
      return (entry.payload as AccidentReport).id;
    case "SEVERITY_ASSESSED":
      return (entry.payload as SeverityAssessedPayload).incidentId;
    case "HOSPITAL_MATCHED":
      return (entry.payload as HospitalMatchedPayload).incidentId;
    case "ROUTE_ESTIMATED":
      return (entry.payload as RouteEstimatedPayload).incidentId;
    case "DISPATCH_SENT":
      return (entry.payload as DispatchRecord).reportId;
    case "DUPLICATE_FLAGGED":
      return (entry.payload as DuplicateFlaggedPayload).newIncidentId;
    default:
      return null;
  }
}

// ── Step data model ───────────────────────────────────────────────────────────

type StepKind =
  | "INCIDENT_CREATED"
  | "SEVERITY_ASSESSED"
  | "HOSPITAL_MATCHED"
  | "ROUTE_ESTIMATED"
  | "ALERT_SENT"
  | "DUPLICATE_FLAGGED";

interface TimelineStep {
  kind: StepKind;
  timestamp: string;
  entries: EventLogEntry[];
}

function buildSteps(entries: EventLogEntry[], incidentId: string): TimelineStep[] {
  const rel = entries.filter((e) => incidentIdOf(e) === incidentId);
  const steps: TimelineStep[] = [];

  const push = (kind: StepKind, group: EventLogEntry[]) => {
    if (!group.length) return;
    steps.push({ kind, timestamp: group[0].timestamp, entries: group });
  };

  push("INCIDENT_CREATED",  rel.filter((e) => e.type === "REPORT_CREATED"));
  push("DUPLICATE_FLAGGED", rel.filter(
    (e) => e.type === "DUPLICATE_FLAGGED" &&
           (e.payload as DuplicateFlaggedPayload).userAction === "PROCEEDED"
  ));
  push("SEVERITY_ASSESSED", rel.filter((e) => e.type === "SEVERITY_ASSESSED"));
  push("HOSPITAL_MATCHED",  rel.filter((e) => e.type === "HOSPITAL_MATCHED"));
  push("ROUTE_ESTIMATED",   rel.filter((e) => e.type === "ROUTE_ESTIMATED"));
  push("ALERT_SENT",        rel.filter((e) => e.type === "DISPATCH_SENT"));

  return steps;
}

// ── Step icons ────────────────────────────────────────────────────────────────

const STEP_DOT: Record<StepKind, { bg: string; text: string; icon: React.ReactNode }> = {
  INCIDENT_CREATED: {
    bg: "#0f2044", text: "#fff",
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
      </svg>
    ),
  },
  SEVERITY_ASSESSED: {
    bg: "#7c3aed", text: "#fff",
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  HOSPITAL_MATCHED: {
    bg: "#2563eb", text: "#fff",
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
      </svg>
    ),
  },
  ROUTE_ESTIMATED: {
    bg: "#0891b2", text: "#fff",
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
      </svg>
    ),
  },
  ALERT_SENT: {
    bg: "#059669", text: "#fff",
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
      </svg>
    ),
  },
  DUPLICATE_FLAGGED: {
    bg: "#d97706", text: "#fff",
    icon: (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    ),
  },
};

// Label keys for each step (translated at render time)
const STEP_LABEL_KEY: Record<StepKind, StringKey> = {
  INCIDENT_CREATED:  "stepCreated",
  SEVERITY_ASSESSED: "stepAssessed",
  HOSPITAL_MATCHED:  "stepMatched",
  ROUTE_ESTIMATED:   "stepRouted",
  ALERT_SENT:        "stepAlerted",
  DUPLICATE_FLAGGED: "stepDuplicate",
};

// ── Step content renderers ────────────────────────────────────────────────────

function StepContent({ step }: { step: TimelineStep }) {
  const t = useT();

  switch (step.kind) {
    case "INCIDENT_CREATED": {
      const p = step.entries[0].payload as AccidentReport;
      return (
        <div className="flex flex-col gap-0.5">
          <p className="text-xs font-semibold text-gray-800">{p.reportMode} report</p>
          <p className="text-xs text-gray-500 leading-snug">{p.locationLabel}</p>
          <p className="text-[10px] font-mono text-gray-400">
            {p.location.lat.toFixed(5)} N, {p.location.lng.toFixed(5)} E
          </p>
          {p.flags.length > 0 && (
            <p className="text-[10px] text-gray-500">{t("recordFlags")}: {p.flags.join(", ")}</p>
          )}
        </div>
      );
    }

    case "SEVERITY_ASSESSED": {
      const { assessment } = step.entries[0].payload as SeverityAssessedPayload;
      const sev = assessment.severity as AssessmentSeverity;
      const isAI = assessment.source === "AI";
      return (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className="inline-flex items-center gap-1 text-xs font-black px-2 py-0.5 rounded-full text-white"
              style={{ background: SEV_DOT[sev] }}
            >
              {sev}/5 {t(SEV_LABEL_KEY[sev])}
            </span>
            <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">
              {assessment.priority}
            </span>
          </div>
          <p className="text-[11px] text-gray-500 leading-snug line-clamp-2">
            {assessment.rationale}
          </p>
          <p className="text-[10px] text-gray-400 flex items-center gap-1">
            {isAI ? (
              <><span>🤖</span> {t("assessSourceAI")} · claude-sonnet-4-6</>
            ) : (
              <><span>⚙️</span> {t("assessSourceHeuristic")}</>
            )}
          </p>
        </div>
      );
    }

    case "HOSPITAL_MATCHED": {
      const { rankedHospitals, nearestPolice } =
        step.entries[0].payload as HospitalMatchedPayload;
      return (
        <div className="flex flex-col gap-1">
          {rankedHospitals.map((r) => {
            const dist =
              r.roadDistanceKm !== null
                ? `${r.roadDistanceKm.toFixed(1)} km road`
                : `${r.straightLineKm.toFixed(1)} km straight-line`;
            return (
              <p key={r.hospital.id} className="text-xs text-gray-700 leading-snug">
                <span className="font-semibold text-[#0f2044]">#{r.rank}</span>{" "}
                {r.hospital.shortName}
                {r.hospital.traumaCapable && (
                  <span className="text-gray-400"> · L{r.hospital.traumaLevel} trauma</span>
                )}
                <span className="text-gray-400"> · ~{dist}</span>
              </p>
            );
          })}
          <p className="text-[10px] text-gray-400">
            {t("matchNearestPS")}: {nearestPolice.station.name}
          </p>
        </div>
      );
    }

    case "ROUTE_ESTIMATED": {
      return (
        <div className="flex flex-col gap-0.5">
          {step.entries.map((e) => {
            const p = e.payload as RouteEstimatedPayload;
            return (
              <p key={e.id} className="text-xs text-gray-700 leading-snug">
                <span className="font-semibold">{p.entityName}</span>
                <span className="text-gray-400">
                  {" "}· {p.roadDistanceKm.toFixed(1)} km / {Math.round(p.roadDurationMin)} min
                </span>
              </p>
            );
          })}
          <p className="text-[10px] text-amber-700 italic">
            {t("matchFreeFlowShort")}
          </p>
        </div>
      );
    }

    case "ALERT_SENT": {
      return (
        <div className="flex flex-col gap-0.5">
          {step.entries.map((e) => {
            const p = e.payload as DispatchRecord;
            return (
              <p key={e.id} className="text-xs text-gray-700 leading-snug">
                <span className="font-semibold">{p.entityName}</span>
                <span className="text-gray-400"> → Notified · SMS/Push</span>
              </p>
            );
          })}
          <p className="text-[10px] text-gray-400">{t("dispatchAck")}</p>
        </div>
      );
    }

    case "DUPLICATE_FLAGGED": {
      const p = step.entries[0].payload as DuplicateFlaggedPayload;
      return (
        <div className="flex flex-col gap-0.5">
          <p className="text-xs text-amber-800 font-semibold">
            Potential duplicate — user chose to report anyway
          </p>
          <p className="text-xs text-gray-500">
            Within {p.distanceM} m and {p.deltaMinutes} min of{" "}
            <span className="font-mono">{p.existingIncidentId}</span>
          </p>
        </div>
      );
    }

    default:
      return null;
  }
}

// ── Single step row ───────────────────────────────────────────────────────────

function StepRow({ step, isLast }: { step: TimelineStep; isLast: boolean }) {
  const t = useT();
  const dot = STEP_DOT[step.kind];
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center flex-shrink-0">
        <div
          className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ background: dot.bg, color: dot.text }}
        >
          {dot.icon}
        </div>
        {!isLast && (
          <div className="w-px flex-1 bg-gray-200 my-1" style={{ minHeight: "1.5rem" }} />
        )}
      </div>
      <div className={`flex-1 ${!isLast ? "pb-4" : ""}`}>
        <div className="flex items-baseline gap-2 mb-1 flex-wrap">
          <span className="text-[10px] font-mono text-gray-400">{toIST(step.timestamp)}</span>
          <span className="text-[11px] font-black uppercase tracking-wide text-gray-700">
            {t(STEP_LABEL_KEY[step.kind])}
          </span>
        </div>
        <StepContent step={step} />
      </div>
    </div>
  );
}

// ── Incident group ────────────────────────────────────────────────────────────

function IncidentGroup({
  incidentId,
  entries,
  onViewRecord,
}: {
  incidentId: string;
  entries: EventLogEntry[];
  onViewRecord?: (id: string) => void;
}) {
  const t = useT();
  const [collapsed, setCollapsed] = useState(false);
  const report = entries.find(
    (e) => e.type === "REPORT_CREATED" && (e.payload as AccidentReport).id === incidentId
  )?.payload as AccidentReport | undefined;

  const steps = buildSteps(entries, incidentId);

  return (
    <div className="rounded-xl border border-gray-200 overflow-hidden">
      {/* Group header */}
      <div className="flex items-center bg-gray-50 border-b border-gray-100">
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="flex-1 flex items-center justify-between px-4 py-3 hover:bg-gray-100 transition-colors text-left"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs font-black font-mono text-[#0f2044] flex-shrink-0">
              {incidentId}
            </span>
            {report && (
              <span className="text-[11px] text-gray-500 truncate">
                {report.reportMode} · {report.locationLabel}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-[10px] text-gray-400">{steps.length} {t("timelineSteps")}</span>
            <svg
              className={`w-3.5 h-3.5 text-gray-400 transition-transform ${collapsed ? "" : "rotate-90"}`}
              fill="currentColor"
              viewBox="0 0 20 20"
            >
              <path fillRule="evenodd" d="M7.293 4.293a1 1 0 011.414 0L14 9.586l-5.293 5.293a1 1 0 01-1.414-1.414L11.586 10 6.293 4.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </div>
        </button>
        {onViewRecord && (
          <button
            onClick={() => onViewRecord(incidentId)}
            className="flex-shrink-0 flex items-center gap-1 px-3 py-3 text-[11px] font-semibold text-[#0f2044] hover:bg-blue-50 transition-colors border-l border-gray-200"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            {t("timelineViewRecord")}
          </button>
        )}
      </div>

      {/* Steps */}
      {!collapsed && (
        <div className="px-4 pt-4 pb-2">
          {steps.map((step, i) => (
            <StepRow key={`${step.kind}-${step.timestamp}`} step={step} isLast={i === steps.length - 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Skipped-duplicate card ────────────────────────────────────────────────────

function SkippedDupCard({ entry }: { entry: EventLogEntry }) {
  const t = useT();
  const p = entry.payload as DuplicateFlaggedPayload;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex gap-3">
      <div className="w-7 h-7 rounded-full bg-amber-400 flex items-center justify-center flex-shrink-0">
        {STEP_DOT.DUPLICATE_FLAGGED.icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-0.5">
          <span className="text-[10px] font-mono text-amber-600">{toIST(entry.timestamp)}</span>
          <span className="text-[11px] font-black uppercase tracking-wide text-amber-800">
            {t("dupSkippedLabel")}
          </span>
        </div>
        <p className="text-xs text-amber-800">
          New report within{" "}
          <span className="font-semibold">{p.distanceM} m</span> and{" "}
          <span className="font-semibold">{p.deltaMinutes} min</span> of{" "}
          <span className="font-mono font-semibold">{p.existingIncidentId}</span>. User chose to use the existing incident.
        </p>
      </div>
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState() {
  const t = useT();
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-center">
      <div className="w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center">
        <svg className="w-6 h-6 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
      <p className="text-sm text-gray-400 font-medium">{t("timelineEmpty")}</p>
      <p className="text-xs text-gray-400 max-w-xs">{t("timelineEmptyDesc")}</p>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function TimelinePanel({
  open,
  onClose,
  onViewRecord,
}: {
  open: boolean;
  onClose: () => void;
  onViewRecord?: (incidentId: string) => void;
}) {
  const t = useT();
  const entries = useEventLog((s) => s.entries);

  if (!open) return null;

  const incidentIds: string[] = [];
  const seen = new Set<string>();
  for (const e of entries) {
    if (e.type === "REPORT_CREATED") {
      const id = (e.payload as AccidentReport).id;
      if (!seen.has(id)) { seen.add(id); incidentIds.push(id); }
    }
  }

  const skippedDups = entries.filter(
    (e) =>
      e.type === "DUPLICATE_FLAGGED" &&
      (e.payload as DuplicateFlaggedPayload).userAction === "SKIPPED"
  );

  const hasContent = incidentIds.length > 0 || skippedDups.length > 0;

  return (
    <>
      {/* Scrim */}
      <div className="fixed inset-0 z-[2001] bg-black/30 backdrop-blur-[2px]" onClick={onClose} />

      {/* Panel */}
      <div
        className="fixed bottom-0 left-0 right-0 z-[2002] flex flex-col bg-white rounded-t-2xl shadow-2xl"
        style={{ maxHeight: "88vh" }}
      >
        {/* Handle */}
        <div className="flex justify-center pt-2.5 flex-shrink-0">
          <div className="w-10 h-1 bg-gray-200 rounded-full" />
        </div>

        {/* Header */}
        <div className="flex items-start justify-between px-4 pt-3 pb-3 border-b border-gray-100 flex-shrink-0">
          <div>
            <h2 className="text-sm font-black text-gray-900 tracking-wide">{t("timelineTitle")}</h2>
            <p className="text-[11px] text-gray-400 mt-0.5">{t("timelineSubtitle")}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close timeline"
            className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-400 flex-shrink-0"
          >
            <svg className="w-4 h-4" aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
          {!hasContent ? (
            <EmptyState />
          ) : (
            <div className="p-4 flex flex-col gap-3">
              {incidentIds.map((id) => (
                <IncidentGroup
                  key={id}
                  incidentId={id}
                  entries={entries}
                  onViewRecord={onViewRecord}
                />
              ))}

              {skippedDups.map((e) => (
                <SkippedDupCard key={e.id} entry={e} />
              ))}

              {/* System boundary */}
              <div className="mt-2 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase mb-1.5">
                  {t("timelineSysBoundary")}
                </p>
                <p className="text-xs text-gray-600 leading-relaxed">{t("timelineBoundaryBody")}</p>
                <p className="text-xs text-gray-500 leading-relaxed mt-1.5">{t("timelineAlertNote")}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
