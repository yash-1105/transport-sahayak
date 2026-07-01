"""
local_extract.py — deterministic, network-free hazard-signal extraction from free text.

This is the PRIMARY path for reading fire/hazmat/road-blocked/entrapment/vulnerable-victim
signals out of an incident description — it always runs, has no external dependency, and
never fails closed the way an API call can (no key, no quota, no network needed). Gemini's
extract_hazard_signals() in gemini_client.py is layered on top as a pure bonus when available;
this module is what makes correctness NOT depend on that.

Returns the same shape gemini_client.extract_hazard_signals() returns, so engine.py can merge
both through the same logic:
    {"fire": bool, "hazmat": bool, "roadBlocked": bool, "entrapment": bool,
     "vulnerableVictim": bool, "estimatedCasualties": int|None, "estimatedVehiclesInvolved": int|None}
"""
import re

_word_re = re.compile(r"[a-z0-9]+")

# ── Negation markers ──────────────────────────────────────────────────────────
# If one of these appears within NEGATION_WINDOW tokens before a matched phrase,
# the match is suppressed. e.g. "fire was already extinguished" must not set
# fire=true. This is the one thing pure keyword matching has never done here.
_NEGATION_MARKERS = {
    "no", "not", "without", "never", "none", "nobody",
    "extinguished", "cleared", "clear", "resolved", "avoided", "prevented",
}
NEGATION_WINDOW = 4

# ── Signal phrase lexicon (English + Hindi/Hinglish) ──────────────────────────
# Deliberately broader than the frontend's classifyIncident() hint-card arrays —
# this is the load-bearing extractor now, not UI decoration. Multi-word phrases
# are checked against the raw lowercased text; single tokens against the token set.

_FIRE_PHRASES = [
    "fire", "burning", "ablaze", "aflame", "flames", "caught fire", "on fire",
    "engine fire", "cabin fire", "burst into flames", "smoke coming out",
    "smoke pouring", "fuel leak", "ignit", "explod", "burnt", "charred",
    "आग", "जल रही", "जलना", "जल गई", "आग लगी", "धुआं", "विस्फोट", "ईंधन रिसाव",
]
_HAZMAT_PHRASES = [
    "hazmat", "chemical", "toxic", "corrosive", "acid spill", "radioactive",
    "gas leak", "leaking gas", "leaking chemical", "tanker leak", "tanker spill",
    "cng leak", "lpg leak", "ammonia", "chlorine", "pesticide", "biohazard",
    "रसायन", "गैस रिसाव", "जहरीला",
]
_ROAD_BLOCKED_PHRASES = [
    "road blocked", "road closed", "blocking traffic", "blocking both lanes",
    "blocking the road", "lane blocked", "lanes blocked", "traffic jam",
    "traffic backed up", "overturned blocking", "obstruction on road",
    "debris on road", "road obstructed",
    "सड़क बंद", "रास्ता बंद", "यातायात जाम",
]
_ENTRAPMENT_PHRASES = [
    "trapped", "stuck inside", "cant get out", "can't get out", "pinned",
    "pinned inside", "jammed door", "unable to exit", "entrapped", "wedged",
    "फँसा", "फंसा", "अंदर फंसा",
]
_VULNERABLE_PHRASES = [
    "child", "children", "infant", "baby", "pregnant", "elderly", "old man",
    "old woman", "senior citizen", "disabled", "heavy bleeding", "unconscious",
    "unresponsive", "bleeding heavily",
    "बच्चा", "गर्भवती", "बुजुर्ग", "बेहोश", "अत्यधिक रक्तस्राव",
]

_SIGNAL_LEXICON = {
    "fire": _FIRE_PHRASES,
    "hazmat": _HAZMAT_PHRASES,
    "roadBlocked": _ROAD_BLOCKED_PHRASES,
    "entrapment": _ENTRAPMENT_PHRASES,
    "vulnerableVictim": _VULNERABLE_PHRASES,
}

# ── Casualty / vehicle count extraction ────────────────────────────────────────
_CASUALTY_RE = re.compile(r"(\d+)\s*(?:people|persons?|casualt\w*|injured|victims?)", re.I)
_VEHICLE_RE = re.compile(r"(\d+)\s*(?:vehicles?|cars?|trucks?|bikes?)", re.I)


def _tokens(text: str):
    return _word_re.findall((text or "").lower())


def _phrase_negated(text_lower: str, phrase: str) -> bool:
    """
    True if a negation marker appears within NEGATION_WINDOW tokens either
    BEFORE or AFTER the phrase's first occurrence in the text. Negation shows
    up on both sides in real reports: "no fire" (marker before the hazard
    word) and "the fire has already been extinguished" (resolution word
    after it) both need to suppress the signal.
    """
    idx = text_lower.find(phrase)
    if idx == -1:
        return False
    before_tokens = _tokens(text_lower[:idx])[-NEGATION_WINDOW:]
    after_tokens = _tokens(text_lower[idx + len(phrase):])[:NEGATION_WINDOW]
    return any(t in _NEGATION_MARKERS for t in before_tokens + after_tokens)


def _signal_present(text_lower: str, phrases: list) -> bool:
    for phrase in phrases:
        if phrase in text_lower and not _phrase_negated(text_lower, phrase):
            return True
    return False


def extract_signals_locally(description: str) -> dict:
    """
    Reads free text and extracts hazard/casualty signals using a curated phrase
    lexicon + negation detection — no network, no external dependency, always
    available. Conservative by construction: only phrases explicitly present
    (and not negated) are set true; counts are only extracted when a clear
    "N <unit>" pattern is found in the text.
    """
    text_lower = (description or "").lower()

    result = {key: _signal_present(text_lower, phrases) for key, phrases in _SIGNAL_LEXICON.items()}

    cas_match = _CASUALTY_RE.search(text_lower)
    result["estimatedCasualties"] = int(cas_match.group(1)) if cas_match else None

    veh_match = _VEHICLE_RE.search(text_lower)
    result["estimatedVehiclesInvolved"] = int(veh_match.group(1)) if veh_match else None

    return result
