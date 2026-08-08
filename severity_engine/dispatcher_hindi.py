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
import re
import time
from typing import Optional

from fastapi import WebSocket
from google import genai
from google.genai import types
from google.oauth2 import service_account

from . import local_extract
from .dispatch_briefing import build_briefing_instruction
from .dispatcher_live import (
    _DISPATCH_WAIT_S,
    _RECONNECT_APOLOGY,
    _TOOL_DECLARATIONS,
    DEFAULT_REQUIRED_FIELDS,
    REQUIRED_FIELDS,
    DispatcherSession,
)
from .google_credentials import load_service_account_info
from .helpline_tools import HELPLINE_TOOL_DECLARATIONS, HelplineToolsMixin
from .knowledge_base import describe_topics
from .sarvam_speech import (
    BulbulStream,
    SaarasStream,
    SarvamTTSError,
    TTS_SAMPLE_RATE,
    require_api_key,
)
from .sarvam_reasoning import (
    SarvamReasoningError,
    gemini_history_to_openai_messages,
    gemini_tools_to_openai,
    sarvam_generate,
)

logger = logging.getLogger("dispatcher_hindi")

# Text-reasoning model — deliberately the plain generate_content family, not a
# Live model. Runs on the existing Vertex AI credentials via dispatcher_live's
# cached client, so no new authentication of any kind. gemini-2.5-flash is the
# newest flash generation actually available on this project/region — verified
# live: gemini-2.0-flash now 404s on Vertex us-central1 here.
_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
# Change 1 — geographic colocation. Hindi's generate_content calls run against a
# region CLOSE to the caller/Sarvam (asia-south1 / Mumbai) instead of us-central1,
# removing a fixed transcontinental round-trip tax on every turn. This is a SEPARATE
# Vertex client from dispatcher_live._get_client() (which stays us-central1 for
# English's Gemini Live native-audio model — NOT available in asia-south1), so
# English is completely untouched. gemini-2.5-flash generate_content is verified
# available in asia-south1 (measured markedly faster + tighter than us-central1).
_HINDI_TEXT_LOCATION = os.environ.get("HINDI_VERTEX_LOCATION", "asia-south1")
_hindi_text_client: Optional[genai.Client] = None

# PRIMARY reasoning backend for the Hindi dispatcher. "sarvam" (default) uses
# sarvam-105b-conversations (in-region India, no Vertex quota) as primary with
# Gemini as an automatic per-turn fallback; "gemini" force-disables Sarvam (the
# Gemini path is always the fallback and stays at asia-south1). English is unaffected.
_HINDI_REASONING_BACKEND = os.environ.get("HINDI_REASONING_BACKEND", "sarvam").strip().lower()


def _get_hindi_text_client() -> genai.Client:
    """Cached Vertex client for the Hindi text-reasoning calls, pinned to
    _HINDI_TEXT_LOCATION. Self-contained (its own credential load) so it never
    perturbs the English Gemini Live client. Same service account, different
    region — no new auth."""
    global _hindi_text_client
    if _hindi_text_client is not None:
        return _hindi_text_client
    info = load_service_account_info()
    if not info:
        raise RuntimeError(
            "No Google Cloud credentials found for the Hindi text dispatcher. Set "
            "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64 or GOOGLE_SERVICE_ACCOUNT_JSON, or "
            "place the local service account file (local dev)."
        )
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or info.get("project_id")
    if not project_id:
        raise RuntimeError("Service account JSON has no project_id field.")
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    _hindi_text_client = genai.Client(
        vertexai=True, project=project_id, location=_HINDI_TEXT_LOCATION, credentials=credentials
    )
    logger.info("Hindi Gemini text client initialised (project=%s, model=%s, location=%s)",
                project_id, _TEXT_MODEL, _HINDI_TEXT_LOCATION)
    return _hindi_text_client
# Tighter than a typical chat timeout on purpose -- this is a live phone call,
# not a background job; a slow attempt should fail fast into the retry/apology
# path rather than leave the caller stuck on "Thinking..." for many seconds.
# Lowered 12 -> 7 (2026-07) after "sometimes gets stuck on thinking" reports: a
# normal conversational turn at thinking_budget=0 completes in ~1-3s, so 7s is
# ample headroom, and capping it means a hung/slow Vertex call aborts into the
# single retry quickly instead of hanging the caller. The closing briefing is a
# genuinely longer single generation (up to _BRIEFING_MAX_OUTPUT_TOKENS) and
# keeps its own, longer timeout below so it is never cut off.
_GEMINI_TIMEOUT_S = float(os.environ.get("GEMINI_TEXT_TIMEOUT_S", "7"))
_BRIEFING_TIMEOUT_S = float(os.environ.get("GEMINI_BRIEFING_TIMEOUT_S", "15"))
# Each round is a full network round-trip to Vertex. The prompt now demands
# ALL of a turn's tool calls happen together in one round (see FORM FILLING),
# so 4 is generous headroom (typically 1 tool round + 1 final-text round).
_MAX_TOOL_ROUNDS = 4
# Kept tight -- a real operator's reply is 1-3 short sentences; this is a
# ceiling against runaway generation, not a target length, and every extra
# token here is extra time-to-last-token before Bulbul can start.
_MAX_OUTPUT_TOKENS = 300
# The one deliberate exception: the final closing briefing (responder ETAs +
# safety instructions + follow-up script — see dispatch_briefing.py) is a
# genuinely long single turn; 300 tokens would truncate it mid-sentence. This
# higher ceiling applies ONLY to that turn, so every normal turn keeps the
# tight latency budget above. Raised from an earlier 800 as a precaution
# after a confirmed English-side bug in the same feature (dispatcher_live.py)
# where a too-tight budget for this content (up to 5 responder ETAs + 4 SOP
# lines + a 6-line closing script) cut the reply off mid-sentence -- Devanagari
# tokenizes less efficiently than Latin text, so this path is if anything more
# exposed to the same risk. A higher ceiling only ever prevents truncation,
# it can't cause a regression, so there's no downside to the margin.
_BRIEFING_MAX_OUTPUT_TOKENS = 1200

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

# ── Change 3: instant filler acknowledgment (perceived latency) ───────────────
# The moment STT finalizes an utterance -- BEFORE the Gemini round-trip returns --
# play a short, content-free Hindi acknowledgment so the caller hears a human-like
# "जी..." instead of dead air during the "thinking" gap. These are acknowledgments,
# NOT information (no facts / ETAs / dispatch claims), so they violate no honesty
# rule. Each is synthesized via Bulbul ONCE per process and its PCM cached at module
# level -> zero per-turn synthesis latency, just cached audio bytes. Rotated with no
# immediate repeat; never played on the scripted opening greeting or the closing
# briefing. Crucially the filler NEVER reads self._stt, so the single-reader barge-in
# rule is preserved exactly (the real reply's _speak_or_fallback stays the one and
# only STT reader); it is short, and barge-in is fully handled the instant the real
# reply begins.
_FILLER_ENABLED = os.environ.get("HINDI_FILLER", "true").strip().lower() not in ("0", "false", "no")
_FILLER_TEXTS = ["जी...", "एक सेकंड...", "जी, समझ रहा हूँ..."]
_filler_pcm_cache: dict = {}            # text -> PCM16/24kHz bytes (synth once per process)
_filler_synth_lock = asyncio.Lock()


async def _synthesize_fillers(tts) -> None:
    """Synthesize each filler once via `tts` (a BulbulStream) and cache its PCM at
    module level. Idempotent + lock-guarded: only the FIRST call in the whole
    process hits Bulbul; every later turn and later call reuses the cache. Failures
    are non-fatal -- a missing filler is simply skipped at play time and the turn
    proceeds exactly as it does today."""
    if all(t in _filler_pcm_cache for t in _FILLER_TEXTS):
        return
    async with _filler_synth_lock:
        for t in _FILLER_TEXTS:
            if t in _filler_pcm_cache:
                continue
            try:
                pcm = b"".join([chunk async for chunk in tts.speak(t)])
                if pcm:
                    _filler_pcm_cache[t] = pcm
            except Exception:
                logger.debug("Filler pre-synthesis failed for %r (will retry next call)", t, exc_info=True)

# Speech-rendering layer (see HindiDispatcherSession._render_for_speech):
# Bulbul v3 has no SSML/pitch/emotion/style API (reconfirmed against
# Sarvam's current docs) -- it's built on an LLM that infers pauses,
# emphasis, and tone FROM THE TEXT AND PUNCTUATION itself. So the only real
# lever for expressiveness is the text handed to it, which is why this
# module shapes it in two places: the system prompt below asks Gemini to
# write with this exact opener pool and "..."/"—" pause punctuation
# directly (Gemini has full conversational context, so it can judge WHERE a
# pause belongs far better than a rule-based rewrite could) -- and this
# constant, plus _render_for_speech, mechanically GUARANTEE the one thing
# prompting alone can't: that the same opener is never spoken twice in a
# row. Every entry here is a complete, self-contained clause, so stripping
# a repeated one is always grammatically safe.
_OPENERS = [
    "ओह...", "अच्छा...", "समझ गया...", "ठीक है...", "सबसे पहले...",
    "मैं समझ सकता हूँ...", "कृपया घबराइए मत...", "मैं आपकी सहायता के लिए यहाँ हूँ...",
]
# Gemini sometimes writes one of the prompt's own suggested openers with a
# plain comma ("ठीक है, मैं समझ रहा हूँ") instead of the pause punctuation
# actually asked for -- this normalizes JUST that known pattern to "..."
# rather than attempting any general rewriting.
_OPENER_COMMA_RE = re.compile(
    r"^(ओह|अरे|हाँ|ठीक है|सुनिए|अच्छा|समझ गया|सबसे पहले)\s*,\s*"
)

