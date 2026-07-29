"use client";

// Aggregator-DPG view inside transport-new: renders Signals' precomputed
// dashboard rollups for the road_safety network via the server-side proxy
// (/api/signals/dashboard). Fetch on open + manual refresh only — no polling.
// Shows last_computed_at explicitly (cached rollup, not live), an amber
// local-instance banner, and honest degraded states. A Signals outage never
// affects anything outside this panel.

import { useCallback, useEffect, useRef, useState } from "react";
import { useT, useBilingual } from "@/hooks/useI18n";
import { useSignalsSync } from "@/store/signalsSyncStore";
import { C, RADIUS } from "@/lib/design";

interface Rollup {
  total_items?: number;
  by_status?: Record<string, number>;
  by_initiated_action_status?: Record<string, number>;
  by_received_action_status?: Record<string, number>;
  total_users?: number;
}

interface DomainBlock {
  rollup?: Rollup;
  items?: Array<Record<string, unknown>>;
  total_matching?: number;
}

interface DashboardData {
  source: string;
  detail?: string | null;
  by_domain?: Record<string, DomainBlock>;
  metadata?: { last_computed_at?: string; ttl_seconds?: number; refreshed?: boolean };
}

const STATUS_KEYS = ["new", "active", "at_risk", "inactive"] as const;

// Hindi sub-labels for the domain section titles (bilingual display).
const DOMAIN_HI: Record<string, string> = {
  control_room: "कंट्रोल रूम घटनाएँ",
  responder: "रिस्पॉन्डर सुविधाएँ",
};

const CAPS: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: ".09em",
  textTransform: "uppercase",
  color: C.muted,
};

