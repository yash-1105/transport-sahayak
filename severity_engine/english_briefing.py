"""
english_briefing.py — the ENGLISH-ONLY post-submission dispatch-briefing pipeline:
Gemini Flash (plain generate_content, text) -> Google Cloud Text-to-Speech (audio).

Architecture (2026-07): Gemini Live's job now ends at "your report has been
submitted successfully" (see dispatcher_live.py's
_end_conversation_and_deliver_briefing). Everything after that — responder
ETAs, SOP guidance, follow-up script, closing — is generated here as plain
text by Gemini Flash (a single batch generate_content call, NOT Gemini Live),
then spoken by Google Cloud TTS (batch synthesis, not streamed per-turn)
instead of Gemini Live's own native audio. This deliberately trades away
Gemini Live's post-submission reliability history (CLAUDE.md's Rounds 1-5 —
native-audio turn generation that could stop partway through) for a delivery
mechanism that has no equivalent failure mode: a plain text-generation call
either returns a complete string or it doesn't, and a batch TTS call either
returns complete audio or it doesn't — neither can be cut off "partway
through speaking" the way a live audio-generation turn can.

Modular by design, per explicit project requirement: swapping to a different
TTS engine later (ElevenLabs, Azure, etc.) means changing ONLY
synthesize_speech's body. dispatcher_live.py calls generate_dispatch_script()
and synthesize_speech() as two independent functions and never touches any
Google-Cloud-TTS-specific type itself.

Deliberately takes NO import from dispatcher_live.py (the caller passes an
already-constructed Gemini client in, mirroring the exact pattern
dispatcher_hindi.py already uses for its own plain-generate_content calls) —
avoids a circular import (dispatcher_live.py imports this module) and keeps
this module's only real dependency on dispatch_briefing.py's shared,
already-tested deterministic content helpers (facts/SOPs/closing), never on
anything Gemini-Live-specific.

Hindi is entirely unaffected: dispatcher_hindi.py's own closing-briefing
delivery (Sarvam Bulbul TTS, build_briefing_instruction) does not import
anything from this module and is untouched by this change.
"""
import asyncio
import logging
import os
import re
import time
from typing import Optional

from google.cloud import texttospeech
from google.genai import types
from google.oauth2 import service_account

from .dispatch_briefing import _CLOSING_EN, _responder_facts_en, select_sops
from .google_credentials import load_service_account_info

logger = logging.getLogger("english_briefing")

# ── Gemini Flash (script generation) ──────────────────────────────────────────
_FLASH_MODEL = os.environ.get("ENGLISH_BRIEFING_TEXT_MODEL", "gemini-2.5-flash")
_FLASH_TIMEOUT_S = float(os.environ.get("ENGLISH_BRIEFING_FLASH_TIMEOUT_S", "8"))
_FLASH_MAX_OUTPUT_TOKENS = int(os.environ.get("ENGLISH_BRIEFING_FLASH_MAX_TOKENS", "800"))

# ── Google Cloud Text-to-Speech ────────────────────────────────────────────────
_TTS_VOICE_LANGUAGE = os.environ.get("ENGLISH_TTS_LANGUAGE_CODE", "en-IN")
# Neural2 is a long-established, generally-available voice family with
# well-documented en-IN voice IDs -- chosen as a default that won't 400 on an
# unverified project. If this project's Google Cloud console shows Chirp3-HD
# or Studio voices enabled (newer, higher-quality tiers), switch
# ENGLISH_TTS_VOICE_NAME to one of those. NOT independently verified live —
# see synthesize_speech's docstring.
_TTS_VOICE_NAME = os.environ.get("ENGLISH_TTS_VOICE_NAME", "en-IN-Neural2-D")
_TTS_SPEAKING_RATE = float(os.environ.get("ENGLISH_TTS_SPEAKING_RATE", "1.0"))
_TTS_PITCH = float(os.environ.get("ENGLISH_TTS_PITCH", "0.0"))
# Matches useVoiceDispatcher.ts's PLAYBACK_SAMPLE_RATE exactly -- do not change
# one without the other; the frontend's raw-PCM16 playback path (built for
# Gemini Live's audio) is reused as-is for this new audio source specifically
# because the sample rate lines up.
_TTS_SAMPLE_RATE_HZ = 24000
_TTS_TIMEOUT_S = float(os.environ.get("ENGLISH_TTS_TIMEOUT_S", "10"))

