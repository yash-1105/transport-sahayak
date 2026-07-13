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
import io
import logging
import os
import re
import time
import wave
from typing import Optional

from google.cloud import texttospeech
from google.genai import types
from google.oauth2 import service_account

from .dispatch_briefing import _CLOSING_EN, _facility_location, _responder_facts_en, _service, select_sops
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


# The 10 mandatory sections, in the exact required order:
#   1. Report submission confirmation
#   2-6. Ambulance / fire / towing / trauma centre / police (each ALWAYS
#        present -- see _responder_facts_en, which now says "currently
#        unavailable" per-service rather than ever omitting one)
#   7. SOP instructions
#   8. Two-hour follow-up call promise
#   9. Callback-if-not-received instruction
#   10. Polite close
# _CONFIRMATION_EN is section 1; _responder_facts_en is 2-6; select_sops is
# 7; _CLOSING_EN is 8-9-10 (see that constant's own comment for why it was
# trimmed to exactly these three lines).
_CONFIRMATION_EN = "Your report has been registered successfully."


def _fallback_script(state, services: Optional[dict]) -> str:
    """Deterministic, plain-language fallback -- used both when Gemini
    Flash fails/times out/returns nothing usable, AND when Flash's output
    is missing a required section (see _script_covers_all_sections).
    Concatenates the SAME facts/SOPs/closing content the prompt below asks
    Flash to narrate, in the mandatory 10-section order, guaranteeing every
    section is present verbatim -- never partially patched, always either
    this exact deterministic script or a Flash version verified to cover
    everything."""
    facts = _responder_facts_en(services)  # always exactly 5 lines, never empty -- see its own docstring
    sop_lines = [s["en"] for s in select_sops(state)]
    lines = [_CONFIRMATION_EN] + facts + sop_lines + _CLOSING_EN
    return " ".join(lines)


_SOP_ANCHORS = {
    "bleeding": "pressure",
    "fire": "away from the vehicle",
    "unconscious": "head and neck",
    "trapped": "crushed doors",
    "hazmat": "chemicals",
    "general": "safe distance",
}


def _required_anchors(state, services: Optional[dict]) -> list:
    """Required-substring GROUPS -- one group per mandatory section, in the
    same order as _fallback_script. A section counts as covered only if
    EVERY substring in its own group appears somewhere in the script
    (case-insensitive). Used by _script_covers_all_sections to verify
    Gemini Flash actually kept every section rather than silently dropping
    one; see generate_dispatch_script for what happens if any is missing.
    Deliberately checks content PRESENCE, not exact wording -- Flash is
    allowed (expected) to rephrase, just never to omit.

    Groups, not flat strings, specifically because of the unavailable-
    service case: a single generic "unavailable" substring, used as one
    FLAT anchor per missing service, would be satisfied by Flash mentioning
    it just ONCE across the whole script even if 4 of 5 services were
    unavailable and only 1 was actually named -- the substring "unavailable"
    doesn't care how many times it needs to match. Pairing it with each
    service's own label (["ambulance", "unavailable"], ["fire",
    "unavailable"], ...) means every unavailable service must be named
    AND called out as unavailable, not just any one of them."""
    groups = [["regist"]]  # section 1: "registered"/"registration"
    service_labels = {
        "ambulance": "ambulance", "fire": "fire", "towing": "towing",
        "hospital": "trauma", "police": "police",
    }
    for key, label in service_labels.items():
        entry = _service(services, key)
        if entry:
            # The facility location is the one thing Flash is explicitly
            # told to keep verbatim, so it's a reliable single-string
            # anchor for "this section wasn't dropped".
            groups.append([_facility_location(entry["name"]).lower()])
        else:
            groups.append([label, "unavailable"])
    for sop in select_sops(state):
        groups.append([_SOP_ANCHORS.get(sop["key"], sop["en"][:20].lower())])
    groups.append(["two hours"])   # section 8
    groups.append(["helpline"])    # section 9 (callback-if-missed mentions calling this helpline again)
    return groups