export default function SignalsDashboardPanel() {
  const t = useT();
  const { showHindi } = useBilingual();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (refresh: boolean) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/signals/dashboard${refresh ? "?refresh=true" : ""}`);
      setData((await res.json()) as DashboardData);
    } catch {
      setData({ source: "unavailable" });
    } finally {
      setLoading(false);
    }
  }, []);

  // Always force a fresh rollup on open — Signals caches dashboards for up to
  // an hour per org, and an operator opening this tab wants current numbers.
  useEffect(() => {
    void load(true);
  }, [load]);

  // Event-driven freshness (no polling): activitySeq bumps after every
  // successful publish from this console (incident create, assessment PATCH,
  // dispatch action). Debounced so one incident's publish burst — create,
  // assessment, two dispatches — collapses into a single recompute fetch.
  const activitySeq = useSignalsSync((s) => s.activitySeq);
  const lastSeenSeqRef = useRef(activitySeq);
  useEffect(() => {
    if (activitySeq === lastSeenSeqRef.current) return;
    lastSeenSeqRef.current = activitySeq;
    const timer = setTimeout(() => void load(true), 1500);
    return () => clearTimeout(timer);
  }, [activitySeq, load]);

  const statusLabel = (key: string): string => {
    switch (key) {
      case "new": return t("signalsStatusNew");
      case "active": return t("signalsStatusActive");
      case "at_risk": return t("signalsStatusAtRisk");
      default: return t("signalsStatusInactive");
    }
  };

  const domainLabel = (domain: string): string =>
    domain === "control_room" ? t("signalsDomainControlRoom") : t("signalsDomainResponder");

  // Status → row dot + status pill colours (design handoff).
  const statusVisual = (status: string): { dot: string; pill: React.CSSProperties } => {
    if (status === "at_risk") {
      return { dot: "#D14036", pill: { fontSize: 11, fontWeight: 600, color: C.redSoftText, background: C.redSoftBg, borderRadius: RADIUS.pill, padding: "3px 10px", flex: "none" } };
    }
    if (status === "new" || status === "active") {
      return { dot: C.green, pill: { fontSize: 11, fontWeight: 600, color: C.greenSoftText, background: C.greenSoftBg, borderRadius: RADIUS.pill, padding: "3px 10px", flex: "none" } };
    }
    return { dot: "#C9CDD6", pill: { fontSize: 11, fontWeight: 600, color: C.muted, background: C.page, borderRadius: RADIUS.pill, padding: "3px 10px", flex: "none" } };
  };

  const chipStyle = (hot: boolean): React.CSSProperties => ({
    fontSize: 11.5,
    border: `1px solid ${hot ? C.redSoftBorder : C.border}`,
    background: hot ? C.redSoftBg : C.inset,
    color: hot ? C.redSoftText : C.secondary,
    borderRadius: RADIUS.pill,
    padding: "4px 11px",
  });

  const btn = (primary: boolean): React.CSSProperties => ({
    padding: "8px 14px",
    border: `1px solid ${C.border}`,
    borderRadius: 9,
    background: "#fff",
    fontSize: 12.5,
    fontWeight: 600,
    color: primary ? C.blue : C.body,
    cursor: "pointer",
  });

  return (
    <div style={{ maxWidth: 1060, margin: "0 auto", padding: "26px 24px 90px" }}>
      {/* Header row */}
      <div className="flex items-end gap-3.5 flex-wrap">
        <div className="flex-1" style={{ minWidth: 260 }}>
          <div style={{ fontSize: 19, fontWeight: 700, letterSpacing: "-.01em", color: C.ink }}>
            {t("signalsDashTitle")}
          </div>
          <div style={{ fontSize: 12.5, color: C.muted, marginTop: 3 }}>
            {data?.source === "signals" && data.metadata?.last_computed_at
              ? t("signalsDashLastComputed").replace("{time}", new Date(data.metadata.last_computed_at).toLocaleTimeString())
              : "Computed rollup — cached, not live"}
            {showHindi && <span> · नेटवर्क डैशबोर्ड</span>}
          </div>
        </div>
        <div className="flex gap-2">
          {data?.source === "signals" && (
            <a href="/api/signals/dashboard/export" style={btn(true)}>{t("signalsDashExport")}</a>
          )}
          <button onClick={() => void load(true)} disabled={loading} style={{ ...btn(false), opacity: loading ? 0.5 : 1 }}>
            {loading ? "…" : t("signalsDashRefresh")}
          </button>
        </div>
      </div>

      {/* Local-instance banner */}
      {data?.source === "signals" && (
        <div
          className="flex gap-2.5 items-baseline"
          style={{ marginTop: 14, background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 11, padding: "11px 15px", fontSize: 12.5, color: C.saffronSoftText }}
        >
          <span style={{ fontWeight: 700, flex: "none" }}>Local instance</span>
          <span>Shows only what this console has published — not a live network feed.</span>
        </div>
      )}

      {loading && !data && <p style={{ fontSize: 13, color: C.faint, marginTop: 16 }}>Loading…</p>}

      {data?.source === "no_key" && (
        <div style={{ marginTop: 14, background: C.inset, border: `1px solid ${C.border}`, borderRadius: 11, padding: "11px 15px" }}>
          <p style={{ fontSize: 12.5, color: C.secondary, lineHeight: 1.5 }}>{t("signalsDashNoKey")}</p>
        </div>
      )}

      {data && data.source !== "signals" && data.source !== "no_key" && (
        <div style={{ marginTop: 14, background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 11, padding: "11px 15px" }}>
          <p style={{ fontSize: 12.5, color: C.saffronSoftText, lineHeight: 1.5 }}>{t("signalsDashUnavailable")}</p>
          {data.detail && <p style={{ fontSize: 11, color: C.saffron, marginTop: 4, fontFamily: "ui-monospace,Menlo,monospace" }}>{data.detail}</p>}
        </div>
      )}

      {data?.source === "signals" &&
        Object.entries(data.by_domain ?? {}).map(([domain, block]) => {
          const r = block.rollup ?? {};
          const sent = r.by_initiated_action_status?.create ?? 0;
          const received = r.by_received_action_status?.create ?? 0;
          const tiles = [
            { v: r.total_items ?? 0, l: t("signalsRollupItems") },
            { v: sent, l: t("signalsRollupNotifSent") },
            { v: received, l: t("signalsRollupNotifRecv") },
          ];
          return (
            <section
              key={domain}
              style={{ marginTop: 18, background: "#fff", border: `1px solid ${C.border}`, borderRadius: RADIUS.card, overflow: "hidden" }}
            >
              <div style={{ padding: "15px 18px 0" }}>
                <span style={CAPS}>{domainLabel(domain)}</span>
                {showHindi && DOMAIN_HI[domain] && (
                  <span style={{ fontSize: 11, color: C.faint }}> · {DOMAIN_HI[domain]}</span>
                )}
              </div>

              {/* Stat tiles */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, padding: "12px 18px 0" }}>
                {tiles.map((tl, i) => (
                  <div key={i} style={{ border: `1px solid ${C.hairline}`, borderRadius: 11, padding: "12px 14px", background: C.inset }}>
                    <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-.02em", color: C.ink }}>{tl.v}</div>
                    <div style={{ fontSize: 11.5, color: C.muted, marginTop: 1 }}>{tl.l}</div>
                  </div>
                ))}
              </div>

              {/* Status chips */}
              <div className="flex flex-wrap" style={{ gap: 7, padding: "12px 18px 4px" }}>
                {STATUS_KEYS.map((key) => (
                  <span key={key} style={chipStyle(key === "at_risk")}>
                    {statusLabel(key)} <b>{r.by_status?.[key] ?? 0}</b>
                  </span>
                ))}
              </div>

              {/* Record rows */}
              <div style={{ padding: "8px 8px 10px" }}>
                {(block.items ?? []).slice(0, 8).map((item, i) => {
                  const status = String(item.profile_status ?? "inactive");
                  const vis = statusVisual(status);
                  return (
                    <div key={i} className="ts-net-row flex items-center gap-2.5" style={{ padding: "9px 12px", borderRadius: 8 }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: vis.dot, flex: "none" }} />
                      <span className="flex-1 truncate" style={{ fontSize: 13, color: C.ink }}>
                        {String(item.name ?? item.profile_item_id ?? "—")}
                      </span>
                      <span style={vis.pill}>{statusLabel(status)}</span>
                    </div>
                  );
                })}
                {(block.total_matching ?? 0) > 8 && (
                  <p style={{ fontSize: 11.5, color: C.faint, padding: "6px 12px 0" }}>
                    +{(block.total_matching ?? 0) - 8} more — {t("signalsDashExport")}
                  </p>
                )}
              </div>
            </section>
          );
        })}
    </div>
  );
}
