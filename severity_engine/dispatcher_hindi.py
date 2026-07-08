"""
dispatcher_hindi.py — the HINDI conversational voice dispatcher.

Pipeline (per this feature's spec — replaces Gemini Live for hi-IN only):

    caller audio (PCM16/16kHz from the browser)
        → Sarvam Saaras v3 streaming STT           (sarvam_speech.SaarasStream)
        → Gemini TEXT reasoning w/ function calling (existing Vertex AI client)
        → the SAME 5 dispatcher tools + incident dataset (dispatcher_live.py)
        → Sarvam Bulbul v3 streaming TTS            (sarvam_speech.BulbulStream)
        → PCM16/24kHz back to the browser

English is completely untouched: /ws/dispatcher still runs
dispatcher_live.DispatcherSession (Gemini Live) for en-IN — app.py only routes
hi-IN here. This class deliberately SUBCLASSES DispatcherSession so every tool
handler (_tool_search_incident_type with its vehicle-pair override,
_tool_update_form_field's taxonomy validation, _tool_submit_incident's
hard-gated required fields), the DispatcherState, the deterministic
next_question computation, and the local-signal backstop are reused
byte-for-byte rather than duplicated — only the audio/reasoning transport is
replaced. The browser-facing WebSocket protocol is also identical (ready /
status / form_update / request_location / submitted / turn_complete /
transcript / error JSON frames + binary PCM out), so useVoiceDispatcher.ts
works unchanged apart from Hindi-only playback/barge-in tweaks.

Gemini here is the plain generate_content API (google-genai, same Vertex
service-account client dispatcher_live already initialises) — NOT Gemini Live,
per spec. Reasoning quality note: unlike the Live pipeline, the model reads
Saaras's full final transcript of each utterance, so "मेरी कार ट्रक से टकरा गई"
reaches search_incident_type verbatim — and the deterministic vehicle-pair
override inherited from dispatcher_live still guarantees a two-vehicle mention
can never be recorded as Car vs. Car.

LATENCY: conversation history is appended incrementally (never rebuilt), the
system prompt is kept compact (fewer input tokens = faster time-to-first-
token), max_output_tokens/tool-round caps are tight, and every turn's timing
is broken down and logged (see _mark/_LOG_LATENCY) so bottlenecks are visible
rather than guessed at. See dispatcher_hindi_bench notes in the project
history for a measured before/after of the Gemini-reasoning portion.
"""
import asyncio
import json
import logging
import os
import time
from typing import Optional

from fastapi import WebSocket
from google.genai import types

from .dispatcher_live import (
    _OPENING_LINE,
    _RECONNECT_APOLOGY,
    _TOOL_DECLARATIONS,
    DispatcherSession,
    _get_client,
)
from .sarvam_speech import (
    BulbulStream,
    SaarasStream,
    SarvamTTSError,
    TTS_SAMPLE_RATE,
    require_api_key,
)

logger = logging.getLogger("dispatcher_hindi")

# Text-reasoning model — deliberately the plain generate_content family, not a
# Live model. Runs on the existing Vertex AI credentials via dispatcher_live's
# cached client, so no new authentication of any kind. gemini-2.5-flash is the
# newest flash generation actually available on this project/region — verified
# live: gemini-2.0-flash now 404s on Vertex us-central1 here.
_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
# Tighter than a typical chat timeout on purpose -- this is a live phone call,
# not a background job; a slow attempt should fail fast into the retry/apology
# path rather than leave the caller in silence for 20s.
_GEMINI_TIMEOUT_S = float(os.environ.get("GEMINI_TEXT_TIMEOUT_S", "12"))
# Each round is a full network round-trip to Vertex. The prompt now demands
# ALL of a turn's tool calls happen together in one round (see FORM FILLING),
# so 4 is generous headroom (typically 1 tool round + 1 final-text round).
_MAX_TOOL_ROUNDS = 4
# Kept tight -- a real operator's reply is 1-3 short sentences; this is a
# ceiling against runaway generation, not a target length, and every extra
# token here is extra time-to-last-token before Bulbul can start.
_MAX_OUTPUT_TOKENS = 300

