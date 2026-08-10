"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import type {
  AccidentReport,
  AssessmentResult,
  AssessmentSeverity,
  Hospital,
  PoliceStation,
  AmbulanceStation,
  FireStation,
  TowingStation,
  HospitalCandidate,
  RankedHospital,
  NearestPolice,
  NearestFireStation,
  NearestTowingStation,
  NearestAmbulanceStation,
  RouteEstimatedPayload,
  EventLogEntry,
  GooglePlace,
  DispatchRecord,
} from "@/lib/types";
import {
  buildCandidates,
  shortlistByDistance,
  rankCandidatesByTraffic,
  rankCandidatesByDistance,
  type TrafficResult,
} from "@/lib/candidates";
import {
  findNearestPolice,
  findNearestFireStation,
  findNearestTowingStation,
  findNearestAmbulanceStation,
  haversineEtaMinutes,
  AVG_AMBULANCE_SPEED_KMPH,
  AVG_FIRE_TRUCK_SPEED_KMPH,
  AVG_TOWING_SPEED_KMPH,
} from "@/lib/matching";
import { generateHospitalAlert, generatePoliceAlert } from "@/lib/dispatch";
import { useRoutingStore, type SimulatedVehicleKind } from "@/store/routingStore";
import { useEventLog } from "@/store/eventLog";
import { useSignalsSync } from "@/store/signalsSyncStore";
import { publishDispatch } from "@/lib/signalsPublisher";
import { C, RADIUS } from "@/lib/design";

// Caps section-label style shared across the post-report cards.
const CAPS: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: ".09em", textTransform: "uppercase", color: C.muted };

// ── Shared helpers ────────────────────────────────────────────────────────────

const SEV_COLOR: Record<1|2|3|4, string> = {
  1: "#15803d", 2: "#b45309", 3: "#c2410c", 4: "#b91c1c",
};

function makeDispatchId() {
  return `DSP-${Date.now()}-${Math.random().toString(36).slice(2, 7).toUpperCase()}`;
}

function toIST(iso: string): string {
  return (
    new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(iso)) + " IST"
  );
}

// ── Subcomponents ─────────────────────────────────────────────────────────────

function HospitalCard({ ranked, isTop }: { ranked: RankedHospital; isTop: boolean }) {
  const h = ranked.hospital;
  const hasTraffic = ranked.roadDistanceKm !== null && ranked.roadDurationMin !== null;
  return (
    <div
      className="flex items-center"
      style={{
        gap: 12,
        padding: "12px 14px",
        borderRadius: 12,
        border: isTop ? `1.5px solid ${C.blue}` : `1px solid ${C.border}`,
        background: isTop ? "#F7FAFE" : "#fff",
      }}
    >
      <span
        className="inline-flex items-center justify-center flex-none"
        style={{
          width: 24, height: 24, borderRadius: "50%",
          background: isTop ? C.navy800 : C.page,
          color: isTop ? "#fff" : C.secondary,
          fontSize: 12, fontWeight: 700,
        }}
      >
        {ranked.rank}
      </span>
      <span className="flex-1" style={{ minWidth: 0 }}>
        <span className="flex items-center" style={{ gap: 8 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600, color: C.ink }}>{h.name}</span>
          {h.capabilitySource === "unverified" ? (
            <span style={{ fontSize: 10.5, fontWeight: 600, background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, color: C.saffronSoftText, borderRadius: RADIUS.pill, padding: "2px 8px", flex: "none" }}>
              Unverified
            </span>
          ) : h.traumaCapable ? (
            <span style={{ fontSize: 10.5, fontWeight: 600, background: C.redSoftBg, border: `1px solid ${C.redSoftBorder}`, color: C.redSoftText, borderRadius: RADIUS.pill, padding: "2px 8px", flex: "none" }}>
              Level {h.traumaLevel} Trauma
            </span>
          ) : null}
        </span>
        <span style={{ display: "block", fontSize: 12, color: C.muted, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {ranked.reasoning}
        </span>
      </span>
      <span className="text-right flex-none">
        {hasTraffic ? (
          <>
            <span style={{ display: "block", fontSize: 14, fontWeight: 700, color: C.blue }}>{Math.round(ranked.roadDurationMin!)} min</span>
            <span style={{ display: "block", fontSize: 11.5, color: C.muted }}>{ranked.roadDistanceKm!.toFixed(1)} km · current traffic</span>
          </>
        ) : (
          <span style={{ fontSize: 11.5, color: C.faint, fontStyle: "italic" }}>Traffic data unavailable</span>
        )}
      </span>
    </div>
  );
}

function PoliceCard({ ps }: { ps: NearestPolice }) {
  const hasTraffic = ps.roadDistanceKm !== null && ps.roadDurationMin !== null;
  return (
    <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, padding: "13px 15px" }}>
      <div style={{ ...CAPS, fontSize: 10.5, letterSpacing: ".08em" }}>Nearest police station</div>
      <div style={{ fontSize: 13.5, fontWeight: 600, marginTop: 4, color: C.ink }}>{ps.station.name}</div>
      <div style={{ fontSize: 12, color: C.muted, marginTop: 1 }}>{ps.station.district} · {ps.station.circle} circle</div>
      <div style={{ fontSize: 12.5, marginTop: 6, color: C.body }}>
        {hasTraffic ? (
          <><b style={{ color: C.blue }}>{Math.round(ps.roadDurationMin!)} min</b> · {ps.roadDistanceKm!.toFixed(1)} km</>
        ) : (
          <span style={{ color: C.faint, fontStyle: "italic" }}>Traffic data unavailable</span>
        )}
        {ps.station.phone && (
          <> · <a href={`tel:${ps.station.phone}`} style={{ color: C.blue }}>{ps.station.phone}</a></>
        )}
      </div>
    </div>
  );
}

