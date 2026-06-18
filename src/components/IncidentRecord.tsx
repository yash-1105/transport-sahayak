"use client";

import { useMemo, useCallback } from "react";
import { useEventLog } from "@/store/eventLog";
import { useT } from "@/hooks/useI18n";
import { buildIncidentRecord, recordToText } from "@/lib/incidentRecord";
import type { AssessmentSeverity } from "@/lib/types";

// ── Severity colours ──────────────────────────────────────────────────────────

const SEV_COLOR: Record<number, string> = {
  1: "#22c55e", 2: "#84cc16", 3: "#f59e0b", 4: "#f97316", 5: "#ef4444",
};

const SEV_BG: Record<number, string> = {
  1: "#dcfce7", 2: "#ecfccb", 3: "#fef9c3", 4: "#ffedd5", 5: "#fee2e2",
};

// ── IST formatter ─────────────────────────────────────────────────────────────

function toIST(iso: string) {
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

// ── Section divider ───────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="py-4 border-b border-gray-100 last:border-0">
      <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase mb-2">
        {title}
      </p>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-xs leading-relaxed">
      <span className="text-gray-400 w-28 flex-shrink-0">{label}</span>
      <span className="text-gray-800 flex-1">{value}</span>
    </div>
  );
}

// ── Download helper ───────────────────────────────────────────────────────────

