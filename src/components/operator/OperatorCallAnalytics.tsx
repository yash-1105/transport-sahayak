"use client";

// Post-Call Analytics for the voice dispatcher (English + Hindi). OPERATOR-ONLY
// (mounted only inside the operator Network area). Reads /api/call-metrics.
//
// HONESTY: the headline "time-to-dispatch" is ready_at → the actual dispatch
// moment (the interim ambulance dispatch when a call staged one, else the
// `submitted` event) — it never includes the post-submit briefing/ETAs.
// Charts are plain empirical summaries (a bucket histogram + an empirical
// closure-time curve), NOT rigorous Kaplan-Meier / Markov modeling.

import { useCallback, useEffect, useState } from "react";
import { C, RADIUS } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";

interface Summary {
  total_calls: number;
  dispatched: number;
  abandoned: number;
  errored: number;
  information: number;
  accident_calls: number;
  dispatch_ready_rate: number | null;
  ttd_median_ms: number | null;
  ttd_mean_ms: number | null;
  ttd_min_ms: number | null;
  ttd_max_ms: number | null;
  avg_caller_turns: number | null;
  avg_call_duration_ms: number | null;
}
interface CallRow {
  id: string;
  incident_id: string | null;
  locale: string | null;
  outcome: string | null;
  time_to_dispatch_ms: number | null;
  total_turns: number | null;
  caller_turns: number | null;
  created_at: string | null;
}
interface HistBucket { lo: number; hi: number | null; count: number }
interface MetricsResponse {
  calls: CallRow[];
  overall: Summary;
  byLocale: Record<string, Summary>;
  histogram: Record<string, HistBucket[]>;
  hist_edges_ms: number[];
}

// ── formatters ────────────────────────────────────────────────────────────────
function fmtDur(ms: number | null | undefined): string {
  if (ms == null) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
function fmtPct(x: number | null | undefined): string {
  return x == null ? "—" : `${Math.round(x * 100)}%`;
}
function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function bucketLabel(b: HistBucket): string {
  const lo = Math.round(b.lo / 1000);
  return b.hi == null ? `${lo}s+` : `${lo}–${Math.round(b.hi / 1000)}s`;
}

const CAPS: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: ".09em", textTransform: "uppercase", color: C.muted };

function outcomePill(outcome: string | null): React.CSSProperties {
  const map: Record<string, { bg: string; bd: string; tx: string }> = {
    dispatched: { bg: C.greenSoftBg, bd: C.greenSoftBorder, tx: C.greenSoftText },
    abandoned: { bg: C.saffronSoftBg, bd: C.saffronSoftBorder, tx: "#8A5A17" },
    error: { bg: C.redSoftBg, bd: C.redSoftBorder, tx: C.redSoftText },
    information: { bg: C.blueSoftBg, bd: C.blueSoftBorder, tx: C.blueSoftText },
  };
  const s = map[outcome ?? ""] ?? { bg: C.page, bd: C.border, tx: C.muted };
  return { fontSize: 11, fontWeight: 600, background: s.bg, border: `1px solid ${s.bd}`, color: s.tx, borderRadius: RADIUS.pill, padding: "2px 9px" };
}

function localeLabel(loc: string | null): string {
  return loc === "hi-IN" ? "हिंदी" : loc === "en-IN" ? "English" : "—";
}

// ── stat tile (matches the Network dashboard tiles) ───────────────────────────
function Tile({ value, label, hi, showHindi, tip, accent }: { value: string; label: string; hi?: string; showHindi: boolean; tip?: string; accent?: string }) {
  return (
    <div title={tip} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, padding: "14px 16px", minWidth: 0 }}>
      <div style={{ fontSize: 26, fontWeight: 700, color: accent ?? C.ink, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 12, color: C.secondary, marginTop: 3 }}>
        {label}{showHindi && hi && <span style={{ color: C.muted }}> · {hi}</span>}
      </div>
    </div>
  );
}

