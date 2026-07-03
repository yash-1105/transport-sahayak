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

from . import classifier
from .google_credentials import load_service_account_info

logger = logging.getLogger("dispatcher_live")

# Verified empirically against the real API -- see module docstring.
_LOCATION = os.environ.get("VERTEX_AI_LOCATION", "us-central1")
_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio")
SUPPORTED_LANGUAGES = ("en-IN", "hi-IN")
_DEFAULT_LANGUAGE = "en-IN"

_LOCATION_TIMEOUT_S = 8.0

_FLAG_NAMES = ["Conscious", "Breathing", "Trapped", "Heavy bleeding", "Fire", "Hazardous material"]


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
                    "description": "Required when field=flag: true if present, false if explicitly ruled out.",
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


def _system_instruction(language_code: str) -> str:
    lang_name = "Hindi" if language_code == "hi-IN" else "English"
    return f"""You are an emergency dispatch call-taker for a road-accident first-response system in Assam, India. You are having a real-time voice conversation with someone reporting a road accident or emergency.

LANGUAGE: Conduct this entire conversation in {lang_name} only. If the caller speaks a different language, gently continue in {lang_name} rather than switching -- never randomly switch languages yourself.

TONE: Calm, brief, professional, like a real emergency dispatcher. Ask one question at a time. Keep every response to 1-2 short sentences -- never make the caller wait through a long speech.

INCIDENT TYPE: Never guess or invent an incident type yourself. Always call search_incident_type with a description of what the caller told you, and refer to the incident only using the exact subType name it returns. If it doesn't sound right, call search_incident_categories to browse alternatives with the caller.

FORM FILLING: Call update_form_field immediately every time the caller gives you a new piece of information -- INCLUDING conditions mentioned in passing, not just direct answers to your questions. If the caller mentions fire, hazmat, anyone trapped, consciousness, breathing, or bleeding ANYWHERE in what they say (even inside a general description), call update_form_field with field=flag for that condition right away -- do not wait for a dedicated question about it. Every tool response includes "still_missing" -- a list of what's not yet known. Use it to decide your next question and to know what NOT to ask again, so you never repeat a question the caller already answered.

LOCATION: Call get_current_location once, early in the call. If it returns a location, just briefly confirm it ("I have your location as X, is that right?") instead of asking the caller to describe it. If it's unavailable, tell the caller to use the map-pin button to mark their location -- do not try to guess a location from a spoken description.

FOLLOW-UP QUESTIONS: Once the incident type is confirmed, ask only about what "still_missing" shows, one thing at a time, phrased naturally for that kind of incident.

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

    # ── Tools ──────────────────────────────────────────────────────────────

    async def _tool_search_incident_type(self, description: str = "") -> dict:
        result = classifier.guess(description or "")
        result["still_missing"] = self._compute_still_missing()
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
            rec = classifier._find_exact(sub_type)
            if not rec:
                return {
                    "ok": False,
                    "error": f"{sub_type!r} is not a real incident type -- call search_incident_type "
                             "or search_incident_categories first and use the exact value returned.",
                }
            self.state.sub_type = rec["subType"]
            self.state.category = classifier._CATEGORY_MAP.get(rec["subType"], category or "Other")
            await self._safe_send_json({
                "type": "form_update", "field": "incidentType",
                "value": {"subType": self.state.sub_type, "category": self.state.category},
            })
        elif field == "description":
            if text_value is None:
                return {"ok": False, "error": "text_value is required for field=description"}
            self.state.description = text_value
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
        return {"ok": True, "still_missing": self._compute_still_missing()}

    async def _tool_get_current_location(self) -> dict:
        if self.state.location:
            return {"status": "already_have_location", **self.state.location, "still_missing": self._compute_still_missing()}
        request_id = str(uuid.uuid4())
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        self._pending_location[request_id] = fut
        await self._safe_send_json({"type": "request_location", "requestId": request_id})
        try:
            return await asyncio.wait_for(fut, timeout=_LOCATION_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending_location.pop(request_id, None)
            return {"status": "unavailable", "error": "Location request timed out.", "still_missing": self._compute_still_missing()}

    async def _tool_submit_incident(self) -> dict:
        blocking = [
            m for m in self._compute_still_missing()
            if "incident type" in m or "location" in m or "description" in m
        ]
        if blocking:
            return {"ok": False, "error": f"Cannot submit yet -- still missing: {'; '.join(blocking)}"}
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
        )

    async def run(self) -> None:
        client = _get_client()
        async with client.aio.live.connect(model=_MODEL, config=self._build_config()) as live_session:
            self._live_session = live_session
            await self._safe_send_json({"type": "ready"})
            await asyncio.gather(self._pump_client_to_gemini(), self._pump_gemini_to_client())

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
                        fut.set_result({"status": "ok", **self.state.location, "still_missing": self._compute_still_missing()})
                elif mtype == "location_error":
                    fut = self._pending_location.pop(msg.get("requestId"), None)
                    if fut and not fut.done():
                        fut.set_result({"status": "unavailable", "error": msg.get("message", "denied"), "still_missing": self._compute_still_missing()})
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