# After Saaras finalizes an utterance, wait this long for the caller to keep
# going (a natural mid-answer pause produces two segments) before treating the
# turn as complete. This is the single largest FIXED latency tax on every
# turn, so it's kept as short as is still safe: Saaras's own VAD already
# requires a silence window before emitting END_SPEECH, so this only needs to
# cover segment-to-segment gaps, not redo silence detection from scratch.
_UTTERANCE_GRACE_S = 0.45
# END_SPEECH arrives before its segment's transcript; never close the turn
# while a transcript is still owed, but don't wait forever if none comes.
_PENDING_TRANSCRIPT_MAX_S = 2.5
_SILENCE_REPROMPT_S = 45.0
_MAX_REPROMPTS = 2

# Barge-in: how long after the agent starts speaking before the STT stream's
# events are trusted as a genuine interruption rather than the leading edge of
# the agent's own voice bleeding into the mic (the browser's echo cancellation
# needs a brief moment to adapt once playback starts).
_BARGE_IN_ARM_DELAY_S = 0.3

_LOG_LATENCY = os.environ.get("HINDI_LATENCY_LOG", "true").strip().lower() not in ("0", "false", "no")

_REPROMPT_LINES = [
    "क्या आप वहाँ हैं? कृपया बताइए, क्या हुआ है?",
    "अगर आप मुझे सुन पा रही हैं या सुन पा रहे हैं, तो कृपया बताइए वहाँ क्या हुआ।",
]
_STT_FAILURE_LINE = (
    "मुझे क्षमा कीजिए, आवाज़ पहचानने में अभी तकनीकी समस्या आ रही है। "
    "कृपया एक पल रुकें और फिर से बोलें।"
)


