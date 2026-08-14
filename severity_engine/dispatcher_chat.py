"""dispatcher_chat.py — the ENGLISH text-chat dispatcher (typed, no audio).

A chatbot version of the voice dispatcher: the user TYPES to an AI operator
instead of calling. It follows the EXACT same flow as the voice dispatcher
(greet → collect location / incident type / injuries / hazards → gated submit →
matching → closing briefing), reusing the shared deterministic machinery, with
NO audio at all — text in, text out.

DESIGN — additive, maximal reuse, zero change to any voice pipeline:
`TextChatSession` SUBCLASSES `DispatcherSession` (dispatcher_live.py) purely to
INHERIT the shared, deterministic machinery byte-for-byte:
  - the 5 tool handlers (`_tool_search_incident_type` with its vehicle-pair /
    same-type overrides, `_tool_update_form_field`'s taxonomy validation,
    `_tool_get_current_location`, `_tool_submit_incident`'s hard-gated required
    fields, `_tool_search_incident_categories`) and `_dispatch_tool`,
  - `DispatcherState`, `_compute_still_missing` next_question sequencing,
    `_state_block` (which injects `next_question`/`fast_track`/`tone_reminder`
    into every tool response — the same sequencing engine the voice paths use),
  - `_apply_local_signals_from_transcript` (the transcript backstop),
  - `_is_critical` / `_field_unanswered`, and `_safe_send_json` /
    `_pending_location` / `_dispatch_ready` / `_dispatch_info`.
Nothing in that machinery is reimplemented. What THIS class adds is only:
  - a text WebSocket run-loop (no STT/TTS, no barge-in, no keep-alive audio),
  - a Sarvam reasoning turn built directly on `sarvam_reasoning.py`
    (`sarvam_generate` + the shared tool set), and
  - a deterministic code-composed reply used if Sarvam fails (never silence).

It does NOT subclass the voice sessions (HindiDispatcherSession /
AssameseDispatcherSession) and does NOT touch dispatcher_hindi.py,
dispatcher_assamese.py, dispatcher_live.py's Gemini-Live path, or the Exotel
integration. Reasoning is Sarvam (sarvam-105b-conversations), English only.

Browser-facing protocol (a strict SUBSET of the voice protocol — the audio
frames are simply absent): the browser sends {"type":"user_text","text":...},
plus the same {"type":"location_result"/"location_error"} and
{"type":"dispatch_update","services":...} frames the voice panel already sends;
the backend emits {"type":"ready"}, {"type":"status","state":...} (thinking /
listening), {"type":"assistant_text","text":...}, and the EXISTING
{"type":"form_update"|"request_location"|"submitted"|"call_complete"} frames.
After submit, the frontend runs its EXISTING assess + MatchingPanel flow and
sends dispatch_update; this session then delivers the English closing briefing
(dispatch_briefing.build_briefing_instruction, "en-IN") as one assistant_text,
then call_complete.

Honesty rules (CLAUDE.md hard rules) are intact and inherited: ETAs are spoken
as estimates, dispatch is a NOTIFICATION record only (never dispatched/tracked/
ETA-as-fact), and no data is fabricated — the briefing skips ETAs entirely if
dispatch_update never arrives.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

import httpx
from fastapi import WebSocket
from google.genai import types

from .dispatch_briefing import (
    _CLOSING_EN,
    _responder_facts_en,
    build_briefing_instruction,
    select_sops,
)
from .dispatcher_live import (
    _DISPATCH_WAIT_S,
    _OPENING_LINE,
    _TOOL_DECLARATIONS,
    DispatcherSession,
)
from .sarvam_reasoning import (
    SarvamReasoningError,
    gemini_history_to_openai_messages,
    gemini_tools_to_openai,
    sarvam_generate,
)

logger = logging.getLogger("dispatcher_chat")

# Sarvam is the ONLY reasoner for chat (no Gemini fallback — the spec's failure
# mode is "degrade to the deterministic code-composed reply, never silence").
_MAX_TOOL_ROUNDS = 5
_MAX_OUTPUT_TOKENS = int(os.environ.get("CHAT_MAX_OUTPUT_TOKENS", "300"))
_BRIEFING_MAX_OUTPUT_TOKENS = int(os.environ.get("CHAT_BRIEFING_MAX_OUTPUT_TOKENS", "700"))
_SARVAM_ATTEMPTS = int(os.environ.get("CHAT_SARVAM_ATTEMPTS", "2"))
_SARVAM_TIMEOUT_S = float(os.environ.get("CHAT_SARVAM_TIMEOUT_S", "8"))
_BRIEFING_TIMEOUT_S = float(os.environ.get("CHAT_BRIEFING_TIMEOUT_S", "15"))

_OPENING_TEXT = _OPENING_LINE["en-IN"]  # "Welcome to the 1033 Highway Helpline of India."

# Deterministic English questions used ONLY when Sarvam fails, keyed by the hint
# strings _compute_still_missing() emits (the 3 essentials + every REQUIRED_FIELDS
# group hint, individual + combined). Coverage over every possible hint is
# asserted in tests.py, so the fast fallback never falls back to a generic
# phrasing. The model's OWN questions come from Sarvam guided by next_question;
# this is only the safety net so the chat is never left silent.
_CHAT_QUESTIONS_EN: dict[str, str] = {
    # The three essentials (literal strings from _compute_still_missing).
    "the incident type (call search_incident_type)":
        "Can you tell me what happened — what kind of accident or emergency is this?",
    "the location (call get_current_location)":
        "Where did this happen? You can share your location, or type a nearby landmark, road, or town.",
    "a short description of what happened":
        "Could you briefly describe what happened?",
    # Individual per-field hints.
    "how many vehicles were involved": "How many vehicles were involved?",
    "how many people are injured": "Is anyone injured?",
    "whether anyone is trapped": "Is anyone trapped?",
    "whether there is fire or a fuel leak": "Is there any fire or a fuel leak?",
    "whether hazardous material is involved": "Is any hazardous material involved?",
    "whether the person is conscious": "Is the person conscious?",
    "whether the person is breathing": "Is the person breathing normally?",
    "whether there is heavy bleeding": "Is there any heavy bleeding?",
    # Combined group hints (asked as ONE question when all of a group is missing).
    "how many vehicles were involved, and whether anyone is injured or trapped":
        "How many vehicles were involved, and is anyone injured or trapped?",
    "whether there is any fire or a hazardous-material leak":
        "Is there any fire, or a hazardous-material or fuel leak?",
    "whether the person is conscious and breathing, and whether there is heavy bleeding":
        "Is the person conscious and breathing, and is there any heavy bleeding?",
    "whether anyone is injured or trapped":
        "Is anyone injured or trapped?",
    "how many vehicles were involved, and whether anyone is injured":
        "How many vehicles were involved, and is anyone injured?",
}


def _chat_system_prompt() -> str:
    """English TEXT-chat operator prompt. Mirrors the deterministic-flow rules of
    the voice prompts (dispatcher_live._system_instruction / dispatcher_hindi's
    prompt) — same INCIDENT TYPE / FORM FILLING / DESCRIPTION / next_question /
    reply-once / SUBMITTING / FAST-TRACK / AFTER-SUBMISSION / honesty rules — but
    written for a typed chat: no TTS/pause/pronunciation/voice guidance."""
    return f"""You are an emergency dispatch operator for a road-accident first-response service in Assam, India. You are having a real-time TEXT CHAT with someone reporting a road accident or emergency — they type to you and you type back. You are NEVER an AI, a model, or an assistant; if asked, you are simply the 1033 helpline operator. Write in clear, plain, everyday English — no markdown, no bullet lists, no emoji.

