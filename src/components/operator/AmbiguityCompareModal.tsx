"use client";

// Operator-only side-by-side compare for a flagged ambiguity cluster. Lets the
// operator read the FULL detail of every report in the cluster — untruncated
// description, exact location (shared pin map), severity/flags/time/id, and the
// real call transcript for voice reports — so they can judge for themselves
// whether the reports are the same incident before choosing Keep-one / Separate.
// ADVISORY ONLY (Hard Rule 5): nothing here dispatches, and ignoring a duplicate
// never un-sends a notification already logged.

import { useEffect, useMemo, useState } from "react";
import { APIProvider, Map, AdvancedMarker, useMap } from "@vis.gl/react-google-maps";
import { C, RADIUS } from "@/lib/design";
import type { DbAccident } from "@/lib/types";
import type { AmbiguityCluster } from "@/lib/ambiguity";

interface CallRow {
  id: string;
  incident_id: string | null;
  locale: string | null;
  outcome: string | null;
  time_to_dispatch_ms: number | null;
  total_turns: number | null;
  caller_turns: number | null;
  questions_asked: number | null;
  transcript: { role: string; at_ms: number; text: string }[] | null;
}

// One distinct colour per report, shared between its map pin and its column
// header, so the operator can match a pin to a column at a glance.
const PIN_COLORS = ["#2456A6", "#C6362C", "#1E7F4F", "#B45309", "#4F46E5", "#0D9488", "#DB2777"];
const colorFor = (i: number) => PIN_COLORS[i % PIN_COLORS.length];

const CAPS: React.CSSProperties = { fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted };

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function fmtDur(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}
function modeLabel(mode: string): string {
  return mode === "DISPATCHER" ? "Voice" : mode;
}
function sevColor(sev: string | null): string {
  if (sev === "CRITICAL" || sev === "HIGH") return C.red;
  if (sev === "MEDIUM") return C.saffron;
  return C.secondary;
}

// Fit the shared map to all member pins (imperative, like AccidentDensityLayer).
function FitBounds({ members }: { members: DbAccident[] }) {
  const map = useMap();
  useEffect(() => {
    if (!map || members.length === 0) return;
    if (members.length === 1) {
      map.setCenter({ lat: members[0].lat, lng: members[0].lng });
      map.setZoom(16);
      return;
    }
    const bounds = new google.maps.LatLngBounds();
    for (const m of members) bounds.extend({ lat: m.lat, lng: m.lng });
    map.fitBounds(bounds, 64);
    // Pins are within ~700 m by construction — don't over-zoom a tight cluster.
    const once = google.maps.event.addListenerOnce(map, "idle", () => {
      if ((map.getZoom() ?? 0) > 17) map.setZoom(17);
    });
    return () => once.remove();
  }, [map, members]);
  return null;
}

