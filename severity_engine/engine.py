"""
engine.py — public entry point. assess(incident, signals, location) -> dict

Rule-first. Gemini is consulted ONLY when classification is uncertain or signals conflict,
and only to choose a record. Severity + agencies are ALWAYS computed by rules.
"""
from . import classifier, severity, dispatch
from .gemini_client import classify_with_gemini

_ALL_CATEGORIES = sorted({r["category"] for r in classifier.INDEX})


def _conflict(incident, record):
    """True if the operator-selected record disagrees with override tokens in the description."""
    desc = incident.get("description", "") or ""
    if not desc.strip() or not record:
        return False
    dt = set(classifier._tokens(desc))
    for pred, cat in classifier._OVERRIDES:
        try:
            if pred(dt) and cat != record["category"]:
                return True
        except Exception:
            continue
    return False


def _lookup(sub_type):
    return classifier._find_exact(sub_type)


def assess(incident: dict, signals: dict = None, location: dict = None) -> dict:
    signals = signals or {}
    res = classifier.classify(incident)
    llm_used = False
    classified_by = res.source

    escalate = res.source == "needs_llm" or _conflict(incident, res.record)

    if escalate:
        gem = classify_with_gemini(
            incident.get("description", ""),
            res.candidates,
            _ALL_CATEGORIES,
        )
        if gem and gem.get("subType"):
            rec = _lookup(gem["subType"])
            if rec:
                res.record = rec
                classified_by = "llm"
                llm_used = True

    # graceful fallback: no record yet -> best rules candidate, low confidence
    low_confidence = False
    if res.record is None:
        if res.candidates:
            res.record = res.candidates[0]
            classified_by = "rules"
            low_confidence = True
        else:
            res.record = classifier.INDEX[0]  # last-resort placeholder
            classified_by = "rules"
            low_confidence = True

    sev = severity.compute(res.record, signals)
    disp = dispatch.resolve(res.record, signals, sev, location)

    return {
        "category": res.record["category"],
        "subType": res.record["subType"],
        "severity": sev.label,
        "severityScore": sev.score,
        "impactNote": sev.impactNote,
        "appliedModifiers": sev.appliedModifiers,
        "agencies": disp.agencies,
        "dataGaps": disp.dataGaps,
        "jurisdictionState": disp.jurisdictionState,
        "classifiedBy": classified_by,
        "llmUsed": llm_used,
        "lowConfidence": low_confidence,
        "confidence": res.confidence,
    }
