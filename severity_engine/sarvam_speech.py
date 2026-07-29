"""
sarvam_speech.py — Sarvam AI speech bridges for the HINDI voice dispatcher only.

Two thin, raw-WebSocket clients (no sarvamai SDK dependency — uvicorn[standard]
already ships the `websockets` package this uses):

  - SaarasStream  — streaming speech-to-text via Saaras v3
                    (wss://api.sarvam.ai/speech-to-text/ws)
  - BulbulStream  — streaming text-to-speech via Bulbul v3
                    (wss://api.sarvam.ai/text-to-speech/ws)

Protocol details were verified against Sarvam's official API reference and the
official `sarvamai` Python SDK (v0.1.28) rather than guessed:
  - STT connect query params use `language-code` (hyphenated!) while every
    other param is underscored — that mismatch is real, straight from the SDK's
    raw_client.py.
  - STT audio messages are {"audio": {"data": <b64>, "encoding": "audio/wav",
    "sample_rate": 16000}} even for headerless PCM16 chunks.
  - With `vad_signals=true`, the server emits {"type": "events"} messages with
    signal_type START_SPEECH/END_SPEECH, and each {"type": "data"} message is a
    FINAL transcript for one detected utterance segment (there are no
    word-by-word interim results in this mode).
  - TTS: send {"type":"config"} once per connection, then {"type":"text"} +
    {"type":"flush"}; audio arrives as base64 chunks, and with
    `send_completion_event=true` a {"type":"event","event_type":"final"}
    marks end-of-synthesis. Idle connections are kept alive with
    {"type":"ping"} (the SDK pings every 20s).
  - Bulbul v3 does NOT support pitch/loudness or SSML (verified against
    Sarvam's own docs) -- only pace, temperature, and the min_buffer_size/
    max_chunk_length streaming-chunk controls are real, tunable parameters,
    so that's all this module exposes; nothing here is invented.

English is untouched by this module — it exists only for the hi-IN dispatcher
(see dispatcher_hindi.py).
"""
import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from typing import AsyncIterator, Optional

import websockets

logger = logging.getLogger("sarvam_speech")

_STT_WS_URL = os.environ.get("SARVAM_STT_WS_URL", "wss://api.sarvam.ai/speech-to-text/ws")
_TTS_WS_URL = os.environ.get("SARVAM_TTS_WS_URL", "wss://api.sarvam.ai/text-to-speech/ws")

_SAMPLE_RATE_IN = 16000   # browser mic worklet output (PCM16 mono)
TTS_SAMPLE_RATE = 24000   # matches the frontend's fixed PLAYBACK_SAMPLE_RATE

# Sarvam's API expects "saaras:v3" / "bulbul:v3", but env files commonly use
# dash-style names ("saaras-v3", per this feature's spec) — accept both.
def _normalize_model(value: str) -> str:
    return re.sub(r"^(saaras|saarika|bulbul)-(v[\d.]+)$", r"\1:\2", value.strip())


