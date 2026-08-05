"use client";

// Administrator: human-in-the-loop "suggested actions" queue. The system
// SUGGESTS corrective actions derived from existing signals; the administrator
// confirms/acts on each. ADVISORY ONLY (Hard Rules) — nothing auto-executes:
// "Act" just takes the human to the relevant flow (or acknowledges), and every
// dispatch remains an honest notification a person still has to make.

import { useCallback, useEffect, useMemo, useState } from "react";
import { C, RADIUS } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import type { DbAccident } from "@/lib/types";
import { findAmbiguousClusters } from "@/lib/ambiguity";
import { sectionOfPoint } from "@/lib/sections";

interface Suggestion {
  id: string;
  kind: "duplicate" | "dispatch" | "review";
  title: string;
  detail: string;
  section: string;
  actLabel: string;
  onAct: () => void;
}

const CAPS: React.CSSProperties = { fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted };

const KIND_STYLE: Record<Suggestion["kind"], { bg: string; bd: string; tx: string; label: string }> = {
  dispatch: { bg: C.redSoftBg, bd: C.redSoftBorder, tx: C.redSoftText, label: "Dispatch" },
  duplicate: { bg: C.blueSoftBg, bd: C.blueSoftBorder, tx: C.blueSoftText, label: "Duplicate" },
  review: { bg: C.saffronSoftBg, bd: C.saffronSoftBorder, tx: "#8A5A17", label: "Review" },
};

export default function AdminSuggestedActions({
  accidents,
  onNavigate,
}: {
  accidents: DbAccident[];
  onNavigate: (sub: "ambiguity") => void;
}) {
  const { showHindi } = useBilingual();
  const [reviewMap, setReviewMap] = useState<Map<string, string>>(new Map());
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/incident-reviews", { cache: "no-store" });
        if (!res.ok) return;
        const rows = (await res.json()) as { incident_id: string; review_status: string }[];
        if (!cancelled) setReviewMap(new Map(rows.map((r) => [r.incident_id, r.review_status])));
      } catch { /* best-effort */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const dismiss = useCallback((id: string) => setDismissed((s) => new Set(s).add(id)), []);

  const suggestions = useMemo<Suggestion[]>(() => {
    const out: Suggestion[] = [];

    // 1) Open likely-duplicate clusters → resolve.
    for (const c of findAmbiguousClusters(accidents, reviewMap)) {
      out.push({
        id: `dup-${c.key}`,
        kind: "duplicate",
        title: `Resolve likely duplicate — ${c.members.length} reports`,
        detail: `${c.members[0].location_label} · ${c.why}`,
        section: sectionOfPoint(c.members[0]).name,
        actLabel: "Open review",
        onAct: () => onNavigate("ambiguity"),
      });
    }

    // 2) High-severity incidents → confirm an ambulance was dispatched.
    const high = accidents
      .filter((a) => (a.severity === "CRITICAL" || a.severity === "HIGH") && a.review_status !== "ignored" && typeof a.lat === "number")
      .sort((a, b) => new Date(b.reported_date).getTime() - new Date(a.reported_date).getTime())
      .slice(0, 12);
    for (const a of high) {
      out.push({
        id: `disp-${a.id}`,
        kind: "dispatch",
        title: "Confirm ambulance dispatch",
        detail: `${a.severity} incident at ${a.location_label} — verify a responder was notified.`,
        section: sectionOfPoint(a).name,
        actLabel: "Acknowledge",
        onAct: () => dismiss(`disp-${a.id}`),
      });
    }

    return out.filter((s) => !dismissed.has(s.id));
  }, [accidents, reviewMap, dismissed, onNavigate, dismiss]);

  return (
    <div style={{ padding: "18px 20px 40px", maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ marginBottom: 10 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: C.ink }}>
          Suggested actions{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 15 }}> · सुझाई गई कार्रवाइयाँ</span>}
        </h2>
        <p style={{ fontSize: 12.5, color: C.muted, marginTop: 2 }}>
          {suggestions.length === 0 ? "Nothing needs attention right now." : `${suggestions.length} suggestion${suggestions.length === 1 ? "" : "s"} for your confirmation`}
        </p>
      </div>

      <div style={{ background: C.saffronSoftBg, border: `1px solid ${C.saffronSoftBorder}`, borderRadius: 11, padding: "10px 14px", fontSize: 12, color: "#8A5A17", lineHeight: 1.5, marginBottom: 16 }}>
        Advisory — a human confirms each action. Nothing here runs automatically, and every dispatch stays an honest notification record (no vehicle tracking, no fabricated arrival times).
      </div>

      {suggestions.length === 0 ? (
        <div style={{ background: C.inset, border: `1px solid ${C.border}`, borderRadius: 11, padding: 18, fontSize: 13, color: C.secondary }}>
          No suggested actions. New suggestions appear as high-severity incidents or likely duplicates are detected.
        </div>
      ) : (
        <div className="flex flex-col" style={{ gap: 10 }}>
          {suggestions.map((s) => {
            const st = KIND_STYLE[s.kind];
            return (
              <div key={s.id} className="flex items-start" style={{ gap: 12, background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, padding: "12px 14px" }}>
                <span style={{ fontSize: 9.5, fontWeight: 700, color: st.tx, background: st.bg, border: `1px solid ${st.bd}`, borderRadius: RADIUS.pill, padding: "2px 8px", flex: "none", marginTop: 1 }}>{st.label}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 700, color: C.ink }}>{s.title}</div>
                  <div style={{ fontSize: 12, color: C.body, marginTop: 1 }}>{s.detail}</div>
                  <div style={{ ...CAPS, marginTop: 4 }}>{s.section}</div>
                </div>
                <div className="flex flex-col" style={{ gap: 6, flex: "none" }}>
                  <button
                    onClick={s.onAct}
                    style={{ padding: "7px 14px", border: "none", borderRadius: 9, background: C.navy800, color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}
                  >
                    {s.actLabel}
                  </button>
                  <button
                    onClick={() => dismiss(s.id)}
                    style={{ padding: "6px 14px", border: `1px solid ${C.border}`, borderRadius: 9, background: "#fff", fontSize: 12, fontWeight: 600, color: C.secondary, cursor: "pointer" }}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
