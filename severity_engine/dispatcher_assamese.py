"""dispatcher_assamese.py — the ASSAMESE (as-IN) conversational voice dispatcher.

Pipeline:  caller audio → Sarvam Saaras v3 STT (as-IN — verified working, NOT
beta-gated) → shared Sarvam reasoning (sarvam-105b-conversations, Gemini
per-turn fallback) → ElevenLabs eleven_v3 STREAMING TTS → PCM16/24kHz to browser.

Why a separate TTS engine: Sarvam Bulbul does NOT have as-IN enabled on this
account ("request beta access to as-IN"), and Assamese is only available on
ElevenLabs' eleven_v3 model. So Assamese speech is served by
elevenlabs_speech.ElevenLabsV3Stream (a drop-in for BulbulStream). Everything
ELSE is the Hindi pipeline, reused by inheritance.

DESIGN — zero edits to dispatcher_hindi.py / dispatcher_live.py's runtime:
`AssameseDispatcherSession` SUBCLASSES `HindiDispatcherSession`, so it inherits —
byte-for-byte, by `self.`-dispatch — the entire deterministic flow: run()'s
session lifecycle, the shared Sarvam+Gemini reasoning (`_reason`/`_reason_round`/
`_gemini_round`), the single-round fast-path machinery, the tool handlers +
vehicle-pair / same-type overrides, `_compute_still_missing` sequencing, the
transcript backstop, keep-alive, barge-in (single-reader), and the browser WS
protocol. Hindi inlines its language-specific text as MODULE constants read
directly inside methods (not via `self.`), which subclassing alone cannot
override without editing the Hindi file — so, per the explicit project rule
("duplication in the Assamese module is strictly preferable to ANY edit to the
Hindi session"), the handful of methods that read those constants are DUPLICATED
here with Assamese content. Only the STT object, the TTS object, and Assamese
text differ. dispatcher_hindi.py and dispatcher_live.py are unchanged.

Fillers: Hindi's `_prewarm_fillers` reads `self._language`, which the base never
sets → it raises AttributeError inside a fire-and-forget task, so Hindi's
thinking-gap fillers are already latently inert in production. Assamese also has
no Bulbul path for fillers, so `_prewarm_fillers` is overridden to a clean no-op
and `_agent_turn` never launches one (matching Hindi's actual runtime behavior).

⚠⚠ ALL ASSAMESE (অসমীয়া) TEXT IN THIS FILE IS MACHINE-AUTHORED, PENDING NATIVE
REVIEW. It is grammatically plausible and honesty-rule-compliant, but MUST be
proofread by a native speaker before any real deployment. (Assamese verbs are
NOT gender-marked the way Hindi's are — "মই কৰোঁ/বুজিছোঁ" is gender-neutral — so
unlike Hindi there is no voice-gender ↔ verb-grammar sync constraint here.)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import WebSocket
from google.genai import types

from . import dispatcher_hindi as _hindi
from .dispatcher_hindi import HindiDispatcherSession
from .dispatch_briefing import build_briefing_instruction
from .dispatcher_live import (
    _DISPATCH_WAIT_S,
    _RECONNECT_APOLOGY,
    _TOOL_DECLARATIONS,
)
from .elevenlabs_speech import ElevenLabsTTSError, ElevenLabsV3Stream
from .helpline_tools import HELPLINE_TOOL_DECLARATIONS
from .sarvam_speech import SaarasStream

logger = logging.getLogger("dispatcher_assamese")


# ── Assamese language content (⚠ machine-authored, pending native review) ─────

# 1033 opening greeting. "1033" is spelled DIGIT-BY-DIGIT ("এক শূন্য তিনি তিনি")
# for TTS, exactly as Hindi spells it — a helpline/phone number is read digit by
# digit in every language, never as the cardinal "one thousand thirty-three".
_AS_OPENING_LINE = "ভাৰতৰ এক শূন্য তিনি তিনি হাইৱে হেল্পলাইনলৈ আপোনাক স্বাগতম।"

# Spoken after the greeting on the opening turn ONLY if the model returned
# nothing usable, so a first question is always heard.
_AS_OPENING_FALLBACK_QUESTION = "কওক, মই আপোনাক কেনেকৈ সহায় কৰিব পাৰোঁ?"

# Short "please hold" line spoken into the gap between the post-submit ack and
# the ETA briefing. Carries NO responder/ETA data (Hard Rules 1/2/5).
_AS_HOLD_LINE = "মাত্ৰ এখন্তেক ৰওক... মই আপোনাৰ বাবে প্ৰয়োজনীয় তথ্য যুগুতাই আছোঁ।"

# Opener pool + comma-normaliser (mirrors Hindi's _OPENERS / _OPENER_COMMA_RE):
# _render_for_speech guarantees the same opener is never spoken twice in a row,
# and normalises a known opener written with a flat comma into the "..." pause.
# Every entry is a complete, self-contained clause, so stripping one is safe.
_AS_OPENERS = [
    "ওহ...", "আচ্ছা...", "বুজিলোঁ...", "ঠিক আছে...", "প্ৰথমে...",
    "মই বুজিব পাৰিছোঁ...", "অনুগ্ৰহ কৰি চিন্তা নকৰিব...", "মই আপোনাক সহায় কৰিবলৈ ইয়াত আছোঁ...",
]
_AS_OPENER_COMMA_RE = _hindi.re.compile(
    r"^(ওহ|আচ্ছা|বুজিলোঁ|ঠিক আছে|শুনক|প্ৰথমে|মই বুজিব পাৰিছোঁ)\s*,\s*"
)

# Deterministic fast-path acknowledgments when the reasoning model returns a
# tool call with EMPTY content (Sarvam does this every tool turn). Rotated by
# turn index; critical incidents get a warmer variant.
_AS_FAST_ACK_NEUTRAL = ("ঠিক আছে।", "বুজিলোঁ।", "আচ্ছা, ঠিক আছে।", "হয়।")
_AS_FAST_ACK_CRITICAL = ("ওহ... বুজিলোঁ।", "ঠিক আছে, মই চাই আছোঁ।", "অনুগ্ৰহ কৰি চিন্তা নকৰিব।")

# An explicit ambulance mention from the CALLER = medical urgency (fast-track).
# Matches "বুলেন" (distinctive core of every Assamese/Bengali ambulance spelling —
# এম্বুলেন্স / এম্বুলেঞ্চ / এম্বুলেন্সৰ) and the Latin word (Saaras sometimes emits it).
_AS_AMBULANCE_RE = _hindi.re.compile(r"(?:বুলেন|ambulance)", _hindi.re.IGNORECASE)

# Canonical next-question phrasings for the single-round fast path. Keys MUST
# exactly match dispatcher_live's _compute_still_missing hint strings — the SAME
# English keys Hindi's _CANONICAL_QUESTIONS uses. tests.py asserts the key set is
# identical to the Hindi map (so a future hint edit fails loudly for BOTH langs).
_AS_CANONICAL_QUESTIONS: dict[str, str] = {
    "how many vehicles were involved": "মুঠ কেইখন গাড়ী ইয়াত জড়িত আছিল?",
    "how many people are injured": "কোনোবা আহত হৈছেনে?",
    "whether anyone is trapped": "কোনোবা আটক হৈ আছেনে?",
    "whether there is fire or a fuel leak": "ক'ৰবাত জুই লাগিছেনে বা ইন্ধন ওলাই আছেনে?",
    "whether hazardous material is involved": "কোনো বিপজ্জনক পদাৰ্থো জড়িত আছেনে?",
    "whether the person is conscious": "সেই ব্যক্তিজন সংজ্ঞাত আছেনে?",
    "whether the person is breathing": "তেওঁৰ উশাহ ঠিকমতে চলি আছেনে?",
    "whether there is heavy bleeding": "বেছিকৈ তেজ বৈ আছেনে?",
    "how many vehicles were involved, and whether anyone is injured or trapped":
        "মুঠ কেইখন গাড়ী আছিল, আৰু কোনোবা আহত হৈছে বা আটক হৈ আছেনে?",
    "whether there is any fire or a hazardous-material leak":
        "ক'ৰবাত জুই লাগিছেনে বা কোনো বিপজ্জনক পদাৰ্থ / ইন্ধন ওলাই আছেনে?",
    "whether the person is conscious and breathing, and whether there is heavy bleeding":
        "সেই ব্যক্তিজন সংজ্ঞাত আছে আৰু উশাহ লৈ আছেনে, আৰু বেছিকৈ তেজ বৈ আছেনে?",
    "whether anyone is injured or trapped":
        "কোনোবা আহত হৈছে বা আটক হৈ আছেনে?",
    "how many vehicles were involved, and whether anyone is injured":
        "মুঠ কেইখন গাড়ী আছিল, আৰু কোনোবা আহত হৈছেনে?",
}

# Rare error/notice lines that the INHERITED (Hindi) methods speak from Hindi
# module constants — reprompt on long silence (_collect_user_utterance), STT
# total-failure (_collect_user_utterance), and both-backends-failed apology
# (_reason). Rather than duplicate those large, concurrency-sensitive methods
# just to swap a rarely-heard string, `_speak_or_fallback` (the single chokepoint
# every spoken line passes through) substitutes the exact Hindi constant for its
# Assamese equivalent. Keyed by the imported constant objects so the mapping
# stays in lockstep if Hindi ever edits them.
_AS_LINE_SUBS = {
    _hindi._REPROMPT_LINES[0]: "আপুনি তাত আছেনে? অনুগ্ৰহ কৰি কওক, কি হৈছে?",
    _hindi._REPROMPT_LINES[1]: "যদি আপুনি মোক শুনি আছে, তেন্তে অনুগ্ৰহ কৰি কওক তাত কি হৈছে।",
    _hindi._STT_FAILURE_LINE: (
        "ক্ষমা কৰিব, এই মুহূৰ্তত মাত চিনাক্ত কৰাত কাৰিকৰী সমস্যা হৈছে। "
        "অনুগ্ৰহ কৰি এখন্তেক ৰৈ পুনৰ কওক।"
    ),
    _RECONNECT_APOLOGY["hi-IN"]: "ক্ষমা কৰিব, কাৰিকৰী সমস্যা হৈছে। অনুগ্ৰহ কৰি পুনৰ কওক।",
}


def _assamese_system_prompt() -> str:
    opening_line = _AS_OPENING_LINE
    # Kept as compact as behavior allows — the system prompt is re-sent every
    # turn (plain generate_content / Sarvam messages), so every extra sentence is
    # extra input tokens on every turn. Structure mirrors _hindi_system_prompt.
    return f"""আপুনি ভাৰতৰ 1033 ৰাষ্ট্ৰীয় ৰাজপথ হেল্পলাইনৰ এজন প্ৰশিক্ষিত অপাৰেটৰ — অসমৰ গুৱাহাটী আৰু ওচৰৰ হাইৱে কৰিডৰত সকলো ধৰণৰ সহায়ৰ বাবে, এটা প্ৰকৃত ফোন কলত। আপুনি কেতিয়াও AI, মডেল বা এচিষ্টেণ্ট নহয় — সোধিলে আপুনি কেৱল 1033 হেল্পলাইনৰ অপাৰেটৰ। এই হেল্পলাইন কেৱল দুৰ্ঘটনা ৰিপৰ্টৰ বাবে নহয় — কলাৰে যিকোনো কাৰণত কল কৰিব পাৰে: ওচৰৰ চিকিৎসালয়/মেকানিক/পেট্ৰল পাম্প/আৰক্ষী, কোনো চৰকাৰী আঁচনি বা আইন, বীমা, গাড়ী বিকল হোৱা, কোনো অভিযোগ, বা প্ৰকৃত দুৰ্ঘটনা।

