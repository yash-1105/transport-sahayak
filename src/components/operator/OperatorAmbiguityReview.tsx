"use client";

// Ambiguity / duplicate review (operator-only sub-tab). Flags clusters of
// incidents that look like the same event reported multiple times from one
// area so the operator can ignore the duplicates. ADVISORY ONLY (Hard Rule 5):
// flagging is a hint, the operator decides; "ignoring" marks the record +
// de-dups downstream views, but NEVER un-sends a dispatch notification already
// logged, and never deletes the incident.

import { useCallback, useEffect, useMemo, useState } from "react";
import { C, RADIUS } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import type { DbAccident } from "@/lib/types";
import {
  findAmbiguousClusters, AMBIGUITY_DIST_M, AMBIGUITY_TIME_MIN, AMBIGUITY_JACCARD_MIN,
  type AmbiguityCluster,
} from "@/lib/ambiguity";

interface ReviewRow { incident_id: string; review_status: string; duplicate_of: string | null }

const CAPS: React.CSSProperties = { fontSize: 11, fontWeight: 700, letterSpacing: ".09em", textTransform: "uppercase", color: C.muted };

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function localeOf(mode: string): string {
  return mode === "DISPATCHER" ? "Voice" : mode;
}

export default function OperatorAmbiguityReview({
  accidents,
  onReviewsChanged,
}: {
  accidents: DbAccident[];
  onReviewsChanged: () => void;
}) {
  const { showHindi } = useBilingual();
  const [reviews, setReviews] = useState<ReviewRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  // Per-cluster chosen "keep" incident id (defaults to the earliest).
  const [keepChoice, setKeepChoice] = useState<Record<string, string>>({});

  const loadReviews = useCallback(async () => {
    try {
      const res = await fetch("/api/incident-reviews", { cache: "no-store" });
      if (res.ok) setReviews((await res.json()) as ReviewRow[]);
    } catch { /* best-effort */ }
  }, []);
  useEffect(() => { void loadReviews(); }, [loadReviews]);

  const reviewMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of reviews) m.set(r.incident_id, r.review_status);
    return m;
  }, [reviews]);

  const clusters = useMemo(() => findAmbiguousClusters(accidents, reviewMap), [accidents, reviewMap]);

  async function postReview(rows: { incident_id: string; review_status: string; duplicate_of?: string | null }[]) {
    await Promise.all(
      rows.map((r) =>
        fetch("/api/incident-reviews", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(r),
        })
      )
    );
    await loadReviews();
    onReviewsChanged(); // let MapView refresh accidents (density + list de-dup)
  }

  async function ignoreRest(cluster: AmbiguityCluster) {
    const keepId = keepChoice[cluster.key] ?? cluster.members[0].id;
    setBusy(cluster.key);
    try {
      await postReview([
        { incident_id: keepId, review_status: "kept" },
        ...cluster.members.filter((m) => m.id !== keepId).map((m) => ({ incident_id: m.id, review_status: "ignored", duplicate_of: keepId })),
      ]);
    } finally {
      setBusy(null);
    }
  }

  async function markSeparate(cluster: AmbiguityCluster) {
    setBusy(cluster.key);
    try {
      await postReview(cluster.members.map((m) => ({ incident_id: m.id, review_status: "kept" })));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ padding: "18px 20px 40px", maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ marginBottom: 8 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: C.ink }}>
          Ambiguity review{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 15 }}> · समान घटनाओं की समीक्षा</span>}
        </h2>
        <p style={{ fontSize: 12.5, color: C.muted, marginTop: 2 }}>
          {clusters.length === 0 ? "No likely-duplicate clusters right now." : `${clusters.length} likely-duplicate cluster${clusters.length === 1 ? "" : "s"} flagged`}
        </p>
      </div>

      {/* honesty banner */}
      <div style={{ background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 11, padding: "10px 14px", fontSize: 12, color: "#8A5A17", lineHeight: 1.5, marginBottom: 16 }}>
        A review aid — flagging is advisory; you decide. Detection: within {AMBIGUITY_DIST_M} m · {AMBIGUITY_TIME_MIN} min · ≥{Math.round(AMBIGUITY_JACCARD_MIN * 100)}% wording overlap.
        Ignoring marks the record and de-dups the map, but it does <b>not</b> cancel any dispatch notification already sent (a notification can’t be un-sent), and never deletes the incident.
      </div>

      {clusters.length === 0 && (
        <div style={{ background: C.inset, border: `1px solid ${C.border}`, borderRadius: 11, padding: 18, fontSize: 13, color: C.secondary }}>
          Nothing to review. New clusters appear here when multiple incidents are reported from the same area, close in time, with similar wording.
        </div>
      )}

      <div className="flex flex-col" style={{ gap: 14 }}>
        {clusters.map((cluster) => {
          const keepId = keepChoice[cluster.key] ?? cluster.members[0].id;
          return (
            <section key={cluster.key} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, overflow: "hidden" }}>
              <div style={{ padding: "12px 16px", borderBottom: `1px solid ${C.hairline}`, background: C.inset }}>
                <div className="flex items-center" style={{ gap: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 700, color: C.ink }}>{cluster.members.length} similar reports</span>
                  <span style={{ fontSize: 11, color: C.muted }}>· {cluster.members[0].location_label}</span>
                </div>
                <div style={{ fontSize: 11.5, color: "#8A5A17", marginTop: 3 }}>{cluster.why}</div>
              </div>

              {/* members — choose which to keep */}
              <div style={{ padding: "6px 8px" }}>
                {cluster.members.map((m) => {
                  const chosen = m.id === keepId;
                  return (
                    <label key={m.id} className="flex items-start" style={{ gap: 10, padding: "9px 10px", borderRadius: 9, cursor: "pointer", background: chosen ? C.blueSoftBg : "transparent" }}>
                      <input
                        type="radio"
                        name={`keep-${cluster.key}`}
                        checked={chosen}
                        onChange={() => setKeepChoice((s) => ({ ...s, [cluster.key]: m.id }))}
                        style={{ marginTop: 3, flex: "none", accentColor: C.blue }}
                      />
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span className="flex items-center" style={{ gap: 8, flexWrap: "wrap" }}>
                          <span style={{ fontSize: 12.5, fontWeight: 600, color: C.ink }}>{fmtTime(m.created_at)}</span>
                          <span style={{ fontSize: 11, color: C.muted }}>{localeOf(m.report_mode)}</span>
                          {chosen && <span style={{ fontSize: 10, fontWeight: 700, color: C.blueSoftText, background: "#fff", border: `1px solid ${C.blueSoftBorder}`, borderRadius: RADIUS.pill, padding: "1px 8px" }}>KEEP</span>}
                        </span>
                        <span style={{ display: "block", fontSize: 12.5, color: C.body, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {m.description?.trim() || "(no description)"}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>

              {/* actions */}
              <div className="flex" style={{ gap: 8, padding: "10px 16px 14px", borderTop: `1px solid ${C.hairline}`, flexWrap: "wrap" }}>
                <button
                  onClick={() => void ignoreRest(cluster)}
                  disabled={busy === cluster.key}
                  style={{ padding: "9px 14px", border: "none", borderRadius: 10, background: "linear-gradient(135deg,#D14036,#B92C22)", color: "#fff", fontSize: 12.5, fontWeight: 700, cursor: busy ? "default" : "pointer", opacity: busy === cluster.key ? 0.7 : 1 }}
                >
                  {busy === cluster.key ? "Saving…" : "Keep one, ignore the rest"}
                </button>
                <button
                  onClick={() => void markSeparate(cluster)}
                  disabled={busy === cluster.key}
                  style={{ padding: "9px 14px", border: `1px solid ${C.border}`, borderRadius: 10, background: "#fff", fontSize: 12.5, fontWeight: 600, color: C.body, cursor: busy ? "default" : "pointer" }}
                >
                  These are separate
                </button>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
