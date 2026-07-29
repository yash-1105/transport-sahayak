"use client";

import React from "react";
import { C, RADIUS, SHADOW } from "@/lib/design";

// The floating left map panel (342px), replacing the old hamburger sidebar +
// horizontal chip row. Two variants share one shell:
//  • Services — search, layer switches with counts, nearby-facilities list.
//  • Accidents — filter pills + a report-card list.
// Collapsed, the whole panel is replaced by a floating "☰ <title> ›" button.

export interface ServiceLayerRow {
  key: string;
  en: string;
  hi: string;
  color: string;
  count: number | null;
  loading?: boolean;
  sample: boolean;
  active: boolean;
  onToggle: () => void;
}

export interface NearbyFacility {
  key: string;
  name: string;
  meta: string;
  distanceLabel: string;
  color: string;
  onSelect: () => void;
}

export interface ReportCardData {
  key: string;
  sev: "HIGH" | "MED" | "LOW";
  title: string;
  time: string;
  loc: string;
  status: string;
  statusHot: boolean;
  onSelect: () => void;
  // Operator-only: an ambiguity-review "ignored" duplicate — dimmed in the list.
  dimmed?: boolean;
}

interface FloatingPanelProps {
  tab: "SERVICES" | "ACCIDENTS";
  open: boolean;
  onToggle: () => void;
  showHindi: boolean;
  // services
  search: string;
  onSearch: (v: string) => void;
  serviceLayers: ServiceLayerRow[];
  nearby: NearbyFacility[];
  // accidents
  showDefects: boolean;
  showAccidents: boolean;
  onToggleDefects: () => void;
  onToggleAccidents: () => void;
  reports: ReportCardData[];
  // Operators see the detailed report-card list; citizens/guests see an
  // aggregated density legend instead (never per-incident accident rows).
  isOperator: boolean;
}

const CAPS: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: ".09em",
  textTransform: "uppercase",
  color: C.muted,
};

function Switch({ on }: { on: boolean }) {
  return (
    <span
      style={{
        width: 34,
        height: 20,
        borderRadius: 10,
        background: on ? C.blue : "#D8D4C9",
        position: "relative",
        flex: "none",
        transition: "background .15s",
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 2,
          left: on ? 16 : 2,
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "#fff",
          transition: "left .15s",
          boxShadow: "0 1px 3px rgba(0,0,0,.25)",
        }}
      />
    </span>
  );
}

const sevChipStyle = (sev: "HIGH" | "MED" | "LOW"): React.CSSProperties => {
  const map = {
    HIGH: { bg: C.redSoftBg, bd: C.redSoftBorder, tx: C.redSoftText },
    MED: { bg: C.saffronSoftBg, bd: C.saffronSoftBorder, tx: "#B06712" },
    LOW: { bg: C.page, bd: C.border, tx: C.secondary },
  }[sev];
  return {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: ".06em",
    background: map.bg,
    border: `1px solid ${map.bd}`,
    color: map.tx,
    borderRadius: 5,
    padding: "2px 7px",
    flex: "none",
  };
};

const statusChipStyle = (hot: boolean): React.CSSProperties => ({
  fontSize: 10.5,
  fontWeight: 600,
  color: hot ? C.redSoftText : C.muted,
  background: hot ? C.redSoftBg : C.page,
  borderRadius: RADIUS.pill,
  padding: "2px 9px",
  flex: "none",
});

const filterPillStyle = (on: boolean, color: string): React.CSSProperties => ({
  padding: "7px 13px",
  border: `1px solid ${on ? color : C.border}`,
  borderRadius: RADIUS.pill,
  background: on ? `${color}14` : "#fff",
  color: on ? color : C.muted,
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
});

