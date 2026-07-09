"""
dispatcher_live.py — conversational voice dispatcher via Gemini Live (Vertex AI).

Bridges a FastAPI WebSocket (see /ws/dispatcher in app.py) to the Gemini Live
API: streams microphone audio in, streams synthesized speech back out, and
implements 5 function-calling tools that let the model search the real
incident taxonomy, populate the incident report form field-by-field, fetch
GPS location, and submit only after verbal confirmation. The model is never
trusted to invent an incident type or to submit without confirmation --
`_tool_update_form_field` re-validates incident types against the real
taxonomy, and `_tool_submit_incident` hard-gates on required fields in plain
Python, not model judgment (matches this project's rule-first architecture --
see severity_engine/dispatch.py, local_extract.py for the same pattern).

Verified empirically before writing this module (Vertex AI Live API is new,
fast-moving, and this project had zero prior Vertex AI usage):
  - genai.Client(vertexai=True, ...) + client.aio.live.connect() works with
    the existing Speech-to-Text service account, project "trans-sahayak",
    region us-central1, model "gemini-live-2.5-flash-native-audio" -- no
    extra IAM grant was needed beyond what Speech-to-Text already had.
  - Function calling works with the standard synchronous FunctionResponse
    pattern (no "scheduling" field needed) -- but critically,
    session.receive() must be called again in an outer loop after each
    turn_complete to get the model's follow-up spoken turn that uses the
    tool result; a single flat `async for` over one receive() call only
    yields the turn containing the tool call itself, then ends.
  - send_realtime_input() needs genuinely real-time-paced audio (chunks
    sent no faster than the audio they represent) for Voice Activity
    Detection to detect end-of-speech -- sending pre-recorded audio "as
    fast as possible" silently produces zero response. A live microphone
    stream in the browser is naturally real-time-paced, so this is a
    testing-methodology note, not a runtime concern for the real feature.

Credentials reuse severity_engine/google_credentials.py (copied from
voice_stream.py's identical loader, not imported from it, so the working
Chirp pipeline is never at risk from changes here).
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket
from google import genai
from google.genai import types
from google.oauth2 import service_account

from . import classifier, local_extract
from .dispatch_briefing import build_briefing_instruction
from .google_credentials import load_service_account_info

logger = logging.getLogger("dispatcher_live")

# Verified empirically against the real API -- see module docstring.
_LOCATION = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
SUPPORTED_LANGUAGES = ("en-IN", "hi-IN")
_DEFAULT_LANGUAGE = "en-IN"

_LOCATION_TIMEOUT_S = 8.0

# ── Reliability (Issue: "sometimes the agent simply stops responding") ────────
# If the caller has spoken and the model has produced nothing for this long,
# the watchdog first nudges the live session with a synthetic "the caller is
# waiting" turn; if that also produces nothing within the same window again,
# the session is torn down and transparently reconnected (state is preserved
# in DispatcherState, so the fresh session apologizes and continues where the
# call left off instead of starting over). Env-overridable for testing.
_RESPONSE_TIMEOUT_S = float(os.environ.get("GEMINI_RESPONSE_TIMEOUT_S", "12"))
_GREETING_TIMEOUT_S = float(os.environ.get("GEMINI_GREETING_TIMEOUT_S", "15"))
_MAX_RECONNECTS = int(os.environ.get("GEMINI_MAX_RECONNECTS", "2"))

# ── Post-submission closing briefing (see dispatch_briefing.py) ───────────────
# After submit_incident the browser dashboard runs its existing matching flow
# and sends the SAME responder ETAs it displays back as one "dispatch_update"
# frame. How long to wait for that frame before closing without ETAs (the
# matching flow normally takes ~5-10s; a duplicate-confirmation dialog can
# stall it, hence the generous but finite window), and how long to give the
# model to actually finish speaking the closing briefing before force-ending
# the call (the watchdog is parked after submission, so the briefing needs its
# own failsafe).
_DISPATCH_WAIT_S = float(os.environ.get("DISPATCH_BRIEFING_WAIT_S", "30"))
# Failsafe for the closing briefing turn -- see _brief_and_close. This is a
# STALL timeout (time since the last audio chunk), not a total-length budget:
# the full briefing (multiple responder ETAs + up to 4 SOP lines + a 6-line
# closing script) can legitimately run well past a minute of continuous
# speech, and an earlier flat "wait N seconds total, then force-end" version
# of this timer was cutting real replies off mid-sentence (confirmed live:
# caller heard the ambulance ETA, then got cut off partway into the fire
# service ETA, because 45s total wasn't enough for that much content and the
# forced call_complete raced ahead of audio the backend was still sending).
# Only genuine silence for this long -- no new audio chunk arriving at all --
# should ever force the call to end early.
_BRIEFING_STALL_TIMEOUT_S = float(os.environ.get("DISPATCH_BRIEFING_STALL_TIMEOUT_S", "15"))

_RECONNECT_APOLOGY = {
    "hi-IN": "मुझे क्षमा कीजिए, तकनीकी समस्या आ गई है। कृपया दोबारा बोलें।",
    "en-IN": "I'm sorry, there was a brief technical problem. Could you please say that again?",
}

# ── Vehicle-pair matching (Issue: "कार की ट्रक से टक्कर" -> Car vs. Car) ──────
# The keyword classifier's "Car vs. Car Collision" record has keyword-stuffed
# cause text ("car to car crash / another car / car hit car ...") that
# out-scores "Truck vs. Car – Speed Differential" even when the caller
# explicitly named both a car AND a truck -- reproduced directly:
# classifier.guess("car collided with a truck") -> Car vs. Car Collision.
# Deterministic fix at the dispatcher-tool level: when the caller's own words
# name two distinct vehicle types, the recorded subType must name both.
# Devanagari aliases are matched as EXACT whole tokens (never substrings) so
# "सरकार" can never match "कार", and English aliases with word boundaries so
# "cargo" can never match "car".
_VEHICLE_TYPES: dict[str, dict] = {
    "car": {"say": ["car", "कार", "गाड़ी"], "subtype": [r"\bcar\b"]},
    "truck": {"say": ["truck", "lorry", "ट्रक", "लॉरी", "ट्राला"], "subtype": [r"\btruck\b"]},
    "bus": {"say": ["bus", "बस"], "subtype": [r"\bbus\b"]},
    "two-wheeler": {
        "say": ["motorcycle", "motorbike", "bike", "scooter", "scooty",
                "बाइक", "मोटरसाइकिल", "स्कूटर", "स्कूटी"],
        "subtype": [r"\btwo-wheeler\b"],
    },
    "auto-rickshaw": {"say": ["auto", "rickshaw", "ऑटो", "रिक्शा"], "subtype": [r"\bauto-rickshaw\b"]},
}

_DEVANAGARI_TOKEN_RE = re.compile(r"[ऀ-ॿ]+")
_LATIN_TOKEN_RE = re.compile(r"[a-z]+")


def _mentioned_vehicle_types(text: str) -> set:
    """Canonical vehicle types explicitly named in the caller's words."""
    lower = (text or "").lower()
    tokens = set(_DEVANAGARI_TOKEN_RE.findall(lower)) | set(_LATIN_TOKEN_RE.findall(lower))
    return {canon for canon, spec in _VEHICLE_TYPES.items() if tokens & set(spec["say"])}


def _find_vehicle_pair_subtype(mentioned: set) -> Optional[str]:
    """The taxonomy subType naming ALL the mentioned vehicle types, if one
    exists (e.g. {car, truck} -> "Truck vs. Car – Speed Differential")."""
    for rec in classifier.INDEX:
        st = rec["subType"].lower()
        if all(any(re.search(p, st) for p in _VEHICLE_TYPES[m]["subtype"]) for m in mentioned):
            return rec["subType"]
    return None


