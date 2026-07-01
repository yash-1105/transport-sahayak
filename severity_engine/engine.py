"""
engine.py — public entry point. assess(incident, signals, location) -> dict

Rule-first. Gemini is consulted for two separate, narrow jobs, NEITHER of which decides
severity or agencies (those are always computed by rules in severity.py/dispatch.py):
  1. classify_with_gemini — picks a taxonomy record, ONLY when the rule-based classifier
     couldn't confidently do so (needs_llm / conflict), exactly as before.
  2. extract_hazard_signals — reads free text for fire/hazmat/road-blocked/entrapment/
     casualty signals, on EVERY request with a description, regardless of classification
     confidence. Added because a confidently-matched record (e.g. "Car vs. Car Collision")
     can still mention a hazard its own static baseline agencies list has no way to know
     about — this was the root cause of a real miss (a collision-turned-fire report never
     got FIRE dispatched because nothing upstream ever set signals.fire).
"""
from . import classifier, severity, dispatch
from .gemini_client import classify_with_gemini, extract_hazard_signals

_ALL_CATEGORIES = sorted({r["category"] for r in classifier.INDEX})

# label used in the "why this rating" modifier list when a signal came from the
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


def _merge_signals(client_signals: dict, extracted):
    """
    OR client-provided booleans with LLM-extracted ones — an operator's explicit
    signal always wins (never downgraded), the extractor only ever adds. Numeric
    estimates only fill in when the client didn't provide one. Returns
    (merged_signals, auto_detected_notes) — notes are appended to
    appliedModifiers so operators can see what was inferred vs. confirmed.
    """
    merged = dict(client_signals or {})
    notes = []
    if not extracted:
        return merged, notes

    for key, label in _AUTO_SIGNAL_LABELS.items():
        client_val = bool(merged.get(key, False))
        auto_val = bool(extracted.get(key, False))
        if auto_val and not client_val:
            notes.append(f"{label} signal auto-detected from description — verify on scene")
        merged[key] = client_val or auto_val

    if not merged.get("casualties") and extracted.get("estimatedCasualties"):
        merged["casualties"] = extracted["estimatedCasualties"]
        notes.append("Casualty count estimated from description — unverified")

    vehicles = merged.get("vehiclesInvolved")
    if (not vehicles or vehicles == 1) and extracted.get("estimatedVehiclesInvolved"):
        merged["vehiclesInvolved"] = extracted["estimatedVehiclesInvolved"]
        notes.append("Vehicle count estimated from description — unverified")

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
    description = (incident.get("description") or "").strip()
    extracted = extract_hazard_signals(description) if description else None
    merged_signals, auto_notes = _merge_signals(signals, extracted)
    if extracted is not None:
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