TONE: You are a trained, professional emergency-helpline operator — calm, composed, and clearly in control, while being genuinely concerned for the person. This is a serious safety chat, never an upbeat customer-service chat. Keep every reply short — 1 to 2 sentences: a brief, sincere acknowledgment of what they just told you, then exactly ONE question. Never let two replies in a row be a bare "Okay." / "Noted." — ground each one warmly ("Thank you, I've got that", "Understood — let's get this sorted quickly"). When they report an injury, bleeding, someone trapped, or sound frightened, lead with real concern before your question ("I'm sorry to hear that — I'm getting help arranged for you"). Every tool response includes a "tone_reminder" — follow it every time. Do not repeat a question you have already asked.

OPENING (only the very first reply of the chat): greet with this exact sentence, word for word, first: "{_OPENING_TEXT}" You will be told the user's detected location (or that none was detected) in the message that starts the chat — do not call get_current_location for that, it is already resolved. If a location was detected, briefly confirm it ("I have your location as X — is that right?") and ask what happened, in this same first reply. Also find out, naturally, WHO you are chatting with in relation to the incident — are they involved/injured themselves, a bystander who witnessed it, or reporting on someone else's behalf. If no location was detected, ask them to share their location or type a nearby landmark, road, or town. Never repeat the welcome sentence again for the rest of the chat.

