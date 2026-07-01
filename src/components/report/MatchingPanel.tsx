"use client";

import { useState, useEffect, useMemo } from "react";
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

function RankBadge({ n }: { n: 1 | 2 | 3 }) {
  const cls =
    n === 1 ? "bg-[#0f2044] text-white" :
    n === 2 ? "bg-gray-200 text-gray-700" :
              "bg-gray-100 text-gray-500";
  return (
    <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-black flex-shrink-0 ${cls}`}>
      {n}
    </span>
  );
}

function CapabilityPill({ h }: { h: HospitalCandidate }) {
  if (h.capabilitySource === "unverified") {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-semibold flex-shrink-0">
        ⚠ Unverified
      </span>
    );
  }
  if (!h.traumaCapable) {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-400 font-semibold flex-shrink-0">
        No trauma
      </span>
    );
  }
  const cls =
    h.traumaLevel === 1 ? "bg-red-100 text-red-800" :
    h.traumaLevel === 2 ? "bg-orange-100 text-orange-800" :
                          "bg-yellow-100 text-yellow-800";
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold flex-shrink-0 ${cls}`}>
      Level {h.traumaLevel} Trauma
    </span>
  );
}

function TrafficDistTag({ roadKm, roadMin, straightKm }: {
  roadKm: number | null;
  roadMin: number | null;
  straightKm: number;
}) {
  if (roadKm !== null && roadMin !== null) {
    return (
      <span className="text-xs text-gray-700 font-medium">
        {roadKm.toFixed(1)} km &middot;{" "}
        <span className="text-green-800 font-semibold">{Math.round(roadMin)} min</span>
        <span className="text-gray-400 font-normal"> current traffic</span>
      </span>
    );
  }
  return (
    <span className="text-xs text-gray-400 italic">
      Traffic data unavailable
    </span>
  );
}

function BedsField() {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-gray-400 italic">
      <svg className="w-3 h-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 7h6l2 3h10v7H3V7z" />
      </svg>
      Beds available: Awaiting hospital capacity feed
    </div>
  );
}

function HospitalCard({ ranked, isTop }: { ranked: RankedHospital; isTop: boolean }) {
  const h = ranked.hospital;
  return (
    <div className={`rounded-xl border p-3 flex flex-col gap-2 ${isTop ? "border-[#0f2044] bg-blue-50/40" : "border-gray-200 bg-white"}`}>
      <div className="flex items-start gap-2">
        <RankBadge n={ranked.rank} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-gray-900 leading-tight">{h.name}</p>
          <p className="text-[11px] text-gray-400">
            {h.type}{h.district ? ` · ${h.district}` : ""}
            {h.capabilitySource === "unverified" && (
              <span className="text-amber-600 ml-1">· Google Places</span>
            )}
          </p>
        </div>
        <CapabilityPill h={h} />
      </div>

      <div className="flex items-center gap-1.5 pl-8">
        <svg className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <TrafficDistTag
          roadKm={ranked.roadDistanceKm}
          roadMin={ranked.roadDurationMin}
          straightKm={ranked.straightLineKm}
        />
      </div>

      {h.capabilitySource === "curated" && (
        <div className="pl-8"><BedsField /></div>
      )}

      {ranked.specialtyMatches.length > 0 && (
        <div className="pl-8 flex flex-wrap gap-1">
          {ranked.specialtyMatches.map((s) => (
            <span key={s} className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100 font-medium">
              {s}
            </span>
          ))}
        </div>
      )}

      <div className="pl-8 border-t border-gray-100 pt-2">
        <p className="text-xs text-gray-600 leading-relaxed">{ranked.reasoning}</p>
      </div>
    </div>
  );
}