def _script_covers_all_sections(script: str, anchor_groups: list) -> tuple:
    """(True, []) if every anchor group is fully covered (every substring
    in that group appears, case-insensitive) in script; otherwise
    (False, [uncovered groups]) -- the caller discards Flash's entire
    output on ANY miss rather than trying to patch it (a partially-AI-
    edited, partially-code-patched script risks an awkward or duplicated
    result; the fully deterministic fallback is always complete and
    already vetted, so there is never a reason to mix)."""
    lowered = script.lower()
    missing = [group for group in anchor_groups if not all(s in lowered for s in group if s)]
    return (not missing, missing)


def _build_flash_prompt(state, services: Optional[dict]) -> str:
    """Reuses the exact same deterministic facts/SOPs/closing content this
    project already trusts (dispatch_briefing.py) -- Flash's only job is to
    turn it into ONE natural-sounding spoken script, never to decide WHAT to
    say, WHETHER to include a section, or invent a number/name of its own
    (same rule-first, LLM-phrases-never-decides pattern as the rest of this
    project). generate_dispatch_script additionally VERIFIES the output
    actually kept every section (see _script_covers_all_sections) rather
    than trusting this prompt alone -- prompts are a strong nudge, not a
    guarantee, so Flash is never the sole enforcement mechanism here."""
    facts = _responder_facts_en(services)  # always exactly 5 lines, never empty
    sop_lines = [s["en"] for s in select_sops(state)]

    return (
        "You are writing the closing script for an emergency dispatcher phone call, to be read aloud "
        "by a text-to-speech voice. Write ONE calm, warm, natural-sounding spoken script -- like a "
        "real, caring human emergency dispatcher, never an upbeat customer-service tone -- as plain "
        "prose sentences. No markdown, no headings, no bullet points, no numbered lists: just the "
        "words the voice should say, in order, in natural paragraphs.\n\n"
        "The script has FOUR mandatory parts, ALL of them required, in this exact order:\n\n"
        "PART 1 -- Begin with ONE short sentence confirming the report was submitted successfully. "
        "Use this exact idea (your own natural words are fine, but do not skip this part):\n"
        f"- {_CONFIRMATION_EN}\n\n"
        "PART 2 -- Then announce ALL 5 of these responding-service facts, every single one below, "
        "using these exact names and numbers, word for word -- never invent, round differently, "
        "omit, or change any name or number, and never skip one even if it says details are "
        "currently unavailable (say that plainly if so -- never invent a name or time instead). "
        "Every time is an estimate and must sound like one (\"estimated\", \"approximately\"), and "
        "every service is described as NOTIFIED / responding from its location — never as "
        "\"dispatched and tracked\" (this system tracks no vehicle):\n"
        + "\n".join(f"- {f}" for f in facts)
        + "\n\nPART 3 -- Then give the caller ALL of these safety instructions, in this order, in "
        "your own natural words while keeping the exact meaning of each one — do not skip any:\n"
        + "\n".join(f"- {line}" for line in sop_lines)
        + "\n\nPART 4 -- Then close the call with ALL of these exact points, in this order, none "
        "skipped or merged away — this includes the instruction about calling back if the caller "
        "does NOT receive the follow-up call, which is easy to accidentally leave out but must be "
        "said:\n"
        + "\n".join(f"- {line}" for line in _CLOSING_EN)
        + "\n\nBefore finishing, double-check silently: does your script include Part 1, all 5 items "
        "from Part 2, every item from Part 3, and all of Part 4? If you find yourself about to skip "
        "or merge away any one of them, go back and include it.\n\n"
        "Return ONLY the spoken script itself — no preamble, no explanation, no label like "
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
    silence) if Flash fails, times out, returns nothing usable, OR returns
    text missing a required section (verified via
    _script_covers_all_sections -- real reported bug: the spoken briefing
    was sometimes incomplete because Flash was trusted to include every
    section on its own judgment; it is now verified, never merely asked).
    The ENTIRE body (including building the prompt) is inside the try block --
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
            anchors = _required_anchors(state, services)
            covered, missing = _script_covers_all_sections(text, anchors)
            if covered:
                return text
            # Do NOT patch/merge -- discard Flash's output entirely and use
            # the fully deterministic script instead (see
            # _script_covers_all_sections' docstring for why never mix).
            logger.warning("Gemini Flash's script is missing required section(s) %s after "
                           "%.2fs -- discarding it and using the deterministic fallback script "
                           "instead of a partially-complete briefing", missing, time.monotonic() - t0)
        else:
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


