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
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket
from google import genai
from google.genai import types
from google.oauth2 import service_account

from . import classifier, local_extract
from .google_credentials import load_service_account_info

logger = logging.getLogger("dispatcher_live")

# Verified empirically against the real API -- see module docstring.
_LOCATION = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
SUPPORTED_LANGUAGES = ("en-IN", "hi-IN")
_DEFAULT_LANGUAGE = "en-IN"

_LOCATION_TIMEOUT_S = 8.0

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
    # Your voice is female in Hindi -- Hindi verbs conjugate by the speaker's
    # gender, so every first-person verb must use feminine forms, in every
    # sentence generated live, not just the fixed example phrases below.
    gender_note = (
        ' Your voice is female, so ALWAYS use feminine grammatical verb forms when referring to '
        'yourself -- "समझती हूँ" not "समझता हूँ", "कर रही हूँ" not "कर रहा हूँ", "रही हूँ"/"गई" not '
        '"रहा हूँ"/"गया", and so on for every first-person verb you say, in every sentence you '
        "generate yourself, not just in the fixed examples below."
        if language_code == "hi-IN" else ""
    )
    return f"""You are an emergency dispatch call-taker for a road-accident first-response system in Assam, India. You are having a real-time voice conversation with someone reporting a road accident or emergency.

LANGUAGE: Conduct this entire conversation in {lang_name} only. If the caller speaks a different language, gently continue in {lang_name} rather than switching -- never randomly switch languages yourself.{gender_note}

TONE: Calm, warm, and genuinely concerned -- like a serious, caring human dispatcher handling an emergency, not a neutral form-filling bot, and absolutely NOT an upbeat customer-service agent. This is a safety call, not a friendly chat -- your delivery must sound measured, sincere, and a little subdued, never cheerful, chipper, energetic, or excited, even when you are simply acknowledging routine details. If in doubt, err toward quieter and more serious rather than lively. This warmth must come through on EVERY call, not only when the caller explicitly mentions an injury or sounds distressed -- even a caller who reports a routine-sounding incident calmly is still someone dealing with a road accident, and should hear a human who cares, not a checklist. Never let two or more responses in a row go by with a purely neutral, transactional acknowledgment ("Okay." / "Noted.") -- always warm it up at least a little, for example (English, said quietly and sincerely, not brightly): "Thank you for telling me, I'm noting that down" / "I understand, let's get this sorted quickly" / "Alright, I have that noted" -- and when the caller mentions an injury, bleeding, or sounds frightened, go further with real concern: "I'm sorry to hear that, help is on the way" / "That sounds frightening, please try to stay calm" / "I understand, we'll get you help as quickly as we can". In Hindi, the same range applies, spoken with the same quiet seriousness and always in feminine grammatical form: "ठीक है, धन्यवाद, मैं इसे नोट कर रही हूँ" / "समझ गई, चलिए इसे जल्दी सुलझाते हैं" for routine acknowledgments, and for real distress: "मुझे यह सुनकर दुख हुआ, मदद आ रही है" / "कृपया घबराइए मत, हम आपकी मदद कर रहे हैं" / "मैं समझती हूँ, हम जल्द से जल्द सहायता भेज रहे हैं". Vary the phrasing -- never repeat the exact same acknowledgment twice in one call. Every tool response you receive includes a "tone_reminder" -- follow it every single time, not just when you happen to remember to. This warmth must never come at the cost of the rest of this prompt: still ask one question at a time, still keep every response to 1-2 short sentences, still speak a little slower than normal conversational pace with clear pronunciation, and still never repeat a sentence you have already said unless the caller explicitly asks you to. Gathering the information needed to send help quickly is still the priority -- empathy should feel human and serious, not slow the call down and not sound upbeat.

OPENING (the very first thing you do, before the caller says anything, and only ever once for the whole call): as soon as the call connects, say this exact sentence, word for word, with nothing before it and nothing added: "{opening_line}" You will be told the caller's detected location (or that none was detected) in the same message that starts the call -- do not call get_current_location for this, it has already been resolved for you. If a location was given, briefly mention it ("I have your location as X, is that right?") and ask what happened, all in this same first turn. If no location was detected, tell the caller to use the map-pin button to mark their location instead -- do not try to guess a location from a spoken description. Once you have done this opening, it is complete -- never say the welcome sentence again for the rest of the call, no matter what happens, even if it feels like the conversation is starting over. Move straight to gathering information about the incident.

INCIDENT TYPE: Never guess or invent an incident type yourself. Always call search_incident_type with a description of what the caller told you -- it automatically records a confident match for you, so once you've called it you do not need a separate step to confirm the type unless the caller says it's wrong. Refer to the incident only using the exact subType name it returns. If it doesn't sound right to the caller, call search_incident_categories to browse alternatives, then call update_form_field with field=incidentType and the exact subType you both agreed on.

FORM FILLING: Call update_form_field immediately every time the caller gives you a new piece of information -- INCLUDING conditions mentioned in passing, not just direct answers to your questions. If the caller mentions fire, hazmat, anyone trapped, consciousness, breathing, or bleeding ANYWHERE in what they say (even inside a general description), call update_form_field with field=flag for that condition right away -- do not wait for a dedicated question about it.

FOLLOW-UP QUESTIONS -- THIS IS A HARD RULE, NOT A SUGGESTION: every tool response includes "next_question", the ONE specific thing to ask about next, or null if nothing is left. This is precomputed for you deterministically -- it is not your judgment call. After any brief acknowledgment, your very next question must be about EXACTLY the topic named in "next_question", worded naturally for the conversation but not substituted for a different topic. Never ask about anything else, never invent your own question (for example, do not ask about consciousness or breathing unless "next_question" specifically says so), never skip ahead to a topic that isn't in "next_question" yet, and never ask about something already answered. Keep asking about the same "next_question" topic (rephrasing if needed) until it is answered and the next tool response gives you a new one, or null. This must produce the exact same sequence of questions regardless of language -- if you find yourself wanting to ask something "next_question" doesn't mention, don't.

FINAL CONFIRMATION: Before calling submit_incident, verbally summarize everything collected (incident type, key facts, location) and ask "Would you like me to submit this report?" Only call submit_incident after the caller clearly confirms. If it comes back still missing something, ask for it and try again.
"""


