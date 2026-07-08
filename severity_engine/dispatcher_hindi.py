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
works unchanged apart from Hindi-only playback tweaks.

Gemini here is the plain generate_content API (google-genai, same Vertex
service-account client dispatcher_live already initialises) — NOT Gemini Live,
per spec. Reasoning quality note: unlike the Live pipeline, the model reads
Saaras's full final transcript of each utterance, so "मेरी कार ट्रक से टकरा गई"
reaches search_incident_type verbatim — and the deterministic vehicle-pair
override inherited from dispatcher_live still guarantees a two-vehicle mention
can never be recorded as Car vs. Car.
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
_GEMINI_TIMEOUT_S = float(os.environ.get("GEMINI_TEXT_TIMEOUT_S", "20"))
_MAX_TOOL_ROUNDS = 6

# After Saaras finalizes an utterance, wait this long for the caller to keep
# going (a natural mid-answer pause produces two segments) before treating the
# turn as complete. Saaras's own VAD already requires a silence window before
# END_SPEECH, so this only needs to cover segment-to-segment gaps.
_UTTERANCE_GRACE_S = 1.0
# END_SPEECH arrives before its segment's transcript; never close the turn
# while a transcript is still owed, but don't wait forever if none comes.
_PENDING_TRANSCRIPT_MAX_S = 4.0
_SILENCE_REPROMPT_S = 45.0
_MAX_REPROMPTS = 2

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
    return f"""आप भारत की 1033 राष्ट्रीय राजमार्ग आपातकालीन हेल्पलाइन की एक प्रशिक्षित महिला ऑपरेटर हैं — असम में सड़क दुर्घटना प्रथम-प्रतिक्रिया प्रणाली के लिए, एक वास्तविक फ़ोन कॉल पर। आप कभी भी AI, मॉडल या असिस्टेंट नहीं हैं — पूछे जाने पर आप 1033 हेल्पलाइन की ऑपरेटर हैं, बस इतना ही।

YOUR WORDS ARE SPOKEN ALOUD (text-to-speech), so every reply must be plain, natural spoken Hindi:
- 1–2 short sentences per reply, never more. No markdown, no lists, no emojis, no headings, no English sentences (common loanwords like लोकेशन, रिपोर्ट, एम्बुलेंस, ट्रक are natural and welcome).
- ALWAYS feminine grammatical forms for yourself: "समझती हूँ", "कर रही हूँ", "दर्ज कर रही हूँ", "पुष्टि करना चाहती हूँ" — in every sentence.
- Simple, modern, everyday Hindi — never formal, literary, or Sanskrit-heavy, never stiff translations of English. Say "क्या हुआ है?" not "कृपया घटना का विवरण प्रदान करें।"; "गाड़ी" not "वाहन"; "मदद" not "सहायता"; "टक्कर हुई" not "दुर्घटनाग्रस्त हुईं"।

EVERY TURN WORKS IN THIS ORDER — HARD RULE, NO EXCEPTIONS:
1. FIRST make ALL your tool calls for what the caller just said: search_incident_type (with their verbatim words) the first time they describe what happened, and update_form_field for EVERY new fact (casualties, trapped, bleeding, fire, vehicle counts, description). Never skip this to reply faster — a fact acknowledged in words but never recorded through a tool is LOST and help goes to the wrong place.
2. ONLY AFTER the tool results come back, give your one short spoken reply: a warm, human acknowledgment of what they told you FIRST, then the ONE question named by "next_question". Never a bare question with no acknowledgment ("कितनी गाड़ियाँ शामिल थीं?" alone — never); never an acknowledgment that dodges the tools. "ठीक है, दो गाड़ियों की टक्कर — मैं दर्ज कर रही हूँ। क्या कोई घायल है?" is the shape. When the caller has just mentioned anyone hurt, trapped, or bleeding, the acknowledgment must carry real concern ("मुझे यह सुनकर दुख हुआ, घबराइए मत — मदद भेज रही हूँ।"), not a plain "ठीक है"।

TONE — like a real, serious, caring emergency operator, never a cheerful customer-service bot and never a flat form-filling machine. Calm, warm, reassuring, a little subdued. Phrases in your natural repertoire: "घबराइए मत, मैं आपकी मदद के लिए यहाँ हूँ।" / "सबसे पहले आपकी सुरक्षा ज़रूरी है।" / "धन्यवाद, मैं जानकारी दर्ज कर रही हूँ।" When the caller mentions an injury, bleeding, someone trapped, or sounds frightened, respond with real, sincere concern FIRST — "मुझे यह सुनकर दुख हुआ, हम जल्द से जल्द मदद भेज रहे हैं।" — never a bare "ठीक है" or "समझ गई" alone. Vary your phrasing; never repeat the same acknowledgment twice in one call. Empathy must never slow the call down: gathering what's needed to send help stays the priority.

OPENING (your very first reply of the call, and only once ever): say this exact sentence word for word, with nothing before it: "{opening_line}" — then, in the same reply, if a detected location was given to you, mention it briefly and ask if it is right, and ask what happened; if no location was detected, ask the caller to mark their location with the map-pin button, and ask what happened. Never repeat the welcome sentence again for the rest of the call.

UNDERSTANDING THE CALLER: people speak colloquially and indirectly — "टायर फट गया", "गाड़ी पलट गई", "ठोक दिया", "भिड़ गई", "आग पकड़ ली". Understand the MEANING from the whole sentence; never ask the caller to rephrase into formal words. Reason semantically about what happened — never by spotting a single keyword.

INCIDENT TYPE — CRITICAL:
- Never guess or invent an incident type yourself. Always call search_incident_type, passing the caller's ACTUAL words verbatim (in Hindi, exactly as transcribed) as the description — never your paraphrase or translation. It records a confident match automatically.
- Pay close attention to WHICH vehicles the caller named. "मेरी कार ट्रक से टकरा गई" involves a car AND a truck — the recorded type must name both, never Car vs. Car. The search tool guarantees this when it receives the caller's real words.
- If the match seems wrong to the caller, or you are genuinely unsure what happened, ask ONE short clarifying question before settling it — for example: "क्या आपकी कार किसी ट्रक से टकराई थी, या किसी और गाड़ी से?" Then call search_incident_type again with the clarified wording, or browse with search_incident_categories and set it via update_form_field.

FORM FILLING: call update_form_field immediately every time the caller gives you a new piece of information — including conditions mentioned in passing (fire, fuel leak, anyone trapped, consciousness, breathing, bleeding), not just direct answers. A "no" is information too: if the caller says there is NO fire / nobody trapped / no leak, record it with update_form_field(field=flag, flag_active=false) — never just move on. Watch flag polarity: "बेहोश है" means Conscious=false; "साँस नहीं आ रही" means Breathing=false; "आग नहीं लगी" means Fire=false. The description field is special: set it early with a rough one-sentence summary and refine it as you learn more — and ALWAYS write the description text in ENGLISH (translate/summarize yourself), no matter that the conversation is in Hindi. It is the only thing you ever write in English.

FOLLOW-UP QUESTIONS — HARD RULE: every tool response includes "next_question" — the ONE topic to ask about next (or null when nothing is left). Your next question must be about exactly that topic, phrased naturally in Hindi — one question at a time, never two, never a topic of your own, never something the caller already told you (if they said "दो लोग घायल हैं", never ask "क्या कोई घायल है?"). Keep asking about the same topic, rephrasing gently, until it is answered.

CONFIRMATION BEFORE SUBMITTING — HARD RULE: when "next_question" is null, verbally summarize everything collected in one or two natural sentences and ask for confirmation, for example: "मैं पुष्टि करना चाहती हूँ — आपकी कार की ट्रक से टक्कर हुई है, दो लोग घायल हैं, लोकेशन NH-48 है। क्या यह जानकारी सही है?" Only after the caller clearly says yes, call submit_incident. If it reports something still missing, ask for that and try again. After a successful submit, close warmly in one sentence — help is being arranged, they should stay safe — and say nothing more.

Each caller message may be an imperfect speech transcript — small transcription errors are normal; understand the intent. Never mention tools, transcripts, systems, or anything technical about how you work."""


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
        self._gen_config = types.GenerateContentConfig(
            system_instruction=_hindi_system_prompt(),
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS)],
            # Same consistency rationale as the Live Hindi path: lower
            # temperature so every call behaves like the same operator.
            temperature=0.4,
            max_output_tokens=500,
            # No thinking for a real-time call — the sub-2s latency target
            # matters more than marginal reasoning depth here.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

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
            await self._agent_turn(
                gemini_client, f"(The call has just connected. {location_note} Begin now.)"
            )

            while not self._ended.is_set() and not self.state.submitted:
                user_text = await self._collect_user_utterance()
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
        messages (location results / end-of-call) handled like the Live pump."""
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

    async def _collect_user_utterance(self) -> Optional[str]:
        """One caller turn: finalized Saaras segments joined together, closed
        after a short grace window of silence. Returns None when the call is
        over. Also owns the long-silence re-prompt and the STT-failure notice
        (spoken in Hindi, per spec)."""
        parts: list[str] = []
        speech_active = False
        pending_transcript_since: Optional[float] = None
        reprompts = 0
        waiting_since = time.monotonic()

        while True:
            if self._ended.is_set():
                return None
            event = await self._stt.get_event(timeout=_UTTERANCE_GRACE_S)
            now = time.monotonic()

            if event is None:
                if pending_transcript_since is not None and now - pending_transcript_since > _PENDING_TRANSCRIPT_MAX_S:
                    pending_transcript_since = None  # segment produced no text (noise); stop holding the turn
                if parts and not speech_active and pending_transcript_since is None:
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

    async def _enter_listening(self) -> None:
        # Anything Saaras produced while the caller's mic was gated (speaker
        # echo picked up before the gate closed) belongs to no turn — drop it.
        self._stt.drain_events()
        await self._safe_send_json({"type": "status", "state": "listening"})

    # ── Reasoning (text Gemini) ──────────────────────────────────────────────

    async def _agent_turn(self, gemini_client, user_text: str) -> None:
        """One full agent turn: reason (with tool calls) → speak → hand the
        turn back to the caller. Mirrors the Live path's client-facing
        status/turn_complete choreography exactly."""
        await self._safe_send_json({"type": "status", "state": "thinking"})
        reply = await self._reason(gemini_client, user_text)
        if reply:
            await self._speak_or_fallback(reply)
        await self._safe_send_json({"type": "turn_complete"})
        if not self.state.submitted:
            await self._enter_listening()

    async def _reason(self, gemini_client, user_text: str) -> str:
        self._history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        spoken_fallback = ""
        for _round in range(_MAX_TOOL_ROUNDS):
            response = await self._generate_with_retry(gemini_client)
            if response is None:
                # Existing error handling pattern: apologize in Hindi and keep
                # the call alive rather than dying silently.
                return _RECONNECT_APOLOGY["hi-IN"]
            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                return spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]
            self._history.append(candidate.content)

            text = " ".join(
                p.text.strip() for p in (candidate.content.parts or []) if getattr(p, "text", None)
            ).strip()
            function_calls = [
                p.function_call for p in (candidate.content.parts or [])
                if getattr(p, "function_call", None)
            ]
            if not function_calls:
                return text or spoken_fallback
            if text:
                spoken_fallback = text  # speak-once: only the final round's text is voiced

            response_parts = []
            for fc in function_calls:
                result = await self._dispatch_tool(fc.name, dict(fc.args or {}))
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

    async def _speak_or_fallback(self, text: str) -> None:
        """Speak via Bulbul; on TTS failure, surface the reply as text (spec:
        'display the Gemini response as text and log the error')."""
        await self._safe_send_json({"type": "transcript", "role": "model", "text": text})
        await self._safe_send_json({"type": "status", "state": "speaking"})
        # Cut off any still-playing previous audio before speaking again.
        await self._safe_send_json({"type": "interrupted"})
        total_samples = 0
        first_chunk_at: Optional[float] = None
        try:
            async for chunk in self._tts.speak(text):
                if first_chunk_at is None:
                    first_chunk_at = time.monotonic()
                total_samples += len(chunk) // 2
                try:
                    await self.websocket.send_bytes(chunk)
                except Exception:
                    return  # browser gone; run() will unwind via the pump
        except SarvamTTSError:
            logger.exception("Bulbul TTS failed — falling back to on-screen text")
            await self._safe_send_json({"type": "tts_text", "text": text})
            return
        # Bulbul synthesizes faster than real time, so the browser is still
        # playing when the last chunk is sent. Hold the turn until playback
        # has roughly finished — otherwise the mic reopens mid-sentence and
        # Saaras hears the operator's own voice as the caller's answer.
        if first_chunk_at is not None and total_samples:
            playback_ends = first_chunk_at + total_samples / TTS_SAMPLE_RATE + 0.25
            remaining = playback_ends - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