export default function OperatorCallAnalytics() {
  const { showHindi } = useBilingual();
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(false);
  const [drillId, setDrillId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(false);
    try {
      const res = await fetch("/api/call-metrics", { cache: "no-store" });
      if (!res.ok) throw new Error();
      setData((await res.json()) as MetricsResponse);
    } catch {
      setErr(true);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const o = data?.overall;
  const en = data?.byLocale["en-IN"];
  const hi = data?.byLocale["hi-IN"];

  return (
    <div style={{ padding: "18px 20px 40px", maxWidth: 1100, margin: "0 auto" }}>
      {/* Header */}
      <div className="flex items-start" style={{ gap: 12, marginBottom: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: C.ink }}>
            Post-Call Analytics{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 15 }}> · कॉल विश्लेषण</span>}
          </h2>
          {o && o.total_calls > 0 && (
            <p style={{ fontSize: 12.5, color: C.muted, marginTop: 2 }}>
              {o.total_calls} call{o.total_calls === 1 ? "" : "s"} · {o.accident_calls} accident, {o.information} information — the completion rate + time-to-dispatch cover accident calls only (never the briefing)
            </p>
          )}
        </div>
        <button onClick={() => void load()} style={{ padding: "8px 14px", border: `1px solid ${C.border}`, borderRadius: 9, background: "#fff", fontSize: 12.5, fontWeight: 600, color: C.blue, cursor: "pointer", flex: "none" }}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {err && (
        <div style={{ background: C.redSoftBg, border: `1px solid ${C.redSoftBorder}`, borderRadius: 11, padding: "11px 15px", fontSize: 12.5, color: C.redSoftText, marginBottom: 16 }}>
          Could not load call metrics.
        </div>
      )}

      {o && o.total_calls === 0 && !err && (
        <div style={{ background: C.inset, border: `1px solid ${C.border}`, borderRadius: 11, padding: "18px", fontSize: 13, color: C.secondary }}>
          No voice-dispatcher calls have been recorded yet. Metrics appear here after the first call.
        </div>
      )}

      {o && o.total_calls > 0 && (
        <>
          {/* KPI tiles — the 3 headline numbers (green on the positive one) */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, marginBottom: 20 }}>
            <Tile showHindi={showHindi} value={String(o.accident_calls)} label="Accident calls" hi="दुर्घटना कॉल" tip="Calls that reported an accident (dispatched or abandoned mid-report). Information calls are counted separately." />
            <Tile showHindi={showHindi} value={fmtPct(o.dispatch_ready_rate)} label="Completion rate" hi="पूर्णता दर" accent={C.green} tip="Share of ACCIDENT calls that reached dispatch. Information/facility/scheme/complaint calls are excluded so they don't lower it." />
            <Tile showHindi={showHindi} value={fmtDur(o.ttd_median_ms)} label="Typical time to dispatch" hi="सामान्य समय" tip="Typical (median) time from ready to dispatch — dispatched accident calls only, never the briefing." />
            <Tile showHindi={showHindi} value={String(o.information)} label="Information calls" hi="जानकारी कॉल" accent={C.blue} tip="General helpline calls (nearest facility, scheme/legal info, complaint, breakdown) — answered, not dispatched, and kept out of the accident metrics." />
          </div>

          {/* English vs Hindi comparison */}
          <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, padding: "14px 16px", marginBottom: 20 }}>
            <div style={{ ...CAPS, marginBottom: 10 }}>English vs Hindi{showHindi && " · अंग्रेज़ी बनाम हिंदी"}</div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: C.muted }}>
                    <th style={thStyle}>Metric</th>
                    <th style={thStyle}>English</th>
                    <th style={thStyle}>हिंदी</th>
                  </tr>
                </thead>
                <tbody>
                  {([
                    { label: "Calls", en: String(en?.total_calls ?? 0), hi: String(hi?.total_calls ?? 0) },
                    { label: "Completion rate", en: fmtPct(en?.dispatch_ready_rate), hi: fmtPct(hi?.dispatch_ready_rate) },
                    { label: "Typical time to dispatch", en: fmtDur(en?.ttd_median_ms), hi: fmtDur(hi?.ttd_median_ms) },
                  ]).map((r) => (
                    <tr key={r.label} style={{ borderTop: `1px solid ${C.hairline}` }}>
                      <td style={tdStyle}><span style={{ color: C.body }}>{r.label}</span></td>
                      <td style={{ ...tdStyle, fontWeight: 600, color: C.ink }}>{r.en}</td>
                      <td style={{ ...tdStyle, fontWeight: 600, color: C.ink }}>{r.hi}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* Distribution + closure curve */}
          {data && (
            <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, padding: "14px 16px", marginBottom: 20 }}>
              <div style={{ ...CAPS, marginBottom: 4 }}>Time-to-dispatch distribution{showHindi && " · वितरण"}</div>
              <p style={{ fontSize: 11.5, color: C.muted, marginBottom: 12 }}>
                Dispatched calls only. Bars = count per bucket; the line is the empirical closure-time curve (cumulative % dispatched by that time).
              </p>
              <DistributionChart buckets={data.histogram.overall} />
            </section>
          )}

          {/* Per-call list */}
          <section style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, overflow: "hidden" }}>
            <div style={{ ...CAPS, padding: "14px 16px 10px" }}>Individual calls{showHindi && " · अलग-अलग कॉल"}</div>
            <div style={{ maxHeight: 380, overflowY: "auto" }}>
              {data?.calls.map((c) => (
                <button
                  key={c.id}
                  onClick={() => setDrillId(c.id)}
                  className="ts-net-row flex items-center w-full"
                  style={{ gap: 12, padding: "10px 16px", borderTop: `1px solid ${C.hairline}`, background: "transparent", cursor: "pointer", textAlign: "left" }}
                >
                  <span style={{ fontSize: 12, color: C.secondary, width: 120, flex: "none" }}>{fmtTime(c.created_at)}</span>
                  <span style={{ fontSize: 12, color: C.body, width: 64, flex: "none" }}>{localeLabel(c.locale)}</span>
                  <span style={{ ...outcomePill(c.outcome), flex: "none" }}>{c.outcome ?? "—"}</span>
                  <span style={{ flex: 1 }} />
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: C.ink, flex: "none" }}>{fmtDur(c.time_to_dispatch_ms)}</span>
                  <span style={{ fontSize: 11.5, color: C.muted, width: 70, textAlign: "right", flex: "none" }}>{c.total_turns ?? 0} turns</span>
                </button>
              ))}
            </div>
          </section>
        </>
      )}

      {drillId && <CallDrilldown id={drillId} showHindi={showHindi} onClose={() => setDrillId(null)} />}
    </div>
  );
}

const thStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, padding: "4px 8px 8px", whiteSpace: "nowrap" };
const tdStyle: React.CSSProperties = { padding: "8px", whiteSpace: "nowrap" };

// ── distribution chart: bars + empirical CDF (closure-time) overlay ───────────
function DistributionChart({ buckets }: { buckets: HistBucket[] }) {
  const total = buckets.reduce((a, b) => a + b.count, 0);
  const maxCount = Math.max(1, ...buckets.map((b) => b.count));
  // padT reserves headroom for the count label that sits ABOVE each bar — the
  // tallest bar's top is at y=padT, so padT must clear the label's height or it
  // clips off the top of the viewBox.
  const W = 560, H = 150, padL = 4, padR = 4, padB = 26, padT = 22;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const bw = innerW / buckets.length;

  // cumulative % points (right edge of each bucket) — empirical closure curve.
  const cdf = buckets.map((_, i) => {
    const upTo = buckets.slice(0, i + 1).reduce((a, b) => a + b.count, 0);
    return total ? upTo / total : 0;
  });

  return (
    <div style={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", minWidth: 480, height: "auto" }} role="img" aria-label="Time-to-dispatch distribution">
        {buckets.map((b, i) => {
          const h = (b.count / maxCount) * innerH;
          const x = padL + i * bw;
          const y = padT + innerH - h;
          return (
            <g key={i}>
              <rect x={x + 3} y={y} width={Math.max(0, bw - 6)} height={h} rx={3} fill={C.blue} opacity={0.85} />
              {b.count > 0 && <text x={x + bw / 2} y={y - 3} textAnchor="middle" fontSize={10} fill={C.secondary}>{b.count}</text>}
              <text x={x + bw / 2} y={H - 8} textAnchor="middle" fontSize={9} fill={C.muted}>{bucketLabel(b)}</text>
            </g>
          );
        })}
        {/* empirical closure-time curve (cumulative % dispatched) */}
        <polyline
          fill="none"
          stroke={C.saffron}
          strokeWidth={2}
          points={cdf.map((p, i) => `${padL + i * bw + bw / 2},${padT + innerH - p * innerH}`).join(" ")}
        />
        {cdf.map((p, i) => (
          <circle key={i} cx={padL + i * bw + bw / 2} cy={padT + innerH - p * innerH} r={2.5} fill={C.saffron} />
        ))}
      </svg>
    </div>
  );
}

// ── drill-down modal (per-call timeline + transcript) ─────────────────────────
interface DrillData {
  id: string;
  incident_id: string | null;
  locale: string | null;
  outcome: string | null;
  time_to_dispatch_ms: number | null;
  call_duration_ms: number | null;
  caller_turns: number | null;
  agent_turns: number | null;
  total_turns: number | null;
  questions_asked: number | null;
  productive_turns: number | null;
  reconnects: number | null;
  fields_collected: { field: string; at_ms: number }[] | null;
  transcript: { role: string; at_ms: number; text: string }[] | null;
  created_at: string | null;
}

function CallDrilldown({ id, showHindi, onClose }: { id: string; showHindi: boolean; onClose: () => void }) {
  const [d, setD] = useState<DrillData | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/call-metrics/${id}`, { cache: "no-store" });
        if (!cancelled) setD(res.ok ? ((await res.json()) as DrillData) : null);
      } catch {
        if (!cancelled) setD(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id]);

  return (
    <>
      <div className="fixed inset-0 z-[2099]" style={{ background: "rgba(14,26,47,.45)" }} onClick={onClose} />
      <div className="fixed z-[2100] flex flex-col" style={{ left: "50%", top: "50%", transform: "translate(-50%,-50%)", width: "min(560px, 94vw)", maxHeight: "86vh", background: "#fff", borderRadius: RADIUS.card, boxShadow: "0 12px 48px rgba(14,26,47,.32)", overflow: "hidden" }}>
        <div className="flex items-center" style={{ gap: 10, padding: "13px 18px", borderBottom: `1px solid ${C.hairline}` }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: C.ink }}>Call detail{showHindi && " · कॉल विवरण"}</div>
            {d?.incident_id && <div style={{ fontSize: 11, color: C.muted, fontFamily: "ui-monospace,Menlo,monospace" }}>{d.incident_id}</div>}
          </div>
          <button onClick={onClose} aria-label="Close" style={{ width: 28, height: 28, border: `1px solid ${C.border}`, borderRadius: 7, background: C.inset, color: C.secondary, cursor: "pointer" }}>✕</button>
        </div>

        <div style={{ overflowY: "auto", padding: "14px 18px 20px" }}>
          {loading && <p style={{ fontSize: 13, color: C.muted }}>Loading…</p>}
          {!loading && !d && <p style={{ fontSize: 13, color: C.redSoftText }}>Could not load this call.</p>}
          {d && (
            <>
              {/* summary chips */}
              <div className="flex flex-wrap" style={{ gap: 8, marginBottom: 14 }}>
                <span style={outcomePill(d.outcome)}>{d.outcome ?? "—"}</span>
                <Chip label={localeLabel(d.locale)} />
                {d.outcome === "information"
                  ? <Chip label="Information call — no dispatch" />
                  : <Chip label={`Time-to-dispatch ${fmtDur(d.time_to_dispatch_ms)}`} strong />}
                <Chip label={`${d.total_turns ?? 0} turns`} />
                <Chip label={`${d.questions_asked ?? 0} questions`} />
                <Chip label={`${d.productive_turns ?? 0} productive`} />
                {(d.reconnects ?? 0) > 0 && <Chip label={`${d.reconnects} reconnect(s)`} />}
              </div>

              {/* fields collected */}
              {d.fields_collected && d.fields_collected.length > 0 && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ ...CAPS, marginBottom: 6 }}>Fields collected{showHindi && " · एकत्रित जानकारी"}</div>
                  {d.fields_collected.map((f, i) => (
                    <div key={i} className="flex items-center" style={{ gap: 8, fontSize: 12.5, padding: "2px 0" }}>
                      <span style={{ fontVariantNumeric: "tabular-nums", color: C.muted, width: 54, flex: "none" }}>{fmtDur(f.at_ms)}</span>
                      <span style={{ color: C.body, fontWeight: 600 }}>{f.field}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* per-turn transcript timeline */}
              <div style={{ ...CAPS, marginBottom: 6 }}>Turn timeline · transcript{showHindi && " · वार्तालाप"}</div>
              {(!d.transcript || d.transcript.length === 0) && (
                <p style={{ fontSize: 12, color: C.muted }}>No transcript captured for this call.</p>
              )}
              {d.transcript && d.transcript.map((t, i) => {
                const caller = t.role === "caller";
                return (
                  <div key={i} className="flex" style={{ gap: 8, marginBottom: 8 }}>
                    <span style={{ fontVariantNumeric: "tabular-nums", color: C.faint, fontSize: 11, width: 48, flex: "none", paddingTop: 4 }}>{fmtDur(t.at_ms)}</span>
                    <div style={{ background: caller ? C.blueSoftBg : C.inset, border: `1px solid ${caller ? C.blueSoftBorder : C.border}`, borderRadius: 9, padding: "7px 11px", fontSize: 12.5, color: C.body, maxWidth: "80%" }}>
                      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: caller ? C.blueSoftText : C.muted, marginBottom: 2 }}>{caller ? "Caller" : "Agent"}</div>
                      {t.text}
                    </div>
                  </div>
                );
              })}
              <p style={{ fontSize: 10.5, color: C.faint, marginTop: 12 }}>Transcript is operator-only content.</p>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function Chip({ label, strong }: { label: string; strong?: boolean }) {
  return (
    <span style={{ fontSize: 11.5, fontWeight: strong ? 700 : 500, background: strong ? C.blueSoftBg : C.page, border: `1px solid ${strong ? C.blueSoftBorder : C.border}`, color: strong ? C.blueSoftText : C.secondary, borderRadius: RADIUS.pill, padding: "3px 10px" }}>{label}</span>
  );
}