# ── Same-vehicle-type collision override (Issue: "मेरी कार दूसरी कार से टकरा
# गई" -> agent asks the caller to confirm instead of recording Car vs. Car
# immediately) ──────────────────────────────────────────────────────────────
# _mentioned_vehicle_types above dedupes into a SET, so "car ... car" (the
# same type named twice, e.g. "my car" and "another car") collapses to
# {"car"} -- indistinguishable from a single passing mention of "car" (e.g.
# "my car broke down", not a collision at all). That single-element set can
# never trigger the two-distinct-type override above, so it fell through
# entirely to classify()'s fuzzy keyword/TF-IDF scoring -- which, even after
# fixing the हिंदी_glossary.json gap that dropped "कार" tokens outright
# (2026-07 fix), remains sensitive to phrasing the glossary/corpus doesn't
# happen to weight strongly, and produced a lowConfidence result for some
# real Hindi phrasings, sending the caller a clarifying question for what is
# unambiguous input ("my car collided with another car" IS Car vs. Car,
# deterministically, no LLM judgment needed). Fixed the same way as the
# two-distinct-type case: when the caller's OWN words name one vehicle type
# TWICE (not deduped) alongside a collision verb, and the taxonomy has an
# "X vs. X" record for that type (today: only Car vs. Car and Two-Wheeler
# vs. Two-Wheeler -- most types have no same-type record, so this correctly
# no-ops for them and classify() remains the only path), that record wins
# outright. Never fires on a single passing mention with no collision
# language ("my car broke down" stays with classify()'s own scoring).
_COLLISION_SIGNAL_RE = re.compile(
    r"\b(collision|collided|collide|crash(?:ed)?|struck|strike|rammed|smashed|slammed|hit)\b"
    r"|टक्कर|टकरा|भिड़ंत|भिड़|ठोकर|ठोक"
)


def _mentions_collision(text: str) -> bool:
    return bool(_COLLISION_SIGNAL_RE.search((text or "").lower()))


def _vehicle_type_mention_counts(text: str) -> dict:
    """Canonical vehicle type -> how many times ANY of its aliases appear in
    the caller's raw words, NOT deduped -- lets "कार ... कार" (the same type
    named twice) be told apart from a single passing mention. Same exact
    whole-token matching as _mentioned_vehicle_types (never a substring)."""
    lower = (text or "").lower()
    tokens = _DEVANAGARI_TOKEN_RE.findall(lower) + _LATIN_TOKEN_RE.findall(lower)
    counts: dict = {}
    for canon, spec in _VEHICLE_TYPES.items():
        say_set = set(spec["say"])
        n = sum(1 for t in tokens if t in say_set)
        if n:
            counts[canon] = n
    return counts


def _find_same_type_subtype(vehicle_type: str) -> Optional[str]:
    """The taxonomy's "<Type> vs. <Type>" record for this vehicle type, if
    one exists -- checked by the type's own subtype pattern matching TWICE
    within a single subType string, so this needs no hardcoded record names
    and correctly returns None for types with no same-type record."""
    pattern = _VEHICLE_TYPES[vehicle_type]["subtype"][0]
    for rec in classifier.INDEX:
        st = rec["subType"].lower()
        if len(re.findall(pattern, st)) >= 2:
            return rec["subType"]
    return None


_FLAG_NAMES = ["Conscious", "Breathing", "Trapped", "Heavy bleeding", "Fire", "Hazardous material"]

# Maps severity_engine.local_extract's signal keys (its lexicon already
# includes Hindi phrases -- see local_extract.py's _FIRE_PHRASES etc.) onto
# this feature's flag vocabulary. Used as a deterministic backstop: confirmed
# live that the model sometimes never calls update_form_field for a
# condition the caller mentioned, even while its own spoken summary shows it
# understood the mention -- so hazard flags are not left depending solely on
# the model remembering to make a separate tool call, same "rule-first,
# LLM-optional" pattern this project already uses for the text-based /assess
# pipeline (see engine.py's _merge_signals, which this mirrors: OR-only,
# never downgrades something already confirmed).
#
# Deliberately does NOT include local_extract's "vulnerableVictim" signal --
# confirmed live that it's a broad "at-risk victim" category (child,
# pregnant, elderly, disabled, OR unconscious -- see local_extract.py's
# _VULNERABLE_PHRASES), not specifically bleeding, so mapping it to the
# "Heavy bleeding" flag produced a false positive the instant a caller said
# "unconscious" with no bleeding mentioned at all. Only signals with a clean,
# unambiguous 1:1 correspondence to a real flag belong here.
_LOCAL_SIGNAL_TO_FLAG = {
    "fire": "Fire",
    "entrapment": "Trapped",
    "hazmat": "Hazardous material",
}

# Speech-to-Text sometimes emits bracketed non-speech annotations for
# silence/background noise (observed live: a literal "{background}" token in
# the transcript) -- strip these before ever using raw transcript as a
# last-resort description fallback.
_TRANSCRIPT_ARTIFACT_RE = re.compile(r"[\{\[\(][^\}\]\)]{0,40}[\}\]\)]")