function fmtClock(min: number): string {
  const totalSec = Math.max(0, Math.round(min * 60));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Generic emergency-vehicle ETA countdown card ──────────────────────────────
// Shared by ambulance / fire / towing — same countdown + progress-bar logic,
// styled per vehicle type. Only rendered when the severity engine actually
// recommended that agency for this incident (see wantsAmbulance/wantsFire/
// wantsTowing below) — a minor breakdown shows only towing, a severe incident
// needing all three shows all three, each on its own independent clock.

const VEHICLE_ETA_CONFIG: Record<SimulatedVehicleKind, {
  label: string; border: string; bg: string; text: string; accent: string; speedKmph: number; serviceNoun: string;
}> = {
  AMBULANCE: {
    label: "Estimated Ambulance Arrival", border: "border-green-200", bg: "bg-green-50/40",
    text: "text-green-800", accent: "#16a34a", speedKmph: AVG_AMBULANCE_SPEED_KMPH, serviceNoun: "Ambulance service",
  },
  FIRE: {
    label: "Estimated Fire Truck Arrival", border: "border-red-200", bg: "bg-red-50/40",
    text: "text-red-800", accent: "#dc2626", speedKmph: AVG_FIRE_TRUCK_SPEED_KMPH, serviceNoun: "Fire service",
  },
  TOWING: {
    label: "Estimated Tow Truck Arrival", border: "border-gray-300", bg: "bg-gray-100/60",
    text: "text-gray-700", accent: "#57534e", speedKmph: AVG_TOWING_SPEED_KMPH, serviceNoun: "Towing service",
  },
};

// Station names in the seed data follow "<Facility label> — <Location>" (e.g.
// "108 Post — Ganeshguri", "Fire Post — Maligaon"). The facility label
// ("108 Post"/"Fire Post"/"Recovery Post") reads as internal jargon to a
// reporter — what actually matters to them is which location it's coming
// from, so the card shows "<Service> inbound from <Location>" instead.
function locationFromStationName(stationName: string): string {
  const idx = stationName.indexOf("—");
  return idx >= 0 ? stationName.slice(idx + 1).trim() : stationName;
}

function EtaCountdownCard({
  kind, stationName, subtitle, distanceKm, etaMinutes, source, computedAt,
}: {
  kind: SimulatedVehicleKind;
  stationName: string;
  subtitle: string;
  distanceKm: number;
  etaMinutes: number;
  source: "road" | "straight_line";
  computedAt: string; // ISO timestamp — when this estimate was first logged, from the event log
}) {
  const cfg = VEHICLE_ETA_CONFIG[kind];
  const location = locationFromStationName(stationName);

  // Countdown is a client-side clock ticking down from `computedAt` — the
  // moment this estimate was first computed and logged (persisted in the
  // append-only event log), NOT this component's mount time. That keeps the
  // countdown consistent across closing/reopening the report panel, since
  // MatchingPanel remounts every time the report sheet is reopened. This is
  // still not a live position feed — we have no vehicle GPS, so it never
  // claims to track the vehicle; see the "not live tracking" disclaimer below
  // and the project hard rule on fake real-time data.
  const startedAt = useMemo(() => new Date(computedAt).getTime(), [computedAt]);
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const elapsedMin = (now - startedAt) / 60000;
  const remainingMin = Math.max(0, etaMinutes - elapsedMin);
  const overdue = elapsedMin >= etaMinutes;
  const progressPct = etaMinutes > 0 ? Math.min(100, (elapsedMin / etaMinutes) * 100) : 100;

  // Soft card palette per vehicle kind (design handoff shows the green ambulance
  // variant; fire/towing reuse the same layout with their own accent).
  const soft = {
    AMBULANCE: { bg: C.greenSoftBg, border: C.greenSoftBorder, accent: C.green, sub: "#4E8265", track: "#CBE5D6" },
    FIRE: { bg: C.redSoftBg, border: C.redSoftBorder, accent: C.red, sub: C.redSoftText, track: "#F0CFCB" },
    TOWING: { bg: "#F2F1ED", border: C.border, accent: "#57534e", sub: C.muted, track: "#DDD9CE" },
  }[kind];
  const barColor = overdue ? C.red : progressPct > 75 ? C.saffron : soft.accent;

  return (
    <section style={{ background: soft.bg, border: `1px solid ${soft.border}`, borderRadius: RADIUS.card, padding: "14px 16px" }}>
      <div className="flex items-start" style={{ gap: 14 }}>
        <div className="flex-1" style={{ minWidth: 0 }}>
          <div style={{ ...CAPS, fontSize: 10.5, letterSpacing: ".08em", color: soft.sub }}>{cfg.label}</div>
          <div style={{ fontSize: 14.5, fontWeight: 600, color: soft.accent, marginTop: 3 }}>
            {cfg.serviceNoun} inbound from {location}
            <span style={{ fontWeight: 500, color: soft.sub }}> · {subtitle}</span>
          </div>
          <div style={{ fontSize: 12.5, color: soft.sub, marginTop: 2 }}>
            ~{Math.round(etaMinutes)} min · {distanceKm.toFixed(1)} km — {source === "road" ? "based on current road distance" : `straight-line estimate (${cfg.speedKmph} km/h)`}
          </div>
        </div>
        <div className="text-right flex-none">
          <div style={{ fontSize: 26, fontWeight: 700, color: barColor, fontVariantNumeric: "tabular-nums" }}>
            {overdue ? "0:00" : fmtClock(remainingMin)}
          </div>
          <div style={{ fontSize: 10, letterSpacing: ".08em", color: soft.sub }}>
            {overdue ? "WINDOW ELAPSED" : "MIN : SEC REMAINING"}
          </div>
        </div>
      </div>

      {/* Countdown bar — times a static estimate; not a live position feed. */}
      <div style={{ marginTop: 10, height: 6, borderRadius: 3, background: soft.track, overflow: "hidden" }}>
        <div className="transition-all duration-1000 ease-linear" style={{ width: `${progressPct}%`, height: "100%", background: barColor, borderRadius: 3 }} />
      </div>

      {overdue && (
        <p style={{ fontSize: 11, color: C.saffronSoftText, background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 6, padding: "4px 8px", marginTop: 7 }}>
          Estimated window elapsed — this does not confirm arrival or delay; we have no live position feed for this vehicle.
        </p>
      )}
      <div style={{ fontSize: 11, color: soft.sub, marginTop: 7 }}>
        Calculated estimate — not live tracking. We do not track vehicles.
      </div>
    </section>
  );
}

function RouteLegend({ color, dash, label }: { color: string; dash?: boolean; label: string }) {
  return (
    <div className="flex items-center" style={{ gap: 8, fontSize: 12.5, color: C.body }}>
      <span
        className="flex-none"
        style={{
          width: 22,
          height: 3,
          borderRadius: 2,
          background: dash ? `repeating-linear-gradient(90deg,${color} 0 5px,transparent 5px 9px)` : color,
        }}
      />
      {label}
    </div>
  );
}

function LoadingStep({ label, done }: { label: string; done?: boolean }) {
  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border border-gray-100 rounded-lg">
      {done ? (
        <div className="w-3.5 h-3.5 rounded-full bg-green-500 flex items-center justify-center flex-shrink-0">
          <svg className="w-2 h-2 text-white" fill="none" viewBox="0 0 12 12" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M2 6l3 3 5-5" />
          </svg>
        </div>
      ) : (
        <div className="w-3.5 h-3.5 border-2 border-gray-400 border-t-gray-600 rounded-full animate-spin flex-shrink-0" />
      )}
      <p className="text-[11px] text-gray-500">{label}</p>
    </div>
  );
}