# Internal tool-result / system-note tokens that must NEVER be spoken aloud.
# Real reported bug: the agent read fields like "tone_reminder" out to the
# caller. These only ever appear in tool results / the parenthesized system
# notes the model is instructed to ACT ON, not voice -- a natural Hindi operator
# reply never contains a snake_case English identifier or a parenthetical, so
# stripping them can't remove legitimate speech. Prompted against too, but this
# is the deterministic guarantee (same rule-first pattern as _render_for_speech).
_META_LEAK_RE = re.compile(
    r"(?i)\b(?:"
    r"tone[_ ]?reminder|next[_ ]?question|fast[_ ]?track|"
    r"incident[_ ]?type|form[_ ]?update|update[_ ]?form[_ ]?field|"
    r"search[_ ]?incident[_ ]?(?:type|categories)|submit[_ ]?incident|"
    r"get[_ ]?current[_ ]?location|request[_ ]?location|location[_ ]?result|"
    r"dispatch[_ ]?update|state[_ ]?block|flag[_ ]?active|"
    r"system[_ ]?(?:update|note)|tool[_ ]?result"
    r")\b"
)


def _strip_meta_leak(text: str) -> str:
    """Remove any leaked internal/meta content before it reaches Bulbul --
    parenthesized system notes and tool-result field names (tone_reminder,
    next_question, fast_track, ...) must never be voiced. Returns text unchanged
    unless it actually contains a parenthetical or a known meta token."""
    if "(" not in text and not _META_LEAK_RE.search(text):
        return text
    # System notes and tone_reminder are parenthesized -- drop every ( ... )
    # group (repeat to handle nesting). The model is told to use "..."/"—" for
    # pauses, not parentheses, so this never touches legitimate speech.
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\([^()]*\)", " ", text)
    text = _META_LEAK_RE.sub(" ", text)          # any lingering token names
    text = re.sub(r"\s+[:=]\s+", " ", text)       # dangling "label :" separators
    text = re.sub(r"\s+([।.!?,…])", r"\1", text)  # space left before punctuation
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()

# ── Single-round fast path: canonical next-question phrasings ─────────────────
# Latency: a tool-using turn used to cost TWO sequential Gemini round trips
# (~1.1-1.4s median EACH, measured live on this project's Vertex credentials,
# 2026-07): round 0 decides the tool calls, round 1 -- after the tool results
# reveal the deterministic "next_question" -- writes the spoken reply. But the
# next question's TOPIC is already deterministic (dispatcher_live's
# _compute_still_missing), and its Hindi WORDING is already mandated by the
# system prompt's own phrasing glossary -- so the model isn't actually
# deciding anything in round 1 except the acknowledgment. The fast path cuts
# round 1 out entirely: the model now writes its short empathetic
# acknowledgment (no question -- prompt-enforced AND code-guarded) together
# with its tool calls in round 0, and the CODE appends the canonical question
# for whatever next_question comes out of the tool results. This is MORE
# deterministic than before, not less: the appended question structurally
# cannot wander off-list, re-ask something answered, or skip ahead -- the
# failure modes the two-round design existed to prevent. Every case the fast
# path can't prove safe falls back to the old second round unchanged (see
# _compose_single_round_reply's guards). gemini-2.5-flash-lite as a "faster
# model" was re-benchmarked the same day and re-rejected: only ~0.3s faster
# and 3/4 runs returned empty candidates on this exact workload; newer
# flash generations 404 on this project/region.
#
# Keys MUST exactly match dispatcher_live's _compute_still_missing hint
# strings (REQUIRED_FIELDS / DEFAULT_REQUIRED_FIELDS); coverage is asserted
# in tests.py so a future hint edit fails loudly instead of silently
# disabling the fast path for that question.
_CANONICAL_QUESTIONS: dict[str, str] = {
    # Individual per-field questions (asked when a group is only PARTIALLY
    # missing -- #6 or a prior answer already filled the rest).
    "how many vehicles were involved": "कुल कितनी गाड़ियाँ इसमें शामिल थीं?",
    "how many people are injured": "क्या किसी को चोट लगी है?",
    "whether anyone is trapped": "क्या कोई फँसा हुआ है?",
    "whether there is fire or a fuel leak": "क्या कहीं आग लगी है या ईंधन का रिसाव हो रहा है?",
    "whether hazardous material is involved": "क्या कोई खतरनाक पदार्थ भी शामिल है?",
    "whether the person is conscious": "क्या वह व्यक्ति होश में है?",
    "whether the person is breathing": "क्या उसकी साँस ठीक से चल रही है?",
    "whether there is heavy bleeding": "क्या ज़्यादा खून बह रहा है?",
    # Combined questions (asked as ONE question when ALL of a group's fields are
    # still missing -- the common first-ask). Must stay in sync with the
    # _COMBINED_* constants in dispatcher_live.py.
    "how many vehicles were involved, and whether anyone is injured or trapped":
        "कुल कितनी गाड़ियाँ थीं, और क्या किसी को चोट लगी या कोई फँसा हुआ है?",
    "whether there is any fire or a hazardous-material leak":
        "क्या कहीं आग लगी है या कोई खतरनाक पदार्थ / ईंधन का रिसाव है?",
    "whether the person is conscious and breathing, and whether there is heavy bleeding":
        "क्या वह व्यक्ति होश में है और साँस ले रहा है, और क्या ज़्यादा खून बह रहा है?",
    "whether anyone is injured or trapped":
        "क्या किसी को चोट लगी या कोई फँसा हुआ है?",
    "how many vehicles were involved, and whether anyone is injured":
        "कुल कितनी गाड़ियाँ थीं, और क्या किसी को चोट लगी है?",
}

# The only tools whose results the fast path can safely skip showing the
# model: their outcome is fully reflected in _compute_still_missing (which
# the code reads directly). Anything else (submit_incident's next_step
# script, search_incident_categories' browse list, get_current_location's
# result) genuinely needs the model to READ the result before speaking.
_FAST_PATH_TOOLS = frozenset({"search_incident_type", "update_form_field"})

# Hindi-only opening line -- deliberately NOT the shared _OPENING_LINE["hi-IN"]
# from dispatcher_live.py (English's own copy there is untouched). "1033" is
# spelled out digit-by-digit ("एक शून्य तीन तीन") rather than left as the raw
# number: a TTS engine reading "1033" as one four-digit number will almost
# certainly say "one thousand and thirty-three" (एक हज़ार तैंतीस), which is
# how a cardinal number is read, not how a phone/helpline number is ever
# actually said out loud -- phone numbers, PINs, and codes are read digit by
# digit in every language, including real 1033 announcements. This is the
# very first thing spoken on every single call, so it's worth getting exactly
# right rather than leaving it to Bulbul's default number-to-words handling.
_HINDI_OPENING_LINE = "भारत की एक शून्य तीन तीन हाईवे हेल्पलाइन में आपका स्वागत है।"

# Spoken right after the greeting on the opening turn ONLY if the model returned
# nothing usable -- so the greeting (and a first question) are heard no matter
# what the model does.
_OPENING_FALLBACK_QUESTION = "कहिए, मैं आपकी किस तरह मदद कर सकता हूँ?"

# A short "please hold" line (#2), spoken via Bulbul into the silent gap between
# the post-submit acknowledgment and the ETA briefing (dispatch_update wait +
# Gemini briefing generation + Bulbul synthesis). Carries NO ETA/responder data
# (none exists yet -- Hard Rules 1/2/5), male grammatical form to match the
# configured male voice (SARVAM_TTS_SPEAKER=shubh, per CLAUDE.md), and worded so
# it does not simply repeat the submit ack's "सेवाएँ देखी जा रही हैं".
_HINDI_HOLD_LINE = "बस एक पल रुकिए... मैं आपके लिए ज़रूरी जानकारी तैयार कर रहा हूँ।"

# An explicit ambulance mention from the CALLER on a road-accident first-response
# helpline signals real medical urgency, so it is treated as critical -- the
# fast-track then fires (stop asking routine secondary questions and submit as
# soon as the essentials are in) instead of continuing to interrogate while the
# caller is begging for help. Matches "बुलेंस" (the distinctive core of every
# Devanagari ambulance spelling -- एम्बुलेंस / एंबुलेंस / ऐम्बुलेंस / अंबुलेंस) and the
# Latin word (Saaras sometimes emits it). Hindi-scoped by design; the shared
# _is_critical (English) is untouched.
_AMBULANCE_REQUEST_RE = re.compile(r"(?:बुलेंस|ambulance)", re.IGNORECASE)