function PoliceCard({ ps }: { ps: NearestPolice }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-3 flex flex-col gap-1.5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-bold text-gray-900">{ps.station.name}</p>
          <p className="text-[11px] text-gray-400">{ps.station.district} · {ps.station.circle} circle</p>
        </div>
        <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#0f2044]/10 text-[#0f2044] font-semibold flex-shrink-0">
          Nearest PS
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <svg className="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <TrafficDistTag
          roadKm={ps.roadDistanceKm}
          roadMin={ps.roadDurationMin}
          straightKm={ps.straightLineKm}
        />
      </div>
      {ps.station.phone && (
        <p className="text-[11px] text-gray-400">Phone: {ps.station.phone}</p>
      )}
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
  label: string; border: string; bg: string; text: string; accent: string; speedKmph: number; roleLabel: string;
}> = {
  AMBULANCE: {
    label: "Estimated Ambulance Arrival", border: "border-green-200", bg: "bg-green-50/40",
    text: "text-green-800", accent: "#16a34a", speedKmph: AVG_AMBULANCE_SPEED_KMPH, roleLabel: "ambulance",
  },
  FIRE: {
    label: "Estimated Fire Truck Arrival", border: "border-red-200", bg: "bg-red-50/40",
    text: "text-red-800", accent: "#dc2626", speedKmph: AVG_FIRE_TRUCK_SPEED_KMPH, roleLabel: "fire truck",
  },
  TOWING: {
    label: "Estimated Tow Truck Arrival", border: "border-gray-300", bg: "bg-gray-100/60",
    text: "text-gray-700", accent: "#57534e", speedKmph: AVG_TOWING_SPEED_KMPH, roleLabel: "tow truck",
  },
};

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
  const barColor = overdue ? "#dc2626" : progressPct > 75 ? "#d97706" : cfg.accent;

  return (
    <div className={`rounded-xl border ${cfg.border} ${cfg.bg} p-3 flex flex-col gap-2`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className={`text-[10px] font-black tracking-widest uppercase ${cfg.text}`}>{cfg.label}</p>
          <p className="text-sm font-bold text-gray-900 mt-0.5">{stationName}</p>
          <p className="text-[11px] text-gray-400">{subtitle}</p>
        </div>
        <div className="text-right flex-shrink-0">
          <p className="text-2xl font-black tabular-nums leading-none" style={{ color: barColor }}>
            {overdue ? "0:00" : fmtClock(remainingMin)}
          </p>
          <p className="text-[9px] text-gray-400 uppercase tracking-wide mt-0.5">
            {overdue ? "window elapsed" : "min : sec remaining"}
          </p>
        </div>
      </div>

      {/* Countdown bar — fills as the calculated estimate window elapses.
          It times a static estimate; it does not track the vehicle's
          real-world position. */}
      <div className="h-2.5 w-full rounded-full bg-gray-200 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-1000 ease-linear"
          style={{ width: `${progressPct}%`, background: barColor }}
        />
      </div>

      <p className="text-sm text-gray-800">
        Estimated arrival <span className="font-semibold" style={{ color: cfg.accent }}>~{Math.round(etaMinutes)} min</span> from {stationName}
        <span className="text-gray-500"> · {distanceKm.toFixed(1)} km</span>
      </p>
      <p className="text-[11px] text-gray-500">
        {source === "road" ? "based on current road distance" : `straight-line estimate (${cfg.speedKmph} km/h)`}
      </p>
      {overdue && (
        <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          Estimated window elapsed — this does not confirm arrival or delay; we have no live position feed for this vehicle.
        </p>
      )}
      <p className="text-[10px] font-medium" style={{ color: cfg.accent }}>
        Calculated estimate — not live tracking. We do not track vehicles.
      </p>
    </div>
  );
}

