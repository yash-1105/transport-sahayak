"""elevenlabs_speech.py — ElevenLabs Eleven v3 streaming TTS, for the ASSAMESE
voice dispatcher ONLY.

Why this exists: Sarvam Bulbul does not have `as-IN` enabled on this account
(returns "request beta access to as-IN"), and Assamese is only available on
ElevenLabs' `eleven_v3` model. So Assamese TTS is served here instead of
sarvam_speech.BulbulStream. Everything else in the Assamese pipeline is the Hindi
pipeline: STT is still Sarvam Saaras (`as-IN`, which DOES work — verified live), and
reasoning is the shared Sarvam backend. English (Gemini Live) and Hindi (Saaras +
Bulbul) are untouched by this module.

`ElevenLabsV3Stream` deliberately mirrors `BulbulStream`'s public surface exactly —
`ensure_open()` / `speak(text, force_split=False) -> AsyncIterator[bytes]` /
`cancel_current()` / `close()`, yielding raw PCM16 mono @ 24 kHz — so the Assamese
session reuses the Hindi session's speak/barge-in/pipeline code verbatim; only the
`self._tts` object differs. On any failure it raises `ElevenLabsTTSError`, which the
session catches and surfaces as the on-screen `tts_text` bubble (same graceful
degradation Bulbul failures already use).

Verified live (2026-08): `eleven_v3` streams Assamese with time-to-first-byte ~1.1s
and keeps ahead of real-time playback; `output_format=pcm_24000` is raw PCM16 mono @
24 kHz, matching the frontend's PLAYBACK_SAMPLE_RATE (no new decode path) and the
Exotel adapter's 24k->8k resample. The ElevenLabs key on this account has
text_to_speech permission but NOT speech_to_text (Scribe), which is the other reason
STT stays on Saaras.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger("elevenlabs_speech")

TTS_SAMPLE_RATE = 24000  # PCM16 mono @ 24 kHz — same as Bulbul/Gemini-Live playback

_API_BASE = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io").rstrip("/")
_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_v3")
# Default voice: a premade ElevenLabs voice (male — matches the Assamese prompt's
# masculine self-reference, same as Hindi's shubh). eleven_v3 applies Assamese
# pronunciation from the text; the voice is timbre only. MUST be ear-verified and
# ideally swapped for a purpose-picked Assamese/Indian voice via ELEVENLABS_VOICE_ID_AS.
_DEFAULT_VOICE = os.environ.get("ELEVENLABS_VOICE_ID_AS", "pNInz6obpgDQGcFmaJgB")
_OUTPUT_FORMAT = os.environ.get("ELEVENLABS_OUTPUT_FORMAT", "pcm_24000")
_REQUEST_TIMEOUT_S = float(os.environ.get("ELEVENLABS_TTS_TIMEOUT_S", "30"))


class ElevenLabsTTSError(RuntimeError):
    """Any ElevenLabs TTS failure — the dispatcher catches this and falls back to
    the tts_text on-screen bubble (the same fallback Bulbul uses)."""


def _api_key() -> str:
    k = os.environ.get("ELEVENLABS_API_KEY")
    if not k:
        raise ElevenLabsTTSError(
            "ELEVENLABS_API_KEY is not set. The Assamese voice dispatcher needs an "
            "ElevenLabs key with text_to_speech permission (eleven_v3 / Assamese)."
        )
    return k


class ElevenLabsV3Stream:
    """Streaming Eleven v3 TTS bridge, drop-in for BulbulStream in the Assamese
    session. One instance per call. Not shared across calls."""

    def __init__(self, language_code: str = "as-IN", voice_id: Optional[str] = None):
        self._language = language_code            # informational; v3 infers language from text
        self._voice = voice_id or _DEFAULT_VOICE
        self._client: Optional[httpx.AsyncClient] = None
        self._closed = False
        self._cancel = asyncio.Event()            # set by cancel_current() to abort an in-flight stream

    async def ensure_open(self) -> None:
        """Warm the HTTP client (connection pool / TLS) so the first speak()'s
        time-to-first-audio isn't paying setup cost. Mirrors BulbulStream.ensure_open;
        there is no persistent socket — Eleven v3 streams over one HTTP request per
        utterance."""
        if self._closed:
            raise ElevenLabsTTSError("ElevenLabsV3Stream is closed")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)

    async def speak(self, text: str, force_split: bool = False) -> AsyncIterator[bytes]:
        """Synthesize one utterance via eleven_v3, yielding raw PCM16/24kHz chunks as
        they stream in. force_split is accepted for interface-compatibility with
        BulbulStream but ignored: eleven_v3 has no per-connection voice-drift issue
        (the reason Bulbul splits), so one streamed request per utterance is correct
        and gives the lowest time-to-first-audio."""
        if self._closed:
            raise ElevenLabsTTSError("ElevenLabsV3Stream is closed")
        text = (text or "").strip()
        if not text:
            return
        self._cancel.clear()
        await self.ensure_open()
        url = f"{_API_BASE}/v1/text-to-speech/{self._voice}/stream?output_format={_OUTPUT_FORMAT}"
        body = {"text": text, "model_id": _MODEL}
        headers = {"xi-api-key": _api_key(), "Content-Type": "application/json"}
        try:
            async with self._client.stream("POST", url, json=body, headers=headers) as r:
                if r.status_code != 200:
                    detail = (await r.aread())[:200]
                    raise ElevenLabsTTSError(f"Eleven v3 HTTP {r.status_code}: {detail!r}")
                # SAMPLE ALIGNMENT (critical — this was the "loud static" bug):
                # `output_format=pcm_24000` is raw headerless PCM16 mono @ 24 kHz,
                # but ElevenLabs' HTTP stream emits arbitrary TCP-sized chunks and
                # ~15% of them land on an ODD byte length, i.e. they split a 16-bit
                # sample across two chunks. The browser (useVoiceDispatcher.ts's
                # playChunk) converts EACH websocket frame INDEPENDENTLY via
                # `new Int16Array(frame)`, which both requires an even byteLength
                # and assumes the frame starts on a sample boundary — so forwarding
                # a raw odd chunk drops that frame AND byte-shifts every sample in
                # the frames that follow, which is exactly the loud static/noise
                # reported on the as-IN bot. Bulbul/Gemini-Live never hit this
                # (their frames are already sample-aligned). Fix: buffer a single
                # trailing odd byte and prepend it to the next chunk, so every
                # frame we yield is an even number of whole PCM16 samples.
                carry = b""
                async for chunk in r.aiter_bytes():
                    if self._cancel.is_set():
                        # Barge-in / teardown: stop pulling audio and close the stream.
                        return
                    if not chunk:
                        continue
                    buf = carry + chunk
                    if len(buf) & 1:
                        carry = buf[-1:]
                        buf = buf[:-1]
                    else:
                        carry = b""
                    if buf:
                        yield buf
                # A leftover carry byte means the whole stream was odd-length,
                # which never happens for valid PCM16 — drop it rather than emit a
                # misaligned 1-byte tail.
                if carry and not self._cancel.is_set():
                    logger.warning("ElevenLabs stream ended on an odd byte — dropped 1 trailing byte")
        except ElevenLabsTTSError:
            raise
        except Exception as e:
            raise ElevenLabsTTSError(f"Eleven v3 stream failed mid-utterance: {e}") from e

    async def cancel_current(self) -> None:
        """Abort whatever is still synthesizing (caller barge-in cut the reply
        short). Sets the cancel flag; the in-flight speak() loop breaks and closes
        its HTTP stream. Mirrors BulbulStream.cancel_current."""
        self._cancel.set()

    async def close(self) -> None:
        self._closed = True
        self._cancel.set()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