class DispatcherSession:
    def __init__(self, websocket: WebSocket, language_code: str):
        self.websocket = websocket
        self.state = DispatcherState(language=language_code)
        self._pending_location: dict[str, "asyncio.Future"] = {}
        self._live_session = None

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

    _TONE_REMINDER = (
        "Acknowledge what the caller just said warmly, seriously, and briefly (not upbeat) "
        "before asking your next question."
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
            "tone_reminder": self._TONE_REMINDER,
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
        # Same backstop reasoning as the flags below, for the description
        # field: confirmed live that the model can get stuck repeatedly
        # asking for "a short description of what happened" even after the
        # caller has already said plenty, because it never called
        # update_form_field(field="description", ...) itself. Keep this
        # auto-derived until the model explicitly sets its own (cleaner,
        # summarized) description -- once it does, stop overwriting it here.
        if not self.state.description_set_explicitly and len(self.state.caller_transcript.strip()) > 12:
            self.state.description = self.state.caller_transcript.strip()[:500]
            await self._safe_send_json({
                "type": "form_update", "field": "description", "value": self.state.description,
            })
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
        # Backstop, mirrors _apply_local_signals_from_transcript: if the model
        # never explicitly called update_form_field(field="description", ...)
        # despite the caller having said plenty (confirmed live this happens
        # occasionally), fall back to the accumulated raw transcript rather
        # than blocking submission on a field that has a reasonable value
        # sitting right there.
        if not self.state.description and self.state.caller_transcript.strip():
            self.state.description = self.state.caller_transcript.strip()[:500]
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
        return {"ok": True}

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
            return {"ok": False, "error": f"Unknown tool {name!r}"}
        try:
            return await handler(**args)
        except Exception:
            logger.exception("Tool %s failed", name)
            return {"ok": False, "error": "Internal error executing this tool -- please try again."}

    # ── Session lifecycle ───────────────────────────────────────────────────

    def _build_config(self) -> "types.LiveConnectConfig":
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(language_code=self.state.language),
            tools=[{"function_declarations": _TOOL_DECLARATIONS}],
            system_instruction=types.Content(parts=[types.Part(text=_system_instruction(self.state.language))]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
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
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                ),
                activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            ),
        )

    async def run(self) -> None:
        client = _get_client()
        async with client.aio.live.connect(model=_MODEL, config=self._build_config()) as live_session:
            self._live_session = live_session
            await self._safe_send_json({"type": "ready"})
            # Start the client->Gemini pump FIRST -- it's the only thing that
            # listens for the browser's "location_result" reply, so the
            # upfront location fetch below would otherwise just hang until
            # its own timeout with nothing ever receiving the response.
            client_task = asyncio.create_task(self._pump_client_to_gemini())
            # Resolve GPS location BEFORE the model says anything, and hand
            # it directly to the kickoff turn as plain text, rather than
            # having the model call get_current_location mid-utterance for
            # its opening line. Verified via live testing this was the real
            # cause of Hindi-specific repeated openings: the model would
            # sometimes end its first turn right after the scripted line
            # (skipping the tool call), and then, on hearing the caller's
            # next utterance, would restart the whole opening from scratch
            # since it considered the greeting "incomplete." English tolerated
            # the mid-turn tool call more reliably, but nothing here should
            # depend on that per-language reliability, so this removes the
            # tool call from the opening turn entirely for both languages.
            location_result = await self._tool_get_current_location()
            if location_result.get("status") in ("ok", "already_have_location"):
                location_note = f"Detected location: {location_result.get('label', '')}."
            else:
                location_note = "No location was detected."
            # Gemini Live is reactive by default -- it won't speak until it
            # receives input. The caller shouldn't have to speak first, so
            # kick off the call with a synthetic system-directed turn (not
            # real caller speech) instructing the model to begin its scripted
            # opening now, with the location already resolved. Verified via
            # live testing that this reliably triggers the model's first
            # spoken turn immediately.
            await live_session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(
                    text=f"(The call has just connected. {location_note} Begin now.)"
                )]),
                turn_complete=True,
            )
            # Run both pumps as cancellable tasks, not a plain gather -- if
            # the client disconnects/ends the call, _pump_client_to_gemini
            # returns, but _pump_gemini_to_client is blocked awaiting the
            # NEXT Gemini message and would otherwise hang indefinitely,
            # leaving the Live session (and its quota/cost) running forever
            # server-side with nobody listening. Whichever pump finishes
            # first, cancel the other so the `async with` block actually
            # exits and the session closes. (client_task was already started
            # above, before the upfront location fetch.)
            gemini_task = asyncio.create_task(self._pump_gemini_to_client())
            _, pending = await asyncio.wait({client_task, gemini_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _pump_client_to_gemini(self) -> None:
        assert self._live_session is not None
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await self._live_session.send_realtime_input(
                        audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                    )
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
        except Exception:
            logger.debug("Client->Gemini pump ended", exc_info=True)

    async def _pump_gemini_to_client(self) -> None:
        assert self._live_session is not None
        while not self.state.submitted:
            async for response in self._live_session.receive():
                if response.tool_call:
                    function_responses = []
                    for fc in response.tool_call.function_calls:
                        result = await self._dispatch_tool(fc.name, fc.args or {})
                        function_responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response=result))
                    await self._live_session.send_tool_response(function_responses=function_responses)

                sc = response.server_content
                if sc is None:
                    continue
                if sc.input_transcription and sc.input_transcription.text:
                    await self._safe_send_json({"type": "transcript", "role": "user", "text": sc.input_transcription.text})
                    self.state.caller_transcript += " " + sc.input_transcription.text
                    await self._apply_local_signals_from_transcript()
                if sc.output_transcription and sc.output_transcription.text:
                    await self._safe_send_json({"type": "transcript", "role": "model", "text": sc.output_transcription.text})
                if sc.model_turn:
                    for part in sc.model_turn.parts:
                        if part.inline_data and part.inline_data.data:
                            await self._safe_send_json({"type": "status", "state": "speaking"})
                            try:
                                await self.websocket.send_bytes(part.inline_data.data)
                            except Exception:
                                return
                if sc.interrupted:
                    await self._safe_send_json({"type": "interrupted"})
                if sc.turn_complete:
                    await self._safe_send_json({"type": "turn_complete"})
                    await self._safe_send_json({"type": "status", "state": "listening"})
                    break
            else:
                break  # receive() generator ended (session closed) with no turn_complete