function RouteLegend({ color, dash, label }: { color: string; dash?: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <svg width="24" height="4" className="flex-shrink-0">
        <line x1="0" y1="2" x2="24" y2="2" stroke={color} strokeWidth="3" strokeDasharray={dash ? "5 3" : undefined} />
      </svg>
      <span className="text-[11px] text-gray-600">{label}</span>
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
  const icon = record.role === "HOSPITAL" ? "🏥" : "🚔";
  const roleLabel = record.role === "HOSPITAL" ? "Hospital notified" : "Police notified";
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-3 flex flex-col gap-2">
      <div className="flex items-start gap-2">
        <div className="w-7 h-7 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0 text-sm">{icon}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <p className="text-xs font-bold text-gray-900">{record.to}</p>
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 text-green-800 font-semibold">{roleLabel}</span>
          </div>
          <p className="text-[11px] text-gray-400 mt-0.5">Sent {toIST(record.sentAt)} · SMS / Push</p>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          <span className="text-[10px] font-bold text-green-700">Sent</span>
        </div>
      </div>
      <div className="flex items-start gap-2 px-1 py-1.5 bg-amber-50 border border-amber-200 rounded-lg">
        <span className="text-amber-600 text-[11px] flex-shrink-0 mt-0.5">⊙</span>
        <p className="text-[11px] text-amber-800 leading-snug">
          <span className="font-semibold">Awaiting acknowledgement</span> — filled by the deployed production system when the recipient responds.
        </p>
      </div>
      <MessageBox text={record.messageText} open={showMsg} onToggle={() => setShowMsg((v) => !v)} />
    </div>
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

  function handleSend() {
    const now = new Date().toISOString();
    appendDispatch({ id: makeDispatchId(), reportId: incident.id, timestamp: now, dispatchedTo: "HOSPITAL", entityId: h1.hospital.id, entityName: h1.hospital.name, status: "NOTIFIED", routePlanningEstimateKm: h1.roadDistanceKm, messageText: hosMsg });
    appendDispatch({ id: makeDispatchId(), reportId: incident.id, timestamp: now, dispatchedTo: "POLICE", entityId: ps.station.id, entityName: ps.station.name, status: "NOTIFIED", routePlanningEstimateKm: ps.roadDistanceKm, messageText: psMsg });
    setSent([
      { id: h1.hospital.id, to: h1.hospital.name, role: "HOSPITAL", sentAt: now, messageText: hosMsg },
      { id: ps.station.id, to: ps.station.name, role: "POLICE", sentAt: now, messageText: psMsg },
    ]);
    setPhase("SENT");
  }

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
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2.5 px-1">
        <div className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
          <svg className="w-3.5 h-3.5 text-green-700" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <div>
          <p className="text-xs font-bold text-gray-900">2 notifications logged</p>
          <p className="text-[11px] text-gray-400">{sent.length > 0 ? `Sent at ${toIST(sent[0].sentAt)}` : ""}</p>
        </div>
      </div>
      {sent.map((rec) => <SentCard key={rec.id + rec.role} record={rec} />)}
      <div className="bg-gray-50 border border-gray-200 rounded-xl px-3 py-3 flex flex-col gap-1.5">
        <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase">About this panel</p>
        <p className="text-[11px] text-gray-600 leading-relaxed">
          This log records that a notification was generated and what it said. It does not confirm delivery.{" "}
          <span className="font-semibold">No &quot;en route&quot; status is shown</span> — the system has no real-time link to the ambulance or responding officer.
        </p>
      </div>
    </div>
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
    () => findEarliestRouteEstimatedAt(eventLogEntries, incident.id, "AMBULANCE"),
    [eventLogEntries, incident.id]
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
      // ── Step 1: Fetch nearby hospitals from Places API ──────────────────────
      let googlePlaces: GooglePlace[] = [];
      try {
        const res = await fetch(
          `/api/places/nearby?type=hospital&lat=${incident.location.lat}&lng=${incident.location.lng}&radius=30000&for_matching=1`,
          { cache: "no-store" }
        );
        if (res.ok) {
          const data = await res.json();
          googlePlaces = (data.places ?? []) as GooglePlace[];
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
    <div className="flex flex-col gap-4">
      {/* Incident location */}
      <div className="flex items-start gap-2 px-1">
        <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 24 24" style={{ color: accentColor }}>
          <path d="M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
        </svg>
        <div>
          <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase">Incident location</p>
          <p className="text-xs text-gray-800 font-medium leading-snug mt-0.5">{incident.locationLabel}</p>
          <p className="text-[10px] text-gray-400 mt-0.5 font-mono">
            {incident.location.lat.toFixed(5)} N, {incident.location.lng.toFixed(5)} E
          </p>
        </div>
      </div>

      {/* Loading steps */}
      {isLoading && (
        <div className="flex flex-col gap-1.5">
          <LoadingStep
            label={`Fetching nearby hospitals (Google Places)…${candidateCount > 0 ? ` ${candidateCount} candidates` : ""}`}
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

      {/* Hospital results */}
      {ranked.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2 px-1">
            <p className="text-[10px] font-black tracking-widest uppercase" style={{ color: accentColor }}>
              Matched Hospitals
            </p>
            <p className="text-[10px] text-gray-400">
              {routeSource === "traffic"
                ? "traffic time · trauma · specialty"
                : "proximity · trauma · specialty"}
            </p>
          </div>

          {/* Source note */}
          {routeSource === "traffic" ? (
            <div className="mb-2 px-3 py-1.5 bg-green-50 border border-green-200 rounded-lg">
              <p className="text-[11px] text-green-800">
                ✓ Drive times from Routes API — current traffic, vehicle leaving now.{" "}
                <span className="text-green-600">We do not track ambulances.</span>
              </p>
            </div>
          ) : routeSource === "straight_line" ? (
            <div className="mb-2 px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg">
              <p className="text-[11px] text-amber-800">
                ⚠ Traffic routing unavailable — ranked by straight-line distance. Set GOOGLE_MAPS_SERVER_KEY for live drive times.
              </p>
            </div>
          ) : null}

          <div className="flex flex-col gap-2">
            {ranked.map((r) => (
              <HospitalCard key={r.hospital.id} ranked={r} isTop={r.rank === 1} />
            ))}
          </div>
        </div>
      )}

      {/* Police */}
      {nearestPSWithRoute && (
        <div>
          <p className="text-[10px] font-black tracking-widest uppercase mb-2 px-1" style={{ color: accentColor }}>
            Nearest Police Station
          </p>
          <PoliceCard ps={nearestPSWithRoute} />
        </div>
      )}

      {/* Fire — only shown when the engine actually recommended FIRE for this incident */}
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

      {/* Towing — only shown when the engine actually recommended TOWING for this incident */}
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

      {/* Route legend */}
      {phase === "done" && (
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-3 flex flex-col gap-2">
          <p className="text-[10px] font-black tracking-widest text-gray-400 uppercase">Routes on map</p>
          <div className="flex flex-col gap-1">
            {ranked[0] && (
              <RouteLegend color="#2563eb" label={`Hospital route — ${ranked[0].hospital.shortName}`} />
            )}
            <RouteLegend color="#1e3a8a" dash label={`Police route — ${nearestPS.station.name}`} />
            {wantsAmbulance && nearestAmbulance && ambulanceEta?.source === "road" && (
              <RouteLegend color="#16a34a" dash label={`Ambulance route — ${nearestAmbulance.station.name}`} />
            )}
            {wantsFire && nearestFire && fireEta?.source === "road" && (
              <RouteLegend color="#dc2626" dash label={`Fire route — ${nearestFire.station.name}`} />
            )}
            {wantsTowing && nearestTowing && towingEta?.source === "road" && (
              <RouteLegend color="#57534e" dash label={`Recovery route — ${nearestTowing.station.name}`} />
            )}
          </div>
          <p className="text-[11px] text-blue-800 bg-blue-50 border border-blue-200 rounded-lg px-2.5 py-1.5 leading-relaxed">
            Est. drive time from facility, current traffic — vehicle leaving now.
            We do not track ambulances or police vehicles.
          </p>
        </div>
      )}

      {/* Dispatch */}
      {ranked.length > 0 && (
        <>
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-gray-200" />
            <span className="text-[10px] font-black tracking-widest text-gray-400 uppercase">Dispatch</span>
            <div className="flex-1 h-px bg-gray-200" />
          </div>
          <DispatchSection
            incident={incident}
            assessment={assessment}
            ranked={ranked}
            nearestPS={nearestPSWithRoute}
          />
        </>
      )}
    </div>
  );
}
