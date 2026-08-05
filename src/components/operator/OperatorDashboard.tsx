"use client";

// Operator/Administrator dashboard shell. Operators get Network · Call Analytics
// · Ambiguity, scoped to the section they monitor. Administrators (super-role)
// additionally get Sections + Suggested-actions and see the WHOLE network
// (no section scoping). Mounted only inside MapView's `tab === "NETWORK" &&
// isOperator` overlay (isOperator is true for admins too), so it's fully gated.

import { useMemo, useState } from "react";
import { C, RADIUS } from "@/lib/design";
import { useBilingual } from "@/hooks/useI18n";
import { useAuthStore, useIsAdmin } from "@/store/authStore";
import type { DbAccident } from "@/lib/types";
import { sectionOfEmail, sectionOfPoint } from "@/lib/sections";
import SignalsDashboardPanel from "@/components/signals/SignalsDashboardPanel";
import OperatorCallAnalytics from "@/components/operator/OperatorCallAnalytics";
import OperatorAmbiguityReview from "@/components/operator/OperatorAmbiguityReview";
import AdminSections from "@/components/operator/AdminSections";
import AdminSuggestedActions from "@/components/operator/AdminSuggestedActions";

type SubTab = "network" | "analytics" | "ambiguity" | "sections" | "actions";

const BASE_TABS: { key: SubTab; en: string; hi: string }[] = [
  { key: "network", en: "Network", hi: "नेटवर्क" },
  { key: "analytics", en: "Call Analytics", hi: "कॉल विश्लेषण" },
  { key: "ambiguity", en: "Ambiguity", hi: "समान घटनाएँ" },
];
const ADMIN_TABS: { key: SubTab; en: string; hi: string }[] = [
  { key: "sections", en: "Sections", hi: "खंड" },
  { key: "actions", en: "Suggested actions", hi: "सुझाव" },
];

export default function OperatorDashboard({
  accidents,
  onReviewsChanged,
}: {
  accidents: DbAccident[];
  onReviewsChanged: () => void;
}) {
  const { showHindi } = useBilingual();
  const isAdmin = useIsAdmin();
  const email = useAuthStore((s) => s.user?.email);
  const [sub, setSub] = useState<SubTab>("network");

  const tabs = isAdmin ? [...BASE_TABS, ...ADMIN_TABS] : BASE_TABS;

  // Operator scoping: a non-admin operator assigned to a section sees only that
  // section's incidents; the admin (and any unassigned operator) sees all.
  const mySection = isAdmin ? null : sectionOfEmail(email);
  const scopedAccidents = useMemo(() => {
    if (isAdmin || !mySection) return accidents;
    return accidents.filter((a) => typeof a.lat === "number" && typeof a.lng === "number" && sectionOfPoint(a).id === mySection.id);
  }, [accidents, isAdmin, mySection]);

  return (
    <div className="flex flex-col" style={{ minHeight: "100%" }}>
      {/* Sub-tab bar */}
      <div
        className="flex items-center flex-none"
        style={{ gap: 6, padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: "#fff", position: "sticky", top: 0, zIndex: 5, flexWrap: "wrap" }}
      >
        {tabs.map((t) => {
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

      {/* Role / scope banner */}
      <div
        className="flex items-center flex-none"
        style={{ gap: 8, padding: "7px 16px", borderBottom: `1px solid ${C.hairline}`, background: isAdmin ? "#FBF1E4" : C.inset, fontSize: 11.5 }}
      >
        <span style={{ fontWeight: 700, color: isAdmin ? "#8A5A17" : C.secondary }}>
          {isAdmin ? "Administrator" : "Operator"}
        </span>
        <span style={{ color: C.muted }}>
          {isAdmin
            ? "· Network-wide oversight (all sections)"
            : mySection
            ? `· Scoped to ${mySection.name}`
            : "· No section assigned — showing all"}
        </span>
      </div>

      <div className="flex-1">
        {sub === "network" && <SignalsDashboardPanel />}
        {sub === "analytics" && <OperatorCallAnalytics />}
        {sub === "ambiguity" && <OperatorAmbiguityReview accidents={scopedAccidents} onReviewsChanged={onReviewsChanged} />}
        {sub === "sections" && isAdmin && <AdminSections accidents={accidents} />}
        {sub === "actions" && isAdmin && <AdminSuggestedActions accidents={accidents} onNavigate={setSub} />}
      </div>
    </div>
  );
}