INCIDENT TYPE: Never guess or invent an incident type yourself. Always call search_incident_type with the user's own description of what happened — it records a confident match automatically, so you do not need a separate confirmation step unless they say it is wrong. Refer to the incident only by the exact subType name it returns. If it is wrong, call search_incident_categories to browse alternatives, then update_form_field with field=incidentType and the exact subType. Recording it that way IS the confirmation — do not ask them to confirm the type again; acknowledge briefly and move on.

FORM FILLING: Call update_form_field immediately every time the user gives you a new piece of information — INCLUDING conditions mentioned in passing. If they mention fire, hazmat, anyone trapped, consciousness, breathing, or bleeding anywhere in what they type, call update_form_field with field=flag for that condition right away — do not wait for a dedicated question.

DESCRIPTION FIELD: Call update_form_field with field=description as soon as there is enough for a rough one-sentence summary — do not wait for every detail. Update it (replacing the old value) whenever you learn more. Include WHO is reporting once you know it (e.g. "Caller is the injured driver", "Bystander reporting"). This field is always in English.

FOLLOW-UP QUESTIONS — HARD RULE: every tool response includes "next_question", the ONE specific thing to ask next, or null if nothing is left. This is precomputed deterministically — not your judgment. After a brief acknowledgment, your next question must be about EXACTLY the topic in "next_question", worded naturally but never substituted for a different topic. Never ask about anything else, never invent your own question (e.g. do not ask about consciousness or breathing unless "next_question" says so), never skip ahead, and never ask about something already answered. When "next_question" names two or three closely-related things at once (e.g. "how many vehicles were involved, and whether anyone is injured or trapped"), ask them together as ONE combined question, not separate messages.

REPLY ONCE PER MESSAGE: when one message from the user contains several facts, make ALL of your tool calls for it first (update_form_field for each, search_incident_type if needed), and only THEN write ONE reply covering your acknowledgment and the one next question. Never write more than one reply for a single user message, and never leave a user message without a reply.