_tts_client: Optional["texttospeech.TextToSpeechAsyncClient"] = None


class EnglishTTSError(RuntimeError):
    """Raised when Google Cloud TTS synthesis fails for any reason. The
    caller (dispatcher_live.py) falls back to sending the script as text —
    the SAME tts_text fallback event type Hindi's Bulbul-failure handling
    already established, so the frontend needs no new fallback path."""


def _get_tts_client() -> "texttospeech.TextToSpeechAsyncClient":
    """Lazily-initialised, cached at module scope — built once, not per
    call. Uses the SAME service-account credential source as every other
    Google Cloud client in this project (google_credentials.py), but its own
    separate credentials.Credentials object: Text-to-Speech is a classic
    Google Cloud API client (like voice_stream.py's SpeechAsyncClient), not
    the Vertex AI genai SDK Gemini Live/Flash use, so it cannot share that
    client object even though the underlying service account is identical."""
    global _tts_client
    if _tts_client is not None:
        return _tts_client
    info = load_service_account_info()
    if not info:
        raise EnglishTTSError(
            "No Google Cloud credentials found for Text-to-Speech. Set "
            "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 or GOOGLE_SERVICE_ACCOUNT_JSON."
        )
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    _tts_client = texttospeech.TextToSpeechAsyncClient(credentials=credentials)
    logger.info("Google Cloud Text-to-Speech client initialised (voice=%s)", _TTS_VOICE_NAME)
    return _tts_client


def _fallback_script(state, services: Optional[dict]) -> str:
    """Deterministic, plain-language fallback if Gemini Flash fails, times
    out, or returns nothing usable -- reuses the EXACT SAME facts/SOPs/
    closing content the prompt below asks Flash to narrate, just
    concatenated directly instead of handed to a model. Never leaves the
    caller with silence merely because Flash was unavailable."""
    facts = _responder_facts_en(services) or [
        "The emergency services have been notified and are being arranged — "
        "no estimated times are available right now."
    ]
    sops = select_sops(state)
    sop_lines = [s["en"] for s in sops]
    lines = ["Your report has been registered successfully."] + facts + sop_lines + _CLOSING_EN
    return " ".join(lines)


def _build_flash_prompt(state, services: Optional[dict]) -> str:
    """Reuses the exact same deterministic facts/SOPs/closing content this
    project already trusts (dispatch_briefing.py) -- Flash's only job is to
    turn it into ONE natural-sounding spoken script, never to decide WHAT to
    say or invent a number/name of its own (same rule-first, LLM-phrases-
    never-decides pattern as the rest of this project)."""
    facts = _responder_facts_en(services) or [
        "The emergency services have been notified and are being arranged — "
        "no estimated times are available right now."
    ]
    sops = select_sops(state)
    sop_lines = [s["en"] for s in sops]
    closing = _CLOSING_EN

    return (
        "You are writing the closing script for an emergency dispatcher phone call, to be read aloud "
        "by a text-to-speech voice. The caller's incident report was just submitted successfully. "
        "Write ONE calm, warm, natural-sounding spoken script -- like a real, caring human emergency "
        "dispatcher, never an upbeat customer-service tone -- as plain prose sentences. No markdown, "
        "no headings, no bullet points, no numbered lists: just the words the voice should say, in "
        "order, in natural paragraphs.\n\n"
        "You MUST include every one of these facts, using these exact names and numbers, word for "
        "word -- never invent, round differently, omit, or change any name or number. Every time is "
        "an estimate and must sound like one (\"estimated\", \"approximately\"), and every service is "
        "described as NOTIFIED / responding from its location — never as \"dispatched and tracked\" "
        "(this system tracks no vehicle):\n"
        + "\n".join(f"- {f}" for f in facts)
        + "\n\nThen give the caller these safety instructions, in this order, in your own natural "
        "words while keeping the exact meaning of each one — do not skip any:\n"
        + "\n".join(f"- {line}" for line in sop_lines)
        + "\n\nThen close the call with these exact points, in this order, none skipped or merged "
        "away — this includes the instruction about calling back if the caller does NOT receive the "
        "follow-up call, which is easy to accidentally leave out but must be said:\n"
        + "\n".join(f"- {line}" for line in closing)
        + "\n\nReturn ONLY the spoken script itself — no preamble, no explanation, no label like "
        "'Script:', nothing before or after the words the voice should actually say."
    )