def _strip_transcript_artifacts(text: str) -> str:
    cleaned = _TRANSCRIPT_ARTIFACT_RE.sub(" ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


class DispatcherCredentialsError(RuntimeError):
    """Raised when no usable Google Cloud credentials can be located."""


# Lazily-initialised, cached at module scope -- built once, not per connection.
_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is not None:
        return _client
    info = load_service_account_info()
    if not info:
        raise DispatcherCredentialsError(
            "No Google Cloud credentials found for the Gemini Live dispatcher. Set "
            "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 or GOOGLE_SERVICE_ACCOUNT_JSON (Railway/"
            "production), or place the local service account file (local dev)."
        )
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or info.get("project_id")
    if not project_id:
        raise DispatcherCredentialsError("Service account JSON has no project_id field.")
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    _client = genai.Client(vertexai=True, project=project_id, location=_LOCATION, credentials=credentials)
    logger.info("Gemini Live client initialised for project %s (model=%s, location=%s)",
                project_id, _MODEL, _LOCATION)
    return _client


@dataclass
class DispatcherState:
    language: str = _DEFAULT_LANGUAGE
    sub_type: Optional[str] = None
    category: Optional[str] = None
    description: str = ""
    vehicles_involved: Optional[int] = None
    casualties: Optional[int] = None
    flags: set = field(default_factory=set)
    flags_discussed: set = field(default_factory=set)  # flags asked about, active or not
    location: Optional[dict] = None  # {"lat", "lng", "label"}
    submitted: bool = False
    caller_transcript: str = ""  # accumulated raw transcript of what the caller has said
    description_set_explicitly: bool = False  # True once update_form_field(description) is called


# Additional structured fields to ask about per curated category (beyond the
# baseline incidentType/location/description required for every incident --
# see _compute_still_missing). Keyed by the 11 curated categories in
# category_groups.json. Deliberately a data map, not model judgment -- the
# model's job is to phrase the question naturally, not decide what to ask.
REQUIRED_FIELDS: dict[str, list[dict]] = {
    "Vehicle Collisions": [
        {"field": "vehiclesInvolved", "hint": "how many vehicles were involved"},
        {"field": "casualties", "hint": "how many people are injured"},
        {"field": "flag:Trapped", "hint": "whether anyone is trapped inside a vehicle"},
        {"field": "flag:Fire", "hint": "whether there is fire or a fuel leak"},
    ],
    "Medical & Casualty": [
        {"field": "flag:Conscious", "hint": "whether the person is conscious"},
        {"field": "flag:Breathing", "hint": "whether the person is breathing"},
        {"field": "flag:Heavy bleeding", "hint": "whether there is heavy bleeding"},
        {"field": "casualties", "hint": "how many people are affected"},
    ],
    "Fire & Hazmat": [
        {"field": "flag:Hazardous material", "hint": "whether hazardous material is involved"},
        {"field": "flag:Trapped", "hint": "whether anyone is trapped or still inside"},
        {"field": "casualties", "hint": "how many people are affected"},
    ],
    "Weather & Terrain Hazards": [
        {"field": "flag:Trapped", "hint": "whether anyone is trapped or stranded"},
        {"field": "vehiclesInvolved", "hint": "how many vehicles are affected"},
    ],
    "Infrastructure & Structures": [
        {"field": "vehiclesInvolved", "hint": "how many vehicles are affected"},
        {"field": "casualties", "hint": "whether anyone is hurt"},
    ],
    "Breakdown & Cargo": [
        {"field": "vehiclesInvolved", "hint": "how many vehicles are affected"},
    ],
    "Crime & Security": [
        {"field": "casualties", "hint": "whether anyone is hurt"},
    ],
    "Wildlife & Rare Situations": [
        {"field": "casualties", "hint": "whether anyone is hurt"},
        {"field": "vehiclesInvolved", "hint": "how many vehicles are involved"},
    ],
}
DEFAULT_REQUIRED_FIELDS: list[dict] = [
    {"field": "vehiclesInvolved", "hint": "how many vehicles were involved, if any"},
    {"field": "casualties", "hint": "how many people are injured, if any"},
]

_TOOL_DECLARATIONS = [
    {
        "name": "search_incident_type",
        "description": (
            "Search the official incident taxonomy for the best-matching incident type given a "
            "free-text description of what happened. ALWAYS call this before telling the caller "
            "what type of incident this is -- never guess or invent an incident type yourself."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description": {
                    "type": "STRING",
                    "description": "Free-text description of what happened, in your own words.",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "search_incident_categories",
        "description": (
            "List all incident categories, or list the incident subtypes within one category. "
            "Use this if search_incident_type's top match doesn't sound right and you need to "
            "browse alternatives with the caller."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "An exact category name to list subtypes for. Omit to list all categories.",
                },
            },
        },
    },
    {
        "name": "update_form_field",
        "description": (
            "Update one field of the incident report form. Call this immediately every time the "
            "caller gives you a new piece of information -- do not wait until the end of the call."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "field": {
                    "type": "STRING",
                    "enum": ["incidentType", "description", "vehiclesInvolved", "casualties", "flag"],
                    "description": "Which field to update.",
                },
                "sub_type": {
                    "type": "STRING",
                    "description": (
                        "Required when field=incidentType: the EXACT subType string returned by "
                        "search_incident_type or search_incident_categories -- never a paraphrase."
                    ),
                },
                "category": {
                    "type": "STRING",
                    "description": "Required when field=incidentType: the category the sub_type belongs to.",
                },
                "flag_name": {
                    "type": "STRING",
                    "enum": _FLAG_NAMES,
                    "description": "Required when field=flag: which condition this is.",
                },
                "flag_active": {
                    "type": "BOOLEAN",
                    "description": (
                        "Required when field=flag: true means the state named by flag_name is "
                        "confirmed TRUE, false means it is confirmed FALSE -- watch the polarity "
                        "carefully, it is NOT always 'true = bad'. For Trapped, Fire, and "
                        "Hazardous material: true means that hazard IS present (bad), false means "
                        "it is ruled out. For Conscious and Breathing: true means the person IS "
                        "conscious / IS breathing normally (good) -- so if the caller says "
                        "'unconscious', set Conscious to false, and if the caller says 'not "
                        "breathing' or 'struggling to breathe', set Breathing to false. For Heavy "
                        "bleeding: true means there IS heavy bleeding (bad)."
                    ),
                },
                "number_value": {
                    "type": "INTEGER",
                    "description": "Required when field=vehiclesInvolved or field=casualties.",
                },
                "text_value": {
                    "type": "STRING",
                    "description": "Required when field=description: a concise summary of what happened.",
                },
            },
            "required": ["field"],
        },
    },
    {
        "name": "get_current_location",
        "description": (
            "Get the caller's current GPS location from their device, if available. Call this "
            "once, early in the call, before asking the caller to describe their location."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "submit_incident",
        "description": (
            "Submit the completed incident report. Only call this AFTER you have verbally "
            "summarized everything collected and the caller has explicitly confirmed (said yes, "
            "please submit, that's correct, or similar) -- never call this without an explicit "
            "verbal confirmation first."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


# The exact, mandatory opening line per language -- India's real national
# highway helpline number is 1033, so this is treated like a scripted IVR
# greeting rather than left to the model's own phrasing. Enforced via a hard
# system-prompt instruction (see OPENING below) rather than backend-side TTS,
# since it's spoken words with no downstream data implications (unlike
# incident-type selection, which IS backend-validated) -- proportionate for
# a PoC, matching the project's existing "prompt-constrain what's low-stakes,
# code-validate what feeds real data" pattern.
_OPENING_LINE = {
    "en-IN": "Welcome to the 1033 Highway Helpline of India.",
    "hi-IN": "भारत की 1033 हाईवे हेल्पलाइन में आपका स्वागत है।",
}


def _system_instruction(language_code: str) -> str:
    lang_name = "Hindi" if language_code == "hi-IN" else "English"
    opening_line = _OPENING_LINE.get(language_code, _OPENING_LINE[_DEFAULT_LANGUAGE])
    # Hindi-only reinforcement, appended without touching a single word of
    # the shared instructions below (which the user confirmed already work
    # well in English) -- confirmed over this feature's whole history that
    # Hindi needs more explicit, more repeated reinforcement to reliably
    # match behavior English already follows by default (gender, tone,
    # question order, and vocabulary register all needed this same
    # treatment at different points). Rather than touch the shared TONE /
    # FOLLOW-UP QUESTIONS / FORM FILLING text itself (which would also
    # change English), this restates the same requirements specifically for
    # Hindi, as an additional nudge only Hindi sessions receive.
    hindi_reinforcement = (
        " WHO YOU ARE IN HINDI: you are a female operator at the 1033 highway emergency helpline "
        "-- a real person doing a serious job, exactly like the operators callers reach when they "
        "dial 1033 in India. You are NEVER an AI, an assistant, or a model -- never say anything "
        'like "as an AI", "I think", or anything technical about yourself or how you work; if '
        "asked who you are, you are the 1033 helpline operator, nothing more. "
        "ALWAYS use feminine grammatical verb forms when referring to yourself -- "
        '"समझती हूँ" not "समझता हूँ", "कर रही हूँ" not "कर रहा हूँ", "दर्ज कर रही हूँ" not '
        '"दर्ज कर रहा हूँ", "पुष्टि करना चाहती हूँ" not "पुष्टि करना चाहता हूँ" -- in every sentence '
        "you generate, not just the fixed examples here. "
        "SPEAK LIKE A REAL OPERATOR, NOT A TRANSLATION: use simple, modern, everyday Hindi, the "
        'way people actually speak in India today -- never formal, literary, or Sanskrit-heavy '
        '("shuddh") Hindi, and never stiff literal translations of English phrasing. Say "क्या '
        'हुआ है?" not "क्या आप घटना का प्रकार बता सकते हैं?"; say "ज़रा बताइए वहाँ क्या हुआ?" not '
        '"कृपया विवरण प्रदान करें।"; say "गाड़ी" not "वाहन", "मदद" not "सहायता", "ठीक है" not '
        '"उचित है". Common English loanwords are natural and welcome ("लोकेशन", "रिपोर्ट", '
        '"एम्बुलेंस", "टाइप"). Phrases a real operator uses, in your natural repertoire: "मैं आपकी '
        'मदद के लिए हूँ।" / "कृपया घबराइए मत।" / "सबसे पहले आपकी सुरक्षा ज़रूरी है।" / "क्या सभी '
        'लोग सुरक्षित हैं?" / "क्या किसी को गंभीर चोट लगी है?" / "एम्बुलेंस भेजने के लिए मुझे थोड़ी '
        'और जानकारी चाहिए।" / "धन्यवाद, मैं जानकारी दर्ज कर रही हूँ।" '
        "UNDERSTAND HOW PEOPLE ACTUALLY TALK: callers use colloquial, indirect expressions -- "
        '"टायर फट गया" (tyre burst), "गाड़ी पलट गई" (vehicle overturned), "ठोक दिया" / "भिड़ गई" '
        '(collided), "आग पकड़ ली" (caught fire). Understand the MEANING, never demand the caller '
        "rephrase into formal words. "
        "INCIDENT TYPE IN HINDI -- CRITICAL: when the caller describes what happened, pass their "
        "ACTUAL words (verbatim, in Hindi, exactly as they said them) as the description to "
        "search_incident_type -- never your own paraphrase or translation, and NEVER decide the "
        "vehicle types yourself. If the caller named two vehicles (जैसे कार और ट्रक), the recorded "
        "incident type must name both -- the search tool guarantees this when it gets the "
        "caller's real words. "
        "CONFIRMATION IN HINDI: before submitting, summarize naturally and ask, for example: "
        '"मैं पुष्टि करना चाहती हूँ — आपकी कार की ट्रक से टक्कर हुई है, दो लोग घायल हैं, एक व्यक्ति '
        'फँसा हुआ है, लोकेशन NH-48 है। क्या यह जानकारी सही है?" '
        "IMPORTANT -- everything else in this system prompt (the TONE section's warmth and "
        "concern, the strict one-at-a-time order in FOLLOW-UP QUESTIONS, calling "
        "update_form_field immediately in FORM FILLING, always writing descriptions in English) "
        "applies to you with EXACTLY the same force in Hindi as it would in English -- none of it "
        "is allowed to become weaker, flatter, slower to kick in, or less carefully followed just "
        "because the conversation is in Hindi. Specifically: the instant a caller mentions an "
        "injury, bleeding, someone trapped, or sounds frightened, react with the same real, "
        "sincere concern you would in English -- never just a flat acknowledgment like \"ठीक है\" "
        'or "समझ गई" alone with no warmth. Never ask about something the caller already told you '
        '(if they said "दो लोग घायल हैं", never ask "क्या कोई घायल है?" afterwards). Follow '
        '"next_question" exactly, one topic at a time, the same way you would in an English call '
        "-- do not wander to a different topic or skip ahead just because you are speaking Hindi. "
        "And the SPEAK ONCE PER CALLER TURN rule applies fully in Hindi too: one caller statement "
        "gets exactly ONE spoken response from you, no matter how many tool results come back for "
        "it -- if you already spoke, stay silent and wait; never ask the same question again in "
        "different words."
        if language_code == "hi-IN" else ""
    )
    return f"""You are an emergency dispatch call-taker for a road-accident first-response system in Assam, India. You are having a real-time voice conversation with someone reporting a road accident or emergency.

LANGUAGE: Conduct this entire conversation in {lang_name} only. If the caller speaks a different language, gently continue in {lang_name} rather than switching -- never randomly switch languages yourself.{hindi_reinforcement}

TONE: Calm, warm, and genuinely concerned -- like a serious, caring human dispatcher handling an emergency, not a neutral form-filling bot, and absolutely NOT an upbeat customer-service agent. This is a safety call, not a friendly chat -- your delivery must sound measured, sincere, and a little subdued, never cheerful, chipper, energetic, or excited, even when you are simply acknowledging routine details. If in doubt, err toward quieter and more serious rather than lively. This warmth must come through on EVERY call, not only when the caller explicitly mentions an injury or sounds distressed -- even a caller who reports a routine-sounding incident calmly is still someone dealing with a road accident, and should hear a human who cares, not a checklist. Never let two or more responses in a row go by with a purely neutral, transactional acknowledgment ("Okay." / "Noted.") -- always warm it up at least a little, for example (English, said quietly and sincerely, not brightly): "Thank you for telling me, I'm noting that down" / "I understand, let's get this sorted quickly" / "Alright, I have that noted" -- and when the caller mentions an injury, bleeding, or sounds frightened, go further with real concern: "I'm sorry to hear that, help is on the way" / "That sounds frightening, please try to stay calm" / "I understand, we'll get you help as quickly as we can". In Hindi, the same range applies, spoken with the same quiet seriousness and always in feminine grammatical form: "ठीक है, धन्यवाद, मैं इसे नोट कर रही हूँ" / "समझ गई, चलिए इसे जल्दी सुलझाते हैं" for routine acknowledgments, and for real distress: "मुझे यह सुनकर दुख हुआ, मदद आ रही है" / "कृपया घबराइए मत, हम आपकी मदद कर रहे हैं" / "मैं समझती हूँ, हम जल्द से जल्द सहायता भेज रहे हैं". Vary the phrasing -- never repeat the exact same acknowledgment twice in one call. Every tool response you receive includes a "tone_reminder" -- follow it every single time, not just when you happen to remember to. This warmth must never come at the cost of the rest of this prompt: still ask one question at a time, still keep every response to 1-2 short sentences, still speak a little slower than normal conversational pace with clear pronunciation, and still never repeat a sentence you have already said unless the caller explicitly asks you to. Gathering the information needed to send help quickly is still the priority -- empathy should feel human and serious, not slow the call down and not sound upbeat.

OPENING (the very first thing you do, before the caller says anything, and only ever once for the whole call): as soon as the call connects, say this exact sentence, word for word, with nothing before it and nothing added: "{opening_line}" You will be told the caller's detected location (or that none was detected) in the same message that starts the call -- do not call get_current_location for this, it has already been resolved for you. If a location was given, briefly mention it ("I have your location as X, is that right?") and ask what happened, all in this same first turn. If no location was detected, tell the caller to use the map-pin button to mark their location instead -- do not try to guess a location from a spoken description. Once you have done this opening, it is complete -- never say the welcome sentence again for the rest of the call, no matter what happens, even if it feels like the conversation is starting over. Move straight to gathering information about the incident.

INCIDENT TYPE: Never guess or invent an incident type yourself. Always call search_incident_type with a description of what the caller told you -- it automatically records a confident match for you, so once you've called it you do not need a separate step to confirm the type unless the caller says it's wrong. Refer to the incident only using the exact subType name it returns. If it doesn't sound right to the caller, call search_incident_categories to browse alternatives, then call update_form_field with field=incidentType and the exact subType you both agreed on.

FORM FILLING: Call update_form_field immediately every time the caller gives you a new piece of information -- INCLUDING conditions mentioned in passing, not just direct answers to your questions. If the caller mentions fire, hazmat, anyone trapped, consciousness, breathing, or bleeding ANYWHERE in what they say (even inside a general description), call update_form_field with field=flag for that condition right away -- do not wait for a dedicated question about it.

DESCRIPTION FIELD -- SPECIAL RULE: call update_form_field with field=description as soon as the caller has said ENOUGH for even a rough one-sentence summary -- do not wait until you have every detail or until the end of the call. Call it again, replacing the old value, whenever you learn something that should be added to the summary. ALWAYS write text_value in ENGLISH, no matter what language the conversation itself is in -- translate and summarize what the caller told you, never copy their words verbatim in Hindi or any other language. This is the one field that must always be English regardless of conversation language.

FOLLOW-UP QUESTIONS -- THIS IS A HARD RULE, NOT A SUGGESTION: every tool response includes "next_question", the ONE specific thing to ask about next, or null if nothing is left. This is precomputed for you deterministically -- it is not your judgment call. After any brief acknowledgment, your very next question must be about EXACTLY the topic named in "next_question", worded naturally for the conversation but not substituted for a different topic. Never ask about anything else, never invent your own question (for example, do not ask about consciousness or breathing unless "next_question" specifically says so), never skip ahead to a topic that isn't in "next_question" yet, and never ask about something already answered. Keep asking about the same "next_question" topic (rephrasing if needed) until it is answered and the next tool response gives you a new one, or null. This must produce the exact same sequence of questions regardless of language -- if you find yourself wanting to ask something "next_question" doesn't mention, don't.

SPEAK ONCE PER CALLER TURN -- exactly once, never zero times and never twice: when one statement from the caller gives you several pieces of information, make ALL of your tool calls for it first (update_form_field for each piece, search_incident_type if needed), and only THEN speak -- one single spoken response covering your acknowledgment and the one next question. Never speak in between your own tool calls, and never speak twice in a row for the same caller statement. If you receive a tool result after you have already spoken your acknowledgment and question for this caller turn, say NOTHING further -- just wait for the caller's answer. But the other direction is equally important: every caller statement MUST get exactly one spoken response from you -- if you have not yet responded to the caller's latest statement, you MUST speak; never leave the caller waiting in silence. Only your greeting at the start of the call does not count as a response to anything.

FINAL CONFIRMATION: Before calling submit_incident, verbally summarize everything collected (incident type, key facts, location) and ask "Would you like me to submit this report?" Only call submit_incident after the caller clearly confirms. If it comes back still missing something, ask for it and try again.

AFTER SUBMISSION: when submit_incident succeeds, follow its "next_step": tell the caller their report has been registered and that you are checking which emergency services are responding -- ask them to stay on the line for a moment, and do NOT say goodbye yet. Shortly afterwards you will receive a SYSTEM UPDATE message (not from the caller) containing the responding services with their estimated times, safety instructions to give, and a closing script -- deliver everything in it as one natural, warm, continuous reply, using its exact names and numbers (always as estimates, never as tracked facts -- we do not track any vehicle), then say nothing more; the call ends there.
"""


class DispatcherSession:
    def __init__(self, websocket: WebSocket, language_code: str):
        self.websocket = websocket
        self.state = DispatcherState(language=language_code)
        self._pending_location: dict[str, "asyncio.Future"] = {}
        self._live_session = None
        self._client_task: Optional["asyncio.Task"] = None
        # Watchdog bookkeeping (see _watchdog): monotonic timestamps of the
        # last caller speech and last model activity in the CURRENT session.
        self._session_started: float = 0.0
        self._caller_last_spoke: float = 0.0
        self._model_last_spoke: float = 0.0
        self._nudge_sent_at: float = 0.0
        # Post-submission closing briefing (see dispatch_briefing.py): the
        # browser's dispatch_update payload (the SAME responder ETAs the
        # dashboard displays), the event that fires when it arrives, and the
        # bookkeeping that lets the pump recognize when the final briefing
        # turn has been spoken so the call can end cleanly.
        self._dispatch_info: Optional[dict] = None
        self._dispatch_ready: "asyncio.Event" = asyncio.Event()
        self._briefing_task: Optional["asyncio.Task"] = None
        self._briefing_sent = False
        self._spoke_after_briefing = False
        self._call_over = False

    async def _safe_send_json(self, payload: dict) -> None:
        try:
            await self.websocket.send_json(payload)
        except Exception:
            logger.debug("Could not send message on /ws/dispatcher (socket likely closed)", exc_info=True)

    def _compute_still_missing(self) -> list[str]:
        missing = []
        if not self.state.sub_type:
            missing.append("the incident type (call search_incident_type)")
        if not self.state.location:
            missing.append("the location (call get_current_location)")
        if not self.state.description:
            missing.append("a short description of what happened")
        required = REQUIRED_FIELDS.get(self.state.category, DEFAULT_REQUIRED_FIELDS) if self.state.category else DEFAULT_REQUIRED_FIELDS
        for item in required:
            f = item["field"]
            if f.startswith("flag:"):
                flag_name = f.split(":", 1)[1]
                if flag_name not in self.state.flags_discussed:
                    missing.append(item["hint"])
            elif f == "vehiclesInvolved" and self.state.vehicles_involved is None:
                missing.append(item["hint"])
            elif f == "casualties" and self.state.casualties is None:
                missing.append(item["hint"])
        return missing

    def _tone_reminder(self) -> str:
        """Dynamic, not a fixed string -- escalates specifically when the
        caller has reported an injury or someone in danger. Confirmed live
        that a generic reminder alone still let the model respond to injury
        reports with a flat "noted" -- naming the injury signal explicitly,
        every single turn while it's true, makes the concern far more
        reliable than depending on the model to remember the system prompt's
        general instruction to escalate for distress."""
        injury_reported = (
            (self.state.casualties or 0) > 0
            or "Heavy bleeding" in self.state.flags
            or "Trapped" in self.state.flags
            or ("Conscious" in self.state.flags_discussed and "Conscious" not in self.state.flags)
            or ("Breathing" in self.state.flags_discussed and "Breathing" not in self.state.flags)
        )
        # Both variants must carry the speak-once exception -- this reminder
        # arrives with EVERY tool response, and telling the model to
        # "acknowledge before asking" on each one was itself nudging it to
        # speak again after every tool result, re-asking the question it had
        # just asked (the "repeats a few questions" report).
        if injury_reported:
            return (
                "The caller has reported an injury, someone trapped, or a person in danger. "
                "When you next speak, express real, sincere concern in your own words first "
                "(not a flat \"noted\" or \"okay\") -- this matters, sound genuinely worried "
                "for them, not like you are filling out a form. If you have not yet responded "
                "to the caller's latest statement, respond now -- never leave them in silence. "
                "But if you ALREADY spoke your response to it, say nothing more -- do not "
                "repeat or rephrase a question you are still waiting on."
            )
        return (
            "When you next speak, acknowledge what the caller just said warmly, seriously, and "
            "briefly (not upbeat) before asking your next question. If you have not yet "
            "responded to the caller's latest statement, respond now -- never leave them in "
            "silence. But if you ALREADY spoke your response to it, say nothing more -- do not "
            "repeat or rephrase a question you are still waiting on."
        )

    def _state_block(self) -> dict:
        """Common context merged into every tool response, so the model
        always gets fresh, unambiguous guidance for its very next utterance
        instead of depending on a system-prompt instruction it can drift
        away from over a long conversation. "next_question" (singular, not a
        list) removes any judgment call about which topic or what order to
        ask in -- confirmed via live testing that giving the model a list to
        interpret ("still_missing") let it wander to unrelated questions or
        a different order between runs and between languages; giving it one
        specific next topic each time does not."""
        missing = self._compute_still_missing()
        return {
            "still_missing": missing,
            "next_question": missing[0] if missing else None,
            "tone_reminder": self._tone_reminder(),
        }

    async def _apply_local_signals_from_transcript(self) -> None:
        """Deterministic backstop, run on every fragment of the caller's own
        speech (not tool calls): re-derive hazard signals from the full
        accumulated transcript and OR them into state. Never downgrades
        something already true, never needs the model to have called any
        tool -- this is what catches a hazard the model verbally understood
        but never recorded via update_form_field."""
        if not self.state.caller_transcript.strip():
            return
        # NOTE: deliberately does NOT auto-fill "description" from the raw
        # transcript here (an earlier version did). Confirmed live this
        # backfired: description must always be in ENGLISH regardless of
        # conversation language, which only the model can do (it requires
        # translating/summarizing, not just copying text) -- and satisfying
        # "next_question" for description this early, with raw untranslated
        # text, removed the model's own reason to ever call
        # update_form_field(field="description") with a proper English
        # summary. The FORM FILLING instruction now tells the model to set
        # this incrementally and always in English; _tool_submit_incident
        # still has a last-resort fallback for the rare case the model never
        # does, but it is no longer the first thing that happens here.
        signals = local_extract.extract_signals_locally(self.state.caller_transcript)
        for signal_key, flag_name in _LOCAL_SIGNAL_TO_FLAG.items():
            if signals.get(signal_key) and flag_name not in self.state.flags:
                self.state.flags.add(flag_name)
                self.state.flags_discussed.add(flag_name)
                await self._safe_send_json({
                    "type": "form_update", "field": "flag",
                    "value": {"flag_name": flag_name, "flag_active": True},
                })
        if self.state.vehicles_involved is None and signals.get("estimatedVehiclesInvolved"):
            self.state.vehicles_involved = signals["estimatedVehiclesInvolved"]
            await self._safe_send_json({
                "type": "form_update", "field": "vehiclesInvolved", "value": self.state.vehicles_involved,
            })
        if self.state.casualties is None and signals.get("estimatedCasualties") is not None:
            self.state.casualties = signals["estimatedCasualties"]
            await self._safe_send_json({
                "type": "form_update", "field": "casualties", "value": self.state.casualties,
            })

    # ── Tools ──────────────────────────────────────────────────────────────

    async def _apply_incident_type(self, sub_type: str, category_hint: Optional[str] = None) -> bool:
        """Validate sub_type against the real taxonomy and, if valid, apply it
        to state and push a form_update. Returns False (no state change) if
        sub_type doesn't match a real record exactly."""
        rec = classifier._find_exact(sub_type)
        if not rec:
            return False
        self.state.sub_type = rec["subType"]
        self.state.category = classifier._CATEGORY_MAP.get(rec["subType"], category_hint or "Other")
        await self._safe_send_json({
            "type": "form_update", "field": "incidentType",
            "value": {"subType": self.state.sub_type, "category": self.state.category},
        })
        return True

    async def _tool_search_incident_type(self, description: str = "") -> dict:
        result = classifier.guess(description or "")
        logger.info("Incident search: %r -> %s (conf %s)",
                    (description or "")[:200], result.get("subType"), result.get("confidence"))
        # Deterministic vehicle-pair override: if the caller's words name two
        # distinct vehicle types and the taxonomy has a subType naming both,
        # that beats whatever keyword-overlap scoring picked -- this is the
        # "कार की ट्रक से टक्कर -> Car vs. Car Collision" fix, done in code
        # rather than trusting the model to notice the mismatch.
        mentioned = _mentioned_vehicle_types(description)
        if len(mentioned) == 2:
            pair_subtype = _find_vehicle_pair_subtype(mentioned)
            if pair_subtype and pair_subtype != result.get("subType"):
                logger.info("Vehicle-pair override: %s -> %s (mentioned: %s)",
                            result.get("subType"), pair_subtype, sorted(mentioned))
                result["subType"] = pair_subtype
                result["category"] = classifier._CATEGORY_MAP.get(pair_subtype, "Other")
                result["confidence"] = 0.9
                result["lowConfidence"] = False
        elif len(mentioned) == 1:
            # Same-vehicle-type-twice override (see _find_same_type_subtype's
            # comment): "my car collided with another car" names "car" TWICE
            # in the caller's raw words with collision language, not once --
            # _mentioned_vehicle_types alone can't see that (it dedupes into
            # a set), so count raw mentions separately.
            only_type = next(iter(mentioned))
            counts = _vehicle_type_mention_counts(description)
            if counts.get(only_type, 0) >= 2 and _mentions_collision(description):
                same_subtype = _find_same_type_subtype(only_type)
                if same_subtype and same_subtype != result.get("subType"):
                    logger.info("Same-type-collision override: %s -> %s (type: %s, mentions: %d)",
                                result.get("subType"), same_subtype, only_type, counts[only_type])
                    result["subType"] = same_subtype
                    result["category"] = classifier._CATEGORY_MAP.get(same_subtype, "Other")
                    result["confidence"] = 0.9
                    result["lowConfidence"] = False
        # Hindi conversations were unreliable at the model completing a
        # SEPARATE update_form_field(field="incidentType", ...) call right
        # after this search -- confirmed live: the model would sometimes move
        # on without ever confirming the type, then get stuck later when
        # submit_incident correctly refused to submit without one, looping on
        # "what kind of incident was this?" with no way to recover mid-call.
        # Apply a confident match immediately here instead of requiring a
        # second precise round-trip. The model can still correct this later
        # via search_incident_categories + update_form_field if the caller
        # says this match is wrong.
        sub_type = result.get("subType")
        if sub_type and not result.get("lowConfidence"):
            await self._apply_incident_type(sub_type, result.get("category"))
        result.update(self._state_block())
        return result

    async def _tool_search_incident_categories(self, category: Optional[str] = None) -> dict:
        if category:
            return {"category": category, "subTypes": classifier.get_subtypes_for(category)}
        return {"categories": classifier.get_categories()}

    async def _tool_update_form_field(
        self,
        field: Optional[str] = None,
        sub_type: Optional[str] = None,
        category: Optional[str] = None,
        flag_name: Optional[str] = None,
        flag_active: bool = True,
        number_value: Optional[int] = None,
        text_value: Optional[str] = None,
    ) -> dict:
        if field == "incidentType":
            if not sub_type:
                return {"ok": False, "error": "sub_type is required for field=incidentType"}
            if not await self._apply_incident_type(sub_type, category):
                return {
                    "ok": False,
                    "error": f"{sub_type!r} is not a real incident type -- call search_incident_type "
                             "or search_incident_categories first and use the exact value returned.",
                }
        elif field == "description":
            if text_value is None:
                return {"ok": False, "error": "text_value is required for field=description"}
            self.state.description = text_value
            self.state.description_set_explicitly = True
            await self._safe_send_json({"type": "form_update", "field": "description", "value": text_value})
        elif field == "vehiclesInvolved":
            if number_value is None:
                return {"ok": False, "error": "number_value is required for field=vehiclesInvolved"}
            self.state.vehicles_involved = int(number_value)
            await self._safe_send_json({"type": "form_update", "field": "vehiclesInvolved", "value": int(number_value)})
        elif field == "casualties":
            if number_value is None:
                return {"ok": False, "error": "number_value is required for field=casualties"}
            self.state.casualties = int(number_value)
            await self._safe_send_json({"type": "form_update", "field": "casualties", "value": int(number_value)})
        elif field == "flag":
            if not flag_name or flag_name not in _FLAG_NAMES:
                return {"ok": False, "error": f"flag_name must be one of {_FLAG_NAMES}"}
            self.state.flags_discussed.add(flag_name)
            if flag_active:
                self.state.flags.add(flag_name)
            else:
                self.state.flags.discard(flag_name)
            await self._safe_send_json({
                "type": "form_update", "field": "flag",
                "value": {"flag_name": flag_name, "flag_active": bool(flag_active)},
            })
        else:
            return {"ok": False, "error": f"Unknown field {field!r}"}
        return {"ok": True, **self._state_block()}

    async def _tool_get_current_location(self) -> dict:
        if self.state.location:
            return {"status": "already_have_location", **self.state.location, **self._state_block()}
        request_id = str(uuid.uuid4())
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        self._pending_location[request_id] = fut
        await self._safe_send_json({"type": "request_location", "requestId": request_id})
        try:
            return await asyncio.wait_for(fut, timeout=_LOCATION_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending_location.pop(request_id, None)
            return {"status": "unavailable", "error": "Location request timed out.", **self._state_block()}

    async def _tool_submit_incident(self) -> dict:
        # Last-resort backstop only -- if the model never explicitly called
        # update_form_field(field="description", ...) despite being told to
        # do so incrementally and in English, fall back to the accumulated
        # raw transcript rather than blocking submission forever. This is
        # NOT translated (no local translation engine available), so it may
        # not be in English if the caller spoke Hindi -- an accepted, rare
        # last resort, not the primary path (see FORM FILLING in the system
        # instruction, which is the primary path and should make this rare).
        if not self.state.description and self.state.caller_transcript.strip():
            cleaned = _strip_transcript_artifacts(self.state.caller_transcript)
            if cleaned:
                self.state.description = cleaned[:500]
                await self._safe_send_json({"type": "form_update", "field": "description", "value": self.state.description})
        blocking = [
            m for m in self._compute_still_missing()
            if "incident type" in m or "location" in m or "description" in m
        ]
        if blocking:
            return {"ok": False, "error": f"Cannot submit yet -- still missing: {'; '.join(blocking)}", **self._state_block()}
        payload = {
            "subType": self.state.sub_type,
            "category": self.state.category,
            "description": self.state.description,
            "vehiclesInvolved": self.state.vehicles_involved,
            "casualties": self.state.casualties,
            "flags": sorted(self.state.flags),
            "location": self.state.location,
        }
        self.state.submitted = True
        await self._safe_send_json({"type": "submitted", "incident": payload})
        # The call does NOT end here anymore: the browser now runs its
        # matching flow and sends back the responder ETAs it displays, which
        # this session delivers as a final closing briefing (ETAs → safety
        # instructions → follow-up-call script) — see dispatch_briefing.py.
        return {
            "ok": True,
            "next_step": (
                "Report submitted successfully. Tell the caller their incident has been "
                "registered and that you are now checking which emergency services are "
                "responding -- ask them to stay on the line for just a moment. Do NOT say "
                "goodbye or end the call yet; you will shortly receive the responder details "
                "to read out."
            ),
        }

    async def _dispatch_tool(self, name: str, args: dict) -> dict:
        handlers = {
            "search_incident_type": self._tool_search_incident_type,
            "search_incident_categories": self._tool_search_incident_categories,
            "update_form_field": self._tool_update_form_field,
            "get_current_location": self._tool_get_current_location,
            "submit_incident": self._tool_submit_incident,
        }
        handler = handlers.get(name)
        if handler is None:
            logger.warning("Unknown tool requested: %r", name)
            return {"ok": False, "error": f"Unknown tool {name!r}"}
        logger.info("Tool call: %s(%s)", name, json.dumps(args, ensure_ascii=False, default=str)[:300])
        try:
            result = await handler(**args)
            logger.info("Tool result: %s -> %s", name,
                        json.dumps(result, ensure_ascii=False, default=str)[:300])
            return result
        except Exception:
            logger.exception("Tool %s failed", name)
            return {"ok": False, "error": "Internal error executing this tool -- please try again."}

    # ── Session lifecycle ───────────────────────────────────────────────────

    def _build_config(self) -> "types.LiveConnectConfig":
        # Hindi-only sampling controls: every Start Conversation opens a fresh
        # Gemini Live session, and at default sampling settings the Hindi
        # model's call-to-call variance was high enough that each call felt
        # like "a different agent" -- different phrasing, sometimes drifting
        # into off-list questions despite next_question. Lower temperature
        # makes each fresh session behave like the same, consistent agent.
        # NO fixed seed, deliberately: an earlier version pinned seed=1033
        # and Hindi promptly became unusable for the real user while every
        # synthesized test call passed -- the exact trap of a fixed seed with
        # real-world audio input: whatever degenerate generation path a
        # particular caller's voice/phrasing happens to hit gets locked in
        # and reproduced on EVERY call for that caller ("not working, every
        # time"), while different test audio never encounters it. Temperature
        # alone gives the consistency without the correlated-failure risk.
        # English is deliberately left at API defaults -- it's confirmed
        # working well and must not change (per user request).
        hindi_consistency: dict = (
            {"temperature": 0.4}
            if self.state.language == "hi-IN" else {}
        )
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(language_code=self.state.language),
            tools=[{"function_declarations": _TOOL_DECLARATIONS}],
            system_instruction=types.Content(parts=[types.Part(text=_system_instruction(self.state.language))]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            **hindi_consistency,
            # Without headphones, the caller's mic inevitably picks up some of
            # Gemini's own voice bleeding out of the speaker -- confirmed live
            # this was being misread as the caller interrupting mid-sentence,
            # which made the model re-say parts of what it had just said
            # ("repeating sentences"). NO_INTERRUPTION plus a lower start-of-
            # speech sensitivity stops server-side VAD from treating that
            # echo as real speech. The frontend also stops transmitting mic
            # audio while status is "speaking" (see useVoiceDispatcher.ts) as
            # a second layer -- between the two, genuine caller barge-in is
            # traded away deliberately in favor of not garbling the model's
            # own speech, which matters more for a dispatch call.
            #
            # end_of_speech_sensitivity/silence_duration_ms were never tuned
            # until a live report that the model was answering before the
            # caller finished a sentence (a brief mid-sentence pause was
            # enough for the default VAD to decide the caller was done) --
            # which also produced repeated questions, since the model was
            # reacting to a cut-off, incomplete answer. Lowering end-of-
            # speech sensitivity and requiring a full second of silence
            # gives real pauses room without being mistaken for a full stop.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    silence_duration_ms=2000,
                ),
                activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            ),
        )

    async def run(self) -> None:
        client = _get_client()
        await self._safe_send_json({"type": "ready"})
        # Start the client->Gemini pump FIRST -- it's the only thing that
        # listens for the browser's "location_result" reply, so the upfront
        # location fetch below would otherwise just hang until its own
        # timeout with nothing ever receiving the response. It runs for the
        # WHOLE call, across Gemini session reconnects -- the browser-facing
        # socket never restarts just because the Gemini side had to.
        self._client_task = asyncio.create_task(self._pump_client_to_gemini())
        try:
            # Resolve GPS location BEFORE the model says anything, and hand
            # it directly to the kickoff turn as plain text, rather than
            # having the model call get_current_location mid-utterance for
            # its opening line (verified live: that mid-turn tool call was
            # the cause of Hindi-specific repeated openings).
            location_result = await self._tool_get_current_location()
            if location_result.get("status") in ("ok", "already_have_location"):
                location_note = f"Detected location: {location_result.get('label', '')}."
            else:
                location_note = "No location was detected."
            kickoff = f"(The call has just connected. {location_note} Begin now.)"

            # Reliability loop (Issue: "sometimes the agent simply stops
            # responding -- mic stays active but Gemini never speaks again").
            # A Gemini Live session can die or wedge mid-call (Google-side
            # session limits, transient stream failures) while the browser
            # socket stays perfectly healthy -- previously that ended the
            # whole call silently. Now: the session is reconnected in place,
            # DispatcherState (all collected form fields) survives because it
            # lives on this object rather than in the model's context, and
            # the fresh session is told to apologize briefly and continue
            # from exactly where the call left off -- never the greeting
            # again, never a silent dead call.
            reconnects = 0
            while True:
                outcome = await self._run_live_session(client, kickoff)
                if outcome == "ended":
                    return
                if reconnects >= _MAX_RECONNECTS:
                    logger.error("Gemini Live session failed after %d reconnect(s) -- giving up", reconnects)
                    await self._safe_send_json({
                        "type": "error",
                        "message": "The voice service hit a technical problem. Please end the call and try again.",
                    })
                    return
                reconnects += 1
                logger.warning("Reconnecting Gemini Live session (attempt %d/%d)", reconnects, _MAX_RECONNECTS)
                await self._safe_send_json({"type": "status", "state": "reconnecting"})
                kickoff = self._reconnect_kickoff()
                await asyncio.sleep(1.0)
        finally:
            for task in (self._client_task, self._briefing_task):
                if task is None:
                    continue
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _run_live_session(self, client: "genai.Client", kickoff_text: str) -> str:
        """One Gemini Live session within the browser call. Returns "ended"
        when the call is genuinely over (client hung up or report submitted),
        "reconnect" when the Gemini side died/wedged but the caller is still
        there."""
        try:
            async with client.aio.live.connect(model=_MODEL, config=self._build_config()) as live_session:
                self._live_session = live_session
                self._session_started = time.monotonic()
                self._model_last_spoke = 0.0
                self._nudge_sent_at = 0.0
                # Gemini Live is reactive -- it won't speak until it receives
                # input, so kick off with a synthetic system-directed turn.
                await live_session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=kickoff_text)]),
                    turn_complete=True,
                )
                gemini_task = asyncio.create_task(self._pump_gemini_to_client())
                watchdog_task = asyncio.create_task(self._watchdog())
                done, _ = await asyncio.wait(
                    {self._client_task, gemini_task, watchdog_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in (gemini_task, watchdog_task):
                    if task not in done:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                if self._client_task in done or self.state.submitted:
                    return "ended"
                if watchdog_task in done and not watchdog_task.cancelled():
                    logger.warning("Watchdog requested reconnect")
                    return "reconnect"
                # gemini pump ended on its own: session closed server-side
                # (or errored) while the caller is still connected.
                logger.warning("Gemini Live session ended mid-call")
                return "reconnect"
        except Exception:
            logger.exception("Gemini Live session failed to start/run")
            return "reconnect"

    async def _watchdog(self) -> None:
        """Returns (completing its task) ONLY when the session looks wedged
        and should be reconnected: the caller spoke (or the call just
        started) and the model has produced nothing for too long, even after
        a nudge. Runs forever otherwise."""
        while True:
            await asyncio.sleep(2.0)
            if self.state.submitted:
                await asyncio.sleep(3600)  # nothing left to guard; park until cancelled
                continue
            now = time.monotonic()
            if self._model_last_spoke == 0.0:
                # Not even the greeting has arrived yet.
                waiting_since = self._session_started
                timeout = _GREETING_TIMEOUT_S
            elif self._caller_last_spoke > self._model_last_spoke:
                waiting_since = self._caller_last_spoke
                timeout = _RESPONSE_TIMEOUT_S
            else:
                continue
            if now - waiting_since <= timeout:
                continue
            if self._nudge_sent_at < waiting_since:
                self._nudge_sent_at = now
                logger.warning("Watchdog: no model response for %.0fs -- nudging session",
                               now - waiting_since)
                try:
                    await self._live_session.send_client_content(
                        turns=types.Content(role="user", parts=[types.Part(
                            text="(The caller is still waiting for your reply. Respond to their last statement now.)"
                        )]),
                        turn_complete=True,
                    )
                except Exception:
                    logger.warning("Watchdog: nudge send failed -- session is dead")
                    return
            elif now - self._nudge_sent_at > timeout:
                logger.warning("Watchdog: nudge did not revive the session -- requesting reconnect")
                return

    def _reconnect_kickoff(self) -> str:
        """Kickoff turn for a fresh Gemini session mid-call: the model has no
        memory of the conversation (state lives here, not in its context), so
        hand it everything collected so far plus the exact apology to say."""
        apology = _RECONNECT_APOLOGY.get(self.state.language, _RECONNECT_APOLOGY["en-IN"])
        recorded = []
        if self.state.sub_type:
            recorded.append(f"incident type: {self.state.sub_type}")
        if self.state.description:
            recorded.append(f"description: {self.state.description}")
        if self.state.vehicles_involved is not None:
            recorded.append(f"vehicles involved: {self.state.vehicles_involved}")
        if self.state.casualties is not None:
            recorded.append(f"casualties: {self.state.casualties}")
        if self.state.flags:
            recorded.append(f"confirmed conditions: {', '.join(sorted(self.state.flags))}")
        if self.state.location:
            recorded.append(f"location: {self.state.location.get('label', '')}")
        summary = "; ".join(recorded) if recorded else "nothing recorded yet"
        missing = self._compute_still_missing()
        next_topic = missing[0] if missing else "the final confirmation and submission"
        return (
            "(The call reconnected after a brief technical problem, mid-conversation. Do NOT say the "
            f'welcome line again. First say exactly this, word for word: "{apology}" '
            f"Already recorded, do not re-ask any of it: {summary}. "
            f"Then continue from where the call left off -- the next thing to ask about is: {next_topic}.)"
        )

    async def _pump_client_to_gemini(self) -> None:
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    # Per-chunk try: this pump outlives Gemini session
                    # reconnects, so a chunk arriving during the brief gap
                    # between sessions (or into a just-died session) must be
                    # dropped, not kill the whole browser-facing call.
                    try:
                        if self._live_session is not None:
                            await self._live_session.send_realtime_input(
                                audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                            )
                    except Exception:
                        logger.debug("Dropped one audio chunk (Gemini session unavailable)")
                    continue
                text = message.get("text")
                if text is None:
                    continue
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype == "end":
                    break
                if mtype == "location_result":
                    fut = self._pending_location.pop(msg.get("requestId"), None)
                    if fut and not fut.done():
                        self.state.location = {"lat": msg.get("lat"), "lng": msg.get("lng"), "label": msg.get("label", "")}
                        fut.set_result({"status": "ok", **self.state.location, **self._state_block()})
                elif mtype == "location_error":
                    fut = self._pending_location.pop(msg.get("requestId"), None)
                    if fut and not fut.done():
                        fut.set_result({"status": "unavailable", "error": msg.get("message", "denied"), **self._state_block()})
                elif mtype == "dispatch_update":
                    # The browser's matching flow finished: these are the SAME
                    # responder ETAs the dashboard is displaying (see
                    # MatchingPanel.tsx / ReportPanel.tsx) — never recomputed
                    # here, only spoken. Wakes _brief_and_close.
                    self._dispatch_info = msg.get("services") or None
                    self._dispatch_ready.set()
        except Exception:
            logger.debug("Client->Gemini pump ended", exc_info=True)

    async def _brief_and_close(self, live_session) -> None:
        """Runs once, spawned at the first turn_complete after submit_incident
        succeeds: wait for the browser's dispatch_update (the SAME responder
        ETAs the dashboard is already displaying), then hand the model one
        final synthetic turn — responder briefing, SOP safety guidance, and
        closing script (see dispatch_briefing.py). Every stage has a failsafe
        so the call always closes, even if the dashboard data never arrives
        or the session wedges (the watchdog is parked after submission)."""
        try:
            await asyncio.wait_for(self._dispatch_ready.wait(), timeout=_DISPATCH_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning("No dispatch_update within %.0fs -- closing without responder ETAs",
                           _DISPATCH_WAIT_S)
        instruction = build_briefing_instruction(self.state, self._dispatch_info, self.state.language)
        briefing_sent_at = time.monotonic()
        try:
            self._briefing_sent = True
            await live_session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=instruction)]),
                turn_complete=True,
            )
        except Exception:
            logger.exception("Could not send the closing briefing -- ending the call")
            self._call_over = True
            await self._safe_send_json({"type": "call_complete"})
            return
        # Poll for genuine STALLING rather than sleeping once for a flat total
        # budget (see _BRIEFING_STALL_TIMEOUT_S comment for why) -- as long as
        # _model_last_spoke (updated on every audio chunk in
        # _pump_gemini_to_client, including throughout this reply) keeps
        # advancing, keep waiting no matter how long the reply naturally
        # runs; only force-end after real silence since either the briefing
        # was sent or the last chunk played, whichever is more recent.
        while not self._call_over:
            await asyncio.sleep(1.0)
            since_activity = time.monotonic() - max(self._model_last_spoke, briefing_sent_at)
            if since_activity > _BRIEFING_STALL_TIMEOUT_S:
                logger.warning("No briefing audio for %.0fs -- forcing call end", since_activity)
                self._call_over = True
                await self._safe_send_json({"type": "call_complete"})
                return

    async def _pump_gemini_to_client(self) -> None:
        live_session = self._live_session
        assert live_session is not None
        try:
            while not self._call_over:
                async for response in live_session.receive():
                    if response.tool_call:
                        # Deliberately NOT counted as the model "speaking" --
                        # the watchdog tracks audible replies only, so a
                        # caller left hanging behind silent tool churn or an
                        # empty turn still gets nudged ("never leave the
                        # user without a spoken reply").
                        function_responses = []
                        for fc in response.tool_call.function_calls:
                            result = await self._dispatch_tool(fc.name, fc.args or {})
                            function_responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
                        await live_session.send_tool_response(function_responses=function_responses)

                    sc = response.server_content
                    if sc is None:
                        continue
                    if sc.input_transcription and sc.input_transcription.text:
                        self._caller_last_spoke = time.monotonic()
                        await self._safe_send_json({"type": "transcript", "role": "user", "text": sc.input_transcription.text})
                        self.state.caller_transcript += " " + sc.input_transcription.text
                        await self._apply_local_signals_from_transcript()
                    if sc.output_transcription and sc.output_transcription.text:
                        self._model_last_spoke = time.monotonic()
                        await self._safe_send_json({"type": "transcript", "role": "model", "text": sc.output_transcription.text})
                    if sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data:
                                self._model_last_spoke = time.monotonic()
                                if self._briefing_sent:
                                    # Audio produced after the closing briefing
                                    # was injected — the turn_complete that
                                    # follows it genuinely ends the call (and
                                    # not a stray turn_complete from a reply
                                    # that was already in flight).
                                    self._spoke_after_briefing = True
                                await self._safe_send_json({"type": "status", "state": "speaking"})
                                try:
                                    await self.websocket.send_bytes(part.inline_data.data)
                                except Exception:
                                    return
                    if sc.interrupted:
                        await self._safe_send_json({"type": "interrupted"})
                    if sc.turn_complete:
                        # Not a _model_last_spoke update: an EMPTY turn (no
                        # audio) is not a reply, and must still trip the
                        # watchdog if the caller is waiting on one.
                        await self._safe_send_json({"type": "turn_complete"})
                        if self.state.submitted:
                            if self._briefing_sent and self._spoke_after_briefing:
                                # Closing briefing delivered — the call is over.
                                self._call_over = True
                                await self._safe_send_json({"type": "call_complete"})
                                return
                            if self._briefing_task is None:
                                # First turn_complete after submission (the
                                # "stay on the line" acknowledgment): start
                                # waiting for the dashboard's responder data.
                                self._briefing_task = asyncio.create_task(
                                    self._brief_and_close(live_session)
                                )
                        await self._safe_send_json({"type": "status", "state": "listening"})
                        break
                else:
                    break  # receive() generator ended (session closed) with no turn_complete
        except Exception:
            # Treated by _run_live_session as "session died" -> reconnect.
            logger.exception("Gemini->client pump errored")
