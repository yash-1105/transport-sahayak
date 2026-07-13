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
from .english_briefing import (
    EnglishTTSError, _TTS_SAMPLE_RATE_HZ, generate_dispatch_script, synthesize_speech,
)
from .google_credentials import load_service_account_info

logger = logging.getLogger("dispatcher_live")

# Verified empirically against the real API -- see module docstring.
_LOCATION = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
SUPPORTED_LANGUAGES = ("en-IN", "hi-IN")
_DEFAULT_LANGUAGE = "en-IN"

# English (en-IN) only -- Hindi never calls _build_config/_run_live_session
# at all (its own pipeline is Sarvam Saaras/Bulbul + plain Gemini
# generate_content, confirmed no reference to either function anywhere in
# dispatcher_hindi.py). Explicitly pins Gemini Live's spoken voice rather
# than leaving it at the API default: the conversation (Gemini Live) and
# the closing briefing (Gemini Flash script + Google Cloud TTS, see
# english_briefing.py) are two entirely different audio-generation systems
# that used to sound like two different people. Google's own developer
# forum confirms the unset default is undocumented and can change without
# notice, so pinning it is correct regardless of which voice is chosen.
# "Charon" (documented character: "Informative") was chosen as the closest
# match among Gemini's native-audio prebuilt voices to this app's required
# tone (calm, warm, serious, NEVER upbeat/chipper -- see the system
# instruction's TONE section) -- ruled out Puck/Fenrir (explicitly
# upbeat/excitable) and Kore/Orus (firm/commanding rather than calm-warm).
# This pin is a DIFFERENT API surface from english_briefing.py's Google
# Cloud TTS voice (below) and is unaffected by that module's own voice
# availability -- confirmed live: a real call completed its conversation
# successfully with this pin in place. english_briefing.py's
# ENGLISH_TTS_VOICE_NAME was ORIGINALLY also set to the identically-named
# `en-IN-Chirp3-HD-Charon` (Chirp 3 HD and Gemini Live's native-audio
# voices are the same underlying named voice models, so same-name would
# have been the closest achievable cross-engine match) -- but that broke
# the closing briefing entirely (silent, no audio) because Chirp3-HD's
# availability was never independently verified live for this project and
# turned out not to be enabled. Reverted to a verified-available Neural2
# voice there; see that module's own comment for the current default and
# why. Do not re-introduce an unverified voice ID as either module's
# DEFAULT without confirming live availability first.
_ENGLISH_VOICE_NAME = os.environ.get("GEMINI_LIVE_VOICE_NAME", "Charon")

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
# Real reported bug: a call hit "The voice service hit a technical problem"
# after exhausting reconnects. Investigated live against this project's real
# Vertex credentials (2026-07): the Gemini Live session itself connects and
# responds reliably (5/5 rapid-fire sessions succeeded), and extensive
# fuzzing of every code path touching a live session (the transcript
# backstop, the classifier, the post-submission briefing state machine)
# turned up no exception on any input tried -- no reproducible code
# regression found. This matches the pre-existing, already-documented
# Gemini Live reliability class this reconnect loop exists for ("Google-side
# session limits, transient stream failures" -- see the reliability-loop
# comment in run()), not a new one. Raised from 2 reconnects (3 total
# attempts) to 4 (5 total) with exponential backoff (was a flat 1.0s sleep)
# so a transient multi-second hiccup has more real time to clear before the
# call gives up -- the same backoff shape the frontend already uses for its
# own reconnects (see RECONNECT_DELAYS_MS in useVoiceDispatcher.ts).
_MAX_RECONNECTS = int(os.environ.get("GEMINI_MAX_RECONNECTS", "4"))
_RECONNECT_BACKOFF_S = (1.0, 2.0, 4.0, 8.0)