STT_MODEL = _normalize_model(os.environ.get("SARVAM_STT_MODEL", "saaras:v3"))
TTS_MODEL = _normalize_model(os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3"))
# "shubh" -- male Bulbul v3 voice, switched from the original female "priya"
# per user feedback after comparing voices in Sarvam's own playground. The
# Hindi system prompt's self-referential grammar (dispatcher_hindi.py) must
# stay in sync with whichever gender is set here -- a male voice speaking
# feminine-conjugated Hindi ("समझ रही हूँ") sounds wrong to any Hindi
# listener, so changing this alone without updating the prompt's gender
# would make speech sound LESS natural, not more.
TTS_SPEAKER = os.environ.get("SARVAM_TTS_SPEAKER", "shubh")
# Pace history: 1.0 -> 1.15 -> 1.3 (too fast) -> 1.2 -> 1.1, per iterative
# user feedback with the "shubh" voice. Earlier live testing (see project
# history) confirmed pace is a real, functioning parameter and that "shubh"
# has a naturally brisker baseline cadence than "priya" did at the same
# value -- don't re-verify this again with a live API call unless the
# parameter itself is in question; API credits are limited.
TTS_PACE = float(os.environ.get("SARVAM_TTS_PACE", "1.1"))
# Real, documented Bulbul v3 config fields (not fabricated) -- v3 does NOT
# support pitch/loudness/SSML, so those are deliberately not offered here.
# temperature governs expressiveness/variability for v3. Lowered 0.7 -> 0.2
# (2026-07) after "talks in multiple different voices randomly / changes its
# tone and emotions" reports: at 0.7 each synthesis got noticeably different
# prosody/energy/emotional colour, which -- with the same shubh speaker -- reads
# as "different voices". A low temperature makes every utterance sound like the
# same, consistent operator. The speaker itself was never changing (config sends
# speaker=shubh on every connection); this was purely per-synthesis expressive
# variance. Env-tunable (SARVAM_TTS_TEMPERATURE) -- raise a little only if it
# sounds too flat; the priority now is a constant voice, not expressiveness.
# min_buffer_size/max_chunk_length control how much text Bulbul buffers
# before it starts streaming audio back. A previous iteration lowered these
# (30/90) purely to shave time-to-first-audio-chunk when LATENCY was the
# priority; restored to Sarvam's own documented defaults (50/150) now that
# per-turn latency is no longer the dominant complaint and prosody continuity
# (fewer, larger synthesis segments = less per-segment "reset", a plausible
# contributor to a choppy/robotic-sounding cadence) matters more.
TTS_TEMPERATURE = float(os.environ.get("SARVAM_TTS_TEMPERATURE", "0.2"))
TTS_MIN_BUFFER_CHARS = int(os.environ.get("SARVAM_TTS_MIN_BUFFER_CHARS", "50"))
TTS_MAX_CHUNK_CHARS = int(os.environ.get("SARVAM_TTS_MAX_CHUNK_CHARS", "150"))
# Optional Saaras v3 VAD tuning for barge-in robustness -- unset by default
# (server default applies); raise this if speaker echo without headphones
# ever false-triggers an interruption in the field. Real, documented
# saaras:v3-only parameter (see Sarvam's streaming STT API reference).
STT_INTERRUPT_MIN_SPEECH_FRAMES = os.environ.get("SARVAM_STT_INTERRUPT_MIN_FRAMES")

_STT_RECONNECT_ATTEMPTS = 3
_STT_KEEPALIVE_IDLE_S = 5.0
_TTS_PING_INTERVAL_S = 20.0
# Voice-consistency guard for LONG utterances (the closing briefing): a single
# long Bulbul synthesis has been observed to drift off the configured `speaker`
# partway through (the closing reverting to Bulbul's default voice). Multi-turn
# CONVERSATIONS never show this -- they are many SHORT text/flush cycles on one
# config'd connection, which proves the per-connection config carries fine
# across syntheses; the only thing different about the briefing is that it's
# ONE long synthesis. So speak() splits a long utterance into sentence-level
# pieces, each its own short text/flush on the SAME connection (so it keeps the
# identical speaker/pace/temperature config, re-applied on any reconnect), and
# streams them back-to-back as one continuous audio stream (sentence-boundary
# gaps read as natural pauses). Conversational replies are already short -> one
# piece -> unchanged. Env-tunable; keep it comfortably above a normal reply's
# length so ordinary turns are never chunked.
_MAX_SYNTH_CHARS = int(os.environ.get("SARVAM_TTS_MAX_SYNTH_CHARS", "180"))
# Split on sentence enders, including the Devanagari danda (।).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[।.!?…])\s+")
# 100ms of PCM16/16kHz silence — sent while the caller's mic is gated (the
# frontend only transmits during "listening") so Sarvam doesn't idle-close.
_SILENCE_CHUNK = b"\x00" * (_SAMPLE_RATE_IN // 10 * 2)


class SarvamCredentialsError(RuntimeError):
    """Raised when SARVAM_API_KEY is missing."""


class SarvamTTSError(RuntimeError):
    """Raised when Bulbul synthesis fails for one utterance."""


def _split_for_synthesis(text: str) -> list:
    """Split a long utterance into <= _MAX_SYNTH_CHARS sentence-level pieces so
    no single Bulbul synthesis is long enough to drift off the configured
    voice. Short text returns [text] (or [] if empty) -- ordinary conversational
    replies are never chunked. Sentence boundaries are preferred; a single
    sentence longer than the cap is hard-split so a piece is never oversized."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= _MAX_SYNTH_CHARS:
        return [text]
    pieces: list = []
    cur = ""
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        s = sentence.strip()
        if not s:
            continue
        if cur and len(cur) + 1 + len(s) > _MAX_SYNTH_CHARS:
            pieces.append(cur)
            cur = ""
        cur = f"{cur} {s}".strip() if cur else s
        while len(cur) > _MAX_SYNTH_CHARS:  # a lone over-long sentence
            pieces.append(cur[:_MAX_SYNTH_CHARS].strip())
            cur = cur[_MAX_SYNTH_CHARS:].strip()
    if cur:
        pieces.append(cur)
    return pieces


def require_api_key() -> str:
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise SarvamCredentialsError(
            "SARVAM_API_KEY is not set. The Hindi voice dispatcher needs a Sarvam AI "
            "subscription key for Saaras (speech-to-text) and Bulbul (text-to-speech)."
        )
    return key


async def _ws_connect(url: str):
    headers = {"api-subscription-key": require_api_key()}
    try:
        # websockets >= 13 (new asyncio implementation)
        return await websockets.connect(url, additional_headers=headers, max_size=None)
    except TypeError:
        # older websockets fall back to the legacy client's kwarg name
        return await websockets.connect(url, extra_headers=headers, max_size=None)


class SaarasStream:
    """One logical Saaras v3 STT stream for a whole dispatcher call.

    Feed raw PCM16/16kHz/mono with send_audio(); consume normalized events via
    get_event(timeout):
        {"kind": "speech_start"} / {"kind": "speech_end"}   — server-side VAD
        {"kind": "transcript", "text": str}                 — FINAL utterance text
        {"kind": "failed", "message": str}                  — gave up reconnecting

    The underlying WebSocket reconnects automatically (up to
    _STT_RECONNECT_ATTEMPTS consecutive failures); in-flight audio during a
    drop is lost, which the dispatcher handles conversationally (the operator
    asks the caller to repeat) rather than pretending otherwise.
    """

    def __init__(self, language_code: str = "hi-IN"):
        self._language = language_code
        self._ws = None
        self._reader_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._events: "asyncio.Queue[dict]" = asyncio.Queue()
        self._last_send = 0.0
        self._consecutive_failures = 0
        self._closed = False

    def _url(self) -> str:
        params = {
            "language-code": self._language,  # hyphenated — verified in the official SDK
            "model": STT_MODEL,
            "mode": "transcribe",
            "sample_rate": str(_SAMPLE_RATE_IN),
            "vad_signals": "true",
        }
        if STT_INTERRUPT_MIN_SPEECH_FRAMES:
            params["interrupt_min_speech_frames"] = STT_INTERRUPT_MIN_SPEECH_FRAMES
        return _STT_WS_URL + "?" + urllib.parse.urlencode(params)

    async def connect(self) -> None:
        if self._closed:
            raise RuntimeError("SaarasStream is closed")
        if self._ws is not None:
            return
        self._ws = await _ws_connect(self._url())
        self._reader_task = asyncio.create_task(self._reader(self._ws))
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive())
        logger.info("Saaras STT connected (model=%s, language=%s)", STT_MODEL, self._language)

    async def _reader(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                data = msg.get("data") or {}
                if mtype == "data":
                    text = (data.get("transcript") or "").strip()
                    if text:
                        await self._events.put({"kind": "transcript", "text": text})
                elif mtype == "events":
                    signal = data.get("signal_type")
                    if signal == "START_SPEECH":
                        await self._events.put({"kind": "speech_start"})
                    elif signal == "END_SPEECH":
                        await self._events.put({"kind": "speech_end"})
                elif mtype == "error":
                    logger.error("Saaras STT server error: %s", data)
        except Exception:
            logger.warning("Saaras STT reader ended (connection lost)", exc_info=True)
        finally:
            if self._ws is ws:
                self._ws = None  # send_audio() will reconnect on the next chunk

    async def _keepalive(self) -> None:
        while not self._closed:
            await asyncio.sleep(_STT_KEEPALIVE_IDLE_S)
            if self._ws is not None and time.monotonic() - self._last_send > _STT_KEEPALIVE_IDLE_S:
                try:
                    await self._send_chunk(self._ws, _SILENCE_CHUNK)
                except Exception:
                    logger.debug("Saaras keepalive send failed (reconnect on next audio)")

    async def _send_chunk(self, ws, chunk: bytes) -> None:
        payload = {
            "audio": {
                "data": base64.b64encode(chunk).decode("ascii"),
                "encoding": "audio/wav",
                "sample_rate": _SAMPLE_RATE_IN,
            }
        }
        await ws.send(json.dumps(payload))
        self._last_send = time.monotonic()

    async def send_audio(self, chunk: bytes) -> None:
        """Forward one PCM chunk, transparently (re)connecting as needed."""
        if self._closed or not chunk:
            return
        for _ in range(2):  # current socket, then one fresh reconnect
            try:
                if self._ws is None:
                    await self.connect()
                await self._send_chunk(self._ws, chunk)
                self._consecutive_failures = 0
                return
            except Exception:
                self._ws = None
                self._consecutive_failures += 1
                if self._consecutive_failures >= _STT_RECONNECT_ATTEMPTS:
                    logger.error("Saaras STT failed %d times in a row — giving up",
                                 self._consecutive_failures)
                    self._consecutive_failures = 0
                    await self._events.put({
                        "kind": "failed",
                        "message": "Speech recognition connection failed repeatedly.",
                    })
                    return
                await asyncio.sleep(0.3)

    async def get_event(self, timeout: float) -> Optional[dict]:
        """Next STT event, or None after `timeout` seconds of nothing."""
        try:
            return await asyncio.wait_for(self._events.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def drain_events(self) -> None:
        """Drop anything buffered (e.g. echo picked up before the mic gate closed)."""
        while not self._events.empty():
            self._events.get_nowait()

    async def close(self) -> None:
        self._closed = True
        for task in (self._reader_task, self._keepalive_task):
            if task:
                task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


class BulbulStream:
    """One logical Bulbul v3 TTS connection for a whole dispatcher call.

    speak(text) is an async generator of raw PCM16/24kHz/mono chunks (linear16,
    ready to forward straight to the browser as binary frames). Turns are
    strictly sequential in the dispatcher, so reading the socket inline here is
    safe. On any failure it raises SarvamTTSError — the caller falls back to
    showing the text (per spec) rather than leaving the caller in silence.
    """

    def __init__(self, language_code: str = "hi-IN"):
        self._language = language_code
        self._ws = None
        self._ping_task: Optional[asyncio.Task] = None
        self._closed = False
        # Guards concurrent connect attempts: the dispatcher pre-connects in
        # parallel with Gemini reasoning (see dispatcher_hindi._agent_turn),
        # and speak() may race it -- without the lock both would open a socket
        # and one would leak.
        self._connect_lock = asyncio.Lock()

    def _url(self) -> str:
        return _TTS_WS_URL + "?" + urllib.parse.urlencode(
            {"model": TTS_MODEL, "send_completion_event": "true"}
        )

    async def ensure_open(self) -> None:
        """Idempotent connect. Called lazily by speak(), and eagerly by the
        dispatcher while Gemini is still reasoning, so the TLS+config
        handshake overlaps thinking time instead of adding to time-to-first-
        audio on the turn's critical path (matters on the first turn of a
        call and after cancel_current() tore the socket down on a barge-in)."""
        if self._closed:
            raise SarvamTTSError("BulbulStream is closed")
        async with self._connect_lock:
            if self._ws is None:
                await self._connect()

    async def _connect(self) -> None:
        self._ws = await _ws_connect(self._url())
        config = {
            "type": "config",
            "data": {
                "target_language_code": self._language,
                "speaker": TTS_SPEAKER,
                "model": TTS_MODEL,
                "pace": TTS_PACE,
                "temperature": TTS_TEMPERATURE,
                "min_buffer_size": TTS_MIN_BUFFER_CHARS,
                "max_chunk_length": TTS_MAX_CHUNK_CHARS,
                "speech_sample_rate": str(TTS_SAMPLE_RATE),
                # Raw PCM16 — decoded client-side by the existing Int16Array
                # playback path (no container, no compression).
                "output_audio_codec": "linear16",
            },
        }
        await self._ws.send(json.dumps(config))
        if self._ping_task is None:
            self._ping_task = asyncio.create_task(self._pinger())
        logger.info("Bulbul TTS connected (model=%s, speaker=%s)", TTS_MODEL, TTS_SPEAKER)

    async def _pinger(self) -> None:
        while not self._closed:
            await asyncio.sleep(_TTS_PING_INTERVAL_S)
            if self._ws is not None:
                try:
                    await self._ws.send(json.dumps({"type": "ping"}))
                except Exception:
                    logger.debug("Bulbul ping failed (reconnect on next speak)")

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        """Synthesize one utterance, yielding PCM16/24kHz chunks as they arrive.

        A LONG utterance (the closing briefing) is split into sentence-level
        pieces (see _split_for_synthesis) and synthesized as several short
        text/flush cycles on the SAME config'd connection, streamed back-to-back
        as one continuous audio stream. This keeps the entire utterance on the
        identical `speaker`/pace/temperature config (a single long synthesis was
        drifting off it partway) while matching the many-short-syntheses shape
        that multi-turn conversations already use with a consistent voice. Short
        utterances are one piece -- unchanged."""
        if self._closed:
            raise SarvamTTSError("BulbulStream is closed")
        pieces = _split_for_synthesis(text)
        if len(pieces) > 1:
            logger.info(
                "Bulbul: splitting a %d-char utterance into %d syntheses (voice consistency)",
                len(text), len(pieces),
            )
        for piece in pieces:
            async for chunk in self._speak_one(piece):
                yield chunk

    async def _speak_one(self, text: str) -> AsyncIterator[bytes]:
        """One Bulbul text/flush synthesis on the current connection (reconnects
        + re-sends config once if the socket is stale). speak() calls this once
        per piece so the config (speaker=shubh + all params) always applies."""
        if self._closed:
            raise SarvamTTSError("BulbulStream is closed")
        last_error: Optional[Exception] = None
        for attempt in range(2):  # existing socket, then one fresh connection
            try:
                await self.ensure_open()
                await self._ws.send(json.dumps({"type": "text", "data": {"text": text}}))
                await self._ws.send(json.dumps({"type": "flush"}))
                break
            except Exception as e:
                last_error = e
                await self._teardown_ws()
                if attempt == 1:
                    raise SarvamTTSError(f"Could not reach Bulbul TTS: {e}") from e
        try:
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=20.0)
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mtype = msg.get("type")
                data = msg.get("data") or {}
                if mtype == "audio":
                    chunk = base64.b64decode(data.get("audio") or "")
                    # linear16 chunks are raw PCM; strip a WAV header defensively
                    # if the server ever frames one (Sarvam's REST path does).
                    if chunk[:4] == b"RIFF":
                        chunk = chunk[44:]
                    if chunk:
                        yield chunk
                elif mtype == "event" and data.get("event_type") == "final":
                    return
                elif mtype == "error":
                    raise SarvamTTSError(f"Bulbul error: {data.get('message')}")
        except SarvamTTSError:
            await self._teardown_ws()
            raise
        except Exception as e:
            await self._teardown_ws()
            raise SarvamTTSError(f"Bulbul stream failed mid-utterance: {e}") from e

    async def _teardown_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def cancel_current(self) -> None:
        """Abort whatever Bulbul is still synthesizing (e.g. a caller
        barge-in cut the reply short). The protocol has no explicit "stop"
        message, so closing the connection is the clean way to discard
        in-flight audio -- the next speak() call opens a fresh one."""
        await self._teardown_ws()

    async def close(self) -> None:
        self._closed = True
        if self._ping_task:
            self._ping_task.cancel()
        await self._teardown_ws()