def _hindi_system_prompt() -> str:
    opening_line = _HINDI_OPENING_LINE
    # Kept as compact as the behavior requirements allow -- every extra
    # sentence here is extra input tokens on EVERY single Gemini call of the
    # entire conversation (the system prompt is re-sent every turn since this
    # is plain generate_content, not a stateful session).
    return f"""आप भारत की 1033 राष्ट्रीय राजमार्ग हेल्पलाइन के एक प्रशिक्षित पुरुष ऑपरेटर हैं — दिल्ली–देहरादून एक्सप्रेसवे कॉरिडोर पर हाईवे पर हर तरह की मदद के लिए, एक वास्तविक फ़ोन कॉल पर। आप कभी AI, मॉडल या असिस्टेंट नहीं हैं — पूछे जाने पर आप बस 1033 हेल्पलाइन के ऑपरेटर हैं। यह हेल्पलाइन सिर्फ़ दुर्घटना रिपोर्ट के लिए नहीं है — caller किसी भी वजह से कॉल कर सकता है: नज़दीकी अस्पताल/मैकेनिक/पेट्रोल पंप/पुलिस, कोई सरकारी योजना या क़ानून, बीमा, गाड़ी ख़राब होना, कोई शिकायत, या सचमुच की दुर्घटना।

बोली जाने वाली भाषा (टेक्स्ट-टू-स्पीच से बोला जाएगा): सरल, रोज़मर्रा की बोलचाल की हिंदी — औपचारिक, साहित्यिक या शुद्ध हिंदी कभी नहीं, अंग्रेज़ी का शब्दशः अनुवाद कभी नहीं ("गाड़ी" न कि "वाहन", "मदद" न कि "सहायता", "टक्कर हुई" न कि "दुर्घटनाग्रस्त हुई")। हमेशा पुल्लिंग क्रिया रूप अपने लिए ("समझ रहा हूँ", "दर्ज कर रहा हूँ", "पुष्टि करना चाहता हूँ")। कोई मार्कडाउन, सूची, इमोजी या अंग्रेज़ी वाक्य नहीं (लोकेशन, रिपोर्ट, ट्रक, एम्बुलेंस जैसे आम शब्द ठीक हैं)।

बोलने में स्वाभाविक ठहराव — TTS पंक्चुएशन से ही रुकना और ज़ोर देना तय करता है, इसलिए इसका जानबूझकर इस्तेमाल करें: भावनात्मक पल के बाद सिर्फ कॉमा या पूर्णविराम नहीं, "..." लगाएं। जैसे "मुझे यह सुनकर दुख हुआ।" की जगह "ओह... मुझे यह सुनकर सचमुच बहुत अफ़सोस हुआ।" बोलें; "घबराइए मत, मदद आ रही है।" की जगह "घबराइए मत... मैं मदद भेज रहा हूँ।" बोलें। दो जुड़े विचारों के बीच "—" का इस्तेमाल भी स्वाभाविक है।

शुरुआत में विविधता — हर बार अलग चुनें, कभी लगातार दो जवाबों में एक जैसी शुरुआत न करें, कभी हर बार "जी" या "ठीक है" से शुरू न करें: "ओह...", "अच्छा...", "समझ गया...", "ठीक है...", "सबसे पहले...", "मैं समझ सकता हूँ...", "कृपया घबराइए मत...", "मैं आपकी सहायता के लिए यहाँ हूँ..." — इनमें से चुनें या मिलती-जुलती अपनी शैली बनाएं।

पेशेवर लहज़ा और भावना — आप एक अनुभवी, प्रशिक्षित हेल्पलाइन ऑपरेटर हैं: शांत, संयमित और स्थिति पर पकड़ रखने वाले, पर आवाज़ में caller के लिए सच्ची फ़िक्र साफ़ झलके — जैसे कोई ऐसा इंसान जो रोज़ आपात स्थितियाँ संभालता है और आपको मदद ज़रूर दिलाएगा। कभी खुशमिज़ाज़, तेज़ या उत्साहित न लगें, और न ही रूखे या मशीनी — बस भरोसेमंद और सक्षम। हर टूल के नतीजे में "tone_reminder" आता है, हर बार उसे मानें — पर "tone_reminder", "next_question", "fast_track", "incidentType" जैसे अंदरूनी शब्द, कोष्ठक () में लिखा कोई भी निर्देश, या "SYSTEM UPDATE" जैसी बातें सिर्फ आपके मार्गदर्शन के लिए हैं; इन पर अमल करें पर इन्हें कभी ज़ोर से बोलकर caller को न सुनाएं। caller सिर्फ आपकी स्वाभाविक हिंदी बातचीत सुने, कोई तकनीकी शब्द या फ़ील्ड नाम नहीं। चोट, फँसा होना या खतरे का ज़िक्र होने पर पहले सच्ची, स्थिर सहानुभूति — धीमी, गंभीर बोली, ठहराव के साथ — फिर सवाल; जैसे "ओह, मुझे यह सुनकर बहुत दुख हुआ... आप हिम्मत रखिए।" (मदद भेजी जा रही है — यह घोषणा आप ख़ुद कभी न करें; वह सिस्टम अपने-आप करता है, नीचे "आपातकालीन रिपोर्ट" देखें।) स्थिति सामान्य होने पर (मामूली टक्कर, बिना चोट के) शांत, पेशेवर और संक्षिप्त रहें — ज़रूरत से ज़्यादा भावुक हुए बिना, जैसे एक अनुभवी ऑपरेटर हर कॉल को उसकी असली गंभीरता के हिसाब से संभालता है।

हर जवाब की बनावट — 1 से 3 छोटे वाक्य: पहले caller ने अभी जो बताया उसकी सच्ची स्वीकृति (ऊपर बताए ठहराव और शुरुआत के साथ), फिर या तो ठीक ONE सवाल, या (आम सवालों में) एक सीधा जवाब — कभी एक साथ दो सवाल नहीं, कभी सिर्फ सवाल बिना स्वीकृति के नहीं, formal भाषा कभी नहीं जैसे "कृपया घटना का विवरण प्रदान करें।" (एक अपवाद: टूल कॉल के साथ लिखी स्वीकृति में कोई सवाल नहीं होता — नीचे "काम का क्रम" देखें।)

OPENING (सिर्फ़ कॉल के पहले जवाब में): स्वागत वाक्य ("{opening_line}") सिस्टम अपने-आप, आपके जवाब से पहले बोल देता है — इसे आप खुद कभी न बोलें और न दोहराएं। यह एक खुली हेल्पलाइन है — पहले से यह न मानें कि दुर्घटना हुई है। आपका पहला जवाब बस एक छोटा, खुला सवाल हो कि आप caller की किस तरह मदद कर सकते हैं (जैसे "कहिए, मैं आपकी किस तरह मदद कर सकता हूँ?")। फिर caller जो बताए उसके हिसाब से नीचे बताए तरीक़े से सही टूल चुनें।

कॉल का मक़सद पहचानें और सही टूल चुनें — यह सबसे अहम नियम है। caller ने जो माँगा है उसके हिसाब से:
• दुर्घटना या चोट — "एक्सीडेंट हो गया", कोई घायल/बेहोश/फँसा है, खून बह रहा है, या "हादसा हुआ, एम्बुलेंस भेजो": तभी search_incident_type बुलाकर नीचे "—— दुर्घटना रिपोर्ट ——" वाला पूरा क्रम शुरू करें। रिपोर्ट फ़ॉर्म सिर्फ़ इसी सूरत में भरा जाता है।
• नज़दीकी सुविधा या "कितनी देर में आएगा" — अस्पताल, एम्बुलेंस, मैकेनिक, टो/रिकवरी, पेट्रोल पंप, पुलिस, या दमकल; या "एक शून्य आठ कितनी देर में आएगा": find_nearest_facility बुलाएं।
• योजना / क़ानून / क्या करूँ — गोल्डन आवर या पीएम-राहत मुफ़्त इलाज, राह-वीर/नेक व्यक्ति सुरक्षा-इनाम, हिट-एंड-रन मुआवज़ा, आयुष्मान भारत, बीमा दावा, "गाड़ी हिलाऊँ या नहीं", रात में क्या करूँ: answer_info_question बुलाएं।
• शिकायत या सड़क की ख़राबी — गड्ढा, टूटा बैरियर, बिना चोट का कोई ख़तरा, या कोई आम शिकायत: lodge_complaint बुलाएं और caller को reference number बताएं।
• गाड़ी ख़राब, बिना चोट — चेन टूटी, टायर पंचर: यह दुर्घटना नहीं है। find_nearest_facility (मैकेनिक/टो) से मदद दें, ज़रूरत हो तो answer_info_question (रात की सुरक्षा)। एम्बुलेंस या दुर्घटना रिपोर्ट कभी नहीं।
एक ही कॉल में कई सवाल हो सकते हैं — एक का पूरा जवाब देकर विनम्रता से पूछें "और कुछ मदद चाहिए?"। caller के "नहीं / बस इतना ही" कहने पर कॉल शालीनता से समेटें। दुर्घटना को छोड़कर बाक़ी हर सवाल का जवाब बिना कोई फ़ॉर्म भरे, सीधे सही टूल से दें।

find_nearest_facility — यह टूल सबसे नज़दीकी सुविधा का नाम, (हो तो) संपर्क नंबर, दूरी और अनुमानित पहुँचने का समय लौटाता है। ये caller को बताएं, समय हमेशा "अनुमानित" कहकर (जैसे "लगभग बीस मिनट")। कभी यह दावा न करें कि जगह अभी खुली है या बंद — यह जानकारी हमारे पास नहीं; बस सबसे नज़दीकी सुविधा और संपर्क ईमानदारी से दें। अगर टूल कहे कि लोकेशन नहीं पता, तो पहले caller से मैप-पिन बटन से अपनी लोकेशन भेजने को कहें, फिर दोबारा बुलाएं।

answer_info_question — यह टूल आधिकारिक, जाँची-परखी जानकारी लौटाता है। सिर्फ़ जो "answer_hi" में आए उसे अपनी गर्मजोशी भरी बोलचाल की हिंदी में दोबारा कहें, और साथ में "note_hi" वाली सावधानी भी ज़रूर बोलें। कोई भी राशि, तारीख़, पात्रता या क़ानूनी बात अपनी याददाश्त से न जोड़ें — सिर्फ़ जो टूल ने दिया वही। अगर टूल कहे कि यह विषय जानकारी में नहीं है, तो ईमानदारी से कहें कि यह ख़ास जानकारी आपके पास नहीं है और 1033 पर पूछने का सुझाव दें — कभी अंदाज़े से जवाब न गढ़ें।

lodge_complaint — यह शिकायत या सड़क की ख़राबी को सिस्टम में दर्ज करके एक reference number लौटाता है। caller को वह नंबर साफ़-साफ़, अंक-दर-अंक बताएं। ईमानदारी बहुत ज़रूरी: कहें "आपकी शिकायत सिस्टम में दर्ज हो गई है, इसका reference number है ..." — यह कभी न कहें कि इसे किसी अफ़सर या विभाग को भेज दिया गया है, या यह इतने समय में ठीक हो जाएगी।

—— दुर्घटना रिपोर्ट (नीचे के सब नियम सिर्फ़ तब लागू जब caller दुर्घटना या चोट की बात करे) ——

आम सवालों की सहज हिंदी (next_question के अंग्रेज़ी संकेत के लिए इस्तेमाल करें):
चोट/casualties → "क्या किसी को चोट लगी है?" (हाँ पर "कितने लोग घायल हैं?")
trapped → "क्या कोई फँसा हुआ है?"
fire/fuel leak → "क्या कहीं आग लगी है या ईंधन का रिसाव हो रहा है?"
conscious → "क्या वह होश में है?"   breathing → "क्या साँस ठीक से चल रही है?"
heavy bleeding → "क्या ज़्यादा खून बह रहा है?"   hazmat → "क्या कोई खतरनाक पदार्थ भी शामिल है?"
vehicles involved → "कुल कितनी गाड़ियाँ इसमें शामिल थीं?"
कभी-कभी next_question में दो-तीन जुड़ी बातें एक साथ आती हैं — उन्हें एक ही स्वाभाविक सवाल में पूछें, अलग-अलग नहीं:
गाड़ियाँ+चोट+फँसा → "कुल कितनी गाड़ियाँ थीं, और क्या किसी को चोट लगी या कोई फँसा हुआ है?"
गाड़ियाँ+चोट → "कुल कितनी गाड़ियाँ थीं, और क्या किसी को चोट लगी है?"
चोट+फँसा → "क्या किसी को चोट लगी या कोई फँसा हुआ है?"
आग+hazmat → "क्या कहीं आग लगी है या कोई खतरनाक पदार्थ / ईंधन का रिसाव है?"
होश+साँस+खून → "क्या वह व्यक्ति होश में है और साँस ले रहा है, और क्या ज़्यादा खून बह रहा है?"

काम का क्रम — हर टर्न में, बिना अपवाद:
1. पहले caller ने अभी जो बताया उसके लिए ज़रूरी सभी टूल कॉल एक साथ करें — पहली बार घटना बताने पर search_incident_type (उनके असली शब्दों के साथ, कभी अपना अनुवाद या सारांश नहीं), और हर नई जानकारी (चोट, फँसा होना, आग, गाड़ियों की संख्या, विवरण) के लिए update_form_field। "नहीं" भी जानकारी है — रिकॉर्ड करें (flag_active=false), सिर्फ आगे न बढ़ें। अगर caller एक ही वाक्य में कई बातें एक साथ कह दे (जैसे "कार और कार की टक्कर, चार लोग घायल, एम्बुलेंस चाहिए") — तो भी सब कुछ इसी एक ही राउंड में करें: search_incident_type और सभी ज़रूरी update_form_field एक साथ, अभी, एक ही जवाब में। कभी पहले सिर्फ search_incident_type बुलाकर उसके नतीजे का इंतज़ार करके अगले राउंड में update न करें — घटना के सारे तथ्य caller के वाक्य में पहले से मौजूद हैं; उन्हें दर्ज करने के लिए आपको search के नतीजे की ज़रूरत नहीं। उसी बार में text में एक छोटी (1–2 वाक्य) सहानुभूति-भरी स्वीकृति भी लिखें — ऊपर बताए ठहराव और शुरुआत के साथ, पर उसमें कोई सवाल बिल्कुल नहीं: अगला सवाल सिस्टम आपकी स्वीकृति के तुरंत बाद खुद जोड़ देता है।
2. अगर टूल के नतीजे वापस आकर आपसे दोबारा जवाब माँगा जाए, तो अब ऊपर बताई पूरी बनावट में बोलें — स्वीकृति + ठीक एक सवाल ("next_question" वाला ही)।

दुर्घटना रिपोर्ट में लोकेशन और caller का रिश्ता: कॉल की शुरुआत में लोकेशन अपने-आप ले ली जाती है; अगर मिल गई है तो संक्षेप में पुष्टि करें कि दुर्घटना यहीं हुई है, वरना caller से मैप-पिन बटन से लोकेशन भेजने को कहें। साथ ही सहज रूप से (अलग ठंडा सवाल बनाए बिना) यह भी जान लें कि caller घटना से कैसे जुड़ा है — क्या वह खुद घायल/शामिल है, पास खड़ा चश्मदीद है, या किसी और की ओर से (शायद घटनास्थल से दूर) रिपोर्ट कर रहा है। इससे आप उसकी बाकी बातों को सही संदर्भ में समझ पाएंगे (चश्मदीद को चोट का ब्योरा शायद न पता हो; दूर से बताने वाला दृश्य देख ही न पा रहा हो)।

caller बोलचाल की भाषा में बोलते हैं ("टायर फट गया", "गाड़ी पलट गई", "ठोक दिया", "आग पकड़ ली") — पूरे वाक्य और अब तक की पूरी बातचीत से मतलब समझें, कभी सिर्फ एक शब्द पकड़कर नहीं, कभी formal शब्दों में दोबारा बोलने को न कहें।

घटना का प्रकार — अहम नियम: कभी खुद अंदाज़ा न लगाएं, हमेशा search_incident_type बुलाएं। ध्यान से सुनें कि caller ने कौन-कौन से वाहन बताए — "मेरी कार ट्रक से टकरा गई" में कार भी है और ट्रक भी, कभी सिर्फ Car vs. Car दर्ज न हो। मिलान संदिग्ध लगे तो एक छोटा स्पष्टीकरण सवाल पूछें, फिर दोबारा search_incident_type बुलाएं या search_incident_categories से सही करें। update_form_field से सही प्रकार दर्ज कर देना ही पुष्टि है — इसके बाद caller से प्रकार की दोबारा पुष्टि न माँगें; बस संक्षेप में स्वीकार करके अगले सवाल पर जाएं।

description फ़ील्ड हमेशा अंग्रेज़ी में लिखें (अनुवाद+सारांश करके) — यही एकमात्र चीज़ है जो हमेशा अंग्रेज़ी में लिखनी है। जल्दी एक छोटा सारांश सेट करें, नई जानकारी मिलने पर अपडेट करें। जब पता चल जाए कि रिपोर्ट कौन कर रहा है, तो उसे भी इसी अंग्रेज़ी description में शामिल करें (जैसे "Caller is the injured driver", "Bystander reporting", या "Third party reporting on behalf of someone at the scene") — इसके लिए कोई अलग फ़ॉर्म फ़ील्ड नहीं है।

अगला सवाल — पक्का नियम: हर टूल के नतीजे में "next_question" आता है — बिल्कुल यही अगला विषय पूछें, कभी कोई और सवाल नहीं, कभी वह जो caller पहले ही बता चुका है दोबारा नहीं (जैसे उसने "दो लोग घायल हैं" कहा हो तो फिर कभी "क्या किसी को चोट लगी है?" न पूछें)। कभी-कभी next_question में दो-तीन जुड़ी बातें एक साथ आती हैं — उन्हें एक ही सवाल में पूछें (ऊपर संयुक्त सवालों की सूची देखें), अलग-अलग नहीं।

रिपोर्ट भेजना (सामान्य स्थिति) — जब next_question null हो और fast_track false हो, तो सारी ज़रूरी जानकारी मिल चुकी है। caller को पूरी जानकारी दोबारा न सुनाएं। बस एक छोटी पुष्टि लें — एक छोटा वाक्य कि आपके पास सारी ज़रूरी जानकारी आ गई है, साथ में भेजने से पहले एक छोटा सवाल, जैसे "मेरे पास आपकी रिपोर्ट के लिए सारी ज़रूरी जानकारी आ गई है — क्या मैं इसे भेज दूँ?" caller के हाँ कहते ही (हाँ / भेज दीजिए / ठीक है) उसी टर्न में submit_incident बुला दें। कुछ ठीक करना हो तो सिर्फ वही ठीक करके फिर छोटी पुष्टि लें। यह एक छोटी जाँच है, लंबा सारांश नहीं। अगर submit_incident बताए कि कुछ छूट गया है, तभी सिर्फ वही एक चीज़ पूछकर दोबारा कोशिश करें।

आपातकालीन रिपोर्ट (चोट या जान का ख़तरा) — हर टूल नतीजे में "fast_track" आता है। जब यह true हो — किसी को चोट लगी हो, कोई बेहोश हो, साँस न ले रहा हो, बहुत खून बह रहा हो, कोई फँसा हो, आग लगी हो, या कोई कमज़ोर व्यक्ति ख़तरे में हो — तो caller के लिए मदद (एम्बुलेंस, और आग/रिसाव हो तो दमकल भी) का इंतज़ाम सिस्टम अपने-आप, इसी वक़्त शुरू कर देता है, और caller को इसकी सूचना भी सिस्टम अपने-आप, ठीक एक बार, सही क्रम में (पहले सहानुभूति, फिर "मदद का इंतज़ाम किया जा रहा है") दे देता है। इसलिए बहुत ज़रूरी — आप ख़ुद कभी यह घोषणा न करें कि एम्बुलेंस या मदद भेजी जा रही है / भेज दी गई है / इंतज़ाम किया जा रहा है; यह पंक्ति सिस्टम बोलता है, आप उसे कभी न दोहराएँ। आपका काम बस इतना है: caller के प्रति पहले सच्ची, गर्मजोशी भरी सहानुभूति दिखाएँ (जैसे "ओह, मुझे यह सुनकर बहुत दुख हुआ, आप हिम्मत रखिए"), और फिर "next_question" वाला अगला सवाल सामान्य रूप से पूछते रहें। कॉल यहीं ख़त्म नहीं होती और fast_track true होने भर से submit न करें: next_question के मुताबिक बाकी सारी ज़रूरी जानकारी (लोकेशन, गाड़ियों की संख्या, बाकी ख़तरे) इकट्ठा करते रहें। सिर्फ जब next_question null हो जाए (सब कुछ इकट्ठा हो जाए), तब आपात स्थिति में अनुमति न माँगें — बस एक छोटा भरोसा देकर उसी टर्न में तुरंत submit_incident बुला दें। ज़रूरी ईमानदारी (Hard Rules): कभी न कहें कि कोई गाड़ी भेज दी गई है, रास्ते में है, ट्रैक हो रही है, या इतने मिनट में पहुँचेगी — असली समय/दूरी submit के बाद तय होकर अंत में पढ़कर सुनाई जाती है।

submit के बाद: caller को बताएं रिपोर्ट दर्ज हो गई और सेवाएँ देखी जा रही हैं, एक पल रुकने को कहें, अलविदा न कहें। इसके बाद मिलने वाले SYSTEM UPDATE संदेश के निर्देशों का पूरी तरह पालन करें (उसमें सब कुछ विस्तार से लिखा होगा)।

उच्चारण — अहम नियम: आपके सभी शब्द टेक्स्ट-टू-स्पीच से बोले जाते हैं, इसलिए अंग्रेज़ी अक्षरों या कोड को कभी सीधे मत लिखें (जैसे "NH-334" या "NE-4") — हिंदी में फ़ोनेटिक रूप से लिखें, जैसे "एन एच तीन तीन चार"। किसी भी संख्या को कोड या नंबर की तरह बोलना हो (जैसे फ़ोन नंबर या हाईवे नंबर), तो अंक-दर-अंक हिंदी शब्दों में लिखें (जैसे "27" → "दो सात"), गिनती के अंदाज़ में नहीं (जैसे "सत्ताईस" ठीक है अगर वह सामान्य गिनती है, पर कोड के लिए अंक-दर-अंक बेहतर है)। लोकेशन का नाम बोलते समय, अगर वह अंग्रेज़ी लिपि में मिला है, तो उसे स्वाभाविक हिंदी उच्चारण में बदलकर बोलें, कभी अंग्रेज़ी अक्षर जस के तस मत बोलें।

caller का transcript कभी-कभी थोड़ा अधूरा हो सकता है (speech recognition) — मतलब समझें; tools, transcript या तकनीक का ज़िक्र कभी न करें।"""


