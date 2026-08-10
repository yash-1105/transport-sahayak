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

Plus two extras the voice dispatcher's transcript backstop uses (engine.py ignores them):
    "flag_determinations": {flag_name: bool}  — every safety flag the narrative DISCUSSED
        (affirmed OR explicitly negated), mapped to this feature's flag vocabulary, with the
        determined value (True = condition present / conscious / breathing, False = ruled out /
        unconscious / not breathing). Presence of a key means "the caller effectively answered
        this already" — the backstop marks it flags_discussed so the deterministic
        next_question stops re-asking it (dispatcher_live._compute_still_missing).

Widened 2026-07 (voice dispatcher, both languages): worded counts ("two people", "दो लोग"),
zero/negation fill ("no one hurt" → casualties 0, "no fire" → Fire discussed-and-ruled-out),
and vital-sign detection (conscious/breathing/heavy-bleeding) — so fewer questions have to be
asked out loud. The negation machinery below is unchanged (the "आग नहीं लगी" / "fire already
extinguished" suppression must still hold), and _mentioned_vehicle_types' "सरकार-contains-कार"
guard lives in dispatcher_live.py, not here, so it is unaffected.
"""
import re

# Devanagari included: without it, Hindi negation words were invisible to the
# window check below ("आग नहीं लगी" tokenized to nothing, so "नहीं" could never
# suppress the "आग" match — found live while testing the Hindi dispatcher).
# English tokenization is unchanged. The Bengali-Assamese block (U+0980–U+09FF)
# is added for as-IN so Assamese tokens aren't silently dropped by the tokenizer;
# it is additive (matches nothing in Hindi/English text, so those token streams
# are byte-identical). NOTE: the hazard-phrase lexicon and _NEGATION_MARKERS
# below are still Hindi/English only — a full Assamese lexicon is future work, so
# this range change alone does not yet make the local backstop fire on Assamese
# (the model's own tool calls are the primary accident-detection path for as-IN).
_word_re = re.compile(r"[a-z0-9]+|[ऀ-ॿ]+|[ঀ-৿]+")

# ── Negation markers ──────────────────────────────────────────────────────────
# If one of these appears within NEGATION_WINDOW tokens before a matched phrase,
# the match is suppressed. e.g. "fire was already extinguished" must not set
# fire=true. This is the one thing pure keyword matching has never done here.
_NEGATION_MARKERS = {
    "no", "not", "without", "never", "none", "nobody",
    "extinguished", "cleared", "clear", "resolved", "avoided", "prevented",
    # Hindi (matched as exact whole tokens, same as the English markers):
    # "नहीं"/"मत" = no/not, "बुझ..." forms = extinguished, "टल"/"बच" = averted.
    "नहीं", "मत", "बुझ", "बुझा", "बुझी", "बुझाई", "टल", "बच",
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

# ── Vital-sign lexicons (voice dispatcher flag vocabulary) ────────────────────
# Heavy bleeding is a single-list signal (present → flag TRUE, negated → ruled
# out FALSE), like the hazard lexicons. Conscious/Breathing are two-directional:
# the "true = good" polarity means an explicit BAD phrase (unconscious / not
# breathing) determines the flag FALSE, and a GOOD phrase determines it TRUE.
# Heavy bleeding is two-directional (like Conscious/Breathing): the HEAVY list
# affirms the flag, the NONE list rules it out. The affirm list stays specific
# to HEAVY bleeding (a "minor scratch, slight bleeding" must NOT set the Heavy
# bleeding flag), so bare "bleeding" is deliberately not an affirm phrase.
_BLEEDING_HEAVY = [
    "bleeding heavily", "heavy bleeding", "lot of blood", "bleeding a lot",
    "losing blood", "profuse bleeding", "bleeding badly", "blood everywhere",
    "खून बह रहा", "बहुत खून", "अत्यधिक रक्तस्राव", "खून निकल रहा", "बहुत ज़्यादा खून",
    "बहुत ज्यादा खून",
]
_BLEEDING_NONE = [
    "no bleeding", "not bleeding", "no blood", "no heavy bleeding", "isn't bleeding",
    "खून नहीं", "रक्तस्राव नहीं",
]
_CONSCIOUS_GOOD = [
    "conscious", "is conscious", "awake", "responsive", "is responding",
    "is talking", "talking normally", "alert",
    "होश में", "होश है", "जाग रहा", "जाग रही", "बात कर रहा", "बात कर रही", "जवाब दे रहा",
]
_CONSCIOUS_BAD = [
    "unconscious", "not conscious", "unresponsive", "not responding", "passed out",
    "blacked out", "knocked out", "fainted", "lost consciousness",
    "बेहोश", "होश नहीं", "बेसुध",
]
_BREATHING_GOOD = [
    "breathing normally", "is breathing", "breathing fine", "breathing okay",
    "breathing ok", "can breathe",
    "साँस चल रही", "सांस चल रही", "साँस ठीक", "सांस ठीक", "साँस आ रही", "सांस आ रही",
]
_BREATHING_BAD = [
    "not breathing", "cant breathe", "can't breathe", "cannot breathe",
    "struggling to breathe", "stopped breathing", "no breathing", "gasping",
    "choking",
    "साँस नहीं", "सांस नहीं", "साँस रुक", "सांस रुक", "दम घुट",
]

# ── Zero-casualty ("no one hurt") phrases ─────────────────────────────────────
# Explicitly-nobody-injured statements. These set casualties to 0 (a concrete
# value, so the "how many injured?" question falls away) and, since a
# no-injuries statement rules out heavy bleeding too, mark that flag discussed.
_NO_CASUALTY_PHRASES = [
    "no one hurt", "no one is hurt", "no one injured", "no one is injured",
    "nobody hurt", "nobody is hurt", "nobody injured", "no injuries", "no injury",
    "no casualties", "everyone is fine", "everyone is okay", "everyone is ok",
    "everybody is fine", "all safe", "all are safe", "everyone safe",
    "कोई घायल नहीं", "किसी को चोट नहीं", "कोई हताहत नहीं", "सब ठीक", "सब सुरक्षित",
    "कोई ज़ख्मी नहीं", "कोई जख्मी नहीं",
]

_SIGNAL_LEXICON = {
    "fire": _FIRE_PHRASES,
    "hazmat": _HAZMAT_PHRASES,
    "roadBlocked": _ROAD_BLOCKED_PHRASES,
    "entrapment": _ENTRAPMENT_PHRASES,
    "vulnerableVictim": _VULNERABLE_PHRASES,
}

# ── Casualty / vehicle count extraction (digits + worded numbers) ─────────────
# "a"/"an" are deliberately NOT number words: "a car hit a truck" must stay a
# two-vehicle collision (resolved by the vehicle-pair/same-type override in
# dispatcher_live), not be read as "1 vehicle". "couple"/"few"/"several" are
# approximate but common in real reports.
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "couple": 2, "few": 3, "several": 4, "single": 1, "lone": 1,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12,
}
# Longest-first so multi-char tokens aren't shadowed by a prefix match.
_NUM_ALT = "|".join(re.escape(t) for t in sorted(_WORD_NUM, key=len, reverse=True))
_NUM_PAT = rf"(\d+|{_NUM_ALT})"
_CASUALTY_UNITS = (
    r"people|persons?|casualt\w*|injured|victims?|hurt|wounded|dead|fatalit\w*|"
    r"लोग|लोगों|व्यक्ति|व्यक्तियों|घायल|ज़ख्मी|जख्मी|हताहत|मृत"
)
_VEHICLE_UNITS = (
    r"vehicles?|cars?|trucks?|bikes?|motorcycles?|buses|bus|lorr(?:y|ies)|autos?|scooters?|"
    r"गाड़ियाँ|गाड़ियां|गाड़ियों|गाड़ी|कारें|कारों|कार|ट्रकों|ट्रक|बाइकें|बाइक|बसें|बस"
)
_CASUALTY_RE = re.compile(rf"{_NUM_PAT}\s*(?:of\s+)?(?:{_CASUALTY_UNITS})", re.I)
_VEHICLE_RE = re.compile(rf"{_NUM_PAT}\s*(?:of\s+)?(?:{_VEHICLE_UNITS})", re.I)


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


def _signal_state(text_lower: str, phrases: list) -> str:
    """One of "affirm" / "negate" / "absent". "affirm" if any listed phrase
    appears un-negated; else "negate" if a phrase appears but every occurrence
    is negated (the caller DID discuss it, and ruled it out); else "absent"
    (never mentioned)."""
    seen = False
    for phrase in phrases:
        if phrase in text_lower:
            seen = True
            if not _phrase_negated(text_lower, phrase):
                return "affirm"
    return "negate" if seen else "absent"


def _signal_present(text_lower: str, phrases: list) -> bool:
    """Back-compat helper (kept for callers that only want the affirm bool)."""
    return _signal_state(text_lower, phrases) == "affirm"


def _any_present(text_lower: str, phrases: list) -> bool:
    """Plain substring presence, WITHOUT the negation window. Used for phrase
    lists that are themselves already negative/confirmed statements ("no one is
    hurt", "unconscious", "no bleeding") -- applying the negation window to those
    is wrong: an unrelated "no" from a neighbouring clause ("...no one is hurt
    and there is no fire") would spuriously suppress them."""
    return any(p in text_lower for p in phrases)


def _to_num(tok: str):
    tok = (tok or "").strip()
    if tok.isdigit():
        return int(tok)
    return _WORD_NUM.get(tok) or _WORD_NUM.get(tok.lower())


def _first_count(regex, text_lower: str):
    m = regex.search(text_lower)
    if not m:
        return None
    return _to_num(m.group(1))


def extract_signals_locally(description: str) -> dict:
    """
    Reads free text and extracts hazard/casualty signals using a curated phrase
    lexicon + negation detection — no network, no external dependency, always
    available. Conservative by construction: only phrases explicitly present
    (and not negated) set a signal true; counts come from a clear "N <unit>"
    pattern (digits or worded numbers).
    """
    text_lower = (description or "").lower()

    fire_state = _signal_state(text_lower, _FIRE_PHRASES)
    hazmat_state = _signal_state(text_lower, _HAZMAT_PHRASES)
    entrap_state = _signal_state(text_lower, _ENTRAPMENT_PHRASES)

    result = {
        "fire": fire_state == "affirm",
        "hazmat": hazmat_state == "affirm",
        "roadBlocked": _signal_present(text_lower, _ROAD_BLOCKED_PHRASES),
        "entrapment": entrap_state == "affirm",
        "vulnerableVictim": _signal_present(text_lower, _VULNERABLE_PHRASES),
    }

    cas = _first_count(_CASUALTY_RE, text_lower)
    if cas is None and _any_present(text_lower, _NO_CASUALTY_PHRASES):
        cas = 0
    result["estimatedCasualties"] = cas
    result["estimatedVehiclesInvolved"] = _first_count(_VEHICLE_RE, text_lower)

    # ── Safety-flag determinations (voice dispatcher) ──────────────────────────
    # Every flag the narrative DISCUSSED, with its determined value. Presence of
    # a key = "the caller effectively answered this already".
    det: dict = {}
    for state, flag in (
        (fire_state, "Fire"),
        (hazmat_state, "Hazardous material"),
        (entrap_state, "Trapped"),
    ):
        if state == "affirm":
            det[flag] = True
        elif state == "negate":
            det[flag] = False
    # Heavy bleeding / Conscious / Breathing — two-directional. Heavy bleeding's
    # affirm ("true = present") comes from the HEAVY list; "no bleeding" or a
    # negated heavy phrase rules it out. Conscious/Breathing are "true = good",
    # so an explicit BAD phrase (unconscious / not breathing) sets the flag
    # False and a GOOD phrase sets it True.
    heavy_state = _signal_state(text_lower, _BLEEDING_HEAVY)
    if heavy_state == "affirm":
        det["Heavy bleeding"] = True
    elif heavy_state == "negate" or _any_present(text_lower, _BLEEDING_NONE):
        det["Heavy bleeding"] = False
    if _any_present(text_lower, _CONSCIOUS_BAD):
        det["Conscious"] = False
    elif _signal_present(text_lower, _CONSCIOUS_GOOD):
        det["Conscious"] = True
    if _any_present(text_lower, _BREATHING_BAD):
        det["Breathing"] = False
    elif _signal_present(text_lower, _BREATHING_GOOD):
        det["Breathing"] = True
    # "No one hurt" rules out heavy bleeding too (if not already determined).
    if cas == 0 and "Heavy bleeding" not in det:
        det["Heavy bleeding"] = False

    result["flag_determinations"] = det
    return result