function downloadText(text: string, filename: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Main component ────────────────────────────────────────────────────────────

export default function IncidentRecord({
  incidentId,
  onClose,
}: {
  incidentId: string | null;
  onClose: () => void;
}) {
  const t = useT();
  const entries = useEventLog((s) => s.entries);

  const data = useMemo(
    () => (incidentId ? buildIncidentRecord(incidentId, entries) : null),
    [incidentId, entries]
  );

  const handlePrint = useCallback(() => {
    window.print();
  }, []);

  const handleExport = useCallback(() => {
    if (!data) return;
    const text = recordToText(data);
    downloadText(text, `${data.id}.txt`);
  }, [data]);

  if (!incidentId || !data) return null;

  const { report, assessment, topHospital, allRankedHospitals, nearestPolice, routes, dispatches, rawEntries } = data;

  return (
    <>
      {/* Scrim */}
      <div
        className="fixed inset-0 z-[2200] bg-black/40 backdrop-blur-[2px] no-print"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        data-print-root
        className="fixed inset-0 z-[2201] flex flex-col bg-white"
        style={{ maxWidth: 680, margin: "0 auto" }}
      >
        {/* ── Header ── */}
        <div className="no-print flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-[#0f2044] text-white flex-shrink-0">
          <div>
            <p className="text-sm font-black tracking-wide">{t("recordTitle")}</p>
            <p className="text-[10px] text-blue-200">{incidentId}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleExport}
              className="text-[11px] font-semibold px-2.5 py-1 rounded bg-white/10 hover:bg-white/20 text-white transition-colors"
            >
              {t("recordExport")}
            </button>
            <button
              onClick={handlePrint}
              className="text-[11px] font-semibold px-2.5 py-1 rounded bg-white/10 hover:bg-white/20 text-white transition-colors"
            >
              {t("recordPrint")}
            </button>
            <button
              onClick={onClose}
              aria-label="Close record"
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-white/10 text-white flex-shrink-0 ml-1"
            >
              <svg className="w-4 h-4" aria-hidden="true" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* ── Print-only header ── */}
        <div className="print-only hidden px-6 pt-6 pb-2">
          <p className="text-xl font-black text-gray-900">Transport Sahayak — Incident Record</p>
          <p className="text-xs text-gray-500">Assam Transport Department · Road Safety Operations</p>
          <p className="text-xs text-gray-500 mt-0.5">Ref: {incidentId} · Generated: {toIST(data.generatedAt)} IST</p>
          <hr className="mt-3 border-gray-300" />
        </div>

        {/* ── Scrollable content ── */}
        <div
          className="flex-1 overflow-y-auto px-4 print-content"
          style={{ paddingBottom: "max(2rem, env(safe-area-inset-bottom))" }}
        >

          {/* Meta */}
          <div className="no-print py-3 border-b border-gray-100">
            <p className="text-[10px] text-gray-400">
              {t("recordGenerated")}: {toIST(data.generatedAt)} IST
            </p>
          </div>

          {/* Location */}
          <Section title={t("recordSectionLocation")}>
            {report ? (
              <div className="flex flex-col gap-1">
                <p className="text-sm font-semibold text-gray-900">{report.locationLabel || "—"}</p>
                <p className="text-[11px] font-mono text-gray-400">
                  {t("recordGPS")}: {report.location.lat.toFixed(5)}°N, {report.location.lng.toFixed(5)}°E
                </p>
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Report */}
          <Section title={t("recordSectionReport")}>
            {report ? (
              <div className="flex flex-col gap-1">
                <Row label={t("recordMode")}   value={report.reportMode} />
                <Row label={t("recordReported")} value={toIST(report.timestamp) + " IST"} />
                <div className="flex gap-2 text-xs">
                  <span className="text-gray-400 w-28 flex-shrink-0">{t("reportDescription")}</span>
                  <span className="text-gray-800 flex-1 leading-relaxed">
                    {report.description || <em className="text-gray-400">{t("recordNotAvail")}</em>}
                  </span>
                </div>
                <Row label={t("recordPersons")} value={report.estimatedCasualties ?? t("recordNotAvail")} />
                <Row label={t("recordFlags")} value={report.flags.length ? report.flags.join(", ") : "—"} />
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Assessment */}
          <Section title={t("recordSectionAssessment")}>
            {assessment ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className="inline-flex items-center gap-1 text-xs font-black px-2.5 py-1 rounded-full"
                    style={{
                      color: SEV_COLOR[assessment.severity as AssessmentSeverity],
                      background: SEV_BG[assessment.severity as AssessmentSeverity],
                    }}
                  >
                    {assessment.severity}/5
                  </span>
                  <span className="text-xs font-semibold text-gray-700 uppercase">
                    {assessment.priority}
                  </span>
                  <span className="text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">
                    {assessment.source === "AI" ? t("assessSourceAI") : t("assessSourceHeuristic")}
                  </span>
                </div>
                <div className="text-xs text-gray-700 leading-relaxed">
                  <span className="font-semibold text-gray-500 block mb-0.5">{t("assessRationale")}:</span>
                  {assessment.rationale}
                </div>
                <div className="text-xs text-gray-700 leading-relaxed">
                  <span className="font-semibold text-gray-500 block mb-0.5">{t("assessRecommended")}:</span>
                  {assessment.recommendedResponse}
                </div>
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Hospital */}
          <Section title={t("recordSectionHospital")}>
            {allRankedHospitals.length > 0 ? (
              <div className="flex flex-col gap-3">
                {allRankedHospitals.map((rh) => {
                  const h = rh.hospital;
                  return (
                    <div key={h.id} className="flex flex-col gap-0.5">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-black text-[#0f2044] bg-blue-50 rounded px-1">#{rh.rank}</span>
                        <span className="text-xs font-semibold text-gray-900">{h.name}</span>
                      </div>
                      <Row label={t("matchRankSubtitle")} value={h.type} />
                      <Row
                        label={t("recordEstimate")}
                        value={
                          rh.roadDistanceKm != null
                            ? `${rh.roadDistanceKm.toFixed(1)} ${t("km")}`
                            : `~${rh.straightLineKm.toFixed(1)} ${t("km")} (straight-line)`
                        }
                      />
                      <Row
                        label={t("recordDriveTime")}
                        value={
                          rh.roadDurationMin != null
                            ? `${Math.round(rh.roadDurationMin)} ${t("min")} (${t("freeFlow")})`
                            : t("recordNotAvail")
                        }
                      />
                      <Row label="Specialties" value={h.specialty.join(", ") || "—"} />
                      <Row label={t("matchBeds").split(":")[0]} value={
                        <span className="text-gray-400 italic">
                          {t("recordNoneYet")} (requires capacity feed)
                        </span>
                      } />
                      {h.capabilitySource === "curated" && (
                        <Row label="Source" value="Curated dataset" />
                      )}
                      {h.capabilitySource === "unverified" && (
                        <Row label="Source" value="Google Places — capability unverified" />
                      )}
                      {rh.reasoning && (
                        <p className="text-[10px] text-gray-400 italic mt-0.5">{rh.reasoning}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Police */}
          <Section title={t("recordSectionPolice")}>
            {nearestPolice ? (
              <div className="flex flex-col gap-0.5">
                <p className="text-xs font-semibold text-gray-900">{nearestPolice.station.name}</p>
                <Row label={t("recordSectionLocation")} value={`${nearestPolice.station.district} · ${nearestPolice.station.circle}`} />
                <Row
                  label={t("recordEstimate")}
                  value={
                    nearestPolice.roadDistanceKm != null
                      ? `${nearestPolice.roadDistanceKm.toFixed(1)} ${t("km")}`
                      : `~${nearestPolice.straightLineKm.toFixed(1)} ${t("km")} (straight-line)`
                  }
                />
                <Row
                  label={t("recordDriveTime")}
                  value={
                    nearestPolice.roadDurationMin != null
                      ? `${Math.round(nearestPolice.roadDurationMin)} ${t("min")} (${t("freeFlow")})`
                      : t("recordNotAvail")
                  }
                />
                <Row label="Emergency" value={nearestPolice.station.emergency} />
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Routes */}
          <Section title={t("recordSectionRoutes")}>
            {routes.length > 0 ? (
              <div className="flex flex-col gap-1">
                {routes.map((r) => (
                  <div key={r.entityId} className="flex items-baseline gap-2 text-xs">
                    <span className="font-semibold text-gray-800 w-32 flex-shrink-0 truncate">{r.entityName}</span>
                    <span className="text-gray-600">
                      {r.roadDistanceKm.toFixed(1)} {t("km")} · {Math.round(r.roadDurationMin)} {t("min")}
                    </span>
                  </div>
                ))}
                <p className="text-[10px] text-amber-700 italic mt-1">
                  ⚠ {t("matchFreeFlow")}
                </p>
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Alerts */}
          <Section title={t("recordSectionAlerts")}>
            {dispatches.length > 0 ? (
              <div className="flex flex-col gap-4">
                {dispatches.map((d, i) => (
                  <div key={d.id} className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <span className="w-5 h-5 rounded-full bg-green-100 text-green-700 text-[9px] font-black flex items-center justify-center flex-shrink-0">
                        ✓
                      </span>
                      <span className="text-xs font-semibold text-gray-900">{d.entityName}</span>
                    </div>
                    <Row label="Type" value={d.dispatchedTo} />
                    <Row label="Sent" value={toIST(d.timestamp) + " IST"} />
                    <Row label="Status" value={
                      <span className="text-amber-700">⊙ {t("dispatchAck")}</span>
                    } />
                    <details className="mt-1">
                      <summary className="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600">
                        {t("dispatchShowMsg")}
                      </summary>
                      <pre className="mt-1 text-[9px] text-gray-500 leading-relaxed bg-gray-50 rounded p-2 border border-gray-100 whitespace-pre-wrap font-mono overflow-x-auto">
                        {d.messageText}
                      </pre>
                    </details>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* Event log */}
          <Section title={t("recordSectionEventLog")}>
            {rawEntries.length > 0 ? (
              <div className="flex flex-col gap-0.5">
                {rawEntries.map((e) => (
                  <div key={e.id} className="flex gap-2 text-[10px] text-gray-500 font-mono">
                    <span className="text-gray-300 flex-shrink-0">{toIST(e.timestamp)}</span>
                    <span className="font-semibold text-gray-600">{e.type}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400 italic">{t("recordNoneYet")}</p>
            )}
          </Section>

          {/* System notes */}
          <Section title={t("recordSectionNotes")}>
            <p className="text-xs text-gray-500 leading-relaxed">{t("recordSystemNotes")}</p>
            <p className="text-[10px] text-amber-700 mt-2 font-medium">
              {t("sampleDataLabel")}
            </p>
          </Section>

        </div>
      </div>
    </>
  );
}