async def generate_dispatch_script(gemini_client, state, services: Optional[dict]) -> str:
    """Gemini Flash, plain generate_content — a single batch text call, not
    a live audio turn, so it carries none of Gemini Live's native-audio
    generation-length reliability history (CLAUDE.md Rounds 1-5). Takes an
    already-constructed Vertex AI client (the SAME one Gemini Live/the
    caller already built via dispatcher_live._get_client()) rather than
    importing a client factory itself, avoiding a circular import between
    this module and dispatcher_live.py — mirrors the exact pattern
    dispatcher_hindi.py already uses for its own plain-generate_content
    calls. Falls back to a deterministic plain-language script (never
    silence) if Flash fails, times out, or returns nothing usable. The
    ENTIRE body (including building the prompt) is inside the try block --
    an earlier version built the prompt outside it, so a bug there would
    have escaped this function's own fallback entirely and propagated to
    the caller, which does not expect this function to ever raise."""
    t0 = time.monotonic()
    try:
        prompt = _build_flash_prompt(state, services)
        logger.info("========================\n"
                    "Stage 4\n"
                    "Building unified briefing\n"
                    "Characters: %d\n"
                    "========================", len(prompt))
        logger.info("========================\n"
                    "Stage 5\n"
                    "Calling Gemini Flash (model=%s)\n"
                    "========================", _FLASH_MODEL)
        response = await asyncio.wait_for(
            gemini_client.aio.models.generate_content(
                model=_FLASH_MODEL,
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    max_output_tokens=_FLASH_MAX_OUTPUT_TOKENS,
                    # No thinking needed -- the content is already fully
                    # determined by the prompt; Flash's only job is phrasing.
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            ),
            timeout=_FLASH_TIMEOUT_S,
        )
        candidate = (response.candidates or [None])[0]
        text = ""
        if candidate is not None and candidate.content is not None:
            text = " ".join(
                p.text.strip() for p in (candidate.content.parts or []) if getattr(p, "text", None)
            ).strip()
        if text:
            logger.info("========================\n"
                        "Stage 6\n"
                        "Gemini Flash completed\n"
                        "Latency: %.2fs\n"
                        "Characters returned: %d\n"
                        "========================", time.monotonic() - t0, len(text))
            return text
        logger.warning("Gemini Flash returned no usable text after %.2fs -- "
                       "using deterministic fallback script", time.monotonic() - t0)
    except Exception:
        # DO NOT swallow silently -- full traceback, then fall back to a
        # deterministic script rather than letting this propagate (the
        # caller, _end_conversation_and_deliver_briefing, does not expect
        # this function to ever raise).
        logger.exception("Gemini Flash request failed after %.2fs -- "
                         "using deterministic fallback script", time.monotonic() - t0)
    return _fallback_script(state, services)


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _to_ssml(text: str) -> str:
    """Deterministic, code-level SSML wrapping -- never asks the LLM to
    produce SSML itself (this project's rule-first pattern: the model
    decides content/phrasing, code decides structure). Inserts a natural
    pause between sentences for calmer, less rushed delivery. XML-escapes
    defensively since Flash's raw text could in principle contain a literal
    '&' (e.g. a facility name)."""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(escaped) if s.strip()]
    body = '<break time="450ms"/>'.join(sentences)
    return f"<speak>{body}</speak>"