def _hindi_system_prompt() -> str:
    opening_line = _OPENING_LINE["hi-IN"]
    # Kept as compact as the behavior requirements allow -- every extra
    # sentence here is extra input tokens on EVERY single Gemini call of the
    # entire conversation (the system prompt is re-sent every turn since this
    # is plain generate_content, not a stateful session).
    return f"""आप भारत की 1033 राष्ट्रीय राजमार्ग आपातकालीन हेल्पलाइन की एक प्रशिक्षित महिला ऑपरेटर हैं — असम में सड़क दुर्घटना प्रथम-प्रतिक्रिया प्रणाली के लिए, एक वास्तविक फ़ोन कॉल पर। आप कभी AI, मॉडल या असिस्टेंट नहीं हैं — पूछे जाने पर आप बस 1033 हेल्पलाइन की ऑपरेटर हैं।

बोली जाने वाली भाषा (टेक्स्ट-टू-स्पीच से बोला जाएगा): सरल, रोज़मर्रा की बोलचाल की हिंदी — औपचारिक, साहित्यिक या शुद्ध हिंदी कभी नहीं, अंग्रेज़ी का शब्दशः अनुवाद कभी नहीं ("गाड़ी" न कि "वाहन", "मदद" न कि "सहायता", "टक्कर हुई" न कि "दुर्घटनाग्रस्त हुई")। हमेशा स्त्रीलिंग क्रिया रूप अपने लिए ("समझ रही हूँ", "दर्ज कर रही हूँ", "पुष्टि करना चाहती हूँ")। कोई मार्कडाउन, सूची, इमोजी या अंग्रेज़ी वाक्य नहीं (लोकेशन, रिपोर्ट, ट्रक, एम्बुलेंस जैसे आम शब्द ठीक हैं)।

हर जवाब की बनावट — हर बार अलग ढंग से शुरू करें, कभी हर जवाब "जी" या "ठीक है" से शुरू न करें: 1 से 3 छोटे वाक्य — पहले caller ने अभी जो बताया उसकी सच्ची, गर्मजोशी भरी स्वीकृति (कभी एक ही वाक्य दोबारा न दोहराएं), फिर ठीक ONE सवाल — कभी एक साथ दो सवाल नहीं। अच्छे उदाहरण: "समझ गई, एक व्यक्ति घायल है। क्या वह होश में है?" · "मुझे यह सुनकर अफ़सोस हुआ, घबराइए मत। क्या कहीं आग तो नहीं लगी?" ख़राब: सिर्फ सवाल बिना स्वीकृति के; हर बार "जी"/"ठीक है" से शुरुआत; formal भाषा जैसे "कृपया घटना का विवरण प्रदान करें।"

आम सवालों की सहज हिंदी (next_question के अंग्रेज़ी संकेत के लिए इस्तेमाल करें):
चोट/casualties → "क्या किसी को चोट लगी है?" (हाँ पर "कितने लोग घायल हैं?")
trapped → "क्या कोई गाड़ी के अंदर फँसा हुआ है?"
fire/fuel leak → "क्या कहीं आग लगी है या ईंधन का रिसाव हो रहा है?"
conscious → "क्या वह होश में है?"   breathing → "क्या साँस ठीक से चल रही है?"
heavy bleeding → "क्या ज़्यादा खून बह रहा है?"   hazmat → "क्या कोई खतरनाक पदार्थ भी शामिल है?"
vehicles involved → "कुल कितनी गाड़ियाँ इसमें शामिल थीं?"

काम का क्रम — हर टर्न में, बिना अपवाद:
1. पहले caller ने अभी जो बताया उसके लिए ज़रूरी सभी टूल कॉल एक साथ करें — पहली बार घटना बताने पर search_incident_type (उनके असली शब्दों के साथ, कभी अपना अनुवाद या सारांश नहीं), और हर नई जानकारी (चोट, फँसा होना, आग, गाड़ियों की संख्या, विवरण) के लिए update_form_field। "नहीं" भी जानकारी है — रिकॉर्ड करें (flag_active=false), सिर्फ आगे न बढ़ें।
2. टूल का नतीजा आने के बाद ही, ऊपर बताई बनावट में बोलें।

OPENING (सिर्फ कॉल के पहले जवाब में, दोबारा कभी नहीं): यह वाक्य शब्दशः बोलें, बिना किसी और चीज़ के पहले: "{opening_line}" उसी जवाब में — अगर लोकेशन मिल चुकी है तो संक्षेप में पूछें कि क्या यह सही है, वरना caller से मैप-पिन बटन से लोकेशन भेजने को कहें — फिर पूछें क्या हुआ।

caller बोलचाल की भाषा में बोलते हैं ("टायर फट गया", "गाड़ी पलट गई", "ठोक दिया", "आग पकड़ ली") — पूरे वाक्य और अब तक की पूरी बातचीत से मतलब समझें, कभी सिर्फ एक शब्द पकड़कर नहीं, कभी formal शब्दों में दोबारा बोलने को न कहें।

घटना का प्रकार — अहम नियम: कभी खुद अंदाज़ा न लगाएं, हमेशा search_incident_type बुलाएं। ध्यान से सुनें कि caller ने कौन-कौन से वाहन बताए — "मेरी कार ट्रक से टकरा गई" में कार भी है और ट्रक भी, कभी सिर्फ Car vs. Car दर्ज न हो। मिलान संदिग्ध लगे तो एक छोटा स्पष्टीकरण सवाल पूछें, फिर दोबारा search_incident_type बुलाएं या search_incident_categories से सही करें।

description फ़ील्ड हमेशा अंग्रेज़ी में लिखें (अनुवाद+सारांश करके) — यही एकमात्र चीज़ है जो हमेशा अंग्रेज़ी में लिखनी है। जल्दी एक छोटा सारांश सेट करें, नई जानकारी मिलने पर अपडेट करें।

अगला सवाल — पक्का नियम: हर टूल के नतीजे में "next_question" आता है — बिल्कुल यही अगला विषय पूछें, कभी कोई और सवाल नहीं, कभी वह जो caller पहले ही बता चुका है दोबारा नहीं (जैसे उसने "दो लोग घायल हैं" कहा हो तो फिर कभी "क्या किसी को चोट लगी है?" न पूछें)। जब यह null हो, सब कुछ एक-दो वाक्यों में दोहराएं और पूछें "क्या यह जानकारी सही है?" — साफ़ हाँ मिलने पर ही submit_incident बुलाएं; कुछ छूट जाए तो पूछकर दोबारा कोशिश करें। सफल submit के बाद एक गर्मजोशी भरे वाक्य से कॉल बंद करें, फिर कुछ न बोलें।

caller का transcript कभी-कभी थोड़ा अधूरा हो सकता है (speech recognition) — मतलब समझें; tools, transcript या तकनीक का ज़िक्र कभी न करें।"""