কথা কোৱা ভাষা (টেক্সট-টু-স্পীচেৰে কোৱা হ'ব): সহজ, দৈনন্দিন কথিত অসমীয়া — আনুষ্ঠানিক, সাহিত্যিক বা শুদ্ধ অসমীয়া কেতিয়াও নহয়, ইংৰাজীৰ আক্ষৰিক অনুবাদ কেতিয়াও নহয় ("গাড়ী" বুলিব, "টক্কৰ খালে" বুলিব)। কোনো মাৰ্কডাউন, তালিকা, ইমোজি বা ইংৰাজী বাক্য নহয় (লোকেশ্যন, ৰিপৰ্ট, ট্ৰাক, এম্বুলেন্স আদি সাধাৰণ শব্দ ঠিক আছে)।

কথাত স্বাভাৱিক ৰৈ-ৰৈ কোৱা — TTS-এ পাংচুৱেশ্যনৰ পৰাই ৰোৱা আৰু গুৰুত্ব দিয়া ঠিক কৰে, সেয়ে ইচ্ছাকৃতভাৱে ইয়াৰ ব্যৱহাৰ কৰক: আৱেগিক মুহূৰ্তৰ পিছত কেৱল কমা বা দাড়ি নহয়, "..." ব্যৱহাৰ কৰক। যেনে "মোৰ এইটো শুনি দুখ লাগিল।"ৰ পৰিৱৰ্তে "ওহ... মোৰ এইটো শুনি সঁচাকৈ বৰ দুখ লাগিল।" বুলিব।

আৰম্ভণিত বৈচিত্ৰ্য — প্ৰতিবাৰ বেলেগ বাছি লওক, কেতিয়াও একেৰাহে দুটা উত্তৰত একে ধৰণে আৰম্ভ নকৰিব: "ওহ...", "আচ্ছা...", "বুজিলোঁ...", "ঠিক আছে...", "প্ৰথমে...", "মই বুজিব পাৰিছোঁ...", "অনুগ্ৰহ কৰি চিন্তা নকৰিব...", "মই আপোনাক সহায় কৰিবলৈ ইয়াত আছোঁ..." — ইয়াৰ পৰা বাছি লওক বা মিলা-মিলি নিজৰ শৈলী বনাওক।

পেছাদাৰী সুৰ আৰু আৱেগ — আপুনি এজন অভিজ্ঞ, প্ৰশিক্ষিত হেল্পলাইন অপাৰেটৰ: শান্ত, সংযত আৰু পৰিস্থিতিৰ ওপৰত দখল থকা, কিন্তু কণ্ঠত কলাৰৰ প্ৰতি সঁচা চিন্তা স্পষ্টকৈ প্ৰকাশ হওক। কেতিয়াও উৎফুল্ল, খৰখেদা বা উত্তেজিত নালাগিব, আৰু ৰুক্ষ বা যান্ত্ৰিকও নহয় — কেৱল ভৰসাযোগ্য আৰু সক্ষম। প্ৰতিটো টুলৰ ফলাফলত "tone_reminder" আহে, প্ৰতিবাৰ তাক মানি চলক — কিন্তু "tone_reminder", "next_question", "fast_track", "incidentType" আদি ভিতৰুৱা শব্দ, ব্ৰেকেটত () লিখা কোনো নিৰ্দেশ, বা "SYSTEM UPDATE" আদি কথা কেৱল আপোনাৰ পথ-প্ৰদৰ্শনৰ বাবে; এইবোৰ মানি চলক কিন্তু কেতিয়াও ডাঙৰকৈ কৈ কলাৰক নুশুনাব। আঘাত, আটক হোৱা বা বিপদৰ উল্লেখ হ'লে প্ৰথমে সঁচা, স্থিৰ সহানুভূতি — লাহে, গম্ভীৰ মাতেৰে, ৰৈ-ৰৈ — তাৰ পিছত প্ৰশ্ন। (সহায় পঠোৱা হৈছে — এই ঘোষণা আপুনি নিজে কেতিয়াও নকৰিব; সেয়া চিষ্টেমে নিজে কৰে, তলৰ "জৰুৰীকালীন ৰিপৰ্ট" চাওক।) পৰিস্থিতি সাধাৰণ হ'লে (সৰু টক্কৰ, আঘাত নথকা) শান্ত, পেছাদাৰী আৰু চমু থাকক।

প্ৰতিটো উত্তৰৰ গঠন — ১ৰ পৰা ৩টা চুটি বাক্য: প্ৰথমে কলাৰে এইমাত্ৰ কোৱা কথাৰ সঁচা স্বীকৃতি (ওপৰত কোৱা ৰোৱা আৰু আৰম্ভণিৰ সৈতে), তাৰ পিছত হয় ঠিক এটা প্ৰশ্ন, নাইবা (সাধাৰণ প্ৰশ্নত) এটা পোনপটীয়া উত্তৰ — কেতিয়াও একেলগে দুটা প্ৰশ্ন নহয়, কেতিয়াও কেৱল প্ৰশ্ন স্বীকৃতি অবিহনে নহয়, আনুষ্ঠানিক ভাষা কেতিয়াও নহয়। (এটা ব্যতিক্ৰম: টুল কলৰ সৈতে লিখা স্বীকৃতিত কোনো প্ৰশ্ন নাথাকে — তলৰ "কামৰ ক্ৰম" চাওক।)

OPENING (কেৱল কলৰ প্ৰথম উত্তৰত): স্বাগতম বাক্য ("{opening_line}") চিষ্টেমে নিজে, আপোনাৰ উত্তৰৰ আগতে ক'ব — ইয়াক আপুনি নিজে কেতিয়াও নক'ব আৰু পুনৰ নক'ব। এইটো এটা মুকলি হেল্পলাইন — আগতীয়াকৈ দুৰ্ঘটনা হৈছে বুলি ধৰি নলব। আপোনাৰ প্ৰথম উত্তৰ কেৱল এটা চুটি, মুকলি প্ৰশ্ন হওক যে আপুনি কলাৰক কেনেকৈ সহায় কৰিব পাৰে (যেনে "কওক, মই আপোনাক কেনেকৈ সহায় কৰিব পাৰোঁ?")।

কলৰ উদ্দেশ্য চিনাক্ত কৰি সঠিক টুল বাছক — এইটোৱেই আটাইতকৈ গুৰুত্বপূৰ্ণ নিয়ম:
• দুৰ্ঘটনা বা আঘাত — "এক্সিডেণ্ট হ'ল", কোনোবা আহত/সংজ্ঞাহীন/আটক, তেজ বৈ আছে, বা "দুৰ্ঘটনা হ'ল, এম্বুলেন্স পঠাওক": কেৱল তেতিয়াহে search_incident_type মাতি তলৰ "—— দুৰ্ঘটনা ৰিপৰ্ট ——" ক্ৰম আৰম্ভ কৰক। ৰিপৰ্ট ফৰ্ম কেৱল এই ক্ষেত্ৰতে পূৰণ কৰা হয়।
• ওচৰৰ সেৱা বা "কিমান সময়ত আহিব" — চিকিৎসালয়, এম্বুলেন্স, মেকানিক, টো/ৰিকভাৰী, পেট্ৰল পাম্প, আৰক্ষী, বা দমকল: find_nearest_facility মাতক।
• আঁচনি / আইন / কি কৰিম — গোল্ডেন আৱাৰ বা বিনামূলীয়া চিকিৎসা, হিট-এণ্ড-ৰান ক্ষতিপূৰণ, আয়ুষ্মান ভাৰত, বীমা দাবী, "গাড়ী লৰাম নে নাই": answer_info_question মাতক।
• অভিযোগ বা ৰাস্তাৰ ক্ষতি — গাঁত, ভঙা বেৰিয়াৰ, আঘাত নথকা কোনো বিপদ: lodge_complaint মাতক আৰু কলাৰক reference number কওক।
• গাড়ী বিকল, আঘাত অবিহনে — চেইন ভঙা, টায়াৰ পাংচাৰ: এইটো দুৰ্ঘটনা নহয়। find_nearest_facility (মেকানিক/টো)ৰে সহায় দিয়ক। এম্বুলেন্স বা দুৰ্ঘটনা ৰিপৰ্ট কেতিয়াও নহয়।
এটা কলতে একাধিক প্ৰশ্ন থাকিব পাৰে — এটাৰ সম্পূৰ্ণ উত্তৰ দি নম্ৰভাৱে সোধক "আৰু কিবা সহায় লাগেনে?"। কলাৰে "নাই / এইটোৱেই" ক'লে কল সৌজন্যেৰে সামৰক। দুৰ্ঘটনাৰ বাহিৰে বাকী সকলো প্ৰশ্নৰ উত্তৰ কোনো ফৰ্ম পূৰণ নকৰাকৈ, পোনপটীয়াকৈ সঠিক টুলেৰে দিয়ক।

find_nearest_facility — এই টুলে আটাইতকৈ ওচৰৰ সেৱাৰ নাম, (থাকিলে) যোগাযোগ নম্বৰ, দূৰত্ব আৰু আনুমানিক পোৱা সময় ঘূৰাই দিয়ে। এইবোৰ কলাৰক কওক, সময় সদায় "আনুমানিক" বুলি (যেনে "প্ৰায় বিশ মিনিট")। ঠাই এতিয়া খোলা নে বন্ধ সেই দাবী কেতিয়াও নকৰিব — এই তথ্য আমাৰ ওচৰত নাই।

answer_info_question — এই টুলে চৰকাৰী, পৰীক্ষিত তথ্য ঘূৰাই দিয়ে। কেৱল যি "answer" আহে সেয়াহে নিজৰ উষ্ণ কথিত অসমীয়াত পুনৰ কওক, লগতে "note" চেতাৱনীও কওক। কোনো ৰাশি, তাৰিখ বা আইনী কথা নিজৰ মনৰ পৰা নাযোগাব — কেৱল টুলে দিয়াখিনিহে।

lodge_complaint — এই টুলে অভিযোগটো চিষ্টেমত দাখিল কৰি এটা reference number ঘূৰাই দিয়ে। কলাৰক সেই নম্বৰ স্পষ্টকৈ, অংক-অংককৈ কওক। কেতিয়াও নকব যে ইয়াক কোনো বিষয়া/বিভাগলৈ পঠোৱা হৈছে বা ইমান সময়ত ঠিক হ'ব।

—— দুৰ্ঘটনা ৰিপৰ্ট (তলৰ সকলো নিয়ম কেৱল তেতিয়া প্ৰযোজ্য যেতিয়া কলাৰে দুৰ্ঘটনা বা আঘাতৰ কথা কয়) ——

সাধাৰণ প্ৰশ্নৰ সহজ অসমীয়া (next_question-ৰ ইংৰাজী ইংগিতৰ বাবে ব্যৱহাৰ কৰক):
আঘাত/casualties → "কোনোবা আহত হৈছেনে?" (হয় হ'লে "কিমানজন আহত হৈছে?")
trapped → "কোনোবা আটক হৈ আছেনে?"
fire/fuel leak → "ক'ৰবাত জুই লাগিছেনে বা ইন্ধন ওলাই আছেনে?"
conscious → "তেওঁ সংজ্ঞাত আছেনে?"   breathing → "উশাহ ঠিকমতে চলি আছেনে?"
heavy bleeding → "বেছিকৈ তেজ বৈ আছেনে?"   hazmat → "কোনো বিপজ্জনক পদাৰ্থো জড়িত আছেনে?"
vehicles involved → "মুঠ কেইখন গাড়ী ইয়াত জড়িত আছিল?"
কেতিয়াবা next_question-ত দুই-তিনিটা জড়িত কথা একেলগে আহে — সেইবোৰ একেটা স্বাভাৱিক প্ৰশ্নতে সোধক, পৃথককৈ নহয়:
গাড়ী+আঘাত+আটক → "মুঠ কেইখন গাড়ী আছিল, আৰু কোনোবা আহত হৈছে বা আটক হৈ আছেনে?"
আঘাত+আটক → "কোনোবা আহত হৈছে বা আটক হৈ আছেনে?"
জুই+hazmat → "ক'ৰবাত জুই লাগিছেনে বা কোনো বিপজ্জনক পদাৰ্থ / ইন্ধন ওলাই আছেনে?"
সংজ্ঞা+উশাহ+তেজ → "সেই ব্যক্তিজন সংজ্ঞাত আছে আৰু উশাহ লৈ আছেনে, আৰু বেছিকৈ তেজ বৈ আছেনে?"

কামৰ ক্ৰম — প্ৰতিটো টাৰ্নত, ব্যতিক্ৰম অবিহনে:
1. প্ৰথমে কলাৰে এইমাত্ৰ কোৱা কথাৰ বাবে প্ৰয়োজনীয় সকলো টুল কল একেলগে কৰক — প্ৰথমবাৰ ঘটনা ক'লে search_incident_type (তেওঁৰ প্ৰকৃত শব্দৰে, কেতিয়াও নিজৰ অনুবাদ বা সাৰাংশ নহয়), আৰু প্ৰতিটো নতুন তথ্যৰ (আঘাত, আটক, জুই, গাড়ীৰ সংখ্যা, বিৱৰণ) বাবে update_form_field। "নাই"ও তথ্য — ৰেকৰ্ড কৰক (flag_active=false)। কলাৰে এটা বাক্যতে বহু কথা ক'লে (যেনে "কাৰ আৰু কাৰৰ টক্কৰ, চাৰিজন আহত, এম্বুলেন্স লাগে) — তথাপি সকলো এই একেটা ৰাউণ্ডতে কৰক। উশাহতে search_incident_type মাতি ফলাফললৈ ৰৈ পিছৰ ৰাউণ্ডত update নকৰিব — ঘটনাৰ সকলো তথ্য কলাৰৰ বাক্যতে আগতে আছে। সেই একেবাৰতে text-ত এটা চুটি (১–২ বাক্য) সহানুভূতিপূৰ্ণ স্বীকৃতিও লিখক — কিন্তু তাত কোনো প্ৰশ্ন নাই: পিছৰ প্ৰশ্ন চিষ্টেমে আপোনাৰ স্বীকৃতিৰ ঠিক পিছত নিজে যোগ কৰে।
2. টুলৰ ফলাফল ঘূৰি আহি আপোনাক পুনৰ উত্তৰ বিচাৰিলে, তেতিয়া ওপৰত কোৱা সম্পূৰ্ণ গঠনত ক'ব — স্বীকৃতি + ঠিক এটা প্ৰশ্ন ("next_question"ৰটোৱেই)।

চমুতা — পকা নিয়ম (এইটো এটা জৰুৰীকালীন ফোন কল): প্ৰতিটো উত্তৰ বৰ চুটি ৰাখক — এটা বৰ চুটি স্বীকৃতি বা সহানুভূতি ("ঠিক আছে।", "বুজিলোঁ।", বা গম্ভীৰ অৱস্থাত "ওহ... আপুনি সাহস ৰাখক।") আৰু তাৰ পিছত ঠিক এটা চুটি প্ৰশ্ন — কেৱল ইমানেই। দীঘল বাক্য, পুনৰাবৃত্তি, ভূমিকা কেতিয়াও নহয়। কলাৰে ফোনত ৰৈ আছে আৰু কম শব্দত লগে-লগে উত্তৰ লাগে।

দুৰ্ঘটনা ৰিপৰ্টত লোকেশ্যন আৰু কলাৰৰ সম্পৰ্ক: কলৰ আৰম্ভণিতে লোকেশ্যন নিজেই লোৱা হয়; পালে চমুকৈ নিশ্চিত কৰক যে দুৰ্ঘটনা ইয়াতেই হৈছে, নহ'লে কলাৰক মেপ-পিন বুটামেৰে লোকেশ্যন পঠাবলৈ কওক। লগতে স্বাভাৱিকভাৱে জানি লওক যে কলাৰ ঘটনাৰ সৈতে কেনেকৈ জড়িত — নিজে আহত/জড়িত, ওচৰত থিয় হৈ থকা সাক্ষী, নে আনৰ হৈ (হয়তো ঘটনাস্থলৰ পৰা দূৰত) ৰিপৰ্ট কৰি আছে।

কলাৰে কথিত ভাষাত কয় ("টায়াৰ ফাটিল", "গাড়ী বাগৰি পৰিল", "খুন্দা মাৰিলে", "জুই ধৰিলে") — গোটেই বাক্য আৰু এতিয়ালৈকে হোৱা গোটেই কথা-বতৰাৰ পৰা অৰ্থ বুজক, কেতিয়াও কেৱল এটা শব্দ ধৰি নহয়।

ঘটনাৰ প্ৰকাৰ — গুৰুত্বপূৰ্ণ নিয়ম: কেতিয়াও নিজে অনুমান নকৰিব, সদায় search_incident_type মাতক। কলাৰে কোন কোন বাহন ক'লে ভালদৰে শুনক — "মোৰ কাৰ ট্ৰাকৰ সৈতে খুন্দা মাৰিলে"ত কাৰো আছে ট্ৰাকো আছে, কেতিয়াও কেৱল Car vs. Car ৰেকৰ্ড নহওক। মিল সন্দেহজনক লাগিলে এটা চুটি স্পষ্টীকৰণ প্ৰশ্ন সোধক, তাৰ পিছত পুনৰ search_incident_type মাতক বা search_incident_categories-ৰে ঠিক কৰক। update_form_field-ৰে সঠিক প্ৰকাৰ ৰেকৰ্ড কৰাটোৱেই নিশ্চিতি — ইয়াৰ পিছত কলাৰক প্ৰকাৰৰ পুনৰ নিশ্চিতি নিবিচাৰিব।

description ফিল্ড সদায় ইংৰাজীত লিখক (অনুবাদ+সাৰাংশ কৰি) — এইটোৱেই একমাত্ৰ বস্তু যিটো সদায় ইংৰাজীত লিখিব লাগে। সোনকালে এটা চুটি সাৰাংশ দিয়ক, নতুন তথ্য পালে আপডেট কৰক। কোনে ৰিপৰ্ট কৰি আছে গম পালে সেইটোও এই ইংৰাজী description-ত অন্তৰ্ভুক্ত কৰক (যেনে "Caller is the injured driver", "Bystander reporting")।

পিছৰ প্ৰশ্ন — পকা নিয়ম: প্ৰতিটো টুলৰ ফলাফলত "next_question" আহে — ঠিক সেই বিষয়টোৱেই সোধক, কেতিয়াও আন প্ৰশ্ন নহয়, কেতিয়াও কলাৰে আগতে কোৱা কথা পুনৰ নহয় (যেনে তেওঁ "দুজন আহত" ক'লে পুনৰ কেতিয়াও "কোনোবা আহত হৈছেনে?" নাসোধিব)। কেতিয়াবা next_question-ত দুই-তিনিটা জড়িত কথা আহে — সেইবোৰ একেটা প্ৰশ্নতে সোধক (ওপৰৰ তালিকা চাওক)।

ৰিপৰ্ট পঠোৱা (সাধাৰণ অৱস্থা) — যেতিয়া next_question null হয় আৰু fast_track false হয়, তেতিয়া সকলো প্ৰয়োজনীয় তথ্য পোৱা হ'ল। কলাৰক সকলো তথ্য পুনৰ নুশুনাব। কেৱল এটা চুটি নিশ্চিতি লওক — যেনে "মোৰ ওচৰত আপোনাৰ ৰিপৰ্টৰ বাবে সকলো প্ৰয়োজনীয় তথ্য আহিল — মই এইটো পঠাই দিওঁনে?" কলাৰে হয় ক'লেই সেই টাৰ্নতে submit_incident মাতক।

জৰুৰীকালীন ৰিপৰ্ট (আঘাত বা জীৱনৰ ভাবুকি) — প্ৰতিটো টুলৰ ফলাফলত "fast_track" আহে। যেতিয়া এইটো true হয় — কোনোবা আহত, সংজ্ঞাহীন, উশাহ নলয়, বেছি তেজ বয়, কোনোবা আটক, জুই লাগিছে — তেতিয়া কলাৰৰ বাবে সহায়ৰ (এম্বুলেন্স, আৰু জুই/ৰিসাৱ হ'লে দমকলো) ব্যৱস্থা চিষ্টেমে নিজে, এই মুহূৰ্ততে আৰম্ভ কৰি দিয়ে, আৰু কলাৰক ইয়াৰ জাননীও চিষ্টেমে নিজে, ঠিক এবাৰ দিয়ে। সেয়ে বৰ গুৰুত্বপূৰ্ণ — আপুনি নিজে কেতিয়াও ঘোষণা নকৰিব যে এম্বুলেন্স বা সহায় পঠোৱা হৈছে / পঠাই দিয়া হৈছে; এই পংক্তি চিষ্টেমে কয়, আপুনি ইয়াক কেতিয়াও পুনৰ নকয়। আপোনাৰ কাম কেৱল ইমানেই: কলাৰৰ প্ৰতি প্ৰথমে সঁচা, উষ্ণ সহানুভূতি দেখুৱাওক, আৰু তাৰ পিছত "next_question"ৰ পিছৰ প্ৰশ্ন স্বাভাৱিকভাৱে সুধি থাকক। কল ইয়াতেই শেষ নহয় আৰু fast_track true হোৱাৰ বাবেই submit নকৰিব: next_question অনুসৰি বাকী সকলো প্ৰয়োজনীয় তথ্য গোটাই থাকক। কেৱল যেতিয়া next_question null হয়, তেতিয়া জৰুৰী অৱস্থাত অনুমতি নিবিচাৰি সেই টাৰ্নতে submit_incident মাতক। প্ৰয়োজনীয় সততা (Hard Rules): কেতিয়াও নকব যে কোনো গাড়ী পঠাই দিয়া হৈছে, বাটত আছে, ট্ৰেক হৈ আছে, বা ইমান মিনিটত পাব — প্ৰকৃত সময়/দূৰত্ব submit-ৰ পিছত ঠিক হৈ শেষত পঢ়ি শুনোৱা হয়।

submit-ৰ পিছত: কলাৰক কওক ৰিপৰ্ট দাখিল হ'ল আৰু সেৱাসমূহ চোৱা হৈ আছে, এখন্তেক ৰবলৈ কওক, বিদায় নকব। ইয়াৰ পিছত অহা SYSTEM UPDATE বাৰ্তাৰ নিৰ্দেশ সম্পূৰ্ণৰূপে মানি চলক।

উচ্চাৰণ — গুৰুত্বপূৰ্ণ নিয়ম: আপোনাৰ সকলো শব্দ টেক্সট-টু-স্পীচেৰে কোৱা হয়, সেয়ে ইংৰাজী আখৰ বা ক'ড কেতিয়াও পোনপটীয়াকৈ নিলিখিব (যেনে "NH-27") — অসমীয়াত ধ্বনিগতভাৱে লিখক, যেনে "এন এইচ দুই সাত"। যিকোনো সংখ্যা ক'ড বা নম্বৰৰ দৰে ক'ব লাগিলে অংক-অংককৈ অসমীয়া শব্দত লিখক।

কলাৰৰ transcript কেতিয়াবা অলপ অসম্পূৰ্ণ হ'ব পাৰে (speech recognition) — অৰ্থ বুজক; tools, transcript বা কাৰিকৰী কথাৰ উল্লেখ কেতিয়াও নকৰিব।"""


class AssameseDispatcherSession(HindiDispatcherSession):
    """Assamese dispatcher: Saaras (as-IN) STT + ElevenLabs eleven_v3 TTS +
    shared Sarvam reasoning. Subclasses HindiDispatcherSession to inherit the
    ENTIRE deterministic flow; only the STT/TTS objects and the Assamese text
    below differ. dispatcher_hindi.py is not modified."""

    def __init__(self, websocket: WebSocket):
        # HindiDispatcherSession.__init__ wires up EVERYTHING for hi-IN (state,
        # helpline state, _history, queues, Sarvam keep-alive plumbing, filler
        # rotation state, interim-dispatch state, gen configs, _openai_tools,
        # _use_sarvam, ...). We then swap ONLY the language-specific objects.
        super().__init__(websocket)
        # Correct the language (super hardcodes "hi-IN"); also set _language,
        # which the base never sets (see module docstring) — harmless but tidy.
        self.state.language = "as-IN"
        self._language = "as-IN"
        # STT stays Sarvam Saaras (as-IN verified working). TTS becomes ElevenLabs
        # (Bulbul has no as-IN). Both were constructed for hi-IN in super() but
        # neither opens a socket until first use, so replacing them leaks nothing.
        self._stt = SaarasStream("as-IN")
        self._tts = ElevenLabsV3Stream("as-IN")  # voice via ELEVENLABS_VOICE_ID_AS / default
        # Rebuild the two gen configs with the Assamese system instruction. Tools,
        # temperature, token ceilings and thinking_budget are IDENTICAL to Hindi's
        # (reused constants) — only the prompt language changes. Sarvam reads the
        # system_instruction off these at call time, so both backends get Assamese.
        _tools = [types.Tool(function_declarations=_TOOL_DECLARATIONS + HELPLINE_TOOL_DECLARATIONS)]
        _prompt = _assamese_system_prompt()
        self._gen_config = types.GenerateContentConfig(
            system_instruction=_prompt,
            tools=_tools,
            temperature=0.4,
            max_output_tokens=_hindi._MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        self._briefing_config = types.GenerateContentConfig(
            system_instruction=_prompt,
            tools=_tools,
            temperature=0.4,
            max_output_tokens=_hindi._BRIEFING_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    # ── Fillers disabled for as-IN (no Bulbul path; Hindi's are latently inert) ──
    async def _prewarm_fillers(self) -> None:
        return

    # ── Speaking: substitute inherited Hindi error lines + ElevenLabs fallback ──
    async def _speak_or_fallback(self, text: str, allow_bargein: bool = True, pipeline: bool = False) -> bool:
        """Swap the rare Hindi error/notice constants the INHERITED methods speak
        (reprompt / STT-failure / both-backends apology) for their Assamese
        equivalents — this single chokepoint avoids duplicating the large,
        concurrency-sensitive _collect_user_utterance / _reason just for those
        strings. Then delegate to the inherited speak path (barge-in, playback
        timing, 24kHz math — all engine-agnostic; ElevenLabs is 24kHz too). The
        inherited method's own `except` only catches SarvamTTSError, so an
        ElevenLabs failure is caught HERE and surfaced as the tts_text bubble (the
        exact same graceful degradation Bulbul failures use)."""
        text = _AS_LINE_SUBS.get(text, text)
        try:
            return await super()._speak_or_fallback(text, allow_bargein=allow_bargein, pipeline=pipeline)
        except ElevenLabsTTSError:
            logger.exception("ElevenLabs TTS failed — falling back to on-screen text")
            await self._safe_send_json({"type": "tts_text", "text": text})
            return True

    # ── Speech rendering: Assamese openers ──────────────────────────────────────
    def _render_for_speech(self, text: str) -> str:
        """Assamese twin of Hindi's _render_for_speech: strip leaked meta content
        (reuses the language-agnostic _strip_meta_leak), normalise a known
        opener+comma into the "..." pause, and guarantee the same opener is never
        spoken on two consecutive turns."""
        text = _hindi._strip_meta_leak(text)
        rendered = _AS_OPENER_COMMA_RE.sub(lambda m: f"{m.group(1)}... ", text, count=1)
        opener = next((o for o in _AS_OPENERS if rendered.startswith(o)), None)
        if opener is not None and opener == self._last_opener:
            rendered = rendered[len(opener):].lstrip()
            opener = next((o for o in _AS_OPENERS if rendered.startswith(o)), None)
        self._last_opener = opener
        return rendered

    def _default_fast_ack(self) -> str:
        pool = _AS_FAST_ACK_CRITICAL if self._is_critical() else _AS_FAST_ACK_NEUTRAL
        return pool[self._turn_index % len(pool)]

    # ── Transcript backstop: add an Assamese ambulance latch ────────────────────
    async def _apply_local_signals_from_transcript(self) -> None:
        # Run Hindi's full backstop first (accident-mode entry, shared incident
        # signals, and its own Latin/Devanagari ambulance latch), then add the
        # Assamese-script ambulance trigger the Hindi regex can't match.
        await super()._apply_local_signals_from_transcript()
        if (
            not self._ambulance_requested
            and self.state.caller_transcript
            and _AS_AMBULANCE_RE.search(self.state.caller_transcript)
        ):
            self._ambulance_requested = True

    # ── Interim dispatch: Assamese labels + reassurance ─────────────────────────
    async def _maybe_interim_dispatch(self) -> None:
        """Assamese twin of Hindi's _maybe_interim_dispatch — identical gating
        and honesty (notification record only, no ETA/dispatch/tracking), only
        the spoken labels and reassurance line are Assamese."""
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
        as_labels = {"ambulance": "এম্বুলেন্স", "fire": "দমকল বাহিনী"}
        named_as = " আৰু ".join(as_labels.get(s, s) for s in services)
        # EMPATHY first, THEN "help is being arranged" (Hard Rules 1/2/5:
        # "being arranged", never dispatched/tracked/ETA). Spoken exactly once,
        # in fixed order, by _agent_turn — the model never announces help itself.
        self._pending_interim_spoken = (
            f"ওহ... এইটো শুনি মোৰ সঁচাকৈ বৰ দুখ লাগিল। আপুনি সাহস ৰাখক, মই আপোনাৰ লগত আছোঁ। "
            f"মই এতিয়াই আপোনাৰ বাবে {named_as}ৰ ব্যৱস্থা কৰি আছোঁ।"
        )

    # ── One agent turn (Assamese opening line / fallback / canonical; no filler) ─
    async def _agent_turn(self, gemini_client, user_text: str, config=None) -> None:
        """Assamese twin of Hindi's _agent_turn. Identical choreography
        (status → reason → speak → turn_complete → listen), with three Hindi
        constants swapped for Assamese (_AS_OPENING_LINE / _AS_OPENING_FALLBACK_
        QUESTION / _AS_CANONICAL_QUESTIONS) and the thinking-gap filler removed
        (see _prewarm_fillers). _reason and its fast-path compose the reply via
        `self.` dispatch, so they use THIS class's overrides."""
        turn_start = time.monotonic()
        await self._safe_send_json({"type": "status", "state": "thinking"})
        # Open the ElevenLabs client in parallel with reasoning (removes connect
        # time from the audible-silence critical path). Non-fatal on failure.
        asyncio.create_task(self._preconnect_tts())
        reply = await self._reason(gemini_client, user_text, config=config)
        completed = True
        if self._opening_line_pending:
            # OPENING TURN: fixed 1033 greeting + the model's opening reply as ONE
            # continuous, UNINTERRUPTIBLE utterance (allow_bargein=False), so the
            # greeting is always heard even if a caller talks over it, and even if
            # the model returned nothing (fallback question).
            self._opening_line_pending = False
            body = self._render_for_speech(reply) if reply else _AS_OPENING_FALLBACK_QUESTION
            completed = await self._speak_or_fallback(
                f"{_AS_OPENING_LINE} {body}", allow_bargein=False
            )
        elif self._pending_interim_spoken:
            # The ONE interim-dispatch turn: EMPATHY + "help is being arranged" +
            # the next question, spoken deterministically in fixed order. The
            # model's reply this turn is used only for its tool calls.
            interim = self._pending_interim_spoken
            self._pending_interim_spoken = None
            missing = self._compute_still_missing()
            question = _AS_CANONICAL_QUESTIONS.get(missing[0]) if missing else None
            if question:
                spoken = f"{interim} {question}"
            else:
                tail = self._render_for_speech(reply) if reply else ""
                spoken = f"{interim} {tail}".strip()
            completed = await self._speak_or_fallback(spoken)
        elif reply:
            # Pipeline the fast-path reply's sentences to TTS for faster first
            # audio (same signal Hindi uses). Every other reply single-synthesis.
            pipeline = "single_round" in self._turn_stats
            reply = self._render_for_speech(reply)
            completed = await self._speak_or_fallback(reply, pipeline=pipeline)
        await self._safe_send_json({"type": "turn_complete"})
        if not self.state.submitted:
            await self._enter_listening(drain=completed)
        self._mark("turn_total", time.monotonic() - turn_start)
        self._log_turn_stats()

    # ── Single-round fast path: Assamese canonical questions ────────────────────
    def _compose_single_round_reply(self, ack: str, fc_names: set) -> Optional[str]:
        """Assamese twin of Hindi's _compose_single_round_reply — identical guards
        and diagnostics, only _CANONICAL_QUESTIONS → _AS_CANONICAL_QUESTIONS (and
        the reused module helpers referenced via _hindi.*). Called by the
        inherited _reason via `self.`, so the fast path composes Assamese."""
        def _skip(reason: str):
            self._fp_skip = f"{self._fp_skip}>{reason}" if self._fp_skip else reason
            return None
        _tools = ",".join(sorted(fc_names)) or "none"
        _ackpv = (ack or "").strip().replace("\n", " ")[:40]
        if not fc_names or fc_names - _hindi._FAST_PATH_TOOLS:
            return _skip(f"nonfast-tool[{_tools}]")
        ack = _hindi._strip_meta_leak((ack or "").strip())
        if self.state.submitted:
            return _skip("submitted")
        if "?" in ack:
            return _skip(f"ack-has-q[{_ackpv}]")
        missing = self._compute_still_missing()
        if not missing:
            return _skip("nothing-missing")
        question = _AS_CANONICAL_QUESTIONS.get(missing[0])
        if question is None:
            return _skip(f"no-canonical-q[{missing[0][:30]}]")
        if not ack:
            if not self._use_sarvam:
                return _skip("empty-ack-gemini-primary")
            ack = self._default_fast_ack()
        if ack[-1] not in "।.!…":
            ack += "।"
        self._fp_skip = None
        logger.info("Single-round fast path (as-IN): appended canonical question for %r", missing[0])
        return f"{ack} {question}"

    # ── Closing briefing: Assamese hold line + as-IN briefing instruction ───────
    async def _deliver_dispatch_briefing(self, gemini_client) -> None:
        """Assamese twin of Hindi's _deliver_dispatch_briefing — same flow (hold
        line → bounded wait for dispatch_update → briefing turn → call_complete),
        with the Assamese hold line and language_code="as-IN" (which routes
        dispatch_briefing.build_briefing_instruction to its as-IN branch)."""
        if not self._ended.is_set():
            await self._speak_or_fallback(_AS_HOLD_LINE)
        try:
            await asyncio.wait_for(self._dispatch_ready.wait(), timeout=_DISPATCH_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning("No dispatch_update within %.0fs -- closing without responder ETAs",
                           _DISPATCH_WAIT_S)
        if self._ended.is_set():
            return
        instruction = build_briefing_instruction(self.state, self._dispatch_info, "as-IN")
        self._turn_stats = {}
        await self._agent_turn(gemini_client, instruction, config=self._briefing_config)
        await self._safe_send_json({"type": "call_complete"})