export default function FloatingPanel(props: FloatingPanelProps) {
  const { tab, open, onToggle, showHindi, isOperator } = props;
  // Citizens/guests see aggregated "accident-prone areas", not per-report detail.
  const accidentsTitleEn = isOperator ? "Reported incidents" : "Accident-prone areas";
  const accidentsTitleHi = isOperator ? "दर्ज घटनाएँ" : "दुर्घटना-संभावित क्षेत्र";
  const titleEn = tab === "SERVICES" ? "Emergency services" : accidentsTitleEn;
  const titleHi = tab === "SERVICES" ? "आपातकालीन सेवाएँ" : accidentsTitleHi;

  if (!open) {
    return (
      <button
        onClick={onToggle}
        className="absolute left-3.5 top-3.5 z-[500] flex items-center gap-2"
        style={{
          background: "#fff",
          border: `1px solid ${C.border}`,
          borderRadius: 10,
          padding: "9px 14px",
          fontSize: 12.5,
          fontWeight: 600,
          color: C.ink,
          cursor: "pointer",
          boxShadow: SHADOW.floatBtn,
        }}
      >
        ☰ {titleEn} ›
      </button>
    );
  }

  return (
    <aside
      className="ts-floating-panel absolute z-[500] flex flex-col overflow-hidden"
      style={{
        left: 14,
        top: 14,
        bottom: 14,
        width: 342,
        background: "#fff",
        border: `1px solid ${C.border}`,
        borderRadius: RADIUS.card,
        boxShadow: SHADOW.panel,
      }}
    >
      {/* Header */}
      <div
        className="flex items-start gap-2.5"
        style={{ padding: "14px 16px 12px", borderBottom: `1px solid ${C.hairline}` }}
      >
        <div className="flex-1 min-w-0">
          <div style={{ fontSize: 13.5, fontWeight: 600, color: C.ink }}>{titleEn}</div>
          {showHindi && <div style={{ fontSize: 11.5, color: C.muted, marginTop: 1 }}>{titleHi}</div>}
        </div>
        <button
          onClick={onToggle}
          title="Collapse panel"
          aria-label="Collapse panel"
          style={{
            width: 26,
            height: 26,
            border: `1px solid ${C.border}`,
            borderRadius: 7,
            background: C.inset,
            color: C.secondary,
            fontSize: 13,
            cursor: "pointer",
            flex: "none",
          }}
        >
          ‹
        </button>
      </div>

      {tab === "SERVICES" ? (
        <>
          <div style={{ padding: "10px 16px 4px" }}>
            <input
              value={props.search}
              onChange={(e) => props.onSearch(e.target.value)}
              placeholder="Search hospitals, police, fuel…"
              style={{
                width: "100%",
                boxSizing: "border-box",
                padding: "9px 12px",
                border: `1px solid ${C.border}`,
                borderRadius: 9,
                fontSize: 13,
                background: C.inset,
                outline: "none",
                color: C.ink,
              }}
            />
          </div>
          <div className="flex-1 overflow-y-auto" style={{ padding: "8px 8px 12px" }}>
            <div style={{ ...CAPS, padding: "8px 10px 4px" }}>
              SERVICE LAYERS{showHindi && <span style={{ fontWeight: 500 }}> · सेवा परतें</span>}
            </div>
            {props.serviceLayers.map((c) => (
              <div
                key={c.key}
                onClick={c.onToggle}
                className="ts-row flex items-center gap-2.5 cursor-pointer"
                style={{ padding: "8px 10px", borderRadius: 9 }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: c.color,
                    flex: "none",
                    opacity: c.active ? 1 : 0.35,
                  }}
                />
                <span className="flex-1 min-w-0" style={{ lineHeight: 1.2 }}>
                  <span style={{ display: "block", fontSize: 13, fontWeight: 500, color: C.ink }}>
                    {c.en}
                    {c.sample && (
                      <span
                        style={{
                          fontSize: 9.5,
                          fontWeight: 600,
                          color: C.saffron,
                          verticalAlign: "2px",
                          letterSpacing: ".04em",
                          marginLeft: 3,
                        }}
                      >
                        sample
                      </span>
                    )}
                  </span>
                  {showHindi && (
                    <span style={{ display: "block", fontSize: 11, color: C.muted }}>{c.hi}</span>
                  )}
                </span>
                {c.loading ? (
                  <span className="inline-block w-3 h-3 rounded-full animate-spin flex-none" style={{ border: "1.5px solid #d1d5db", borderTopColor: C.secondary }} />
                ) : c.count !== null ? (
                  <span
                    style={{
                      fontSize: 11,
                      color: C.muted,
                      background: C.page,
                      borderRadius: RADIUS.pill,
                      padding: "2px 8px",
                      fontWeight: 500,
                      flex: "none",
                    }}
                  >
                    {c.count}
                  </span>
                ) : null}
                <Switch on={c.active} />
              </div>
            ))}

            <div style={{ height: 1, background: C.hairline, margin: "10px 10px" }} />
            <div style={{ ...CAPS, padding: "2px 10px 4px" }}>
              NEARBY FACILITIES{showHindi && <span style={{ fontWeight: 500 }}> · निकट सुविधाएँ</span>}
            </div>
            {props.nearby.length === 0 && (
              <p style={{ fontSize: 12, color: C.faint, padding: "6px 10px" }}>No facilities in view.</p>
            )}
            {props.nearby.map((f) => (
              <div
                key={f.key}
                onClick={f.onSelect}
                className="ts-row flex items-center gap-2.5 cursor-pointer"
                style={{ padding: "9px 10px", borderRadius: 9 }}
              >
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: f.color, flex: "none" }} />
                <span className="flex-1 min-w-0" style={{ lineHeight: 1.25 }}>
                  <span
                    style={{
                      display: "block",
                      fontSize: 13,
                      fontWeight: 500,
                      color: C.ink,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {f.name}
                  </span>
                  <span style={{ display: "block", fontSize: 11.5, color: C.muted }}>{f.meta}</span>
                </span>
                <span style={{ fontSize: 12, fontWeight: 600, color: C.blue, flex: "none" }}>{f.distanceLabel}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="flex gap-2" style={{ padding: "12px 16px 6px" }}>
            <button onClick={props.onToggleDefects} style={filterPillStyle(props.showDefects, "#6B4226")}>
              ◆ Road defects
            </button>
            <button onClick={props.onToggleAccidents} style={filterPillStyle(props.showAccidents, "#D14036")}>
              ● {isOperator ? "Accidents" : "Accident zones"}
            </button>
          </div>

          {/* Citizen/guest: density legend instead of any per-incident rows. */}
          {!isOperator ? (
            <div className="flex-1 overflow-y-auto" style={{ padding: "10px 16px 14px" }}>
              {props.showAccidents ? (
                <>
                  <div style={{ ...CAPS, marginBottom: 8 }}>Accident density{showHindi && " · दुर्घटना घनत्व"}</div>
                  {([
                    { color: C.red, en: "High", hi: "अधिक" },
                    { color: "#EA580C", en: "Medium", hi: "मध्यम" },
                    { color: C.saffron, en: "Low", hi: "कम" },
                  ]).map((d) => (
                    <div key={d.en} className="flex items-center" style={{ gap: 9, padding: "5px 2px" }}>
                      <span style={{ width: 14, height: 14, borderRadius: "50%", background: d.color, opacity: 0.75, flex: "none", border: `1px solid ${d.color}` }} />
                      <span style={{ fontSize: 12.5, color: C.body }}>
                        {d.en}{showHindi && <span style={{ color: C.muted }}> · {d.hi}</span>}
                      </span>
                    </div>
                  ))}
                  <p style={{ fontSize: 11, color: C.muted, lineHeight: 1.5, marginTop: 10 }}>
                    Warmer zones = more reported accidents nearby. Tap a zone for the count.
                  </p>
                  <p style={{ fontSize: 11, color: C.faint, lineHeight: 1.5, marginTop: 6 }}>
                    Based on reported incidents — not official blackspots.
                    {showHindi && <span style={{ display: "block" }}>दर्ज घटनाओं पर आधारित — आधिकारिक ब्लैकस्पॉट नहीं।</span>}
                  </p>
                </>
              ) : (
                <p style={{ fontSize: 12, color: C.faint, padding: "6px 2px" }}>
                  Turn on “Accident zones” to see accident-prone areas.
                </p>
              )}
            </div>
          ) : (
          <div className="flex-1 overflow-y-auto" style={{ padding: "6px 8px 12px" }}>
            {props.reports.length === 0 && (
              <p style={{ fontSize: 12, color: C.faint, padding: "10px" }}>No reported incidents.</p>
            )}
            {props.reports.map((r) => (
              <div
                key={r.key}
                onClick={r.onSelect}
                className="ts-report-card cursor-pointer"
                style={{ padding: "11px 10px", borderRadius: 10, border: "1px solid transparent", opacity: r.dimmed ? 0.5 : 1 }}
              >
                <div className="flex items-center gap-2">
                  <span style={sevChipStyle(r.sev)}>{r.sev}</span>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: C.ink,
                      flex: 1,
                      minWidth: 0,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {r.title}
                  </span>
                  <span style={{ fontSize: 11, color: C.muted, flex: "none" }}>{r.time}</span>
                </div>
                <div className="flex items-center gap-2" style={{ marginTop: 4, paddingLeft: 2 }}>
                  <span
                    style={{
                      fontSize: 12,
                      color: C.secondary,
                      flex: 1,
                      minWidth: 0,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {r.loc}
                  </span>
                  <span style={statusChipStyle(r.statusHot)}>{r.status}</span>
                </div>
              </div>
            ))}
          </div>
          )}
        </>
      )}
    </aside>
  );
}