# Real reported bug (predates the 2026-07 Gemini-Flash+TTS redesign below):
# the call would sometimes go silent mid-briefing with no error shown at all
# -- not a spoken cutoff, a hard connection drop. Root cause: this WebSocket
# had NO application-level keepalive of any kind, and the post-submission
# phase can legitimately have multi-second stretches with zero bytes on the
# wire in either direction -- waiting on the browser's dispatch_update (up to
# DISPATCH_BRIEFING_WAIT_S), and, now, while Gemini Flash generates the
# script and Google Cloud TTS synthesizes it (nothing streams during that
# latency either). If Railway (or any proxy between it and the browser) has
# an idle-connection timeout, a long enough silent gap gets the socket closed
# out from under the call -- which the frontend's ws.onclose handler treats,
# once submitted=True, as a normal call end (no reconnect, no error; see
# submittedRef in useVoiceDispatcher.ts) -- i.e. exactly "the agent just
# stops talking" with nothing on screen to explain why. Fixed with a
# periodic lightweight JSON frame sent for the WHOLE call (not just
# post-submission -- normal conversational pauses can be silent too), often
# enough that no idle gap this call can ever produce gets close to a
# plausible proxy timeout. The frontend already safely no-ops any
# unrecognized event type (see the `default` case in handleServerEvent),
# so this needed no frontend change to be effective.
_KEEPALIVE_INTERVAL_S = float(os.environ.get("DISPATCHER_KEEPALIVE_INTERVAL_S", "10"))

# ── Post-submission closing briefing (see english_briefing.py) ────────────────
# After submit_incident the browser dashboard runs its existing matching flow
# and sends the SAME responder ETAs it displays back as one "dispatch_update"
# frame. How long to wait for that frame before closing without ETAs (the
# matching flow normally takes ~5-10s; a duplicate-confirmation dialog can
# stall it, hence the generous but finite window).
#
# There is deliberately no stall/failsafe timeout for the briefing DELIVERY
# itself anymore (an earlier Gemini-Live-based design needed one -- see
# CLAUDE.md Rounds 1-5 -- because a live audio-generation turn could wedge or
# get cut off partway through speaking). Gemini Flash (script) and Google
# Cloud TTS (audio) are both single batch calls with their own request
# timeouts (ENGLISH_BRIEFING_FLASH_TIMEOUT_S / ENGLISH_TTS_TIMEOUT_S in
# english_briefing.py) -- each either returns a complete result or raises,
# with no "half-delivered" state in between for a stall timer to detect.
_DISPATCH_WAIT_S = float(os.environ.get("DISPATCH_BRIEFING_WAIT_S", "30"))

# Real reported bug: the agent started speaking the closing briefing for
# real, then was cut off abruptly mid-sentence. Root cause was on the
# frontend (useVoiceDispatcher.ts's ws.onclose calling stop() instantly
# instead of draining queued playback like its own call_complete handler
# already correctly does) -- that is the actual fix. This margin is
# defense in depth only: app.py's route handler closes this WebSocket in
# its own `finally` right after run() returns, so keeping the connection
# open for roughly as long as the audio's real playback duration means the
# server-side close can never plausibly race the browser, independent of
# whatever the frontend does.
_POST_BRIEFING_DRAIN_MARGIN_S = float(os.environ.get("DISPATCH_BRIEFING_DRAIN_MARGIN_S", "1.0"))

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

