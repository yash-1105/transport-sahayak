"use client";

// Operator-only dashboard shell: a small sub-tab bar (Network · Call Analytics ·
// Ambiguity) over the existing Signals dashboard. Mounted only inside MapView's
// `tab === "NETWORK" && isOperator` overlay, so it is entirely operator-gated.

import { useState } from "react";
import { C, RADIUS } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import type { DbAccident } from "@/lib/types";
import SignalsDashboardPanel from "@/components/signals/SignalsDashboardPanel";
import OperatorCallAnalytics from "@/components/operator/OperatorCallAnalytics";
import OperatorAmbiguityReview from "@/components/operator/OperatorAmbiguityReview";

type SubTab = "network" | "analytics" | "ambiguity";

const TABS: { key: SubTab; en: string; hi: string }[] = [
  { key: "network", en: "Network", hi: "नेटवर्क" },
  { key: "analytics", en: "Call Analytics", hi: "कॉल विश्लेषण" },
  { key: "ambiguity", en: "Ambiguity", hi: "समान घटनाएँ" },
];

export default function OperatorDashboard({
  accidents,
  onReviewsChanged,
}: {
  accidents: DbAccident[];
  onReviewsChanged: () => void;
}) {
  const { showHindi } = useBilingual();
  const [sub, setSub] = useState<SubTab>("network");

  return (
    <div className="flex flex-col" style={{ minHeight: "100%" }}>
      {/* Sub-tab bar */}
      <div
        className="flex items-center flex-none"
        style={{ gap: 6, padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: "#fff", position: "sticky", top: 0, zIndex: 5, flexWrap: "wrap" }}
      >
        {TABS.map((t) => {
          const on = sub === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setSub(t.key)}
              style={{
                padding: "7px 14px",
                border: `1px solid ${on ? C.navy800 : C.border}`,
                borderRadius: RADIUS.pill,
                background: on ? C.navy800 : "#fff",
                color: on ? "#fff" : C.secondary,
                fontSize: 12.5,
                fontWeight: 600,
                cursor: "pointer",
                lineHeight: 1.15,
              }}
            >
              {t.en}
              {showHindi && <span style={{ fontWeight: 400, opacity: on ? 0.8 : 1, color: on ? "#C6D0E2" : C.muted }}> · {t.hi}</span>}
            </button>
          );
        })}
      </div>

      <div className="flex-1">
        {sub === "network" && <SignalsDashboardPanel />}
        {sub === "analytics" && <OperatorCallAnalytics />}
        {sub === "ambiguity" && <OperatorAmbiguityReview accidents={accidents} onReviewsChanged={onReviewsChanged} />}
      </div>
    </div>
  );
}
