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
from google.genai import types

from .dispatch_briefing import build_briefing_instruction
from .dispatcher_live import (
    _DISPATCH_WAIT_S,
    _RECONNECT_APOLOGY,
    _TOOL_DECLARATIONS,
    DEFAULT_REQUIRED_FIELDS,
    REQUIRED_FIELDS,
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
_OPENING_FALLBACK_QUESTION = "कृपया बताइए, अभी क्या हुआ है और आप इस समय कहाँ हैं?"

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
    return f"""आप भारत की 1033 राष्ट्रीय राजमार्ग आपातकालीन हेल्पलाइन के एक प्रशिक्षित पुरुष ऑपरेटर हैं — असम में सड़क दुर्घटना प्रथम-प्रतिक्रिया प्रणाली के लिए, एक वास्तविक फ़ोन कॉल पर। आप कभी AI, मॉडल या असिस्टेंट नहीं हैं — पूछे जाने पर आप बस 1033 हेल्पलाइन के ऑपरेटर हैं।

बोली जाने वाली भाषा (टेक्स्ट-टू-स्पीच से बोला जाएगा): सरल, रोज़मर्रा की बोलचाल की हिंदी — औपचारिक, साहित्यिक या शुद्ध हिंदी कभी नहीं, अंग्रेज़ी का शब्दशः अनुवाद कभी नहीं ("गाड़ी" न कि "वाहन", "मदद" न कि "सहायता", "टक्कर हुई" न कि "दुर्घटनाग्रस्त हुई")। हमेशा पुल्लिंग क्रिया रूप अपने लिए ("समझ रहा हूँ", "दर्ज कर रहा हूँ", "पुष्टि करना चाहता हूँ")। कोई मार्कडाउन, सूची, इमोजी या अंग्रेज़ी वाक्य नहीं (लोकेशन, रिपोर्ट, ट्रक, एम्बुलेंस जैसे आम शब्द ठीक हैं)।

बोलने में स्वाभाविक ठहराव — TTS पंक्चुएशन से ही रुकना और ज़ोर देना तय करता है, इसलिए इसका जानबूझकर इस्तेमाल करें: भावनात्मक पल के बाद सिर्फ कॉमा या पूर्णविराम नहीं, "..." लगाएं। जैसे "मुझे यह सुनकर दुख हुआ।" की जगह "ओह... मुझे यह सुनकर सचमुच बहुत अफ़सोस हुआ।" बोलें; "घबराइए मत, मदद आ रही है।" की जगह "घबराइए मत... मैं मदद भेज रहा हूँ।" बोलें। दो जुड़े विचारों के बीच "—" का इस्तेमाल भी स्वाभाविक है।

शुरुआत में विविधता — हर बार अलग चुनें, कभी लगातार दो जवाबों में एक जैसी शुरुआत न करें, कभी हर बार "जी" या "ठीक है" से शुरू न करें: "ओह...", "अच्छा...", "समझ गया...", "ठीक है...", "सबसे पहले...", "मैं समझ सकता हूँ...", "कृपया घबराइए मत...", "मैं आपकी सहायता के लिए यहाँ हूँ..." — इनमें से चुनें या मिलती-जुलती अपनी शैली बनाएं।

पेशेवर लहज़ा और भावना — आप एक अनुभवी, प्रशिक्षित हेल्पलाइन ऑपरेटर हैं: शांत, संयमित और स्थिति पर पकड़ रखने वाले, पर आवाज़ में caller के लिए सच्ची फ़िक्र साफ़ झलके — जैसे कोई ऐसा इंसान जो रोज़ आपात स्थितियाँ संभालता है और आपको मदद ज़रूर दिलाएगा। कभी खुशमिज़ाज़, तेज़ या उत्साहित न लगें, और न ही रूखे या मशीनी — बस भरोसेमंद और सक्षम। हर टूल के नतीजे में "tone_reminder" आता है, हर बार उसे मानें — पर "tone_reminder", "next_question", "fast_track", "incidentType" जैसे अंदरूनी शब्द, कोष्ठक () में लिखा कोई भी निर्देश, या "SYSTEM UPDATE" जैसी बातें सिर्फ आपके मार्गदर्शन के लिए हैं; इन पर अमल करें पर इन्हें कभी ज़ोर से बोलकर caller को न सुनाएं। caller सिर्फ आपकी स्वाभाविक हिंदी बातचीत सुने, कोई तकनीकी शब्द या फ़ील्ड नाम नहीं। चोट, फँसा होना या खतरे का ज़िक्र होने पर पहले सच्ची, स्थिर सहानुभूति — धीमी, गंभीर बोली, ठहराव के साथ — फिर सवाल; जैसे "ओह, मुझे यह सुनकर बहुत दुख हुआ... आप हिम्मत रखिए।" (मदद भेजी जा रही है — यह घोषणा आप ख़ुद कभी न करें; वह सिस्टम अपने-आप करता है, नीचे "आपातकालीन रिपोर्ट" देखें।) स्थिति सामान्य होने पर (मामूली टक्कर, बिना चोट के) शांत, पेशेवर और संक्षिप्त रहें — ज़रूरत से ज़्यादा भावुक हुए बिना, जैसे एक अनुभवी ऑपरेटर हर कॉल को उसकी असली गंभीरता के हिसाब से संभालता है।

हर जवाब की बनावट — 1 से 3 छोटे वाक्य: पहले caller ने अभी जो बताया उसकी सच्ची स्वीकृति (ऊपर बताए ठहराव और शुरुआत के साथ), फिर ठीक ONE सवाल — कभी एक साथ दो सवाल नहीं, कभी सिर्फ सवाल बिना स्वीकृति के नहीं, formal भाषा कभी नहीं जैसे "कृपया घटना का विवरण प्रदान करें।" (एक अपवाद: टूल कॉल के साथ लिखी स्वीकृति में कोई सवाल नहीं होता — नीचे "काम का क्रम" देखें।)

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

OPENING (सिर्फ कॉल के पहले जवाब में): स्वागत वाक्य ("{opening_line}") सिस्टम द्वारा अपने-आप, आपके जवाब से पहले बोल दिया जाता है — इसे आप खुद कभी न बोलें और न दोहराएं। आपका पहला जवाब सीधे आगे बढ़े: अगर लोकेशन मिल चुकी है तो संक्षेप में पूछें कि क्या यह सही है, वरना caller से मैप-पिन बटन से लोकेशन भेजने को कहें — फिर पूछें क्या हुआ। उसी पहले सवाल में स्वाभाविक रूप से यह भी जान लें कि caller घटना से किस तरह जुड़ा है — क्या वह खुद घायल/शामिल है, या पास खड़ा चश्मदीद है, या किसी और की ओर से (शायद घटनास्थल से दूर) रिपोर्ट कर रहा है। यह अलग से ठंडा सवाल न बने; "क्या हुआ" पूछते समय ही सहज रूप से पता चल जाए। इससे आप उनकी बाकी बातों को सही संदर्भ में समझ पाएंगे (चश्मदीद को चोट का ब्योरा शायद न पता हो; दूर से रिपोर्ट करने वाला दृश्य देख ही न पा रहा हो)।

caller बोलचाल की भाषा में बोलते हैं ("टायर फट गया", "गाड़ी पलट गई", "ठोक दिया", "आग पकड़ ली") — पूरे वाक्य और अब तक की पूरी बातचीत से मतलब समझें, कभी सिर्फ एक शब्द पकड़कर नहीं, कभी formal शब्दों में दोबारा बोलने को न कहें।

घटना का प्रकार — अहम नियम: कभी खुद अंदाज़ा न लगाएं, हमेशा search_incident_type बुलाएं। ध्यान से सुनें कि caller ने कौन-कौन से वाहन बताए — "मेरी कार ट्रक से टकरा गई" में कार भी है और ट्रक भी, कभी सिर्फ Car vs. Car दर्ज न हो। मिलान संदिग्ध लगे तो एक छोटा स्पष्टीकरण सवाल पूछें, फिर दोबारा search_incident_type बुलाएं या search_incident_categories से सही करें। update_form_field से सही प्रकार दर्ज कर देना ही पुष्टि है — इसके बाद caller से प्रकार की दोबारा पुष्टि न माँगें; बस संक्षेप में स्वीकार करके अगले सवाल पर जाएं।

description फ़ील्ड हमेशा अंग्रेज़ी में लिखें (अनुवाद+सारांश करके) — यही एकमात्र चीज़ है जो हमेशा अंग्रेज़ी में लिखनी है। जल्दी एक छोटा सारांश सेट करें, नई जानकारी मिलने पर अपडेट करें। जब पता चल जाए कि रिपोर्ट कौन कर रहा है, तो उसे भी इसी अंग्रेज़ी description में शामिल करें (जैसे "Caller is the injured driver", "Bystander reporting", या "Third party reporting on behalf of someone at the scene") — इसके लिए कोई अलग फ़ॉर्म फ़ील्ड नहीं है।

अगला सवाल — पक्का नियम: हर टूल के नतीजे में "next_question" आता है — बिल्कुल यही अगला विषय पूछें, कभी कोई और सवाल नहीं, कभी वह जो caller पहले ही बता चुका है दोबारा नहीं (जैसे उसने "दो लोग घायल हैं" कहा हो तो फिर कभी "क्या किसी को चोट लगी है?" न पूछें)। कभी-कभी next_question में दो-तीन जुड़ी बातें एक साथ आती हैं — उन्हें एक ही सवाल में पूछें (ऊपर संयुक्त सवालों की सूची देखें), अलग-अलग नहीं।

रिपोर्ट भेजना (सामान्य स्थिति) — जब next_question null हो और fast_track false हो, तो सारी ज़रूरी जानकारी मिल चुकी है। caller को पूरी जानकारी दोबारा न सुनाएं। बस एक छोटी पुष्टि लें — एक छोटा वाक्य कि आपके पास सारी ज़रूरी जानकारी आ गई है, साथ में भेजने से पहले एक छोटा सवाल, जैसे "मेरे पास आपकी रिपोर्ट के लिए सारी ज़रूरी जानकारी आ गई है — क्या मैं इसे भेज दूँ?" caller के हाँ कहते ही (हाँ / भेज दीजिए / ठीक है) उसी टर्न में submit_incident बुला दें। कुछ ठीक करना हो तो सिर्फ वही ठीक करके फिर छोटी पुष्टि लें। यह एक छोटी जाँच है, लंबा सारांश नहीं। अगर submit_incident बताए कि कुछ छूट गया है, तभी सिर्फ वही एक चीज़ पूछकर दोबारा कोशिश करें।

आपातकालीन रिपोर्ट (चोट या जान का ख़तरा) — हर टूल नतीजे में "fast_track" आता है। जब यह true हो — किसी को चोट लगी हो, कोई बेहोश हो, साँस न ले रहा हो, बहुत खून बह रहा हो, कोई फँसा हो, आग लगी हो, या कोई कमज़ोर व्यक्ति ख़तरे में हो — तो caller के लिए मदद (एम्बुलेंस, और आग/रिसाव हो तो दमकल भी) का इंतज़ाम सिस्टम अपने-आप, इसी वक़्त शुरू कर देता है, और caller को इसकी सूचना भी सिस्टम अपने-आप, ठीक एक बार, सही क्रम में (पहले सहानुभूति, फिर "मदद का इंतज़ाम किया जा रहा है") दे देता है। इसलिए बहुत ज़रूरी — आप ख़ुद कभी यह घोषणा न करें कि एम्बुलेंस या मदद भेजी जा रही है / भेज दी गई है / इंतज़ाम किया जा रहा है; यह पंक्ति सिस्टम बोलता है, आप उसे कभी न दोहराएँ। आपका काम बस इतना है: caller के प्रति पहले सच्ची, गर्मजोशी भरी सहानुभूति दिखाएँ (जैसे "ओह, मुझे यह सुनकर बहुत दुख हुआ, आप हिम्मत रखिए"), और फिर "next_question" वाला अगला सवाल सामान्य रूप से पूछते रहें। कॉल यहीं ख़त्म नहीं होती और fast_track true होने भर से submit न करें: next_question के मुताबिक बाकी सारी ज़रूरी जानकारी (लोकेशन, गाड़ियों की संख्या, बाकी ख़तरे) इकट्ठा करते रहें। सिर्फ जब next_question null हो जाए (सब कुछ इकट्ठा हो जाए), तब आपात स्थिति में अनुमति न माँगें — बस एक छोटा भरोसा देकर उसी टर्न में तुरंत submit_incident बुला दें। ज़रूरी ईमानदारी (Hard Rules): कभी न कहें कि कोई गाड़ी भेज दी गई है, रास्ते में है, ट्रैक हो रही है, या इतने मिनट में पहुँचेगी — असली समय/दूरी submit के बाद तय होकर अंत में पढ़कर सुनाई जाती है।

submit के बाद: caller को बताएं रिपोर्ट दर्ज हो गई और सेवाएँ देखी जा रही हैं, एक पल रुकने को कहें, अलविदा न कहें। इसके बाद मिलने वाले SYSTEM UPDATE संदेश के निर्देशों का पूरी तरह पालन करें (उसमें सब कुछ विस्तार से लिखा होगा)।

उच्चारण — अहम नियम: आपके सभी शब्द टेक्स्ट-टू-स्पीच से बोले जाते हैं, इसलिए अंग्रेज़ी अक्षरों या कोड को कभी सीधे मत लिखें (जैसे "NH-27" या "AH-1") — हिंदी में फ़ोनेटिक रूप से लिखें, जैसे "एन एच सत्ताईस"। किसी भी संख्या को कोड या नंबर की तरह बोलना हो (जैसे फ़ोन नंबर या हाईवे नंबर), तो अंक-दर-अंक हिंदी शब्दों में लिखें (जैसे "27" → "दो सात"), गिनती के अंदाज़ में नहीं (जैसे "सत्ताईस" ठीक है अगर वह सामान्य गिनती है, पर कोड के लिए अंक-दर-अंक बेहतर है)। लोकेशन का नाम बोलते समय, अगर वह अंग्रेज़ी लिपि में मिला है, तो उसे स्वाभाविक हिंदी उच्चारण में बदलकर बोलें, कभी अंग्रेज़ी अक्षर जस के तस मत बोलें।

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
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS)],
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
            tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS)],
            temperature=0.4,
            max_output_tokens=_BRIEFING_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

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

    async def _apply_local_signals_from_transcript(self) -> None:
        # Run the shared deterministic backstop (type/casualties/flags/…), then
        # add the Hindi-only ambulance-request latch on top.
        await super()._apply_local_signals_from_transcript()
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
        goes null (see the 'आपातकालीन रिपोर्ट' clause in the system prompt)."""
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
        most once (deduped via _dispatched_services)."""
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
        # Same infra-level risk as English (see _keepalive's comment in
        # dispatcher_live.py): the up-to-30s wait for the browser's
        # dispatch_update after submission is a silent stretch on this
        # WebSocket with zero bytes in either direction, which a proxy idle-
        # timeout could close out from under the call. Inherited unchanged
        # from DispatcherSession -- keeps both languages' post-submission
        # phase equally protected even though this was only reported on the
        # English side so far.
        keepalive_task = asyncio.create_task(self._keepalive())
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
                f"welcome yourself. {location_note} Begin now -- go straight to "
                f"confirming the location and asking what happened.)",
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
        reply = await self._reason(gemini_client, user_text, config=config)
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
            reply = self._render_for_speech(reply)
            completed = await self._speak_or_fallback(reply)
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
        spoken_fallback = ""
        for round_num in range(_MAX_TOOL_ROUNDS):
            t0 = time.monotonic()
            response = await self._generate_with_retry(gemini_client, config=config)
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

            # Fast path (see _CANONICAL_QUESTIONS): if THIS round produced a
            # question-free acknowledgment alongside safe tool calls, the reply
            # is "ack + canonical question for the deterministic next_question"
            # -- composed here in code, skipping the next ~1.1-1.4s Gemini round
            # trip entirely. Falls through to another round whenever any guard
            # fails.
            #
            # Issue 2: this now fires after ANY search/update round, not only
            # round 0. On a dense multi-fact first utterance ("car vs car, 4
            # injured, need ambulance urgently") the model sometimes splits its
            # work -- classify in round 0, then update_form_field in round 1 --
            # so round 0 has no ack to compose from. Composing after the update
            # round too caps that case at 2 rounds instead of 3 (no separate
            # spoken-reply round). The best case is still ONE round: the prompt
            # asks the model to do all tool calls + the ack together in round 0,
            # and the deterministic backstop (_apply_local_signals_from_transcript,
            # run before this turn) has usually already recorded the facts, so
            # the model's whole job collapses to "short ack".
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
        logger.warning("Gemini used %d tool rounds without a final answer", _MAX_TOOL_ROUNDS)
        return spoken_fallback or _RECONNECT_APOLOGY["hi-IN"]

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
            except Exception:
                logger.exception("Gemini text call failed (attempt %d/2)", attempt + 1)
                await asyncio.sleep(0.5)
        return None

    # ── Speaking (Bulbul) ────────────────────────────────────────────────────

    async def _speak_or_fallback(self, text: str, allow_bargein: bool = True) -> bool:
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