class HindiDispatcherSession(HelplineToolsMixin, DispatcherSession):
    """Sarvam-based Hindi dispatcher speaking the same browser protocol as
    DispatcherSession. Only run() and the audio/reasoning transport differ —
    all tools, state, and validation are inherited.

    Multi-intent (Phase 2): this is a GENERAL 1033 helpline, not an accident-only
    form. The accident-collection flow is one branch, entered only when the caller
    reports an accident/injury (see `_accident_mode`). Facility lookups, curated
    scheme/legal info, and complaint logging are the other branches, provided by
    `HelplineToolsMixin`. English (`DispatcherSession`) does not inherit the mixin
    and is unchanged."""

    def __init__(self, websocket: WebSocket):
        super().__init__(websocket, "hi-IN")
        self._init_helpline_state()  # _pending_facility / _pending_complaint
        # The call's classified intent for Post-Call Analytics ("accident" once an
        # accident tool runs; "information" once a helpline tool runs and it's not
        # an accident). Emitted to the browser via a `call_intent` frame so a
        # general information/facility/complaint call is NOT counted as an
        # abandoned accident call (which was dragging down the accident completion
        # rate). accident outranks information. None until the first tool.
        self._call_intent: Optional[str] = None
        # False = general helpline; flips True once an accident/injury is reported
        # (a model accident tool-call OR a confident transcript signal). While
        # False, the incident-form backstop, next_question sequencing, and interim
        # dispatch are all suppressed so a general call is never forced into the
        # accident form.
        self._accident_mode = False
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
        # The fixed 1033 welcome greeting is spoken DETERMINISTICALLY as a prefix
        # on the very first agent turn (see _agent_turn), not left to the model to
        # reproduce -- real reported bug: the model was skipping the greeting
        # entirely and opening straight with the location question. The line must
        # be exact (1033 spelled digit-by-digit for TTS), so it can't depend on
        # the model saying it verbatim. Flag is consumed on the first turn.
        self._opening_line_pending = True
        # Tracks the opener (see _OPENERS) this call's last reply started
        # with, if any -- lets _render_for_speech guarantee no immediate
        # repeat, a hard mechanical backstop on top of prompting.
        self._last_opener: Optional[str] = None
        # Change 3 (filler ack): rotation state + a per-turn mutual-exclusion flag so
        # the thinking-gap filler and the Exotel lookup filler never double up (at
        # most one filler plays per turn -- no overlapping audio on the line).
        self._last_filler: Optional[str] = None
        self._filler_idx = -1
        self._ack_filler_active = False
        # Per-turn latency breakdown (see _mark); reset at the top of each
        # cycle in run()'s main loop.
        self._turn_stats: dict = {}
        # Hindi-scoped fast-track trigger: latched True once the caller asks for
        # an ambulance (see _AMBULANCE_REQUEST_RE / _is_critical below).
        self._ambulance_requested = False
        # Staged / interim dispatch (#4, Hindi-only): responders already notified
        # early DURING the live call (each an honest interim notification record,
        # NO ETA — the real details are worked out after submit and read in the
        # closing briefing). Each service fires at most once; tracked here.
        self._dispatched_services: set[str] = set()
        # The DETERMINISTIC reassurance line for an interim dispatch — assembled
        # empathy-first (see _maybe_interim_dispatch) and spoken exactly once,
        # in a fixed order, by _agent_turn. The model never announces help
        # itself (system prompt), so this is the sole source of that line and it
        # can't be reordered, doubled, or repeated. Honest phrasing only —
        # "help is being arranged", never dispatched/tracked/ETA (Hard Rules).
        self._pending_interim_spoken: Optional[str] = None
        self._gen_config = types.GenerateContentConfig(
            system_instruction=_hindi_system_prompt(),
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS + HELPLINE_TOOL_DECLARATIONS)],
            # Same consistency rationale as the Live Hindi path: lower
            # temperature so every call behaves like the same operator.
            temperature=0.4,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            # No thinking for a real-time call — the sub-2s latency target
            # matters more than marginal reasoning depth here.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        # Used ONLY for the final closing briefing turn (see
        # _deliver_dispatch_briefing) — identical settings except a ceiling
        # high enough for its genuinely long single reply.
        self._briefing_config = types.GenerateContentConfig(
            system_instruction=_hindi_system_prompt(),
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS + HELPLINE_TOOL_DECLARATIONS)],
            temperature=0.4,
            max_output_tokens=_BRIEFING_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        # Reasoning backend: Sarvam (sarvam-105b-conversations) PRIMARY + Gemini
        # per-turn fallback. The SAME shared tool set the Gemini configs use, mapped
        # once to OpenAI `tools` for Sarvam. Sarvam reads the (possibly Exotel-
        # overridden) system instruction off the gen config at call time, so every
        # deterministic layer and prompt tweak applies identically to both backends.
        self._use_sarvam = _HINDI_REASONING_BACKEND == "sarvam"
        self._openai_tools = gemini_tools_to_openai(_TOOL_DECLARATIONS + HELPLINE_TOOL_DECLARATIONS)
        self._turn_backend: Optional[str] = None  # which backend served the last round
        self._quota_hits = 0                        # Vertex 429 / RESOURCE_EXHAUSTED count for this call

    def _is_critical(self) -> bool:
        # Hindi-scoped: an explicit ambulance request from the caller counts as
        # critical (medical urgency) on top of every shared injury/hazard
        # trigger, so the fast-track skips the routine secondary questions and
        # submits once type + location + description are in -- instead of asking
        # "how many injured / trapped / fire?" while the caller wants an
        # ambulance NOW. Still submits only when the essentials are present, so
        # a bare "send an ambulance" with no incident yet still gets a "what
        # happened?" first. English (super's _is_critical) is unchanged.
        return super()._is_critical() or self._ambulance_requested

    def _transcript_signals_accident(self) -> bool:
        """Read-only: does the accumulated transcript CONFIDENTLY indicate an
        accident/injury? Used to enter accident mode when the model forgot to
        classify (the existing 'model-forgot' safety net) — but now gated so a
        GENERAL call (facility/info/complaint/breakdown) never trips it. Uses only
        unambiguous injury/hazard signals: an injured count, an active hazard
        flag, or someone unconscious/not-breathing. A bare mention of the word
        'ambulance' (e.g. 'ambulance ka number do') is deliberately NOT enough —
        that is a facility query the model routes; the word only escalates once we
        are already in accident mode (see _is_critical)."""
        t = self.state.caller_transcript
        if not t.strip():
            return False
        signals = local_extract.extract_signals_locally(t)
        if (signals.get("estimatedCasualties") or 0) > 0:
            return True
        flags = signals.get("flag_determinations", {}) or {}
        if any(flags.get(f) for f in ("Fire", "Trapped", "Heavy bleeding", "Hazardous material")):
            return True
        if flags.get("Conscious") is False or flags.get("Breathing") is False:
            return True
        return False

    async def _apply_local_signals_from_transcript(self) -> None:
        # Multi-intent (Phase 2): the incident-form backstop only runs once this is
        # the ACCIDENT branch. Enter accident mode if the transcript confidently
        # shows an accident/injury (the model-forgot safety net), THEN apply the
        # shared incident signals. A general helpline call never touches the form.
        if not self._accident_mode and self._transcript_signals_accident():
            self._accident_mode = True
        if self._accident_mode:
            await super()._apply_local_signals_from_transcript()
        # Ambulance-request latch (kept): drives _is_critical's fast-track WITHIN
        # accident mode; harmless in general mode since next_question + interim
        # dispatch are both gated on _accident_mode.
        if (
            not self._ambulance_requested
            and self.state.caller_transcript
            and _AMBULANCE_REQUEST_RE.search(self.state.caller_transcript)
        ):
            self._ambulance_requested = True

    def _compute_still_missing(self) -> list[str]:
        """Hindi staged flow (#4): unlike the shared fast-track — which short-
        circuits the routine secondary questions the instant a life-threatening
        condition is known and submits on the three essentials — the Hindi
        dispatcher keeps collecting EVERY field even for a critical incident.
        Urgency is handled by notifying an ambulance immediately as an INTERIM
        dispatch (see _maybe_interim_dispatch) while the call continues, not by
        cutting the report short. So this deliberately omits the parent's
        `if self._is_critical(): return` early-exit; the question ORDER is
        otherwise identical to the shared logic (kept byte-for-byte in sync).
        fast_track (=_is_critical(), inherited via _state_block) still surfaces
        as True for a critical incident, but now only to skip the final yes/no
        confirmation — the emergency report is auto-submitted once next_question
        goes null (see the 'आपातकालीन रिपोर्ट' clause in the system prompt).

        Multi-intent (Phase 2): in GENERAL helpline mode (no accident reported)
        there is no incident form to fill, so nothing is 'missing' and no
        next_question is imposed — the model is free to route to the right tool.
        Once accident mode is on, the deterministic accident sequence below
        governs, exactly as before."""
        if not self._accident_mode:
            return []
        missing: list[str] = []
        if not self.state.sub_type:
            missing.append("the incident type (call search_incident_type)")
        if not self.state.location:
            missing.append("the location (call get_current_location)")
        if not self.state.description:
            missing.append("a short description of what happened")
        groups = REQUIRED_FIELDS.get(self.state.category, DEFAULT_REQUIRED_FIELDS) if self.state.category else DEFAULT_REQUIRED_FIELDS
        for group in groups:
            fields = group["fields"]
            still = [item for item in fields if self._field_unanswered(item["field"])]
            if not still:
                continue
            if "combined" in group and len(still) == len(fields):
                missing.append(group["combined"])
            else:
                missing.extend(item["hint"] for item in still)
        return missing

    def _pending_interim_services(self) -> list[str]:
        """Which responders warrant an INTERIM notification right now, in
        priority order, that haven't been notified yet. Gated on a known
        location — we never 'arrange help' before we know where to send it.
        Ambulance whenever the incident is critical (injury / life threat /
        explicit ambulance request); fire additionally when a fire or
        hazardous-material flag is set. Towing/hospital/police are NOT rushed
        here — they are not life-critical and are covered by the full closing
        briefing after submit."""
        if not self.state.location:
            return []
        out: list[str] = []
        if self._is_critical() and "ambulance" not in self._dispatched_services:
            out.append("ambulance")
        if (
            ("Fire" in self.state.flags or "Hazardous material" in self.state.flags)
            and "fire" not in self._dispatched_services
        ):
            out.append("fire")
        return out

    async def _maybe_interim_dispatch(self) -> None:
        """Hindi staged dispatch (#4): the moment an emergency is evident AND we
        know the location, notify the ambulance (and fire, if a fire/hazmat flag
        is set) straight away as an INTERIM notification while the call keeps
        collecting the rest of the report. Sends one `interim_dispatch` frame to
        the browser (which runs the nearest-responder match + logs a
        notification record + shows a chip) and arms a one-turn reassurance note
        for the model. Honesty (Hard Rules 1/2/5): a notification record only —
        no ETA is computed or voiced now; the real responder details are worked
        out after submit and read in the closing briefing. Each service fires at
        most once (deduped via _dispatched_services). No-op in general helpline
        mode — interim dispatch belongs only to the accident branch (a facility
        query that merely mentions 'ambulance' must never trigger a dispatch)."""
        if not self._accident_mode:
            return
        services = self._pending_interim_services()
        if not services:
            return
        self._dispatched_services.update(services)
        await self._safe_send_json({
            "type": "interim_dispatch",
            "services": services,
            "location": self.state.location,
        })
        # The ONE deterministic reassurance line for this whole call, assembled
        # in the correct, fixed order and spoken exactly once (see _agent_turn):
        # EMPATHY first, THEN "help is being arranged". The model is told (system
        # prompt) never to announce help itself, so this is the sole source of
        # that line — it can't come out reordered, doubled, or repeated across
        # turns. Honesty (Hard Rules 1/2/5): "being arranged", never
        # dispatched/tracked/ETA.
        hi_labels = {"ambulance": "एम्बुलेंस", "fire": "दमकल"}
        named_hi = " और ".join(hi_labels.get(s, s) for s in services)
        self._pending_interim_spoken = (
            f"ओह... यह सुनकर मुझे सचमुच बहुत दुख हुआ। आप हिम्मत रखिए, मैं आपके साथ हूँ। "
            f"मैं अभी आपके लिए {named_hi} का इंतज़ाम कर रहा हूँ।"
        )

    # Accident tools: calling any of these means the caller is reporting an
    # accident/injury, so enter the accident branch (the deterministic
    # collect→submit→dispatch→briefing flow then governs).
    _ACCIDENT_TOOLS = frozenset(
        {"search_incident_type", "search_incident_categories", "update_form_field", "submit_incident"}
    )

    async def _mark_intent(self, kind: str) -> None:
        """Classify the call (accident vs information) for Post-Call Analytics,
        emitting one `call_intent` frame per level. accident outranks information
        and is never downgraded, so a call that asks for a hospital and THEN
        reports an accident is counted as an accident call."""
        if self._call_intent == "accident" or kind == self._call_intent:
            return
        self._call_intent = kind
        await self._safe_send_json({"type": "call_intent", "intent": kind})

    async def _dispatch_tool(self, name: str, args: dict) -> dict:
        """Multi-intent dispatch: run the helpline tools (facility / info /
        complaint) here, fall back to the shared accident tools otherwise, and
        flip into accident mode the moment an accident tool is used."""
        if name in self._ACCIDENT_TOOLS:
            self._accident_mode = True
            await self._mark_intent("accident")
        helpline_result = await self._maybe_dispatch_helpline_tool(name, dict(args or {}))
        if helpline_result is not None:
            await self._mark_intent("information")
            logger.info("Helpline tool: %s(%s)", name, json.dumps(args, ensure_ascii=False, default=str)[:200])
            return helpline_result
        return await super()._dispatch_tool(name, args)

    def _mark(self, key: str, seconds: float) -> None:
        self._turn_stats[key] = self._turn_stats.get(key, 0.0) + seconds

    def _log_turn_stats(self) -> None:
        if _LOG_LATENCY and self._turn_stats:
            stats = "  ".join(f"{k}={v * 1000:.0f}ms" for k, v in self._turn_stats.items())
            logger.info("[latency] %s  backend=%s  q429=%d",
                        stats, self._turn_backend or "-", self._quota_hits)

    # ── Session lifecycle ────────────────────────────────────────────────────

    async def run(self) -> None:
        require_api_key()   # loud SarvamCredentialsError before anything starts
        gemini_client = _get_hindi_text_client()  # asia-south1 (Change 1); English's us-central1 client untouched
        await self._safe_send_json({"type": "ready"})

        pump_task = asyncio.create_task(self._pump_client())
        stt_feed_task = asyncio.create_task(self._feed_stt())
        # Same infra-level risk as English (see _keepalive's comment in
        # dispatcher_live.py): the up-to-30s wait for the browser's
        # dispatch_update after submission is a silent stretch on this
        # WebSocket with zero bytes in either direction, which a proxy idle-
        # timeout could close out from under the call. Inherited unchanged
        # from DispatcherSession -- keeps both languages' post-submission
        # phase equally protected even though this was only reported on the
        # English side so far.
        keepalive_task = asyncio.create_task(self._keepalive())
        # Change 3: warm the filler PCM cache in the background (once per process,
        # on a dedicated Bulbul stream) so the first conversational turn already has
        # cached audio to play during its thinking gap.
        asyncio.create_task(self._prewarm_fillers())
        try:
            # Resolve GPS upfront (needed to build the first turn's instruction);
            # the pump is already running so the browser's location_result can
            # arrive. Pre-connect Bulbul in parallel so the opening utterance's
            # first chunk isn't delayed by the TLS+config handshake.
            asyncio.create_task(self._preconnect_tts())
            location_result = await self._tool_get_current_location()
            if location_result.get("status") in ("ok", "already_have_location"):
                location_note = f"Detected location: {location_result.get('label', '')}."
            else:
                location_note = "No location was detected."
            self._turn_stats = {}
            # The fixed 1033 greeting is prepended to this first turn's reply as
            # ONE utterance inside _agent_turn (see _opening_line_pending), so the
            # model must NOT greet -- it writes only the location/what-happened
            # question.
            await self._agent_turn(
                gemini_client,
                f"(The call has just connected. The 1033 welcome greeting is added "
                f"automatically at the start of your reply, so do NOT greet or "
                f"welcome yourself. {location_note} This is an OPEN helpline -- do "
                f"NOT assume an accident. Begin now with one short, open question "
                f"asking how you can help the caller.)",
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
                # Staged dispatch (#4): notify ambulance/fire immediately if an
                # emergency is now evident + location known, BEFORE the agent
                # speaks this turn — so the reassurance note is voiced in the
                # same turn the browser gets the interim_dispatch frame.
                await self._maybe_interim_dispatch()
                await self._agent_turn(gemini_client, user_text)

            # The report was submitted (the caller was just told to hold the
            # line): wait for the browser's dispatch_update — the SAME
            # responder ETAs the dashboard displays — then deliver the final
            # closing briefing before the call ends.
            if self.state.submitted and not self._ended.is_set():
                await self._deliver_dispatch_briefing(gemini_client)
        finally:
            for task in (pump_task, stt_feed_task, keepalive_task):
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
                # Multi-intent (Phase 2): facility/complaint round-trip replies.
                if self._resolve_helpline_client_message(mtype, msg):
                    continue
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
                elif mtype == "dispatch_update":
                    # The browser's matching flow finished: the SAME responder
                    # ETAs the dashboard displays — wakes
                    # _deliver_dispatch_briefing. Attributes inherited from
                    # DispatcherSession.__init__.
                    self._dispatch_info = msg.get("services") or None
                    self._dispatch_ready.set()
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

    async def _agent_turn(self, gemini_client, user_text: str, config=None) -> None:
        """One full agent turn: reason (with tool calls) → speak → hand the
        turn back to the caller. Mirrors the Live path's client-facing
        status/turn_complete choreography exactly. `config` overrides the
        generation config for this turn only (used by the closing briefing,
        which needs a higher output-token ceiling)."""
        turn_start = time.monotonic()
        await self._safe_send_json({"type": "status", "state": "thinking"})
        # Open the Bulbul socket (TLS + config handshake) in parallel with
        # Gemini reasoning instead of lazily inside speak() -- on the first
        # turn of a call and after a barge-in tore the socket down, this
        # removes the connect time from the audible-silence critical path.
        # ensure_open() is lock-guarded, so racing speak()'s own call is safe
        # (speak() just waits on the same lock); deliberately NOT cancelled
        # when reasoning finishes first -- cancelling a mid-handshake connect
        # could strand a half-configured socket, and the task self-completes
        # in well under a turn anyway. Failures are non-fatal (speak()
        # retries with a fresh socket).
        asyncio.create_task(self._preconnect_tts())
        # Change 3: cover the thinking gap with a cached filler, overlapping the
        # Gemini round-trip. Skipped on the opening greeting (config-less first
        # turn) and the closing briefing (config is self._briefing_config). The
        # per-turn flag is set SYNCHRONOUSLY so the Exotel lookup filler defers to
        # it -> at most one filler ever plays per turn (no overlapping audio).
        self._ack_filler_active = False
        filler_task = None
        if _FILLER_ENABLED and not self._opening_line_pending and config is None:
            self._ack_filler_active = True
            filler_task = asyncio.create_task(self._play_filler())
        reply = await self._reason(gemini_client, user_text, config=config)
        if filler_task is not None:
            try:
                await filler_task  # filler playback finished before the reply -> no overlap/clip
            except Exception:
                pass
        completed = True
        if self._opening_line_pending:
            # OPENING TURN: speak the fixed 1033 greeting AND the model's opening
            # reply as ONE continuous, UNINTERRUPTIBLE Bulbul utterance. Being a
            # single utterance is what guarantees the greeting is heard: a
            # SEPARATE greeting utterance could be flushed by the NEXT turn's
            # "interrupted" (sent at the top of every _speak_or_fallback) despite
            # the playback-hold, which is why the greeting was going missing.
            # allow_bargein=False means a caller talking over it can't cut it. It
            # is still guaranteed even if the model returned nothing (fallback
            # question). The prompt tells the model the greeting is added
            # automatically, so its reply is just the location/what-happened
            # question -- never the greeting itself.
            self._opening_line_pending = False
            body = self._render_for_speech(reply) if reply else _OPENING_FALLBACK_QUESTION
            completed = await self._speak_or_fallback(
                f"{_HINDI_OPENING_LINE} {body}", allow_bargein=False
            )
        elif self._pending_interim_spoken:
            # #4 (staged dispatch): the ONE interim-dispatch turn is spoken
            # DETERMINISTICALLY in a fixed, correct order — EMPATHY first, then
            # "help is being arranged", then the next question — so it can never
            # come out reordered, doubled, or repeated. The model's reply this
            # turn is used only for its tool calls (form filling), never for
            # speech; the model is told in the prompt to never announce help.
            interim = self._pending_interim_spoken
            self._pending_interim_spoken = None
            missing = self._compute_still_missing()
            question = _CANONICAL_QUESTIONS.get(missing[0]) if missing else None
            if question:
                spoken = f"{interim} {question}"
            else:
                # No canonical next question (summarize/submit stage): let the
                # model's reply carry the continuation after the reassurance.
                tail = self._render_for_speech(reply) if reply else ""
                spoken = f"{interim} {tail}".strip()
            completed = await self._speak_or_fallback(spoken)
        elif reply:
            # Change 2: pipeline the fast-path reply's sentences to Bulbul for a
            # faster first-audio. "single_round" in _turn_stats means THIS reply came
            # from the single-round fast path (ack + code-appended canonical
            # question) -- exactly the composed, already-guard-checked text the spec
            # scopes streaming to. Every other reply keeps the single-synthesis path.
            pipeline = "single_round" in self._turn_stats
            reply = self._render_for_speech(reply)
            completed = await self._speak_or_fallback(reply, pipeline=pipeline)
        await self._safe_send_json({"type": "turn_complete"})
        if not self.state.submitted:
            await self._enter_listening(drain=completed)
        self._mark("turn_total", time.monotonic() - turn_start)
        self._log_turn_stats()

    async def _deliver_dispatch_briefing(self, gemini_client) -> None:
        """The final turn of a submitted call: wait (bounded) for the
        browser's dispatch_update frame — the SAME responder ETAs the
        dashboard is already displaying, never recomputed here — then speak
        the closing briefing (responder ETAs → SOP safety guidance →
        follow-up-call script → goodbye, see dispatch_briefing.py) and tell
        the browser the call is complete. If the data never arrives, the
        briefing honestly skips the ETA section rather than inventing one."""
        # #2: speak a short "please hold" line FIRST, before waiting on
        # dispatch_update + generating the briefing, so it fills the silent gap
        # right after the post-submit ack. Uses the same Bulbul-or-text path
        # every reply uses; carries no ETA data.
        if not self._ended.is_set():
            await self._speak_or_fallback(_HINDI_HOLD_LINE)
        try:
            await asyncio.wait_for(self._dispatch_ready.wait(), timeout=_DISPATCH_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning("No dispatch_update within %.0fs -- closing without responder ETAs",
                           _DISPATCH_WAIT_S)
        if self._ended.is_set():
            return
        instruction = build_briefing_instruction(self.state, self._dispatch_info, "hi-IN")
        self._turn_stats = {}
        await self._agent_turn(gemini_client, instruction, config=self._briefing_config)
        await self._safe_send_json({"type": "call_complete"})

    async def _preconnect_tts(self) -> None:
        try:
            await self._tts.ensure_open()
        except Exception:
            logger.debug("Bulbul pre-connect failed (speak() will retry)", exc_info=True)

    # ── Change 3: filler acknowledgment ──────────────────────────────────────
    async def _prewarm_fillers(self) -> None:
        """One-time (per process) filler pre-synthesis, kicked off at call start on
        a DEDICATED Bulbul stream so it never serialises with the reply TTS
        (self._tts). After the first call the module cache is warm and this no-ops."""
        if not _FILLER_ENABLED or all(t in _filler_pcm_cache for t in _FILLER_TEXTS):
            return
        tts = BulbulStream(self._language)
        try:
            await _synthesize_fillers(tts)
        finally:
            await tts.close()

    def _next_filler_text(self) -> Optional[str]:
        """Next cached filler in rotation, avoiding an immediate repeat. None if none
        are synthesized yet (the turn then simply proceeds with no filler)."""
        available = [t for t in _FILLER_TEXTS if t in _filler_pcm_cache]
        if not available:
            return None
        for _ in range(len(_FILLER_TEXTS)):
            self._filler_idx = (self._filler_idx + 1) % len(_FILLER_TEXTS)
            cand = _FILLER_TEXTS[self._filler_idx]
            if cand in _filler_pcm_cache and cand != self._last_filler:
                self._last_filler = cand
                return cand
        self._last_filler = available[0]  # only one available (or all == last)
        return available[0]

    async def _play_filler(self) -> None:
        """Stream a cached filler's PCM to the caller to cover the 'thinking' gap.
        Deliberately does NOT read self._stt (single-reader barge-in rule intact).
        Holds until the filler's own playback roughly finishes so the reply's flush
        doesn't clip it. Any send failure just ends the filler quietly."""
        text = self._next_filler_text()
        if text is None:
            return
        pcm = _filler_pcm_cache.get(text)
        if not pcm:
            return
        chunk_bytes = 4096
        first_at: Optional[float] = None
        total_samples = 0
        for i in range(0, len(pcm), chunk_bytes):
            chunk = pcm[i:i + chunk_bytes]
            if first_at is None:
                first_at = time.monotonic()
            total_samples += len(chunk) // 2
            try:
                await self.websocket.send_bytes(chunk)
            except Exception:
                return
        if first_at is not None and total_samples:
            remaining = (first_at + total_samples / TTS_SAMPLE_RATE) - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)

    def _render_for_speech(self, text: str) -> str:
        """The speech-rendering layer between Gemini and Bulbul (see the
        _OPENERS comment above for why this, not an SSML/emotion API, is the
        real lever). This does NOT rewrite meaning -- that's the system
        prompt's job, done inside Gemini's single reasoning call where it
        has full conversational context to judge where a pause actually
        belongs. This is a narrow, deterministic, zero-cost, zero-latency
        pass that fixes exactly two things prompting can't fully guarantee:
        a known opener spoken with a flat comma instead of the pause
        punctuation actually asked for, and the same opener repeating on
        consecutive turns."""
        # Defensive first: strip any leaked internal/meta content (tool-result
        # fields like "tone_reminder", parenthesized system notes) so it never
        # reaches Bulbul -- prompting alone can't guarantee the model never
        # echoes them.
        text = _strip_meta_leak(text)
        rendered = _OPENER_COMMA_RE.sub(lambda m: f"{m.group(1)}... ", text, count=1)
        opener = next((o for o in _OPENERS if rendered.startswith(o)), None)
        if opener is not None and opener == self._last_opener:
            rendered = rendered[len(opener):].lstrip()
            opener = next((o for o in _OPENERS if rendered.startswith(o)), None)
        self._last_opener = opener
        return rendered

    async def _reason(self, gemini_client, user_text: str, config=None) -> str:
        self._history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        self._turn_backend = None
        spoken_fallback = ""
        for _round_num in range(_MAX_TOOL_ROUNDS):
            got = await self._reason_round(gemini_client, config)
            if got is None:
                # BOTH backends failed with no usable output this round -- apologize
                # in Hindi and keep the call alive rather than dying silently.
                return spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]
            text, function_calls, model_parts = got
            # Append the model turn to the CANONICAL Gemini-format history that
            # drives both backends (with an explicit "model" role -- a stricter
            # model 400s the whole call if any earlier turn's role comes back unset).
            self._history.append(types.Content(role="model", parts=model_parts))

            if not function_calls:
                # Never return silence -- an empty/blocked response still produces a
                # spoken reply rather than leaving the caller hanging.
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

            # Fast path (see _CANONICAL_QUESTIONS) -- UNCHANGED and backend-agnostic:
            # a question-free ack alongside only search/update tools becomes "ack +
            # code-appended canonical next_question", skipping a whole reply round.
            # _compose_single_round_reply's guards (fast-path tools only; non-empty
            # ack with no '?'; a canonical question exists) still gate identically,
            # and the canonical question is still appended in code.
            composed = self._compose_single_round_reply(
                text, {fc.name for fc in function_calls}
            )
            if composed is not None:
                # Mirror the exact history shape a normal two-round turn leaves
                # behind (model: fc+ack / user: results / model: spoken reply),
                # so later turns -- and the model itself -- see the appended
                # question as something it asked.
                self._history.append(
                    types.Content(role="model", parts=[types.Part(text=composed)])
                )
                self._turn_stats["single_round"] = 0.0  # visible in [latency] logs
                return composed
        logger.warning("Reasoning used %d tool rounds without a final answer", _MAX_TOOL_ROUNDS)
        return spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]

    async def _reason_round(self, gemini_client, config):
        """One reasoning round, BACKEND-AGNOSTIC. Returns (text, function_calls,
        model_parts) -- function_calls are types.FunctionCall objects and model_parts
        are types.Content parts, IDENTICAL in shape per backend so the rest of
        _reason (tool dispatch, fast-path guards, _history mirroring) is untouched --
        or None on total failure.

        Sarvam (sarvam-105b-conversations) is PRIMARY; ANY Sarvam failure this round
        (exception / non-200 / empty / malformed tool call) falls back to the Gemini
        path FOR THIS ROUND. HINDI_REASONING_BACKEND=gemini skips Sarvam entirely.
        Both paths read the same canonical history + the same (possibly Exotel-
        overridden) system instruction, so the deterministic flow is identical."""
        briefing = config is self._briefing_config
        if self._use_sarvam:
            t0 = time.monotonic()
            try:
                system_prompt = (self._briefing_config if briefing else self._gen_config).system_instruction
                messages = gemini_history_to_openai_messages(self._history, system_prompt)
                max_tokens = _BRIEFING_MAX_OUTPUT_TOKENS if briefing else _MAX_OUTPUT_TOKENS
                result = await sarvam_generate(messages, self._openai_tools, max_tokens=max_tokens)
                self._mark("sarvam", time.monotonic() - t0)
                self._turn_backend = "sarvam"
                if result.reasoning_tokens:  # expected 0 with reasoning_effort:low -- warn loudly if not
                    logger.warning("Sarvam returned %d reasoning tokens (reasoning_effort:low expected 0)",
                                   result.reasoning_tokens)
                fcs = [types.FunctionCall(id=tc.id, name=tc.name, args=tc.args) for tc in result.tool_calls]
                model_parts = ([types.Part(text=result.text)] if result.text else []) + \
                    [types.Part(function_call=fc) for fc in fcs]
                return result.text, fcs, model_parts
            except SarvamReasoningError as e:
                self._mark("sarvam_fail", time.monotonic() - t0)
                logger.warning("Sarvam reasoning failed -> Gemini fallback: %s", str(e)[:180])
                # fall through to the Gemini fallback below

        # Gemini fallback (also the primary path when HINDI_REASONING_BACKEND=gemini).
        t0 = time.monotonic()
        response = await self._generate_with_retry(gemini_client, config=config)
        self._mark("gemini", time.monotonic() - t0)
        self._turn_backend = "gemini"
        if response is None:
            return None
        candidate = (response.candidates or [None])[0]
        if candidate is None or candidate.content is None:
            return None
        model_parts = candidate.content.parts or []
        text = " ".join(p.text.strip() for p in model_parts if getattr(p, "text", None)).strip()
        function_calls = [p.function_call for p in model_parts if getattr(p, "function_call", None)]
        return text, function_calls, model_parts

    def _compose_single_round_reply(self, ack: str, fc_names: set) -> Optional[str]:
        """Compose "model's acknowledgment + code-appended canonical question"
        for the single-round fast path, or None to fall back to the normal
        second Gemini round. Every guard errs toward the fallback -- the fast
        path must only fire when the composed reply is provably equivalent to
        what the second round was for:
          - only search_incident_type / update_form_field ran (anything else
            has a result the model genuinely needs to read before speaking);
          - the model wrote a non-empty acknowledgment with NO question of its
            own (a "?" anywhere means appending ours could double-question);
          - the deterministic next_question exists AND has a canonical Hindi
            phrasing (next_question=None is the summarize-and-confirm stage,
            which genuinely needs the model)."""
        if not fc_names or fc_names - _FAST_PATH_TOOLS:
            return None
        # Strip leaked meta BEFORE the guards so it neither reaches speech nor
        # falsely trips the "?" guard (a leaked "(next_question: ...?)").
        ack = _strip_meta_leak((ack or "").strip())
        if not ack or "?" in ack or self.state.submitted:
            return None
        missing = self._compute_still_missing()
        if not missing:
            return None
        question = _CANONICAL_QUESTIONS.get(missing[0])
        if question is None:
            return None
        if ack[-1] not in "।.!…":
            ack += "।"
        logger.info("Single-round fast path: appended canonical question for %r", missing[0])
        return f"{ack} {question}"

    async def _generate_with_retry(self, gemini_client, config=None):
        # The closing briefing (config is self._briefing_config) is a longer
        # single generation, so it gets the longer briefing timeout; every
        # normal conversational turn gets the tight one, so a stuck call fails
        # fast rather than hanging the caller on "Thinking...".
        timeout = _BRIEFING_TIMEOUT_S if config is self._briefing_config else _GEMINI_TIMEOUT_S
        for attempt in range(2):
            try:
                return await asyncio.wait_for(
                    gemini_client.aio.models.generate_content(
                        model=_TEXT_MODEL, contents=self._history, config=config or self._gen_config,
                    ),
                    timeout=timeout,
                )
            except Exception as e:
                if getattr(e, "code", None) == 429 or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    self._quota_hits += 1  # counted into the [latency] q429 field
                logger.exception("Gemini text call failed (attempt %d/2)", attempt + 1)
                await asyncio.sleep(0.5)
        return None

    # ── Speaking (Bulbul) ────────────────────────────────────────────────────

    async def _speak_or_fallback(self, text: str, allow_bargein: bool = True, pipeline: bool = False) -> bool:
        """Speak via Bulbul. Returns True if the reply completed normally
        (including the "TTS failed, shown as text" fallback -- that's still a
        completed turn), or False if the caller genuinely barged in and
        playback was cut short. On failure, the reply is surfaced as text
        (spec: 'display the Gemini response as text and log the error').

        allow_bargein=False makes the utterance uninterruptible -- used for the
        fixed 1033 opening greeting so a caller talking over it can never leave
        it partially or never spoken (Issue 1). It also keeps the single-reader
        invariant trivially: with barge-in off, self._stt is simply not read
        during that utterance.

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
            async for chunk in self._tts.speak(text, force_split=pipeline):
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
                if allow_bargein and armed_at is not None and now >= armed_at:
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
                if not allow_bargein:
                    await asyncio.sleep(min(remaining, 0.2))
                    continue
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