// ── Dispatch section (unchanged logic, updated labels) ────────────────────────

type DispatchPhase = "READY" | "PREVIEW" | "SENT";

interface SentRecord {
  id: string;
  to: string;
  role: "HOSPITAL" | "POLICE";
  sentAt: string;
  messageText: string;
}

function MessageBox({ text, open, onToggle }: { text: string; open: boolean; onToggle: () => void }) {
  return (
    <div className="mt-2">
      <button
        onClick={onToggle}
        className="flex items-center gap-1.5 text-[11px] text-blue-700 font-semibold hover:text-blue-900"
      >
        <svg className={`w-3 h-3 transition-transform ${open ? "rotate-90" : ""}`} fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M7.293 4.293a1 1 0 011.414 0L14 9.586l-5.293 5.293a1 1 0 01-1.414-1.414L11.586 10 6.293 4.707a1 1 0 010-1.414z" clipRule="evenodd" />
        </svg>
        {open ? "Hide message text" : "Show exact message text"}
      </button>
      {open && (
        <pre className="mt-2 text-[10px] font-mono text-gray-700 bg-gray-50 border border-gray-200 rounded-lg p-3 whitespace-pre-wrap leading-relaxed overflow-auto max-h-64">
          {text}
        </pre>
      )}
    </div>
  );
}

