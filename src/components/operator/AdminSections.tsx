"use client";

// Administrator: section-wise monitoring. The corridor is split into sections
// (src/lib/sections), each with an assigned monitor. Shows each section, its
// monitor, and its incident/activity summary. Read-only oversight.

import { useMemo } from "react";
import { C, RADIUS } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import type { DbAccident } from "@/lib/types";
import { CORRIDOR_SECTIONS, sectionOfPoint, fmtKm } from "@/lib/sections";

const CAPS: React.CSSProperties = { fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted };

function isHigh(a: DbAccident) {
  return a.severity === "CRITICAL" || a.severity === "HIGH";
}
function relTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function AdminSections({ accidents }: { accidents: DbAccident[] }) {
  const { showHindi } = useBilingual();

  const bySection = useMemo(() => {
    const m = new Map<string, DbAccident[]>();
    for (const s of CORRIDOR_SECTIONS) m.set(s.id, []);
    for (const a of accidents) {
      if (typeof a.lat !== "number" || typeof a.lng !== "number") continue;
      m.get(sectionOfPoint(a).id)!.push(a);
    }
    return m;
  }, [accidents]);

  return (
    <div style={{ padding: "18px 20px 40px", maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: C.ink }}>
          Section monitoring{showHindi && <span style={{ fontWeight: 500, color: C.muted, fontSize: 15 }}> · खंड निगरानी</span>}
        </h2>
        <p style={{ fontSize: 12.5, color: C.muted, marginTop: 2 }}>
          The corridor is split into {CORRIDOR_SECTIONS.length} sections, each with an assigned monitor.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
        {CORRIDOR_SECTIONS.map((s) => {
          const items = bySection.get(s.id) ?? [];
          const high = items.filter(isHigh).length;
          const latest = items
            .map((a) => new Date(a.reported_date).getTime())
            .filter((t) => !Number.isNaN(t))
            .sort((a, b) => b - a)[0];
          return (
            <section key={s.id} style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: RADIUS.card, overflow: "hidden" }}>
              <div style={{ padding: "12px 14px", borderBottom: `1px solid ${C.hairline}`, background: C.inset }}>
                <div className="flex items-center" style={{ gap: 8 }}>
                  <span style={{ fontSize: 10.5, fontWeight: 700, color: "#fff", background: C.navy800, borderRadius: RADIUS.pill, padding: "1px 8px" }}>{s.id}</span>
                  <span style={{ fontSize: 13.5, fontWeight: 700, color: C.ink }}>{s.name}</span>
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 3 }}>
                  {fmtKm(s.fromKm)}–{fmtKm(s.toKm)}{showHindi && ` · ${s.hi}`}
                </div>
              </div>

              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
                <div>
                  <div style={{ ...CAPS, marginBottom: 2 }}>Monitor</div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>{s.monitorName}</div>
                  {s.monitorEmail && <div style={{ fontSize: 11, color: C.muted, fontFamily: "ui-monospace,Menlo,monospace" }}>{s.monitorEmail}</div>}
                </div>

                <div className="flex" style={{ gap: 18 }}>
                  <Stat value={String(items.length)} label="Incidents" />
                  <Stat value={String(high)} label="High/critical" accent={high > 0 ? C.red : undefined} />
                </div>

                <div>
                  <div style={{ ...CAPS, marginBottom: 2 }}>Latest activity</div>
                  <div style={{ fontSize: 12, color: latest ? C.body : C.faint }}>
                    {latest ? relTime(new Date(latest).toISOString()) : "No incidents recorded"}
                  </div>
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function Stat({ value, label, accent }: { value: string; label: string; accent?: string }) {
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700, color: accent ?? C.ink, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 11, color: C.secondary, marginTop: 1 }}>{label}</div>
    </div>
  );
}