class HindiDispatcherSession(DispatcherSession):
    """Sarvam-based Hindi dispatcher speaking the same browser protocol as
    DispatcherSession. Only run() and the audio/reasoning transport differ —
    all tools, state, and validation are inherited."""

    def __init__(self, websocket: WebSocket):
        super().__init__(websocket, "hi-IN")
        self._history: list = []  # types.Content conversation history for text Gemini
        self._audio_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._ended = asyncio.Event()
        self._stt = SaarasStream("hi-IN")
        self._tts = BulbulStream("hi-IN")
        # Set by _watch_for_bargein when a caller interruption is detected via
        # a bare VAD speech_start (no transcript yet) -- tells the next
        # _collect_user_utterance() call that speech is already in progress,
        # so it doesn't wait for a speech_start event that already happened.
        self._resume_speech_active = False
        # Per-turn latency breakdown (see _mark); reset at the top of each
        # cycle in run()'s main loop.
        self._turn_stats: dict = {}
        self._gen_config = types.GenerateContentConfig(
            system_instruction=_hindi_system_prompt(),
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS)],
            # Same consistency rationale as the Live Hindi path: lower
            # temperature so every call behaves like the same operator.
            temperature=0.4,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            # No thinking for a real-time call — the sub-2s latency target
            # matters more than marginal reasoning depth here.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    def _mark(self, key: str, seconds: float) -> None:
        self._turn_stats[key] = self._turn_stats.get(key, 0.0) + seconds

    def _log_turn_stats(self) -> None:
        if _LOG_LATENCY and self._turn_stats:
            logger.info("[latency] %s", "  ".join(
                f"{k}={v * 1000:.0f}ms" for k, v in self._turn_stats.items()
            ))

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def run(self) -> None:
        require_api_key()   # loud SarvamCredentialsError before anything starts
        gemini_client = _get_client()  # existing Vertex AI auth, cached module-wide
        await self._safe_send_json({"type": "ready"})

        pump_task = asyncio.create_task(self._pump_client())
        stt_feed_task = asyncio.create_task(self._feed_stt())
        try:
            # Resolve GPS upfront, exactly like the Live path — the pump is
            # already running, so the browser's location_result can arrive.
            location_result = await self._tool_get_current_location()
            if location_result.get("status") in ("ok", "already_have_location"):
                location_note = f"Detected location: {location_result.get('label', '')}."
            else:
                location_note = "No location was detected."
            self._turn_stats = {}
            await self._agent_turn(
                gemini_client, f"(The call has just connected. {location_note} Begin now.)"
            )

            while not self._ended.is_set() and not self.state.submitted:
                self._turn_stats = {}
                user_text = await self._collect_user_utterance(
                    already_speaking=self._resume_speech_active
                )
                self._resume_speech_active = False
                if user_text is None:
                    break
                self.state.caller_transcript += " " + user_text
                await self._apply_local_signals_from_transcript()
                await self._safe_send_json({"type": "transcript", "role": "user", "text": user_text})
                await self._agent_turn(gemini_client, user_text)
        finally:
            for task in (pump_task, stt_feed_task):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await self._stt.close()
            await self._tts.close()

    async def _pump_client(self) -> None:
        """Browser → backend: binary mic audio to the STT queue, JSON control
        messages (location results / end-of-call) handled like the Live pump.
        The browser now sends audio continuously (not just while "listening")
        so Saaras can detect a caller barge-in while the agent is speaking —
        see useVoiceDispatcher.ts's hi-IN-only mic-gate change."""
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is not None:
                    await self._audio_queue.put(data)
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
                        self.state.location = {
                            "lat": msg.get("lat"), "lng": msg.get("lng"), "label": msg.get("label", ""),
                        }
                        fut.set_result({"status": "ok", **self.state.location, **self._state_block()})
                elif mtype == "location_error":
                    fut = self._pending_location.pop(msg.get("requestId"), None)
                    if fut and not fut.done():
                        fut.set_result({
                            "status": "unavailable",
                            "error": msg.get("message", "denied"),
                            **self._state_block(),
                        })
        except Exception:
            logger.debug("Client pump ended", exc_info=True)
        finally:
            self._ended.set()

    async def _feed_stt(self) -> None:
        while True:
            chunk = await self._audio_queue.get()
            await self._stt.send_audio(chunk)

    # ── Listening (Saaras) ───────────────────────────────────────────────────

    async def _collect_user_utterance(self, already_speaking: bool = False) -> Optional[str]:
        """One caller turn: finalized Saaras segments joined together, closed
        after a short grace window of silence. Returns None when the call is
        over. Also owns the long-silence re-prompt and the STT-failure notice
        (spoken in Hindi, per spec).

        `already_speaking` is set when this call follows a caller barge-in
        (see _watch_for_bargein): the triggering speech_start event was
        already consumed there, so the state machine must not wait for one
        that has already happened."""
        parts: list[str] = []
        speech_active = already_speaking
        pending_transcript_since: Optional[float] = None
        reprompts = 0
        waiting_since = time.monotonic()
        stt_started_at = time.monotonic()

        while True:
            if self._ended.is_set():
                return None
            event = await self._stt.get_event(timeout=_UTTERANCE_GRACE_S)
            now = time.monotonic()

            if event is None:
                if pending_transcript_since is not None and now - pending_transcript_since > _PENDING_TRANSCRIPT_MAX_S:
                    pending_transcript_since = None  # segment produced no text (noise); stop holding the turn
                if parts and not speech_active and pending_transcript_since is None:
                    self._mark("saaras_total", now - stt_started_at)
                    return " ".join(parts)
                if not parts and now - waiting_since > _SILENCE_REPROMPT_S and reprompts < _MAX_REPROMPTS:
                    await self._speak_or_fallback(_REPROMPT_LINES[reprompts])
                    await self._safe_send_json({"type": "turn_complete"})
                    await self._enter_listening()
                    reprompts += 1
                    waiting_since = time.monotonic()
                continue

            kind = event.get("kind")
            if kind == "speech_start":
                speech_active = True
            elif kind == "speech_end":
                speech_active = False
                pending_transcript_since = now
            elif kind == "transcript":
                if pending_transcript_since is not None:
                    self._mark("saaras_finalize", now - pending_transcript_since)
                parts.append(event["text"])
                pending_transcript_since = None
                waiting_since = now
            elif kind == "failed":
                # Saaras exhausted its automatic reconnects — tell the caller
                # in Hindi and keep listening (SaarasStream will retry again
                # on the next audio chunk).
                await self._speak_or_fallback(_STT_FAILURE_LINE)
                await self._safe_send_json({"type": "turn_complete"})
                await self._enter_listening()
                waiting_since = time.monotonic()

    async def _enter_listening(self, drain: bool = True) -> None:
        # After a NORMAL (uninterrupted) reply, anything Saaras produced
        # while the agent was talking (speaker echo, mostly) belongs to no
        # turn and is discarded. After a BARGE-IN, drain=False -- the events
        # that proved the caller was talking (and whatever follows) must be
        # kept, since they're the start of the caller's next utterance.
        if drain:
            self._stt.drain_events()
        await self._safe_send_json({"type": "status", "state": "listening"})

    # ── Reasoning (text Gemini) ──────────────────────────────────────────────

    async def _agent_turn(self, gemini_client, user_text: str) -> None:
        """One full agent turn: reason (with tool calls) → speak → hand the
        turn back to the caller. Mirrors the Live path's client-facing
        status/turn_complete choreography exactly."""
        turn_start = time.monotonic()
        await self._safe_send_json({"type": "status", "state": "thinking"})
        reply = await self._reason(gemini_client, user_text)
        completed = True
        if reply:
            completed = await self._speak_or_fallback(reply)
        await self._safe_send_json({"type": "turn_complete"})
        if not self.state.submitted:
            await self._enter_listening(drain=completed)
        self._mark("turn_total", time.monotonic() - turn_start)
        self._log_turn_stats()

    async def _reason(self, gemini_client, user_text: str) -> str:
        self._history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        spoken_fallback = ""
        for round_num in range(_MAX_TOOL_ROUNDS):
            t0 = time.monotonic()
            response = await self._generate_with_retry(gemini_client)
            self._mark(f"gemini_r{round_num}", time.monotonic() - t0)
            if response is None:
                # Existing error handling pattern: apologize in Hindi and keep
                # the call alive rather than dying silently.
                return _RECONNECT_APOLOGY["hi-IN"]
            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                return spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]
            # Defensive: append with an explicit role rather than trusting
            # candidate.content.role to always be "model" -- found empirically
            # that a stricter model variant 400s the whole call ("Please use a
            # valid role: user, model") if any earlier turn's role ever comes
            # back unset. Costs nothing on a well-behaved model, prevents a
            # hard failure on a less well-behaved one.
            model_parts = candidate.content.parts or []
            self._history.append(types.Content(role="model", parts=model_parts))

            text = " ".join(
                p.text.strip() for p in model_parts if getattr(p, "text", None)
            ).strip()
            function_calls = [
                p.function_call for p in model_parts if getattr(p, "function_call", None)
            ]
            if not function_calls:
                # Never return silence -- an empty candidate (blocked, or a
                # response with no usable parts) still must produce a spoken
                # reply rather than leave the caller hanging.
                return text or spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]
            if text:
                spoken_fallback = text  # speak-once: only the final round's text is voiced

            response_parts = []
            for fc in function_calls:
                t0 = time.monotonic()
                result = await self._dispatch_tool(fc.name, dict(fc.args or {}))
                self._mark(f"tool:{fc.name}", time.monotonic() - t0)
                response_parts.append(types.Part(
                    function_response=types.FunctionResponse(
                        id=getattr(fc, "id", None), name=fc.name, response=result,
                    )
                ))
            self._history.append(types.Content(role="user", parts=response_parts))
        logger.warning("Gemini used %d tool rounds without a final answer", _MAX_TOOL_ROUNDS)
        return spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]

    async def _generate_with_retry(self, gemini_client):
        for attempt in range(2):
            try:
                return await asyncio.wait_for(
                    gemini_client.aio.models.generate_content(
                        model=_TEXT_MODEL, contents=self._history, config=self._gen_config,
                    ),
                    timeout=_GEMINI_TIMEOUT_S,
                )
            except Exception:
                logger.exception("Gemini text call failed (attempt %d/2)", attempt + 1)
                await asyncio.sleep(0.5)
        return None

    # ── Speaking (Bulbul) ────────────────────────────────────────────────────

    async def _speak_or_fallback(self, text: str) -> bool:
        """Speak via Bulbul. Returns True if the reply completed normally
        (including the "TTS failed, shown as text" fallback -- that's still a
        completed turn), or False if the caller genuinely barged in and
        playback was cut short. On failure, the reply is surfaced as text
        (spec: 'display the Gemini response as text and log the error').

        Barge-in detection is done INLINE in this same coroutine (polling
        self._stt between chunks and during the trailing playback-hold wait)
        rather than via a separate concurrently-running watcher task. That
        used to be two tasks independently calling self._stt.get_event() --
        found empirically that when both happened to be near completion at
        the same moment (routinely true right as a reply finishes), whichever
        one "lost" the race had usually ALREADY dequeued a real event as a
        side effect before being cancelled, silently stealing or corrupting
        whatever the NEXT _collect_user_utterance() call was about to see.
        get_event() is a single-consumer read (like a Queue.get()) -- it must
        never have two independent callers racing on it. Keeping exactly one
        reader, in one coroutine, for the whole reply removes the race
        entirely instead of trying to tune around it.
        """
        await self._safe_send_json({"type": "transcript", "role": "model", "text": text})
        await self._safe_send_json({"type": "status", "state": "speaking"})
        # Cut off any still-playing previous audio, and drop anything Saaras
        # queued up before this reply started (e.g. tail-end echo of the
        # PREVIOUS reply) -- a genuine barge-in during THIS reply is watched
        # for separately below, from this point forward.
        await self._safe_send_json({"type": "interrupted"})
        self._stt.drain_events()

        total_samples = 0
        first_chunk_at: Optional[float] = None
        armed_at: Optional[float] = None
        tts_start = time.monotonic()
        try:
            async for chunk in self._tts.speak(text):
                now = time.monotonic()
                if first_chunk_at is None:
                    first_chunk_at = now
                    self._mark("tts_first_chunk", now - tts_start)
                    # Arm relative to when audio actually STARTS, not to when
                    # this method began -- Bulbul's connect+first-chunk
                    # network latency can itself exceed the arm delay, and
                    # arming any earlier risks reacting to the tail of the
                    # CALLER's own preceding utterance as an interruption of
                    # audio nobody has heard yet.
                    armed_at = now + _BARGE_IN_ARM_DELAY_S
                total_samples += len(chunk) // 2
                try:
                    await self.websocket.send_bytes(chunk)
                except Exception:
                    return True  # browser gone; run() will unwind via the pump
                if armed_at is not None and now >= armed_at:
                    if await self._caller_interrupted():
                        return await self._handle_bargein()
        except SarvamTTSError:
            logger.exception("Bulbul TTS failed — falling back to on-screen text")
            await self._safe_send_json({"type": "tts_text", "text": text})
            return True

        # Bulbul synthesizes faster than real time, so the browser is still
        # playing when the last chunk is sent. Hold the turn until playback
        # has roughly finished — otherwise the mic reopens mid-sentence and
        # Saaras hears the operator's own voice as the caller's answer. Polled
        # in short slices (rather than one blind sleep) so a barge-in during
        # this trailing window -- the most likely place for a real one, since
        # the agent is still audibly speaking from the caller's side -- is
        # still caught promptly.
        if first_chunk_at is not None and total_samples:
            playback_ends = first_chunk_at + total_samples / TTS_SAMPLE_RATE + 0.25
            while True:
                remaining = playback_ends - time.monotonic()
                if remaining <= 0:
                    break
                if await self._caller_interrupted(timeout=min(remaining, 0.2)):
                    return await self._handle_bargein()
        self._mark("tts_total", time.monotonic() - tts_start)
        return True

    async def _caller_interrupted(self, timeout: float = 0.0) -> bool:
        """The one and only place that reads self._stt while a reply is
        playing -- see _speak_or_fallback's docstring for why that matters.
        timeout=0 makes this a non-blocking poll between chunks."""
        event = await self._stt.get_event(timeout=timeout)
        if event and event.get("kind") in ("speech_start", "transcript"):
            self._resume_speech_active = event.get("kind") == "speech_start"
            return True
        return False

    async def _handle_bargein(self) -> bool:
        """Genuine caller barge-in: stop forwarding audio immediately, drop
        the in-flight synthesis (Sarvam has no "stop" message, so the clean
        way to discard whatever it's still generating is to close the
        connection -- the next reply just opens a fresh one), and tell the
        browser to flush anything already queued for playback."""
        logger.info("Caller barge-in detected -- stopping Bulbul playback")
        await self._tts.cancel_current()
        await self._safe_send_json({"type": "interrupted"})
        return False