function NumberPin({ n, color }: { n: number; color: string }) {
  return (
    <div style={{
      width: 26, height: 26, borderRadius: "50%", background: color, border: "2px solid #fff",
      boxShadow: "0 2px 6px rgba(0,0,0,0.3)", color: "#fff", fontSize: 12, fontWeight: 700,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      {n}
    </div>
  );
}

function TranscriptBlock({ call, loading }: { call: CallRow | null; loading: boolean }) {
  if (loading) return <p style={{ fontSize: 12, color: C.muted }}>Loading transcript…</p>;
  if (!call) return <p style={{ fontSize: 12, color: C.muted }}>No linked voice call found.</p>;
  const t = call.transcript ?? [];
  return (
    <>
      <div className="flex flex-wrap" style={{ gap: 6, marginBottom: 8 }}>
        <Chip label={`Dispatch ${fmtDur(call.time_to_dispatch_ms)}`} />
        <Chip label={`${call.total_turns ?? 0} turns`} />
        <Chip label={`${call.questions_asked ?? 0} questions`} />
      </div>
      {t.length === 0 ? (
        <p style={{ fontSize: 12, color: C.muted }}>No transcript captured for this call.</p>
      ) : (
        <div style={{ maxHeight: 260, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {t.map((turn, i) => {
            const caller = turn.role === "caller";
            return (
              <div key={i} style={{ display: "flex", justifyContent: caller ? "flex-start" : "flex-end" }}>
                <div style={{ background: caller ? C.blueSoftBg : C.inset, border: `1px solid ${caller ? C.blueSoftBorder : C.border}`, borderRadius: 9, padding: "6px 10px", fontSize: 12, color: C.body, maxWidth: "88%" }}>
                  <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: caller ? C.blueSoftText : C.muted, marginBottom: 1 }}>
                    {caller ? "Caller" : "Agent"} · {fmtDur(turn.at_ms)}
                  </div>
                  {turn.text}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

function Chip({ label }: { label: string }) {
  return (
    <span style={{ fontSize: 10.5, fontWeight: 600, color: C.secondary, background: C.inset, border: `1px solid ${C.border}`, borderRadius: RADIUS.pill, padding: "2px 8px" }}>
      {label}
    </span>
  );
}

export default function AmbiguityCompareModal({
  cluster,
  keepId,
  showHindi,
  onClose,
}: {
  cluster: AmbiguityCluster;
  keepId: string;
  showHindi: boolean;
  onClose: () => void;
}) {
  const browserKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY ?? "";
  const members = cluster.members;

  // Fetch the linked voice call (transcript) for each DISPATCHER report.
  const voiceMembers = useMemo(() => members.filter((m) => m.report_mode === "DISPATCHER"), [members]);
  const [calls, setCalls] = useState<Record<string, CallRow | null>>({});
  // Only "loading" if there is at least one voice report to fetch (avoids a
  // synchronous setState in the effect for the no-voice case).
  const [loadingCalls, setLoadingCalls] = useState(voiceMembers.length > 0);
  useEffect(() => {
    if (voiceMembers.length === 0) return;
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        voiceMembers.map(async (m) => {
          try {
            const res = await fetch(`/api/call-metrics?incident_id=${encodeURIComponent(m.id)}`, { cache: "no-store" });
            const body = res.ok ? ((await res.json()) as { call: CallRow | null }) : { call: null };
            return [m.id, body.call] as const;
          } catch {
            return [m.id, null] as const;
          }
        })
      );
      if (!cancelled) { setCalls(Object.fromEntries(entries)); setLoadingCalls(false); }
    })();
    return () => { cancelled = true; };
  }, [voiceMembers]);

  return (
    <>
      <div className="fixed inset-0 z-[2199]" style={{ background: "rgba(14,26,47,.5)" }} onClick={onClose} />
      <div
        className="fixed z-[2200] flex flex-col bg-white"
        style={{
          left: "50%", top: "50%", transform: "translate(-50%,-50%)",
          width: "min(1040px, 96vw)", maxHeight: "90vh",
          borderRadius: RADIUS.card, boxShadow: "0 20px 60px rgba(14,26,47,.4)", overflow: "hidden",
        }}
      >
        {/* header */}
        <div className="flex items-center" style={{ gap: 10, padding: "14px 18px", borderBottom: `1px solid ${C.hairline}` }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: C.ink }}>
              Compare reports{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 13 }}> · रिपोर्ट तुलना</span>}
            </div>
            <div style={{ fontSize: 11.5, color: C.muted, marginTop: 1 }}>{members.length} reports · {cluster.why}</div>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ width: 30, height: 30, border: `1px solid ${C.border}`, borderRadius: 8, background: C.inset, color: C.secondary, fontSize: 14, cursor: "pointer", flex: "none" }}>✕</button>
        </div>

        <div style={{ overflowY: "auto" }}>
          {/* shared pin map — same spot or not? */}
          {browserKey && members.every((m) => typeof m.lat === "number" && typeof m.lng === "number") && (
            <div style={{ height: 200, position: "relative", borderBottom: `1px solid ${C.hairline}` }}>
              <APIProvider apiKey={browserKey}>
                <Map mapId="DEMO_MAP_ID" defaultCenter={{ lat: members[0].lat, lng: members[0].lng }} defaultZoom={15} gestureHandling="greedy" disableDefaultUI className="absolute inset-0 w-full h-full">
                  <FitBounds members={members} />
                  {members.map((m, i) => (
                    <AdvancedMarker key={m.id} position={{ lat: m.lat, lng: m.lng }} title={`Report ${i + 1}`}>
                      <NumberPin n={i + 1} color={colorFor(i)} />
                    </AdvancedMarker>
                  ))}
                </Map>
              </APIProvider>
            </div>
          )}

          {/* side-by-side columns */}
          <div style={{ display: "flex", gap: 12, overflowX: "auto", padding: 16, alignItems: "stretch" }}>
            {members.map((m, i) => {
              const color = colorFor(i);
              const isKeep = m.id === keepId;
              const isVoice = m.report_mode === "DISPATCHER";
              return (
                <div key={m.id} style={{ flex: "0 0 300px", maxWidth: 300, border: `1px solid ${isKeep ? C.blueSoftBorder : C.border}`, borderRadius: RADIUS.card, background: isKeep ? C.blueSoftBg : C.card, display: "flex", flexDirection: "column", overflow: "hidden" }}>
                  {/* column header */}
                  <div className="flex items-center" style={{ gap: 8, padding: "10px 12px", borderBottom: `1px solid ${C.hairline}` }}>
                    <span style={{ width: 22, height: 22, borderRadius: "50%", background: color, color: "#fff", fontSize: 11, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}>{i + 1}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 700, color: C.ink }}>{fmtTime(m.created_at)}</div>
                      <div style={{ fontSize: 11, color: C.muted }}>{modeLabel(m.report_mode)} report</div>
                    </div>
                    {isKeep && <span style={{ fontSize: 9.5, fontWeight: 700, color: C.blueSoftText, background: "#fff", border: `1px solid ${C.blueSoftBorder}`, borderRadius: RADIUS.pill, padding: "1px 7px", flex: "none" }}>KEEP</span>}
                  </div>

                  <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
                    {/* severity + flags */}
                    <div className="flex flex-wrap" style={{ gap: 6 }}>
                      <span style={{ fontSize: 10.5, fontWeight: 700, color: "#fff", background: sevColor(m.severity), borderRadius: RADIUS.pill, padding: "2px 9px" }}>{m.severity ?? "UNKNOWN"}</span>
                      {(m.flags ?? []).map((f) => (
                        <span key={f} style={{ fontSize: 10.5, fontWeight: 600, color: C.body, background: C.inset, border: `1px solid ${C.border}`, borderRadius: RADIUS.pill, padding: "2px 8px" }}>{f}</span>
                      ))}
                    </div>

                    {/* full description */}
                    <div>
                      <div style={{ ...CAPS, marginBottom: 3 }}>Description</div>
                      <div style={{ fontSize: 12.5, color: C.body, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                        {m.description?.trim() || "(no description)"}
                      </div>
                    </div>

                    {/* location */}
                    <div>
                      <div style={{ ...CAPS, marginBottom: 3 }}>Location</div>
                      <div style={{ fontSize: 12, color: C.body }}>{m.location_label || "—"}</div>
                      <div style={{ fontSize: 11, color: C.muted, fontFamily: "ui-monospace,Menlo,monospace", marginTop: 1 }}>{m.lat.toFixed(5)}, {m.lng.toFixed(5)}</div>
                    </div>

                    {/* incident id */}
                    <div>
                      <div style={{ ...CAPS, marginBottom: 3 }}>Incident ID</div>
                      <div style={{ fontSize: 10.5, color: C.secondary, fontFamily: "ui-monospace,Menlo,monospace", wordBreak: "break-all" }}>{m.id}</div>
                    </div>

                    {/* transcript (voice only) */}
                    {isVoice && (
                      <div>
                        <div style={{ ...CAPS, marginBottom: 5 }}>Call transcript{showHindi && " · वार्तालाप"}</div>
                        <TranscriptBlock call={calls[m.id] ?? null} loading={loadingCalls} />
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* footer — honest framing, non-alarming */}
        <div style={{ padding: "10px 18px", borderTop: `1px solid ${C.hairline}`, fontSize: 11, color: C.muted }}>
          Advisory — you decide. Ignoring a duplicate marks the record and de-dups the map; it never un-sends a dispatch already logged, and never deletes the incident.
        </div>
      </div>
    </>
  );
}
