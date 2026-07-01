"""
classifier.py — map an incident (dropdown subType OR free-text description) to a
record in the accident index. Deterministic. No network, no LLM here.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
with open(os.path.join(_DATA_DIR, "accident_index.json"), encoding="utf-8") as _f:
    INDEX = json.load(_f)
with open(os.path.join(_DATA_DIR, "category_groups.json"), encoding="utf-8") as _f:
    _CATEGORY_GROUPS = json.load(_f)
    _CATEGORY_GROUPS.pop("_meta", None)

# ---- build a keyword index once at import ----
_STOP = {
    "the", "a", "an", "of", "on", "to", "in", "by", "or", "and", "with", "at", "for",
    "collision", "crash", "strike", "accident", "situations", "vehicle", "road", "expressway",
    "highway", "vs", "from", "into", "off", "no", "of.", "-", "high", "speed", "high-speed",
}
_word_re = re.compile(r"[a-z0-9]+")


def _tokens(text: str):
    return [t for t in _word_re.findall((text or "").lower()) if t not in _STOP and len(t) > 2]


_KW = []  # list of (record, set_subtype_tokens, set_cause_tokens)
for _rec in INDEX:
    _KW.append((_rec, set(_tokens(_rec["subType"])), set(_tokens(_rec["cause"]))))

# Hard-override tokens -> category that should win regardless of weak scores.
# Each rule: (predicate on token set, category name).
_OVERRIDES = [
    (lambda t: "bleve" in t or (("lpg" in t or "cng" in t) and "fire" in t), "Fire Situations"),
    (lambda t: ("tanker" in t and bool(t & {"spill", "leak", "chemical", "acid", "hazmat"})) or "radioactive" in t, "Hazardous Material"),
    (lambda t: bool(t & {"explosion", "exploded", "ied", "bomb", "blast"}), "Hazardous Material"),
    (lambda t: "collapse" in t and bool(t & {"bridge", "flyover"}), "Structural / Catastrophic"),
    (lambda t: "tunnel" in t and bool(t & {"fire", "smoke", "trapped", "dark", "stuck", "blocked"}), "Tunnel Incidents"),
    (lambda t: bool(t & {"flood", "submerged", "swept", "drowning", "waterlogged"}), "Flood / Water Emergency"),
    (lambda t: bool(t & {"landslide", "rockfall", "boulder", "mudslide"}), "Landslide / Cliff Fall"),
    (lambda t: bool(t & {"elephant", "leopard", "cattle", "cow", "buffalo", "nilgai", "boar", "animal", "deer", "monkey"}), "Vehicle to Animal"),
    (lambda t: bool(t & {"pedestrian", "cyclist", "walking", "crossing"}), "Vehicle to Person"),
    (lambda t: bool(t & {"cardiac", "stroke", "childbirth", "anaphylaxis", "overdose", "seizure", "unconscious"}), "Driver / Passenger Medical"),
]


@dataclass
class ClassificationResult:
    record: Optional[dict]
    confidence: float
    source: str  # "operator" | "rules" | "needs_llm"
    candidates: list = field(default_factory=list)


def _find_exact(sub_type: str):
    if not sub_type:
        return None
    s = sub_type.strip().lower()
    for rec in INDEX:
        if rec["subType"].strip().lower() == s:
            return rec
    return None


def classify(incident: dict) -> ClassificationResult:
    # 1. exact dropdown selection -> zero ambiguity, zero LLM
    rec = _find_exact(incident.get("subType", ""))
    if rec:
        return ClassificationResult(record=rec, confidence=1.0, source="operator")

    desc = incident.get("description", "") or ""
    if not desc.strip():
        return ClassificationResult(record=None, confidence=0.0, source="needs_llm")

    dt = set(_tokens(desc))

    # NOTE: the source spreadsheet's Category column is row-shifted in places, so we do NOT
    # filter by category. We score every record globally by sub-type/cause keyword overlap;
    # sub-type tokens (weighted 2x) carry the signal. Overrides are used only by the engine's
    # conflict guard, not as a hard category filter here.
    scored = []
    for rec, st, ct in _KW:
        score = 2 * len(dt & st) + 1 * len(dt & ct)
        if score:
            scored.append((score, rec))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return ClassificationResult(record=None, confidence=0.0, source="needs_llm")

    top = scored[0][0]
    runner = scored[1][0] if len(scored) > 1 else 0
    confidence = top / (top + runner + 1)
    best = scored[0][1]
    candidates = [r for _, r in scored[:3]]

    # accept when there's a clear, meaningful sub-type hit; else escalate
    if top >= 4 and confidence >= 0.45:
        return ClassificationResult(record=best, confidence=round(confidence, 2),
                                    source="rules", candidates=candidates)
    if top >= 2 and confidence >= 0.6:
        return ClassificationResult(record=best, confidence=round(confidence, 2),
                                    source="rules", candidates=candidates)
    return ClassificationResult(record=best, confidence=round(confidence, 2),
                                source="needs_llm", candidates=candidates)


# ── Category consolidation ─────────────────────────────────────────────────────
# The source spreadsheet's raw `category` field already has 50 reasonably
# balanced values (1-47 records each) — far better than deriving categories by
# keyword-matching each subType string. category_groups.json (single source of
# truth, also read by the TS port in src/lib/incidentClassifier.ts) maps every
# one of those 50 raw categories onto 11 curated, balanced top-level UI
# categories — every record is assigned, no leftover "Other" catch-all.

_CATEGORY_MAP: dict[str, str] = {
    rec["subType"]: _CATEGORY_GROUPS.get(rec["category"], rec["category"]) for rec in INDEX
}


def get_categories() -> list[dict]:
    """Return [{category, count}] sorted descending by count."""
    counts: dict[str, int] = {}
    for cat in _CATEGORY_MAP.values():
        counts[cat] = counts.get(cat, 0) + 1
    return sorted(
        [{"category": c, "count": n} for c, n in counts.items()],
        key=lambda x: -x["count"],
    )


def get_subtypes_for(category: str) -> list[str]:
    """Return sorted list of subType strings belonging to this category."""
    return sorted(sub for sub, cat in _CATEGORY_MAP.items() if cat == category)


def guess(description: str) -> dict:
    """
    Classify a free-text description and return a confirm-card payload for the UI.
    """
    result = classify({"description": description})
    if not result.record:
        return {
            "subType": None,
            "category": None,
            "confidence": 0.0,
            "lowConfidence": True,
            "candidates": [],
        }
    top_cat = _CATEGORY_MAP.get(result.record["subType"], "Other")
    candidates = [
        {
            "subType": r["subType"],
            "category": _CATEGORY_MAP.get(r["subType"], "Other"),
        }
        for r in (result.candidates or [])
    ]
    return {
        "subType": result.record["subType"],
        "category": top_cat,
        "confidence": result.confidence,
        "lowConfidence": result.confidence < 0.5 or result.source == "needs_llm",
        "candidates": candidates,
    }