def _extract_pcm_from_wav(wav_bytes: bytes) -> bytes:
    """Parses a WAV container via the stdlib `wave` module (robust to minor
    header/chunk variations, unlike a hand-rolled fixed-offset strip) and
    returns the raw sample bytes. Raises ValueError if the audio isn't the
    exact mono/16-bit/24kHz shape the frontend's playback path expects --
    better to fail loudly here (falling back to on-screen text) than to
    hand the browser audio it will render as static/noise, which is
    exactly the real reported bug this replaces."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels, sampwidth, framerate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        if channels != 1 or sampwidth != 2 or framerate != _TTS_SAMPLE_RATE_HZ:
            raise ValueError(
                f"Unexpected WAV format from Google TTS: channels={channels}, "
                f"sample_width_bytes={sampwidth}, frame_rate={framerate} "
                f"(expected mono, 16-bit, {_TTS_SAMPLE_RATE_HZ}Hz)"
            )
        return wf.readframes(wf.getnframes())


async def synthesize_speech(text: str) -> bytes:
    """Google Cloud Text-to-Speech — one batch synthesis call, returns raw
    HEADERLESS PCM16/24kHz mono bytes ready for useVoiceDispatcher.ts's
    existing Gemini-Live-built playback path (no new audio-decoding logic
    needed there).

    Uses AudioEncoding.LINEAR16, not PCM. LINEAR16 is decades-established
    and universally supported across every Google Cloud TTS API surface;
    PCM is newer and was tried first here on the strength of its own proto
    docstring's claim ("audio won't be wrapped in a WAV header"), but a
    real live call produced loud static with no intelligible speech --
    the classic symptom of the frontend's raw-Int16Array playback path
    misinterpreting bytes that are NOT actually headerless 16-bit PCM
    samples (a WAV-wrapped response, a different encoding entirely, or
    some other mismatch — the docstring's claim did not hold up against
    the real API for this project). LINEAR16's behavior (always WAV-
    wrapped) is unambiguous and well documented, so its header is now
    explicitly parsed and stripped via the stdlib `wave` module
    (_extract_pcm_from_wav) rather than assumed away — parsing real chunk
    boundaries is robust to minor header variations in a way a fixed
    44-byte offset strip would not be.

    Raises EnglishTTSError on ANY failure -- including client construction,
    SSML building, and WAV parsing, which an earlier version left OUTSIDE
    (or entirely without) their own error handling, so a credentials/
    library/format exception there would have escaped as some other
    exception type entirely, past both this function's own handling and
    the caller's `except EnglishTTSError` -- so the caller MUST wrap this
    call in its own broad exception handler too; do not assume this
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
                    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                    sample_rate_hertz=_TTS_SAMPLE_RATE_HZ,
                    speaking_rate=_TTS_SPEAKING_RATE,
                    pitch=_TTS_PITCH,
                ),
            ),
            timeout=_TTS_TIMEOUT_S,
        )
        wav_bytes = response.audio_content
        if not wav_bytes:
            raise ValueError("Google TTS returned no audio content")
        audio = _extract_pcm_from_wav(wav_bytes)
    except EnglishTTSError:
        raise
    except Exception as e:
        # Full traceback, then convert to EnglishTTSError -- this is the
        # ONLY exception type the caller is allowed to assume it will ever
        # see from this function. Covers the TTS request itself, an empty
        # response, AND a malformed/unexpected WAV payload -- all three
        # must fall back to on-screen text, never hand the browser bytes
        # it can't safely play.
        logger.exception("Google TTS request/parsing failed after %.2fs", time.monotonic() - t0)
        raise EnglishTTSError(str(e)) from e
    if not audio:
        logger.error("Google TTS's WAV contained no audio frames after %.2fs", time.monotonic() - t0)
        raise EnglishTTSError("Google TTS returned a WAV file with no audio frames")
    duration_s = len(audio) / 2 / _TTS_SAMPLE_RATE_HZ  # 16-bit mono PCM -> 2 bytes/sample
    logger.info("========================\n"
                "Stage 8\n"
                "Google TTS completed\n"
                "Latency: %.2fs\n"
                "Audio bytes: %d\n"
                "Duration: %.2fs\n"
                "========================", time.monotonic() - t0, len(audio), duration_s)
    return audio
