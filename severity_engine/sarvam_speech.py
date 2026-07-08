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
# 1.3: verified live against the real API that pace is a real, functioning
# parameter (confirmed at extremes and small increments at low temperature,
# where stochastic variance between calls doesn't swamp the effect). Bumped
# again from 1.15 per user feedback that speech was still too slow --
# "shubh" also has a naturally brisker baseline cadence than "priya" did at
# the same pace value (measured live: ~35% shorter audio for identical text).
TTS_PACE = float(os.environ.get("SARVAM_TTS_PACE", "1.3"))
# Real, documented Bulbul v3 config fields (not fabricated) -- v3 does NOT
# support pitch/loudness/SSML, so those are deliberately not offered here.
# 0.7: a modest bump from the SDK's own 0.6 default -- per Sarvam's own
# parameter semantics, temperature governs expressiveness/naturalness for v3,
# and per user feedback that the previous setting sounded robotic/flat.
# min_buffer_size/max_chunk_length control how much text Bulbul buffers
# before it starts streaming audio back. A previous iteration lowered these
# (30/90) purely to shave time-to-first-audio-chunk when LATENCY was the
# priority; restored to Sarvam's own documented defaults (50/150) now that
# per-turn latency is no longer the dominant complaint and prosody continuity
# (fewer, larger synthesis segments = less per-segment "reset", a plausible
# contributor to a choppy/robotic-sounding cadence) matters more.
TTS_TEMPERATURE = float(os.environ.get("SARVAM_TTS_TEMPERATURE", "0.7"))
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
# 100ms of PCM16/16kHz silence — sent while the caller's mic is gated (the
# frontend only transmits during "listening") so Sarvam doesn't idle-close.
_SILENCE_CHUNK = b"\x00" * (_SAMPLE_RATE_IN // 10 * 2)


class SarvamCredentialsError(RuntimeError):
    """Raised when SARVAM_API_KEY is missing."""


class SarvamTTSError(RuntimeError):
    """Raised when Bulbul synthesis fails for one utterance."""


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

    def _url(self) -> str:
        return _TTS_WS_URL + "?" + urllib.parse.urlencode(
            {"model": TTS_MODEL, "send_completion_event": "true"}
        )

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
        """Synthesize one utterance, yielding PCM16/24kHz chunks as they arrive."""
        if self._closed:
            raise SarvamTTSError("BulbulStream is closed")
        last_error: Optional[Exception] = None
        for attempt in range(2):  # existing socket, then one fresh connection
            try:
                if self._ws is None:
                    await self._connect()
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