async def synthesize_speech(text: str) -> bytes:
    """Google Cloud Text-to-Speech — one batch synthesis call, returns raw
    HEADERLESS PCM16/24kHz mono bytes. Uses AudioEncoding.PCM specifically
    (confirmed from this project's installed google-cloud-texttospeech
    source: "audio won't be wrapped in a WAV (or any other) header" — unlike
    AudioEncoding.LINEAR16, which returns a WAV file and would need its
    header stripped before the raw samples could be used). 24kHz exactly
    matches useVoiceDispatcher.ts's PLAYBACK_SAMPLE_RATE, so the existing
    frontend playback path (already built for Gemini Live's raw PCM16/24kHz
    output) needs no new audio-decoding logic for this new audio source.

    NOT independently verified against a live Google Cloud TTS API call in
    this environment (no live credentials available here) — before relying
    on this in production, verify live: the Cloud Text-to-Speech API is
    enabled for this project's service account, the chosen voice name
    exists, and AudioEncoding.PCM is accepted by the batch synthesize_speech
    RPC (as opposed to being restricted to the newer streaming_synthesize
    RPC only). If PCM is ever rejected, the documented fallback is
    LINEAR16 + stripping its WAV header via the stdlib `wave` module.

    Raises EnglishTTSError on ANY failure -- including client construction
    and SSML building, which an earlier version left OUTSIDE the try block,
    so a credentials/library exception there would have escaped as some
    other exception type entirely, past both this function's own handling
    and the caller's `except EnglishTTSError` -- so the caller MUST wrap
    this call in its own broad exception handler too; do not assume this
    docstring's promise alone is sufficient defense in depth.
    """
    t0 = time.monotonic()
    try:
        client = _get_tts_client()
        ssml = _to_ssml(text)
        logger.info("========================\n"
                    "Stage 7\n"
                    "Calling Google Cloud TTS (%d chars of SSML, voice=%s)\n"
                    "========================", len(ssml), _TTS_VOICE_NAME)
        response = await asyncio.wait_for(
            client.synthesize_speech(
                input=texttospeech.SynthesisInput(ssml=ssml),
                voice=texttospeech.VoiceSelectionParams(
                    language_code=_TTS_VOICE_LANGUAGE, name=_TTS_VOICE_NAME,
                ),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.PCM,
                    sample_rate_hertz=_TTS_SAMPLE_RATE_HZ,
                    speaking_rate=_TTS_SPEAKING_RATE,
                    pitch=_TTS_PITCH,
                ),
            ),
            timeout=_TTS_TIMEOUT_S,
        )
    except EnglishTTSError:
        raise
    except Exception as e:
        # Full traceback, then convert to EnglishTTSError -- this is the
        # ONLY exception type the caller is allowed to assume it will ever
        # see from this function.
        logger.exception("Google TTS request failed after %.2fs", time.monotonic() - t0)
        raise EnglishTTSError(str(e)) from e
    audio = response.audio_content
    if not audio:
        logger.error("Google TTS returned no audio content after %.2fs", time.monotonic() - t0)
        raise EnglishTTSError("Google TTS returned no audio content")
    duration_s = len(audio) / 2 / _TTS_SAMPLE_RATE_HZ  # 16-bit mono PCM -> 2 bytes/sample
    logger.info("========================\n"
                "Stage 8\n"
                "Google TTS completed\n"
                "Latency: %.2fs\n"
                "Audio bytes: %d\n"
                "Duration: %.2fs\n"
                "========================", time.monotonic() - t0, len(audio), duration_s)
    return audio