AFTER SUBMISSION: when submit_incident succeeds, follow its "next_step": tell the caller their report has been registered and that you are checking which emergency services are responding -- ask them to stay on the line for a moment. This is the LAST thing you say. Do not say goodbye, do not ask any further question, and do not call any more tools -- after this one sentence, your part of the call is complete; say nothing else.
"""


class DispatcherSession:
    def __init__(self, websocket: WebSocket, language_code: str):
        self.websocket = websocket
        self.state = DispatcherState(language=language_code)
        self._pending_location: dict[str, "asyncio.Future"] = {}
        self._live_session = None
        self._client_task: Optional["asyncio.Task"] = None
        self._keepalive_task: Optional["asyncio.Task"] = None
        # Real reported bug: the agent started speaking the closing briefing
        # for real, then went silent/cut off partway through -- root-caused
        # to unsynchronized concurrent writes on this single WebSocket
        # connection. _keepalive() sends a frame every _KEEPALIVE_INTERVAL_S
        # (10s) for the ENTIRE call with no coordination, while
        # _send_audio_chunks() streams potentially 100+ chunks of a full
        # briefing (ambulance/fire/towing/hospital/police/SOPs/closing can
        # easily run past 10s of speech) and _pump_gemini_to_client() also
        # writes audio/JSON during the conversation phase -- nothing
        # prevented two of these from calling send_bytes()/send_json() on
        # the SAME ASGI WebSocket at the same instant. A concurrent write
        # collision on Starlette/uvicorn's WebSocket can raise (or, worse,
        # interleave frames), and _send_audio_chunks() previously treated
        # ANY send exception as fatal for the WHOLE remaining clip (logs and
        # returns), then _deliver_briefing_or_raise still sent call_complete
        # right after regardless -- silently dropping however many chunks
        # were left, which is exactly "spoke real words, then shut down
        # abruptly." Every WebSocket write in this class now goes through
        # this single lock (_safe_send_json, _send_audio_chunks, and the
        # raw send_bytes for Gemini Live's own audio in
        # _pump_gemini_to_client) so no two writes can ever interleave.
        self._ws_send_lock: "asyncio.Lock" = asyncio.Lock()
        # Watchdog bookkeeping (see _watchdog): monotonic timestamps of the
        # last caller speech and last model activity in the CURRENT session.
        self._session_started: float = 0.0
        self._caller_last_spoke: float = 0.0
        self._model_last_spoke: float = 0.0
        self._nudge_sent_at: float = 0.0
        # Post-submission closing briefing (see english_briefing.py). Gemini
        # Live's OWN job now ends right after the post-submit acknowledgment
        # ("your report has been submitted successfully -- stay on the
        # line"): _end_conversation_and_deliver_briefing (spawned on that
        # turn's turn_complete) waits for the browser's dispatch_update (the
        # SAME responder ETAs the dashboard displays), gracefully closes the
        # Gemini Live session, then hands off to Gemini Flash (script text)
        # and Google Cloud TTS (audio) -- neither of which is a live,
        # per-turn audio-generation call, so neither carries the native-audio
        # generation-length/session-lifecycle risk Gemini Live's own delivery
        # used to (see CLAUDE.md Rounds 1-5 for that history).
        #
        # _live_phase_done and _call_over are DELIBERATELY two different
        # flags, not one: _live_phase_done means "Gemini Live's job in this
        # call is over" (set the instant we intentionally close the Live
        # session after the ack) -- it's what _run_live_session/
        # _pump_gemini_to_client check to stop trying to read/reconnect the
        # Live side. _call_over means "the ENTIRE call is over" (set only
        # once the Flash+TTS briefing has actually been delivered, or the
        # browser disconnected) -- run() uses THIS to decide whether to await
        # _briefing_task to completion (browser still there, let it finish
        # generating/speaking) or cancel it (browser gone, no point). Using
        # a single flag for both would mean either cancelling the Flash/TTS
        # work the instant Gemini Live closes (killing the very thing that's
        # supposed to run AFTER that), or never closing Gemini Live early at
        # all -- both wrong.
        self._dispatch_info: Optional[dict] = None
        self._dispatch_ready: "asyncio.Event" = asyncio.Event()
        self._briefing_task: Optional["asyncio.Task"] = None
        self._live_phase_done: bool = False
        self._call_over: bool = False

    async def _safe_send_json(self, payload: dict) -> None:
        try:
            async with self._ws_send_lock:
                await self.websocket.send_json(payload)
        except Exception:
            logger.debug("Could not send message on /ws/dispatcher (socket likely closed)", exc_info=True)

    async def _safe_send_bytes(self, data: bytes) -> bool:
        """Same lock-protected send discipline as _safe_send_json, for
        binary audio frames -- see the _ws_send_lock comment in __init__
        for why every WebSocket write in this class must go through one of
        these two methods, never self.websocket.send_bytes/send_json
        directly. Returns False (rather than raising) on failure so callers
        that need to know whether a chunk actually went out (unlike
        _safe_send_json's fire-and-forget JSON events) can react -- see
        _send_audio_chunks."""
        try:
            async with self._ws_send_lock:
                await self.websocket.send_bytes(data)
            return True
        except Exception:
            logger.debug("Could not send bytes on /ws/dispatcher (socket likely closed)", exc_info=True)
            return False

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
        # Incident-type backstop, same rule-first pattern as the flag/count
        # backstops above (real reported bug this closes: the caller described
        # a car-on-car collision, the model recorded the DESCRIPTION but never
        # called search_incident_type, then asked "what kind of incident was
        # it?" -- flags and counts were protected against exactly this
        # forgetting, incident type was not). Runs the caller's own
        # accumulated words through the SAME deterministic classification
        # path the search tool uses (vehicle-pair + same-type-collision
        # overrides + keyword classifier) and applies only a CONFIDENT match,
        # only while no type has been recorded yet -- the model can still
        # correct it later via search_incident_categories + update_form_field.
        if self.state.sub_type is None:
            cleaned = _strip_transcript_artifacts(self.state.caller_transcript)
            if cleaned:
                result = self._classify_incident_text(cleaned)
                if result.get("subType") and not result.get("lowConfidence"):
                    logger.info("Incident-type backstop applied from transcript: %s",
                                result["subType"])
                await self._apply_classification(result)

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

    def _classify_incident_text(self, description: str) -> dict:
        """The deterministic classification path shared by the
        search_incident_type tool AND the transcript backstop in
        _apply_local_signals_from_transcript: keyword/TF-IDF classifier,
        upgraded by the vehicle-pair and same-type-collision overrides."""
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
            if pair_subtype:
                if pair_subtype != result.get("subType"):
                    logger.info("Vehicle-pair override: %s -> %s (mentioned: %s)",
                                result.get("subType"), pair_subtype, sorted(mentioned))
                result["subType"] = pair_subtype
                result["category"] = classifier._CATEGORY_MAP.get(pair_subtype, "Other")
                result["confidence"] = 0.9
                result["lowConfidence"] = False
                # The caller named exactly two vehicles -- the count is known,
                # don't make the agent ask "how many vehicles?" right after
                # the caller just said "my car hit a truck". Minimum bound;
                # update_form_field can still overwrite with a caller
                # correction. Set whenever the deterministic evidence holds,
                # NOT only when the subtype needed changing -- the classifier
                # often already picks the right record on its own, and the
                # implied count is just as known in that case.
                result["impliedVehicleCount"] = 2
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
                if same_subtype:
                    if same_subtype != result.get("subType"):
                        logger.info("Same-type-collision override: %s -> %s (type: %s, mentions: %d)",
                                    result.get("subType"), same_subtype, only_type, counts[only_type])
                    result["subType"] = same_subtype
                    result["category"] = classifier._CATEGORY_MAP.get(same_subtype, "Other")
                    result["confidence"] = 0.9
                    result["lowConfidence"] = False
                    # Same reasoning as the pair override: "car collided with
                    # another car" names two vehicles.
                    result["impliedVehicleCount"] = 2
        return result

    async def _apply_classification(self, result: dict) -> None:
        """Apply a confident _classify_incident_text result to state: the
        incident type itself, plus the deterministically-implied vehicle
        count when one of the vehicle overrides established it (only if the
        count isn't already known -- never overwrites the caller's own
        number)."""
        sub_type = result.get("subType")
        if sub_type and not result.get("lowConfidence"):
            await self._apply_incident_type(sub_type, result.get("category"))
        implied = result.get("impliedVehicleCount")
        if implied and self.state.vehicles_involved is None:
            self.state.vehicles_involved = int(implied)
            await self._safe_send_json({
                "type": "form_update", "field": "vehiclesInvolved", "value": int(implied),
            })

    async def _tool_search_incident_type(self, description: str = "") -> dict:
        result = self._classify_incident_text(description)
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
        await self._apply_classification(result)
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
        logger.info("========================\n"
                    "Stage 2\n"
                    "submit_incident completed\n"
                    "========================")
        # Gemini Live's job in this call ends right after this acknowledgment
        # -- the browser's matching flow result (responder ETAs) is no longer
        # read out by Gemini Live at all. Once this turn's turn_complete
        # arrives, _pump_gemini_to_client spawns
        # _end_conversation_and_deliver_briefing, which gracefully closes
        # this Gemini Live session and hands off to Gemini Flash (script
        # text) + Google Cloud TTS (audio) -- see english_briefing.py.
        return {
            "ok": True,
            "next_step": (
                "Report submitted successfully. Tell the caller their incident has been "
                "registered and that you are now checking which emergency services are "
                "responding -- ask them to stay on the line for just a moment. This is the "
                "LAST thing you say in this call -- after this one sentence, say nothing "
                "more, do not say goodbye, and do not call any more tools."
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
        # Hindi's own sampling/consistency settings above are the only
        # config that ever differs by language for THIS reason; the voice
        # pin below is a separate, English-only change (see
        # _ENGLISH_VOICE_NAME's module-level comment for why) -- Hindi
        # never reaches this method at all, so the language check here is
        # belt-and-suspenders, not the real safety mechanism.
        hindi_consistency: dict = (
            {"temperature": 0.4}
            if self.state.language == "hi-IN" else {}
        )
        speech_config = (
            types.SpeechConfig(
                language_code=self.state.language,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=_ENGLISH_VOICE_NAME)
                ),
            )
            if self.state.language == "en-IN"
            else types.SpeechConfig(language_code=self.state.language)
        )
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=speech_config,
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

    async def _keepalive(self) -> None:
        """Sends a lightweight JSON frame every _KEEPALIVE_INTERVAL_S for the
        whole call, so no idle stretch on this WebSocket (waiting on
        dispatch_update, between briefing segments, or just a normal
        conversational pause) can plausibly trip a proxy's idle-connection
        timeout -- see _KEEPALIVE_INTERVAL_S comment for the real bug this
        fixes. The frontend safely ignores this event type already."""
        try:
            while True:
                await asyncio.sleep(_KEEPALIVE_INTERVAL_S)
                await self._safe_send_json({"type": "keepalive"})
        except asyncio.CancelledError:
            pass

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
        self._keepalive_task = asyncio.create_task(self._keepalive())
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
                    break
                if reconnects >= _MAX_RECONNECTS:
                    logger.error("Gemini Live session failed after %d reconnect(s) -- giving up "
                                 "(submitted=%s, post-submission handoff started=%s)",
                                 reconnects, self.state.submitted, self._briefing_task is not None)
                    await self._safe_send_json({
                        "type": "error",
                        "message": "The voice service hit a technical problem. Please end the call and try again.",
                    })
                    return
                delay = _RECONNECT_BACKOFF_S[min(reconnects, len(_RECONNECT_BACKOFF_S) - 1)]
                reconnects += 1
                logger.warning("Reconnecting Gemini Live session (attempt %d/%d, waiting %.0fs)",
                                reconnects, _MAX_RECONNECTS, delay)
                await self._safe_send_json({"type": "status", "state": "reconnecting"})
                kickoff = self._reconnect_kickoff()
                await asyncio.sleep(delay)

            # Gemini Live's own job is over (_run_live_session returned
            # "ended" -- see _live_phase_done). Gemini Live is now
            # deliberately closed EARLY, right after the post-submit
            # acknowledgment, well before the post-submission Flash+TTS
            # handoff (_briefing_task) has necessarily finished generating
            # and speaking the closing briefing -- so "Gemini Live's job is
            # over" must NOT be treated as "the whole call is over" here.
            # If the caller is still on the line, wait for that task to
            # actually finish; if the caller hung up meanwhile, cancel it
            # instead (no point synthesizing/speaking a briefing to no one).
            if self._briefing_task is not None and not self._briefing_task.done():
                if self._client_task.done():
                    logger.info("Caller disconnected while the closing briefing was still "
                                "in progress -- cancelling it")
                    self._briefing_task.cancel()
                    try:
                        await self._briefing_task
                    except (asyncio.CancelledError, Exception):
                        pass
                else:
                    try:
                        await self._briefing_task
                    except Exception:
                        logger.exception("Post-submission briefing task failed")
        finally:
            for task in (self._client_task, self._keepalive_task, self._briefing_task):
                if task is None:
                    continue
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _run_live_session(self, client: "genai.Client", kickoff_text: str) -> str:
        """One Gemini Live session within the browser call. Returns "ended"
        when Gemini Live's OWN job is genuinely over (client hung up, or the
        post-submit acknowledgment was delivered and
        _end_conversation_and_deliver_briefing intentionally closed this
        session to hand off to Gemini Flash + Google Cloud TTS -- see
        _live_phase_done), "reconnect" when the Gemini side died/wedged but
        there's still something Gemini Live itself needs to say (the
        conversation isn't done yet, or the post-submit acknowledgment never
        got delivered)."""
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
                if self._client_task in done or self._live_phase_done:
                    return "ended"
                if self.state.submitted:
                    # The post-submit acknowledgment ("your report has been
                    # submitted successfully -- stay on the line") hasn't
                    # been confirmed delivered yet (that confirmation is
                    # exactly what sets _live_phase_done, via the pump
                    # spawning _end_conversation_and_deliver_briefing on that
                    # turn's turn_complete) -- the session died before or
                    # during that one short line. Reconnect and re-send it
                    # (_reconnect_kickoff's holding line); note this can very
                    # rarely double-speak that one line if the death happened
                    # in the brief window just AFTER it was actually heard
                    # but before _live_phase_done was set -- an accepted
                    # tradeoff (repeating one short line beats risking the
                    # caller never hearing it at all).
                    logger.warning("Gemini Live session ended before the post-submit "
                                    "acknowledgment was confirmed delivered -- reconnecting")
                    return "reconnect"
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
        hand it everything collected so far plus the exact apology to say.

        Post-submission, Gemini Live has exactly one thing left to say ever
        again -- the short "your report has been submitted, stay on the
        line" acknowledgment (see _tool_submit_incident's next_step) -- so a
        session death in this phase always just means "that one line didn't
        get confirmed delivered yet"; the kickoff is always the same short
        holding line, never a resume of any further content (the closing
        briefing itself is generated by Gemini Flash and spoken by Google
        Cloud TTS, entirely outside Gemini Live -- see
        _end_conversation_and_deliver_briefing / english_briefing.py)."""
        if self.state.submitted:
            logger.info("Reconnected post-submission -- sending the holding line")
            return (
                "(The call reconnected after a brief technical problem, just after the "
                "caller's incident report was submitted successfully. Do NOT say the "
                "welcome line and do NOT re-ask anything about the incident. Briefly "
                "reassure the caller in one short sentence: their report is registered, "
                "you are still checking which emergency services are responding, and "
                "they should stay on the line for a moment. Then say nothing more and "
                "wait.)"
            )
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
                    # here, only spoken. Wakes _end_conversation_and_deliver_briefing.
                    self._dispatch_info = msg.get("services") or None
                    logger.info("dispatch_update received from browser (services: %s)",
                                sorted((msg.get("services") or {}).keys()))
                    self._dispatch_ready.set()
        except Exception:
            logger.debug("Client->Gemini pump ended", exc_info=True)

    async def _end_conversation_and_deliver_briefing(self) -> None:
        """Runs once, spawned at the turn_complete of submit_incident's
        post-submit acknowledgment (Stage 1/2 -- see the pump's turn_complete
        handler and _tool_submit_incident), which is the LAST thing Gemini
        Live ever says in this call. From here: wait for the browser's
        dispatch_update, gracefully close this Gemini Live session, then
        hand off entirely to Gemini Flash (script text) and Google Cloud TTS
        (audio) -- see english_briefing.py.

        This is a TOP-LEVEL safety net, not the actual pipeline (see
        _deliver_briefing_or_raise for that) -- a real reported bug: the
        agent went completely silent after submission with no audio and no
        error, root-caused to this function previously having NO enclosing
        try/except at all, so an unexpected exception ANYWHERE in the chain
        (a credentials/library exception constructing the TTS client, a bug
        in prompt-building -- both of which used to be OUTSIDE their own
        inner try blocks too, see english_briefing.py's fixes) killed this
        fire-and-forget task with nothing ever reaching the frontend: no
        call_complete, no tts_text, nothing -- exactly "goes silent forever"
        with only a "Task exception was never retrieved" line in the logs,
        easy to miss. This wrapper guarantees SOME terminal signal always
        reaches the frontend and the call always ends, no matter what
        breaks, and always logs the full traceback of whatever did.

        Runs to completion AFTER Gemini Live closes -- see run()'s handling
        of _briefing_task, which awaits (not cancels) this task as long as
        the caller is still connected, exactly so closing Gemini Live early
        doesn't kill the work that's supposed to happen next."""
        t_start = time.monotonic()
        try:
            await self._deliver_briefing_or_raise()
        except Exception:
            logger.exception("Post-submission briefing pipeline failed unexpectedly after "
                             "%.2fs -- ending the call instead of leaving it silent",
                             time.monotonic() - t_start)
            await self._safe_send_json({
                "type": "tts_text",
                "text": ("Your report has been registered successfully. Emergency services "
                         "have been notified. If you do not hear back, please call this "
                         "helpline again."),
            })
            await self._safe_send_json({"type": "call_complete"})
        finally:
            self._call_over = True
            logger.info("Backend: call_complete sent, task finished (total %.2fs)",
                        time.monotonic() - t_start)

    async def _deliver_briefing_or_raise(self) -> None:
        """The actual Flash+TTS pipeline. Deliberately allowed to raise --
        _end_conversation_and_deliver_briefing (the sole caller) is what
        guarantees the call still ends gracefully no matter what happens
        here; this function does not need its own top-level catch-all."""
        try:
            await asyncio.wait_for(self._dispatch_ready.wait(), timeout=_DISPATCH_WAIT_S)
            logger.info("========================\n"
                        "Stage 3\n"
                        "Dispatch services calculated\n"
                        "Services: %s\n"
                        "========================", sorted((self._dispatch_info or {}).keys()))
        except asyncio.TimeoutError:
            logger.warning("No dispatch_update within %.0fs -- closing without responder ETAs",
                           _DISPATCH_WAIT_S)

        # _live_phase_done (not _call_over) is what _run_live_session /
        # _pump_gemini_to_client check -- see the comment on these two flags
        # in __init__ for why closing Gemini Live must NOT be conflated with
        # the whole call being over.
        self._live_phase_done = True
        try:
            if self._live_session is not None:
                await self._live_session.close()
        except Exception:
            logger.debug("Closing the Gemini Live session failed (already dead?)", exc_info=True)
        logger.info("Gemini Live session closed -- handing off to Gemini Flash + Google TTS")

        client = _get_client()  # the SAME cached Vertex AI client/credentials Gemini Live used
        script = await generate_dispatch_script(client, self.state, self._dispatch_info)

        try:
            audio = await synthesize_speech(script)
        except EnglishTTSError:
            logger.exception("Google TTS failed -- falling back to on-screen text")
            # Reuses the EXACT SAME fallback event type Hindi's Bulbul-
            # failure handling already established -- the frontend needs no
            # new handling for this.
            await self._safe_send_json({"type": "tts_text", "text": script})
            await self._safe_send_json({"type": "call_complete"})
            logger.info("Call ended (TTS fallback path)")
            return

        logger.info("========================\n"
                    "Stage 9\n"
                    "Sending audio to frontend\n"
                    "========================")
        await self._safe_send_json({"type": "status", "state": "briefing"})
        await self._send_audio_chunks(audio)
        await self._safe_send_json({"type": "call_complete"})
        # Belt-and-suspenders alongside the frontend's own fix (see
        # useVoiceDispatcher.ts's ws.onclose): the browser drives its own
        # drain-before-teardown timing from what it actually has queued, so
        # this delay is not what makes playback safe. But app.py's route
        # handler awaits run() and then closes this WebSocket in its own
        # finally block -- keeping the connection open for roughly as long
        # as real playback takes means the server-side close itself can
        # never plausibly race the browser's playback, on top of (not
        # instead of) the frontend already handling this correctly.
        audio_duration_s = len(audio) / 2 / _TTS_SAMPLE_RATE_HZ  # 16-bit mono PCM -> 2 bytes/sample
        await asyncio.sleep(audio_duration_s + _POST_BRIEFING_DRAIN_MARGIN_S)
        logger.info("Call ended (briefing delivered, %.1fs after sending the final chunk)", audio_duration_s)

    async def _send_audio_chunks(self, audio: bytes) -> None:
        """Sends already-synthesized PCM16/24kHz audio to the browser as a
        burst of binary WS frames -- the exact same frame shape Gemini
        Live's own audio already used, so useVoiceDispatcher.ts's existing
        playChunk/call_complete-drain logic needs no changes to play this.
        playChunk queues each frame on the Web Audio timeline regardless of
        arrival pace, so sending this whole (short) clip in a fast burst,
        rather than live-paced like a real-time stream, schedules
        identically to several consecutive Gemini Live turns would have.
        Chunked rather than sent as one frame to mirror that existing
        pattern and avoid one very large WS message.

        Every send goes through _safe_send_bytes (the shared _ws_send_lock)
        -- see that lock's comment in __init__ for the real bug this fixes:
        an unsynchronized write here could previously collide with
        _keepalive's periodic frame (sent for the whole call, uncoordinated)
        and abort the ENTIRE remaining clip on the first exception, silently
        dropping however much of the briefing was left unsent."""
        chunk_size = 8192
        total_chunks = (len(audio) + chunk_size - 1) // chunk_size
        for n, i in enumerate(range(0, len(audio), chunk_size), start=1):
            if not await self._safe_send_bytes(audio[i:i + chunk_size]):
                logger.warning("Briefing audio send failed at chunk %d/%d -- "
                               "socket likely closed, abandoning the rest", n, total_chunks)
                return
        logger.info("Sent all %d briefing audio chunk(s) (%d bytes)", total_chunks, len(audio))

    async def _pump_gemini_to_client(self) -> None:
        live_session = self._live_session
        assert live_session is not None
        try:
            while not self._live_phase_done:
                async for response in live_session.receive():
                    # Gemini Live announces an imminent server-side connection
                    # termination (connection lifetime limits) with a GoAway
                    # message shortly before closing. Previously unread and
                    # invisible -- log it so Railway logs show exactly why a
                    # session ended. The death itself is handled: the receive
                    # stream ends and run()'s reconnect loop resumes the call
                    # (including mid-briefing -- see _reconnect_kickoff).
                    if getattr(response, "go_away", None) is not None:
                        logger.warning("Gemini Live sent GoAway (time_left=%s) -- "
                                       "server will close this connection soon",
                                       getattr(response.go_away, "time_left", None))
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
                                await self._safe_send_json({"type": "status", "state": "speaking"})
                                if not await self._safe_send_bytes(part.inline_data.data):
                                    return
                    if sc.interrupted:
                        await self._safe_send_json({"type": "interrupted"})
                    if sc.turn_complete:
                        # Not a _model_last_spoke update: an EMPTY turn (no
                        # audio) is not a reply, and must still trip the
                        # watchdog if the caller is waiting on one.
                        await self._safe_send_json({"type": "turn_complete"})
                        if self.state.submitted:
                            if self._briefing_task is None:
                                # This is the post-submit acknowledgment's
                                # turn_complete -- the LAST thing Gemini Live
                                # says in this call. Hand off to Gemini Flash
                                # + Google Cloud TTS; that task closes this
                                # Gemini Live session itself once it's ready
                                # to (see _end_conversation_and_deliver_
                                # briefing), so nothing more is expected from
                                # Gemini Live from here on regardless of how
                                # many more turn_completes this pump sees.
                                logger.info("========================\n"
                                            "Stage 1\n"
                                            "Gemini Live finished\n"
                                            "========================")
                                self._briefing_task = asyncio.create_task(
                                    self._end_conversation_and_deliver_briefing()
                                )
                            # Do NOT reopen the mic here (real reported bug:
                            # the agent kept speaking automatically, repeating
                            # itself, without waiting for a real reply).
                            # Gemini Live is reactive -- it stays silent until
                            # it receives caller audio -- but sending
                            # "listening" reopens the frontend mic gate
                            # (useVoiceDispatcher.ts) for the ENTIRE
                            # remaining post-submission phase, so any caller
                            # utterance or background noise during that
                            # limbo window (the caller has nothing new to
                            # say -- the report is already submitted) got
                            # treated as a fresh turn, and with no new
                            # information to report yet, the model just
                            # repeated its "please hold on" line, over and
                            # over, every time it heard anything at all.
                            # "thinking" keeps the client-side mic gate closed
                            # (micOpen only opens on "listening" for en-IN)
                            # without needing a new status the frontend
                            # doesn't already recognize -- the caller has
                            # nothing left to say at this point in the call
                            # regardless, and Gemini Live itself never speaks
                            # again either way.
                            await self._safe_send_json({"type": "status", "state": "thinking"})
                            break
                        await self._safe_send_json({"type": "status", "state": "listening"})
                        break
                else:
                    # receive() generator ended (session closed server-side)
                    # with no turn_complete.
                    logger.warning("Gemini Live receive() stream ended server-side "
                                   "(submitted=%s, live_phase_done=%s)",
                                   self.state.submitted, self._live_phase_done)
                    break
        except Exception:
            # Treated by _run_live_session as "session died" -> reconnect.
            logger.exception("Gemini->client pump errored (submitted=%s, live_phase_done=%s)",
                             self.state.submitted, self._live_phase_done)
