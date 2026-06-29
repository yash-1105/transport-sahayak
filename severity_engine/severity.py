"""
severity.py — deterministic severity from base + signal modifiers. No LLM ever.
1 = LOW, 2 = MEDIUM, 3 = HIGH, 4 = CRITICAL
"""
from dataclasses import dataclass, field

LABELS = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}


@dataclass
class SeverityResult:
    score: int
    label: str
    impactNote: str
    appliedModifiers: list = field(default_factory=list)


def _clamp(x, lo=1, hi=4):
    return max(lo, min(hi, x))


def compute(record: dict, signals: dict) -> SeverityResult:
    s = signals or {}
    casualties = int(s.get("casualties", 0) or 0)
    fatalities = int(s.get("fatalities", 0) or 0)
    vehicles = int(s.get("vehiclesInvolved", 1) or 1)
    fire = bool(s.get("fire", False))
    hazmat = bool(s.get("hazmat", False))
    entrapment = bool(s.get("entrapment", False))
    road_blocked = bool(s.get("roadBlocked", False))
    vulnerable = bool(s.get("vulnerableVictim", False))

    score = int(record["baseSeverity"])
    applied = []

    if 3 <= casualties <= 5 or 3 <= vehicles <= 4:
        score += 1
        applied.append("multiple casualties/vehicles (+1)")
    if fire:
        score += 1
        applied.append("fire reported (+1)")
    if entrapment:
        score += 1
        applied.append("entrapment/submersion (+1)")
    if vulnerable:
        score += 1
        applied.append("vulnerable victim (+1)")
    if hazmat:
        score = max(score, 3)
        applied.append("hazmat present (floor HIGH)")

    # ---- hard overrides -> CRITICAL ----
    sub = record["subType"].lower()
    agencies = set(record.get("agencies", []))
    override = (
        casualties >= 20
        or fatalities >= 5
        or "bleve" in sub
        or "full collapse" in sub
        or "(50+" in sub
        or (hazmat and (agencies & {"GAS_DETECTION", "BOMB_SQUAD", "AERB"}))
    )
    if override:
        score = 4
        applied.append("hard override -> CRITICAL")

    score = _clamp(score)

    # ---- factual impact note (no ETAs, no crew status) ----
    parts = []
    if fatalities:
        parts.append(f"{fatalities} fatality(ies)")
    if casualties:
        parts.append(f"{casualties} casualty(ies)")
    if vehicles and vehicles > 1:
        parts.append(f"{vehicles} vehicles")
    if fire:
        parts.append("fire reported")
    if hazmat:
        parts.append("hazardous material")
    if entrapment:
        parts.append("persons trapped")
    if road_blocked:
        parts.append("road blocked")
    impact = "; ".join(parts) if parts else "no aggravating factors reported yet"

    return SeverityResult(score=score, label=LABELS[score],
                          impactNote=impact, appliedModifiers=applied)