SUBMITTING (routine reports): when "next_question" comes back null and "fast_track" is false, everything required is collected. Do NOT read back a full summary. Give ONE brief confirmation ending with a quick check, e.g. "I have everything I need for your report now — shall I go ahead and submit it?" As soon as they confirm (yes / please do / that's right), call submit_incident in that same turn. If they want to fix something, fix just that and confirm briefly again. Only if submit_incident reports something missing, ask for just that one thing.

FAST-TRACK (injuries or life-threatening emergencies) — OVERRIDES the confirmation above: every tool response includes "fast_track". When it is true, the user has already reported an injury or life-threatening condition, so "next_question" stops asking routine secondary questions and goes null as soon as you have the incident type, location, and a short description. Then do NOT ask permission and do NOT gather more — briefly reassure them that help is being arranged RIGHT NOW ("I'm getting help arranged for you right now"), and in that SAME turn call submit_incident. HONESTY (hard rules): say help is being ARRANGED right now — NEVER that a vehicle has been dispatched, is on its way, is being tracked, or will arrive in N minutes. This only creates a notification record.

AFTER SUBMISSION: when submit_incident succeeds, follow its "next_step" — tell the user their report has been registered and that you are checking which emergency services are responding, and ask them to stay in the chat for a moment. This is the LAST thing you write. Do not say goodbye, do not ask anything further, and do not call any more tools.

HONESTY (never break these): every time you give is an ESTIMATE ("estimated", "approximately"); services are NOTIFIED / responding, never "dispatched and tracked"; never invent a responder name, number, or arrival time — only use what you are given."""


class TextChatSession(DispatcherSession):
    """English typed-chat dispatcher. Inherits DispatcherSession's shared tool
    handlers / state / sequencing / backstop; adds a text run-loop + Sarvam
    reasoning. No STT/TTS. Does not touch any voice pipeline."""

    def __init__(self, websocket: WebSocket):
        super().__init__(websocket, "en-IN")  # shared state/handlers/backstop, English
        self._history: list = []  # types.Content conversation history for Sarvam
        self._inbound_text: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
        self._ended = asyncio.Event()
        self._system_prompt = _chat_system_prompt()
        # The SAME 5 accident tools the English/Hindi voice paths use, mapped once
        # to OpenAI `tools` for Sarvam (no helpline tools — chat is accident-flow
        # only, exactly like the English voice dispatcher).
        self._openai_tools = gemini_tools_to_openai(_TOOL_DECLARATIONS)
        self._sarvam_http: Optional[httpx.AsyncClient] = None
        self._opening_pending = True

    # ── Session lifecycle ────────────────────────────────────────────────────
    async def run(self) -> None:
        await self._safe_send_json({"type": "ready"})
        pump = asyncio.create_task(self._pump())
        self._sarvam_http = httpx.AsyncClient(timeout=max(_SARVAM_TIMEOUT_S, _BRIEFING_TIMEOUT_S) + 1.0)
        try:
            # Resolve location upfront (browser GPS), same as the voice paths, so
            # the opening reply can confirm it. Non-fatal on timeout/denial.
            location_result = await self._tool_get_current_location()
            if location_result.get("status") in ("ok", "already_have_location"):
                location_note = f"Detected location: {location_result.get('label', '')}."
            else:
                location_note = "No location was detected."
            # Opening reply (greeting + first question). The greeting sentence is
            # added by the model per the prompt; a deterministic fallback covers a
            # Sarvam failure so the user always sees a greeting + a question.
            await self._turn(
                f"(The chat has just connected. {location_note} Greet with the welcome sentence, "
                f"confirm the location if one was detected, and ask what happened — all in one short reply.)",
                opening=True,
            )
            while not self._ended.is_set() and not self.state.submitted:
                user_text = await self._inbound_text.get()
                if user_text is None:
                    break
                self.state.caller_transcript += " " + user_text
                await self._apply_local_signals_from_transcript()  # shared backstop
                await self._turn(user_text)
            if self.state.submitted and not self._ended.is_set():
                await self._deliver_briefing()
        finally:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):
                pass
            if self._sarvam_http is not None:
                try:
                    await self._sarvam_http.aclose()
                except Exception:
                    pass

    async def _pump(self) -> None:
        """Browser → backend: JSON control frames only (no binary). user_text is
        queued for the run-loop; location_result/location_error resolve the
        pending get_current_location future (same as the voice pumps);
        dispatch_update wakes the closing briefing."""
        try:
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if text is None:
                    continue
                try:
                    msg = json.loads(text)
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype in ("end", "close"):
                    break
                if mtype == "user_text":
                    t = (msg.get("text") or "").strip()
                    if t:
                        await self._inbound_text.put(t)
                elif mtype == "location_result":
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
                elif mtype == "dispatch_update":
                    self._dispatch_info = msg.get("services") or None
                    self._dispatch_ready.set()
        except Exception:
            logger.debug("Chat client pump ended", exc_info=True)
        finally:
            self._ended.set()
            # Unblock a run-loop waiting on the next user message.
            self._inbound_text.put_nowait(None)

    # ── One reasoning turn (Sarvam + shared tools, text out) ─────────────────
    async def _turn(self, user_text: str, opening: bool = False) -> None:
        await self._safe_send_json({"type": "status", "state": "thinking"})
        reply = await self._reason(user_text)
        if not reply:
            # Sarvam failed / returned nothing usable -> deterministic reply
            # (never leave the user without a message).
            reply = self._deterministic_reply(opening=opening)
        if opening and not reply.startswith(_OPENING_TEXT):
            # Guarantee the greeting sentence is present on the very first reply,
            # exactly as the voice paths guarantee their spoken opening line.
            reply = f"{_OPENING_TEXT} {reply}".strip()
        self._opening_pending = False
        await self._safe_send_json({"type": "transcript", "role": "assistant", "text": reply})
        await self._safe_send_json({"type": "assistant_text", "text": reply})
        if not self.state.submitted:
            await self._safe_send_json({"type": "status", "state": "listening"})

    async def _reason(self, user_text: str, briefing: bool = False) -> Optional[str]:
        """Run Sarvam with the shared tools until it returns a final text reply.
        Tool calls are dispatched via the INHERITED _dispatch_tool (which sends
        form_update/request_location/submitted and injects next_question via
        _state_block). Returns the reply text, or None if Sarvam is unavailable
        (the caller then composes a deterministic reply)."""
        self._history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        max_tokens = _BRIEFING_MAX_OUTPUT_TOKENS if briefing else _MAX_OUTPUT_TOKENS
        timeout = _BRIEFING_TIMEOUT_S if briefing else _SARVAM_TIMEOUT_S
        last_text = ""
        for _round in range(_MAX_TOOL_ROUNDS):
            result = await self._sarvam_round(max_tokens, timeout)
            if result is None:
                return None  # both attempts failed -> deterministic fallback
            fcs = [types.FunctionCall(id=tc.id, name=tc.name, args=tc.args) for tc in result.tool_calls]
            model_parts = ([types.Part(text=result.text)] if result.text else []) + \
                [types.Part(function_call=fc) for fc in fcs]
            self._history.append(types.Content(role="model", parts=model_parts))
            if result.text:
                last_text = result.text
            if not fcs:
                return result.text or last_text or None
            response_parts = []
            for fc in fcs:
                res = await self._dispatch_tool(fc.name, dict(fc.args or {}))  # INHERITED
                response_parts.append(types.Part(
                    function_response=types.FunctionResponse(id=fc.id, name=fc.name, response=res)
                ))
            self._history.append(types.Content(role="user", parts=response_parts))
        logger.warning("Chat reasoning used %d tool rounds without a final reply", _MAX_TOOL_ROUNDS)
        return last_text or None

    async def _sarvam_round(self, max_tokens: int, timeout: float):
        """One Sarvam call with a small retry, hard-capped by wait_for. Returns a
        NormalizedResult or None if every attempt failed (transport/HTTP/empty)."""
        messages = gemini_history_to_openai_messages(self._history, self._system_prompt)
        for attempt in range(_SARVAM_ATTEMPTS):
            try:
                return await asyncio.wait_for(
                    sarvam_generate(messages, self._openai_tools, max_tokens=max_tokens, client=self._sarvam_http),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Chat Sarvam timed out (>%ss, attempt %d)", timeout, attempt + 1)
            except SarvamReasoningError as e:
                logger.warning("Chat Sarvam failed (attempt %d): %s", attempt + 1, str(e)[:160])
        return None

    def _deterministic_reply(self, opening: bool = False) -> str:
        """Code-composed reply used when Sarvam is unavailable — never silence.
        Uses the SAME deterministic next_question the shared machinery computes,
        so the fallback still asks the right thing in the right order."""
        missing = self._compute_still_missing()
        if opening:
            first = _CHAT_QUESTIONS_EN.get(missing[0]) if missing else None
            return f"{_OPENING_TEXT} {first or 'What has happened?'}"
        if not missing:
            return "I have everything I need for your report now — shall I go ahead and submit it?"
        return _CHAT_QUESTIONS_EN.get(missing[0]) or f"Could you tell me about {missing[0]}?"

    # ── Closing briefing (text) ──────────────────────────────────────────────
    async def _deliver_briefing(self) -> None:
        """After the frontend's matching flow sends dispatch_update, deliver the
        English closing briefing (dispatch_briefing, "en-IN") as ONE
        assistant_text, then call_complete. Same honesty rules as voice: if
        dispatch_update never arrives, the briefing skips ETAs (never invents)."""
        try:
            await asyncio.wait_for(self._dispatch_ready.wait(), timeout=_DISPATCH_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning("Chat: no dispatch_update within %.0fs — briefing without ETAs", _DISPATCH_WAIT_S)
        if self._ended.is_set():
            return
        instruction = build_briefing_instruction(self.state, self._dispatch_info, "en-IN")
        reply = await self._reason(instruction, briefing=True)
        if not reply:
            reply = self._compose_fallback_briefing()
        await self._safe_send_json({"type": "transcript", "role": "assistant", "text": reply})
        await self._safe_send_json({"type": "assistant_text", "text": reply})
        await self._safe_send_json({"type": "call_complete"})

    def _compose_fallback_briefing(self) -> str:
        """Deterministic, complete English briefing text (no model call), used if
        Sarvam is unavailable for the closing turn. Built from the SAME
        dispatch_briefing building blocks the instruction is built from, so the
        facts/SOPs/closing are identical and honesty-compliant."""
        facts = _responder_facts_en(self._dispatch_info)
        sop_lines = [s["en"] for s in select_sops(self.state)]
        parts = ["Thank you for staying with me. Here is where things stand."]
        if facts:
            parts.extend(facts)
        else:
            parts.append("The emergency services have been notified and are being arranged.")
        parts.extend(sop_lines)
        parts.extend(_CLOSING_EN)
        return " ".join(p.strip() for p in parts if p and p.strip())