function SentCard({ record }: { record: SentRecord }) {
  const [showMsg, setShowMsg] = useState(false);
  const isHospital = record.role === "HOSPITAL";
  const roleLabel = isHospital ? "Hospital notified" : "Police notified";
  const tag = isHospital
    ? { bg: C.greenSoftBg, bd: C.greenSoftBorder, tx: C.greenSoftText }
    : { bg: C.blueSoftBg, bd: C.blueSoftBorder, tx: C.blueSoftText };
  return (
    <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.hairline}` }}>
      <div className="flex items-center" style={{ gap: 9 }}>
        <span className="flex-1" style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>{record.to}</span>
        <span style={{ fontSize: 10.5, fontWeight: 600, background: tag.bg, border: `1px solid ${tag.bd}`, color: tag.tx, borderRadius: RADIUS.pill, padding: "2px 9px", flex: "none" }}>
          {roleLabel}
        </span>
        <span style={{ fontSize: 12, fontWeight: 600, color: C.green, flex: "none" }}>✓ Sent</span>
      </div>
      <div style={{ fontSize: 11.5, color: C.muted, marginTop: 3 }}>
        SMS / Push · {toIST(record.sentAt)} — awaiting acknowledgement from recipient ·{" "}
        <button onClick={() => setShowMsg((v) => !v)} style={{ color: C.blue }}>
          {showMsg ? "Hide message text" : "Show exact message text"}
        </button>
      </div>
      {showMsg && (
        <pre style={{ marginTop: 8, fontSize: 10, fontFamily: "ui-monospace,Menlo,monospace", color: C.body, background: C.inset, border: `1px solid ${C.border}`, borderRadius: RADIUS.input, padding: 12, whiteSpace: "pre-wrap", lineHeight: 1.5, overflow: "auto", maxHeight: 240 }}>
          {record.messageText}
        </pre>
      )}
    </div>
  );
}

// Honest per-incident mirror indicator: "Signals ✓" only after the local
// Signals DPG acknowledged the item create; "unavailable" on failure; hidden
// entirely when the mirror is not configured. Never implies delivery beyond
// the acknowledged POST to the local instance.
function SignalsSyncBadge({ incidentId }: { incidentId: string }) {
  const entry = useSignalsSync((s) => s.byIncident[incidentId]);
  if (!entry || entry.state === "disabled") return null;
  if (entry.state === "published") {
    return (
      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200" title={`Mirrored to local Signals DPG (item ${entry.itemId})`}>
        Signals ✓
      </span>
    );
  }
  if (entry.state === "unavailable") {
    return (
      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200" title="Signals DPG instance unreachable — incident reporting is unaffected">
        Signals — unavailable
      </span>
    );
  }
  return (
    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-gray-50 text-gray-400 border border-gray-200">
      Signals…
    </span>
  );
}

function DispatchSection({
  incident, assessment, ranked, nearestPS,
}: {
  incident: AccidentReport;
  assessment: AssessmentResult;
  ranked: RankedHospital[];
  nearestPS: NearestPolice;
}) {
  const [phase, setPhase] = useState<DispatchPhase>("READY");
  const [hosMsg, setHosMsg] = useState("");
  const [psMsg, setPsMsg] = useState("");
  const [sent, setSent] = useState<SentRecord[]>([]);
  const appendDispatch = useEventLog((s) => s.appendDispatch);
  const h1 = ranked[0];
  const ps = nearestPS;

  function handlePreview() {
    setHosMsg(generateHospitalAlert(incident, assessment, h1.hospital.name, h1.roadDistanceKm, h1.roadDurationMin));
    setPsMsg(generatePoliceAlert(incident, assessment, ps.station.name, ps.roadDistanceKm, ps.roadDurationMin));
    setPhase("PREVIEW");
  }

  function sendNow(hMsg: string, pMsg: string) {
    const now = new Date().toISOString();
    const hospitalDispatch: DispatchRecord = { id: makeDispatchId(), reportId: incident.id, timestamp: now, dispatchedTo: "HOSPITAL", entityId: h1.hospital.id, entityName: h1.hospital.name, status: "NOTIFIED", routePlanningEstimateKm: h1.roadDistanceKm, messageText: hMsg };
    const policeDispatch: DispatchRecord = { id: makeDispatchId(), reportId: incident.id, timestamp: now, dispatchedTo: "POLICE", entityId: ps.station.id, entityName: ps.station.name, status: "NOTIFIED", routePlanningEstimateKm: ps.roadDistanceKm, messageText: pMsg };
    appendDispatch(hospitalDispatch);
    appendDispatch(policeDispatch);
    // Fire-and-forget Signals DPG mirror. Google-Places hospitals pass their
    // placeId so the server routes them to the "unverified" placeholder item —
    // Google names are never persisted into Signals (hard rule 6).
    publishDispatch(incident, hospitalDispatch, assessment.severity, h1.hospital.placeId ?? null);
    publishDispatch(incident, policeDispatch, assessment.severity, null);
    setSent([
      { id: h1.hospital.id, to: h1.hospital.name, role: "HOSPITAL", sentAt: now, messageText: hMsg },
      { id: ps.station.id, to: ps.station.name, role: "POLICE", sentAt: now, messageText: pMsg },
    ]);
    setPhase("SENT");
  }

  function handleSend() {
    sendNow(hosMsg, psMsg);
  }

  // Auto-dispatch: the notification records are logged the moment matching
  // delivers a target hospital + police station (i.e. right after severity
  // assessment) — no manual confirm step. Still strictly a notification
  // record (hard rule 5): nothing here implies delivery, acknowledgement or
  // tracking. Guarded so a panel remount never double-dispatches — prior
  // DISPATCH_SENT records for this incident are re-displayed instead.
  const autoDispatchRef = useRef(false);
  useEffect(() => {
    if (autoDispatchRef.current) return;
    autoDispatchRef.current = true;
    const prior = useEventLog
      .getState()
      .entries.filter((e) => e.type === "DISPATCH_SENT")
      .map((e) => e.payload as DispatchRecord)
      .filter((d) => d.reportId === incident.id && (d.dispatchedTo === "HOSPITAL" || d.dispatchedTo === "POLICE"));
    if (prior.length > 0) {
      setSent(prior.map((d) => ({
        id: d.entityId,
        to: d.entityName,
        role: d.dispatchedTo as "HOSPITAL" | "POLICE",
        sentAt: d.timestamp,
        messageText: d.messageText,
      })));
      setPhase("SENT");
      return;
    }
    sendNow(
      generateHospitalAlert(incident, assessment, h1.hospital.name, h1.roadDistanceKm, h1.roadDurationMin),
      generatePoliceAlert(incident, assessment, ps.station.name, ps.roadDistanceKm, ps.roadDurationMin),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === "READY") {
    return (
      <div className="rounded-xl border border-[#0f2044]/20 bg-[#0f2044]/5 p-4 flex flex-col gap-3">
        <div>
          <p className="text-[10px] font-black tracking-widest text-[#0f2044] uppercase mb-1">Dispatch Alert</p>
          <p className="text-xs text-gray-600 leading-relaxed">
            Send a notification to <span className="font-semibold">{h1.hospital.shortName}</span> and{" "}
            <span className="font-semibold">{ps.station.name}</span> with incident location, severity, and victim count.
          </p>
          <p className="text-[11px] text-gray-400 mt-1">
            Production delivery: SMS or push notification. Acknowledgement is recorded by the deployed system.
          </p>
        </div>
        <button
          onClick={handlePreview}
          className="w-full py-3 bg-[#0f2044] hover:bg-[#1a3567] text-white rounded-lg text-sm font-bold tracking-wide transition-colors flex items-center justify-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          Preview &amp; Send Alert
        </button>
      </div>
    );
  }

  if (phase === "PREVIEW") {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between px-1">
          <p className="text-[10px] font-black tracking-widest text-gray-800 uppercase">Preview Alert Messages</p>
          <button onClick={() => setPhase("READY")} className="text-xs text-gray-400 hover:text-gray-700">← Back</button>
        </div>
        <div className="rounded-xl border border-gray-300 bg-gray-50 p-3 flex flex-col gap-1.5">
          <div className="flex justify-between"><p className="text-xs font-bold text-gray-800">Hospital alert</p><span className="text-[10px] text-gray-400">To: {h1.hospital.name}</span></div>
          <MessageBox text={hosMsg} open={true} onToggle={() => {}} />
        </div>
        <div className="rounded-xl border border-gray-300 bg-gray-50 p-3 flex flex-col gap-1.5">
          <div className="flex justify-between"><p className="text-xs font-bold text-gray-800">Police alert</p><span className="text-[10px] text-gray-400">To: {ps.station.name}</span></div>
          <MessageBox text={psMsg} open={true} onToggle={() => {}} />
        </div>
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          <p className="text-[11px] text-amber-800 leading-relaxed">
            <span className="font-semibold">Production delivery:</span> These messages would be sent via SMS gateway or push. This PoC logs the record and renders the text — it does not transmit to any external system.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setPhase("READY")} className="flex-1 py-2.5 border border-gray-300 rounded-lg text-sm text-gray-600 font-semibold hover:bg-gray-50">Cancel</button>
          <button onClick={handleSend} className="flex-1 py-2.5 bg-[#0f2044] text-white rounded-lg text-sm font-bold hover:bg-[#1a3567] transition-colors flex items-center justify-center gap-1.5">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
            Confirm &amp; Log Alert
          </button>
        </div>
      </div>
    );
  }

  return (
    <section style={{ border: `1px solid ${C.border}`, borderRadius: RADIUS.card, overflow: "hidden" }}>
      <div className="flex items-center" style={{ gap: 10, padding: "12px 16px", borderBottom: `1px solid ${C.hairline}` }}>
        <span className="inline-flex items-center justify-center flex-none" style={{ width: 22, height: 22, borderRadius: "50%", background: C.green, color: "#fff", fontSize: 12 }}>✓</span>
        <span className="flex-1" style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>
          {sent.length} notifications logged — dispatched on severity assessment
        </span>
        <span style={{ fontSize: 11.5, color: C.muted }}>{sent.length > 0 ? toIST(sent[0].sentAt) : ""}</span>
      </div>
      {sent.map((rec) => <SentCard key={rec.id + rec.role} record={rec} />)}
      <div style={{ padding: "10px 16px", fontSize: 11.5, color: C.muted, background: C.inset, lineHeight: 1.5 }}>
        This log records that a notification was generated. It does not confirm delivery — no &quot;en route&quot; status is shown; the system has no real-time link to responders.
      </div>
    </section>
  );
}

// ── Main MatchingPanel ────────────────────────────────────────────────────────

export interface MatchingPanelProps {
  hospitals: Hospital[];
  policeStations: PoliceStation[];
  ambulanceStations: AmbulanceStation[];
  fireStations: FireStation[];
  towingStations: TowingStation[];
  incident: AccidentReport;
  assessment: AssessmentResult;
  /** #4: ms epoch when the ambulance was actually dispatched mid-call (Hindi
   * staged flow). When set, the ambulance ETA countdown is anchored to THIS
   * moment (so "time remaining" reflects the real dispatch time), not to when
   * matching finished. Null/undefined → anchor to the ROUTE_ESTIMATED log time
   * as before (text/SOS/English calls). */
  ambulanceDispatchedAt?: number | null;
  onReady?: () => void;
}

type Phase =
  | "fetching_places"   // Step 1: Places API for nearby hospitals
  | "computing_matrix"  // Step 2: Routes API matrix (N→1)
  | "loading_routes"    // Step 3: Route polylines for #1 + police
  | "done"
  | "error";

export default function MatchingPanel({
  hospitals,
  policeStations,
  ambulanceStations,
  fireStations,
  towingStations,
  incident,
  assessment,
  ambulanceDispatchedAt,
  onReady,
}: MatchingPanelProps) {
  const sev = assessment.severityScore as AssessmentSeverity;
  const accentColor = SEV_COLOR[sev];

  // Context-aware gating: only recommend/simulate the agencies the severity
  // engine actually flagged for this incident type (per the accident-index
  // rule book) — a minor breakdown gets towing only, a severe multi-hazard
  // incident gets whichever of ambulance/fire/towing the rules call for.
  // Ambulance has one safety-net exception: if the engine returned no opinion
  // at all (assessment.agencies is empty — e.g. a transient engine outage,
  // see ReportPanel's offline fallback stub), default to showing it, since
  // "no data" shouldn't silently hide the most broadly-relevant service.
  const wantsAmbulance = assessment.agencies.length === 0
    ? true
    : assessment.agencies.some((a) => a.code === "AMBULANCE");
  const wantsFire = assessment.agencies.some((a) => a.code === "FIRE");
  const wantsTowing = assessment.agencies.some((a) => a.code === "TOWING");

  interface EmergencyEta {
    distanceKm: number;
    etaMinutes: number;
    source: "road" | "straight_line";
    routeCoords: [number, number][] | null;
  }

  const [phase, setPhase] = useState<Phase>("fetching_places");
  const [phasesDone, setPhasesDone] = useState<Set<Phase>>(new Set());
  const [ranked, setRanked] = useState<RankedHospital[]>([]);
  const [nearestPS] = useState<NearestPolice>(() => findNearestPolice(policeStations, incident));
  const [nearestPSWithRoute, setNearestPSWithRoute] = useState<NearestPolice>(() =>
    findNearestPolice(policeStations, incident)
  );
  const [nearestAmbulance] = useState<NearestAmbulanceStation | null>(() =>
    wantsAmbulance && ambulanceStations.length ? findNearestAmbulanceStation(ambulanceStations, incident) : null
  );
  const [ambulanceEta, setAmbulanceEta] = useState<EmergencyEta | null>(null);
  const [nearestFire, setNearestFire] = useState<NearestFireStation | null>(() =>
    wantsFire && fireStations.length ? findNearestFireStation(fireStations, incident) : null
  );
  const [fireEta, setFireEta] = useState<EmergencyEta | null>(null);
  const [nearestTowing, setNearestTowing] = useState<NearestTowingStation | null>(() =>
    wantsTowing && towingStations.length ? findNearestTowingStation(towingStations, incident) : null
  );
  const [towingEta, setTowingEta] = useState<EmergencyEta | null>(null);
  const [routeSource, setRouteSource] = useState<"traffic" | "straight_line" | null>(null);
  const [candidateCount, setCandidateCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const setRoutes = useRoutingStore((s) => s.setRoutes);
  const upsertSimulatedVehicle = useRoutingStore((s) => s.upsertSimulatedVehicle);
  const appendHospitalMatched = useEventLog((s) => s.appendHospitalMatched);
  const appendRouteEstimated = useEventLog((s) => s.appendRouteEstimated);
  const eventLogEntries = useEventLog((s) => s.entries);

  // Each countdown card needs a stable reference point for "when was this
  // estimate first computed" that survives MatchingPanel remounting (e.g. the
  // user closes and reopens the report sheet). The event log is append-only
  // and outlives this component, so use the *earliest* logged ROUTE_ESTIMATED
  // entry of the matching entityType for this incident, rather than local
  // component state (which resets on every remount).
  function findEarliestRouteEstimatedAt(
    entries: EventLogEntry[],
    incidentId: string,
    entityType: RouteEstimatedPayload["entityType"]
  ): string | null {
    for (const e of entries) {
      if (e.type === "ROUTE_ESTIMATED") {
        const p = e.payload as RouteEstimatedPayload;
        if (p.incidentId === incidentId && p.entityType === entityType) return e.timestamp;
      }
    }
    return null;
  }

  const ambulanceEtaComputedAt = useMemo(
    () =>
      // #4: if the ambulance was dispatched mid-call (Hindi staged flow), anchor
      // the countdown to THAT moment so the remaining time reflects the real
      // dispatch time — otherwise to the earliest logged ROUTE_ESTIMATED (when
      // matching finished), exactly as before.
      (ambulanceDispatchedAt != null ? new Date(ambulanceDispatchedAt).toISOString() : null) ??
      findEarliestRouteEstimatedAt(eventLogEntries, incident.id, "AMBULANCE"),
    [ambulanceDispatchedAt, eventLogEntries, incident.id]
  );
  const fireEtaComputedAt = useMemo(
    () => findEarliestRouteEstimatedAt(eventLogEntries, incident.id, "FIRE"),
    [eventLogEntries, incident.id]
  );
  const towingEtaComputedAt = useMemo(
    () => findEarliestRouteEstimatedAt(eventLogEntries, incident.id, "TOWING"),
    [eventLogEntries, incident.id]
  );

  // Push each simulated vehicle marker to the map whenever a road-based route
  // is available, anchored to the same persisted computedAt as its countdown
  // card — so both stay in sync and both survive panel remounts. Purely
  // cosmetic: each walks its own actual highlighted route, not a real
  // position feed. One effect per vehicle type so each ticks independently.
  useEffect(() => {
    if (ambulanceEta?.source === "road" && ambulanceEta.routeCoords && ambulanceEtaComputedAt && nearestAmbulance) {
      upsertSimulatedVehicle({
        id: `sim-ambulance-${nearestAmbulance.station.id}`,
        kind: "AMBULANCE",
        coords: ambulanceEta.routeCoords,
        startedAt: ambulanceEtaComputedAt,
        durationMin: ambulanceEta.etaMinutes,
      });
    }
  }, [ambulanceEta, ambulanceEtaComputedAt, nearestAmbulance, upsertSimulatedVehicle]);

  useEffect(() => {
    if (fireEta?.source === "road" && fireEta.routeCoords && fireEtaComputedAt && nearestFire) {
      upsertSimulatedVehicle({
        id: `sim-fire-${nearestFire.station.id}`,
        kind: "FIRE",
        coords: fireEta.routeCoords,
        startedAt: fireEtaComputedAt,
        durationMin: fireEta.etaMinutes,
      });
    }
  }, [fireEta, fireEtaComputedAt, nearestFire, upsertSimulatedVehicle]);

  useEffect(() => {
    if (towingEta?.source === "road" && towingEta.routeCoords && towingEtaComputedAt && nearestTowing) {
      upsertSimulatedVehicle({
        id: `sim-towing-${nearestTowing.station.id}`,
        kind: "TOWING",
        coords: towingEta.routeCoords,
        startedAt: towingEtaComputedAt,
        durationMin: towingEta.etaMinutes,
      });
    }
  }, [towingEta, towingEtaComputedAt, nearestTowing, upsertSimulatedVehicle]);

  useEffect(() => {
    let alive = true;

    async function run() {
      // ── Step 1: Fetch matching-grade hospital candidates from the Aggregator
      // DPG (Google-Places-synced entries; specialty clinics filtered
      // server-side). Google Places itself is never queried here.
      let googlePlaces: GooglePlace[] = [];
      try {
        const res = await fetch(`/api/aggregator/responders?for_matching=1`, { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          googlePlaces = (data.places?.hospital ?? []) as GooglePlace[];
        }
      } catch {
        // Non-fatal: proceed with curated only
      }
      if (!alive) return;

      // ── Build hybrid candidates, shortlist nearest 10 ──────────────────────
      const allCandidates = buildCandidates(hospitals, googlePlaces);
      const shortlisted = shortlistByDistance(allCandidates, incident, 10);
      setCandidateCount(shortlisted.length);

      setPhasesDone((prev) => new Set([...prev, "fetching_places"]));
      setPhase("computing_matrix");

      // ── Step 2: Route Matrix (one call for all shortlisted) ─────────────────
      let trafficResults: TrafficResult[] = [];
      let usedTraffic = false;
      try {
        const res = await fetch("/api/routes/matrix", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            origins: shortlisted.map(({ candidate: c }) => ({ lat: c.lat, lng: c.lng })),
            destination: { lat: incident.location.lat, lng: incident.location.lng },
          }),
          cache: "no-store",
        });
        if (res.ok) {
          const data = await res.json();
          if (data.source === "google" && Array.isArray(data.results)) {
            trafficResults = data.results as TrafficResult[];
            usedTraffic = true;
          }
        }
      } catch {
        // Non-fatal: fall back to straight-line ranking
      }
      if (!alive) return;

      // ── Rank candidates ────────────────────────────────────────────────────
      let newRanked: RankedHospital[];
      if (usedTraffic && trafficResults.length > 0) {
        newRanked = rankCandidatesByTraffic(shortlisted, trafficResults, incident, assessment);
        setRouteSource("traffic");
      } else {
        newRanked = rankCandidatesByDistance(shortlisted, incident, assessment);
        setRouteSource("straight_line");
      }

      if (!alive) return;
      setRanked(newRanked);

      // Log the hospital match event — call onReady so the parent panel re-renders
      appendHospitalMatched(incident.id, newRanked, nearestPS);
      onReady?.();

      setPhasesDone((prev) => new Set([...prev, "computing_matrix"]));
      setPhase("loading_routes");

      // ── Step 3: Polyline routes for #1 hospital + nearest police ───────────
      const h1 = newRanked[0];
      if (!h1) {
        setPhase("done");
        setPhasesDone((prev) => new Set([...prev, "loading_routes"]));
        return;
      }

      function fetchRoute(origin: { lat: number; lng: number }) {
        return fetch("/api/routes/single", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            origin,
            destination: { lat: incident.location.lat, lng: incident.location.lng },
          }),
          cache: "no-store",
        }).then((r) => r.json());
      }

      const [hosResult, psResult, ambResult, fireResult, towingResult] = await Promise.allSettled([
        fetchRoute({ lat: h1.hospital.lat, lng: h1.hospital.lng }),
        fetchRoute({ lat: nearestPS.station.lat, lng: nearestPS.station.lng }),
        nearestAmbulance
          ? fetchRoute({ lat: nearestAmbulance.station.lat, lng: nearestAmbulance.station.lng })
          : Promise.resolve(null),
        nearestFire
          ? fetchRoute({ lat: nearestFire.station.lat, lng: nearestFire.station.lng })
          : Promise.resolve(null),
        nearestTowing
          ? fetchRoute({ lat: nearestTowing.station.lat, lng: nearestTowing.station.lng })
          : Promise.resolve(null),
      ]);

      if (!alive) return;

      const mapRoutes: ReturnType<typeof useRoutingStore.getState>["routes"] = [];

      if (hosResult.status === "fulfilled" && hosResult.value?.route) {
        const r = hosResult.value.route;
        const roadKm = r.distanceMeters / 1000;
        const roadMin = r.durationSec / 60;

        setRanked((prev) => {
          const next = [...prev];
          if (next[0]) {
            next[0] = {
              ...next[0],
              roadDistanceKm: roadKm,
              roadDurationMin: roadMin,
              routeCoords: r.coords,
            };
          }
          return next;
        });

        mapRoutes.push({
          id: `hospital-${h1.hospital.id}`,
          color: "#2563eb",
          coords: r.coords,
          label: h1.hospital.shortName,
        });

        appendRouteEstimated(incident.id, h1.hospital.id, h1.hospital.name, "HOSPITAL", roadKm, roadMin);
      }

      if (psResult.status === "fulfilled" && psResult.value?.route) {
        const r = psResult.value.route;
        const roadKm = r.distanceMeters / 1000;
        const roadMin = r.durationSec / 60;

        setNearestPSWithRoute((prev) => ({
          ...prev,
          roadDistanceKm: roadKm,
          roadDurationMin: roadMin,
          routeCoords: r.coords,
        }));

        mapRoutes.push({
          id: `police-${nearestPS.station.id}`,
          color: "#1e3a8a",
          dashArray: "6 4",
          coords: r.coords,
          label: nearestPS.station.name,
        });

        appendRouteEstimated(incident.id, nearestPS.station.id, nearestPS.station.name, "POLICE", roadKm, roadMin);
      }

      // Emergency-vehicle ETAs — always attempt Google road distance; fall
      // back to a straight-line + fixed-speed estimate. Both paths are
      // clearly labelled as calculated estimates, never presented as live
      // tracking. Each is only computed when nearestX is non-null, i.e. the
      // severity engine actually recommended that agency for this incident.
      if (nearestAmbulance) {
        if (ambResult.status === "fulfilled" && ambResult.value?.route) {
          const r = ambResult.value.route;
          const roadKm = r.distanceMeters / 1000;
          const roadMin = r.durationSec / 60;

          setAmbulanceEta({ distanceKm: roadKm, etaMinutes: roadMin, source: "road", routeCoords: r.coords });

          mapRoutes.push({
            id: `ambulance-${nearestAmbulance.station.id}`,
            color: "#16a34a",
            dashArray: "6 4",
            coords: r.coords,
            label: nearestAmbulance.station.name,
          });

          appendRouteEstimated(incident.id, nearestAmbulance.station.id, nearestAmbulance.station.name, "AMBULANCE", roadKm, roadMin);
        } else {
          const distanceKm = nearestAmbulance.straightLineKm;
          const fallbackEtaMin = haversineEtaMinutes(distanceKm, AVG_AMBULANCE_SPEED_KMPH);
          setAmbulanceEta({ distanceKm, etaMinutes: fallbackEtaMin, source: "straight_line", routeCoords: null });
          // Log this path too — the countdown card needs a persisted timestamp
          // regardless of which estimate source was used.
          appendRouteEstimated(incident.id, nearestAmbulance.station.id, nearestAmbulance.station.name, "AMBULANCE", distanceKm, fallbackEtaMin);
        }
      }

      if (nearestFire) {
        if (fireResult.status === "fulfilled" && fireResult.value?.route) {
          const r = fireResult.value.route;
          const roadKm = r.distanceMeters / 1000;
          const roadMin = r.durationSec / 60;

          setNearestFire((prev) => (prev ? { ...prev, roadDistanceKm: roadKm, roadDurationMin: roadMin, routeCoords: r.coords } : prev));
          setFireEta({ distanceKm: roadKm, etaMinutes: roadMin, source: "road", routeCoords: r.coords });

          mapRoutes.push({
            id: `fire-${nearestFire.station.id}`,
            color: "#dc2626",
            dashArray: "6 4",
            coords: r.coords,
            label: nearestFire.station.name,
          });

          appendRouteEstimated(incident.id, nearestFire.station.id, nearestFire.station.name, "FIRE", roadKm, roadMin);
        } else {
          const distanceKm = nearestFire.straightLineKm;
          const fallbackEtaMin = haversineEtaMinutes(distanceKm, AVG_FIRE_TRUCK_SPEED_KMPH);
          setFireEta({ distanceKm, etaMinutes: fallbackEtaMin, source: "straight_line", routeCoords: null });
          appendRouteEstimated(incident.id, nearestFire.station.id, nearestFire.station.name, "FIRE", distanceKm, fallbackEtaMin);
        }
      }

      if (nearestTowing) {
        if (towingResult.status === "fulfilled" && towingResult.value?.route) {
          const r = towingResult.value.route;
          const roadKm = r.distanceMeters / 1000;
          const roadMin = r.durationSec / 60;

          setNearestTowing((prev) => (prev ? { ...prev, roadDistanceKm: roadKm, roadDurationMin: roadMin, routeCoords: r.coords } : prev));
          setTowingEta({ distanceKm: roadKm, etaMinutes: roadMin, source: "road", routeCoords: r.coords });

          mapRoutes.push({
            id: `towing-${nearestTowing.station.id}`,
            color: "#57534e",
            dashArray: "6 4",
            coords: r.coords,
            label: nearestTowing.station.name,
          });

          appendRouteEstimated(incident.id, nearestTowing.station.id, nearestTowing.station.name, "TOWING", roadKm, roadMin);
        } else {
          const distanceKm = nearestTowing.straightLineKm;
          const fallbackEtaMin = haversineEtaMinutes(distanceKm, AVG_TOWING_SPEED_KMPH);
          setTowingEta({ distanceKm, etaMinutes: fallbackEtaMin, source: "straight_line", routeCoords: null });
          appendRouteEstimated(incident.id, nearestTowing.station.id, nearestTowing.station.name, "TOWING", distanceKm, fallbackEtaMin);
        }
      }

      setRoutes(mapRoutes);
      setPhasesDone((prev) => new Set([...prev, "loading_routes"]));
      setPhase("done");
    }

    run().catch((err) => {
      console.error("[MatchingPanel]", err);
      if (alive) {
        setErrorMessage(String(err));
        setPhase("error");
      }
    });

    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isLoading = phase !== "done" && phase !== "error";

  return (
    <div className="flex flex-col" style={{ gap: 12 }}>
      {/* Incident location */}
      <div className="flex items-start" style={{ gap: 8 }}>
        <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 24 24" style={{ color: accentColor }}>
          <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
        </svg>
        <div style={{ minWidth: 0 }}>
          <div className="flex items-center gap-2">
            <span style={{ ...CAPS, fontSize: 10, letterSpacing: ".08em" }}>Incident location</span>
            <SignalsSyncBadge incidentId={incident.id} />
          </div>
          <p style={{ fontSize: 12.5, color: C.body, fontWeight: 500, marginTop: 2 }}>{incident.locationLabel}</p>
        </div>
      </div>

      {/* Loading steps */}
      {isLoading && (
        <div className="flex flex-col gap-1.5">
          <LoadingStep
            label={`Fetching hospital candidates (Aggregator DPG)…${candidateCount > 0 ? ` ${candidateCount} candidates` : ""}`}
            done={phasesDone.has("fetching_places")}
          />
          <LoadingStep
            label="Computing traffic-aware drive times (Routes API)…"
            done={phasesDone.has("computing_matrix")}
          />
          <LoadingStep
            label="Loading route polylines…"
            done={phasesDone.has("loading_routes")}
          />
        </div>
      )}

      {/* Error */}
      {phase === "error" && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <p className="text-xs font-semibold text-red-800">Matching failed</p>
          {errorMessage && <p className="text-[11px] text-red-600 mt-0.5">{errorMessage}</p>}
        </div>
      )}

      {/* Ambulance ETA — only shown when the engine recommended AMBULANCE for
          this incident (or gave no opinion at all, see wantsAmbulance above).
          Shown first, above hospital/police results, since it's the most
          time-critical card when present. */}
      {phase === "done" && wantsAmbulance && nearestAmbulance && ambulanceEta && ambulanceEtaComputedAt && (
        <div>
          <EtaCountdownCard
            kind="AMBULANCE"
            stationName={nearestAmbulance.station.name}
            subtitle={`${nearestAmbulance.station.district} · ${nearestAmbulance.station.ambulanceCount} ambulances (${nearestAmbulance.station.types.join(", ")})`}
            distanceKm={ambulanceEta.distanceKm}
            etaMinutes={ambulanceEta.etaMinutes}
            source={ambulanceEta.source}
            computedAt={ambulanceEtaComputedAt}
          />
        </div>
      )}

      {/* Fire — only shown when the engine actually recommended FIRE for this
          incident. Kept directly below the ambulance card, above hospital
          results, since it's equally time-critical when present. */}
      {phase === "done" && wantsFire && nearestFire && fireEta && fireEtaComputedAt && (
        <div>
          <EtaCountdownCard
            kind="FIRE"
            stationName={nearestFire.station.name}
            subtitle={`${nearestFire.station.district} · ${nearestFire.station.vehicleTypes.join(", ")}`}
            distanceKm={fireEta.distanceKm}
            etaMinutes={fireEta.etaMinutes}
            source={fireEta.source}
            computedAt={fireEtaComputedAt}
          />
        </div>
      )}

      {/* Towing — only shown when the engine actually recommended TOWING for
          this incident. Kept directly below fire/ambulance for the same
          reason — the most time-critical, highest-severity-agency cards lead. */}
      {phase === "done" && wantsTowing && nearestTowing && towingEta && towingEtaComputedAt && (
        <div>
          <EtaCountdownCard
            kind="TOWING"
            stationName={nearestTowing.station.name}
            subtitle={`${nearestTowing.station.district} · ${nearestTowing.station.vehicleTypes.join(", ")}`}
            distanceKm={towingEta.distanceKm}
            etaMinutes={towingEta.etaMinutes}
            source={towingEta.source}
            computedAt={towingEtaComputedAt}
          />
        </div>
      )}

      {/* Hospital results */}
      {ranked.length > 0 && (
        <div>
          <div className="flex items-baseline" style={{ gap: 8, padding: "0 2px 8px" }}>
            <span style={{ ...CAPS, flex: 1 }}>Matched hospitals</span>
            <span style={{ fontSize: 11, color: C.faint }}>
              {routeSource === "traffic" ? "traffic time · trauma · specialty" : "proximity · trauma · specialty"}
            </span>
          </div>

          <div className="flex flex-col" style={{ gap: 8 }}>
            {ranked.map((r) => (
              <HospitalCard key={r.hospital.id} ranked={r} isTop={r.rank === 1} />
            ))}
          </div>

          {/* Source note */}
          {routeSource === "traffic" ? (
            <div style={{ fontSize: 11.5, color: C.greenSoftText, background: C.greenSoftBg, border: `1px solid ${C.greenSoftBorder}`, borderRadius: 9, padding: "8px 12px", marginTop: 8 }}>
              ✓ Drive times from Routes API — current traffic, vehicle leaving now. We do not track ambulances.
            </div>
          ) : routeSource === "straight_line" ? (
            <div style={{ fontSize: 11.5, color: C.saffronSoftText, background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 9, padding: "8px 12px", marginTop: 8 }}>
              ⚠ Traffic routing unavailable — ranked by straight-line distance. Set GOOGLE_MAPS_SERVER_KEY for live drive times.
            </div>
          ) : null}
        </div>
      )}

      {/* Two-up: nearest police + routes on map */}
      {(nearestPSWithRoute || phase === "done") && (
        <div className="ts-twoup" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {nearestPSWithRoute && <PoliceCard ps={nearestPSWithRoute} />}
          {phase === "done" && (
            <div style={{ border: `1px solid ${C.border}`, borderRadius: 12, padding: "13px 15px" }}>
              <div style={{ ...CAPS, fontSize: 10.5, letterSpacing: ".08em", marginBottom: 8 }}>Routes on map</div>
              <div className="flex flex-col" style={{ gap: 6 }}>
                {ranked[0] && <RouteLegend color="#2456A6" label={`Hospital — ${ranked[0].hospital.shortName}`} />}
                <RouteLegend color="#14243E" dash label={`Police — ${nearestPS.station.name}`} />
                {wantsAmbulance && nearestAmbulance && ambulanceEta?.source === "road" && (
                  <RouteLegend color="#1E7F4F" dash label={`Ambulance — ${nearestAmbulance.station.name}`} />
                )}
                {wantsFire && nearestFire && fireEta?.source === "road" && (
                  <RouteLegend color="#C6362C" dash label={`Fire — ${nearestFire.station.name}`} />
                )}
                {wantsTowing && nearestTowing && towingEta?.source === "road" && (
                  <RouteLegend color="#57534e" dash label={`Recovery — ${nearestTowing.station.name}`} />
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Dispatch log */}
      {ranked.length > 0 && (
        <DispatchSection
          incident={incident}
          assessment={assessment}
          ranked={ranked}
          nearestPS={nearestPSWithRoute}
        />
      )}
    </div>
  );
}
