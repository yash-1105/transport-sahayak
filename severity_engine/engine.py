"""
engine.py — public entry point. assess(incident, signals, location) -> dict

Rule-first: severity and agencies are ALWAYS computed by rules (severity.py/dispatch.py) —
nothing here ever lets an LLM decide what gets dispatched. Reading the free-text description
for hazard signals and taxonomy hints happens in two layers, in order:
  1. local_extract.extract_signals_locally — deterministic, curated phrase lexicon + negation
     detection, zero network, always runs. This is the PRIMARY path and correctness does not
     depend on it being available (it always is). Added after a real miss: a Gemini-only
     extraction path meant a Google Cloud billing/quota hiccup silently meant fire never got
     dispatched for a report that clearly said "...and now there is fire".
  2. Gemini (classify_with_gemini for taxonomy escalation, extract_hazard_signals for hazard
     signals) — a pure BONUS layered on top when available. Both already fail closed to None
     on any error (no key, quota, timeout, bad output) and only ever ADD to what local
     extraction already found, never remove or override it.
"""
from . import classifier, severity, dispatch, local_extract
from .gemini_client import classify_with_gemini, extract_hazard_signals

_ALL_CATEGORIES = sorted({r["category"] for r in classifier.INDEX})

# label used in the "why this rating" modifier list when a signal came from an
# extractor rather than the operator, so it's clearly distinguishable in the UI
_AUTO_SIGNAL_LABELS = {
    "fire": "Fire",
    "hazmat": "Hazardous material",
    "roadBlocked": "Road blockage",
    "entrapment": "Entrapment",
    "vulnerableVictim": "Vulnerable victim",
}


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


def _merge_signals(prior_signals: dict, extracted, source_label: str):
    """
    OR prior signals (whatever was already known — client-provided, or already
    merged from an earlier extraction layer) with a new extraction's booleans —
    an already-true signal always stays true (never downgraded), each layer
    only ever adds. Numeric estimates only fill in when nothing already
    provided one. Returns (merged_signals, auto_detected_notes) — notes are
    appended to appliedModifiers so operators can see what was inferred vs.
    confirmed, and which layer (local rules vs. Gemini) caught it.
    """
    merged = dict(prior_signals or {})
    notes = []
    if not extracted:
        return merged, notes

    for key, label in _AUTO_SIGNAL_LABELS.items():
        prior_val = bool(merged.get(key, False))
        auto_val = bool(extracted.get(key, False))
        if auto_val and not prior_val:
            notes.append(f"{label} signal auto-detected from description ({source_label}) — verify on scene")
        merged[key] = prior_val or auto_val

    if not merged.get("casualties") and extracted.get("estimatedCasualties"):
        merged["casualties"] = extracted["estimatedCasualties"]
        notes.append(f"Casualty count estimated from description ({source_label}) — unverified")

    vehicles = merged.get("vehiclesInvolved")
    if (not vehicles or vehicles == 1) and extracted.get("estimatedVehiclesInvolved"):
        merged["vehiclesInvolved"] = extracted["estimatedVehiclesInvolved"]
        notes.append(f"Vehicle count estimated from description ({source_label}) — unverified")

    return merged, notes


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

    # Always try to pull hazard signals out of free text, regardless of how the
    # subType was matched — a confident rules match can still hide a hazard its
    # own static baseline agencies list doesn't know about (see module docstring).
    # Local extraction is the primary, always-available layer; Gemini is a pure
    # bonus merged on top when it's available (never required for correctness).
    description = (incident.get("description") or "").strip()
    auto_notes = []

    local_extracted = local_extract.extract_signals_locally(description) if description else None
    merged_signals, local_notes = _merge_signals(signals, local_extracted, "local")
    auto_notes += local_notes

    gemini_extracted = extract_hazard_signals(description) if description else None
    merged_signals, gemini_notes = _merge_signals(merged_signals, gemini_extracted, "Gemini")
    auto_notes += gemini_notes
    if gemini_extracted is not None:
        llm_used = True

    sev = severity.compute(res.record, merged_signals)
    disp = dispatch.resolve(res.record, merged_signals, sev, location)

    return {
        "category": res.record["category"],
        "subType": res.record["subType"],
        "severity": sev.label,
        "severityScore": sev.score,
        "impactNote": sev.impactNote,
        "appliedModifiers": sev.appliedModifiers + auto_notes,
        "agencies": disp.agencies,
        "dataGaps": disp.dataGaps,
        "jurisdictionState": disp.jurisdictionState,
        "classifiedBy": classified_by,
        "llmUsed": llm_used,
        "lowConfidence": low_confidence,
        "confidence": res.confidence,
    }
