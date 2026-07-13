"""Guardrail tests — determinism + cost. Run: python tests.py"""
from severity_engine import engine, classifier, severity, local_extract

def check(name, cond):
    print(("PASS" if cond else "FAIL"), "-", name)
    assert cond, name

# determinism: same input -> identical output 100x
a = engine.assess({"subType": "Head-On Collision"}, {"roadBlocked": True}, {"km": 196})
for _ in range(100):
    b = engine.assess({"subType": "Head-On Collision"}, {"roadBlocked": True}, {"km": 196})
    assert a == b
check("deterministic over 100 runs", True)

# operator selection never uses LLM
o = engine.assess({"subType": "Rear-End Collision"}, {}, {"km": 10})
check("operator dropdown -> no LLM", o["llmUsed"] is False and o["severity"] == "MEDIUM")

# hard override -> CRITICAL
o = engine.assess({"subType": "Rear-End Collision"}, {"casualties": 25}, None)
check("25 casualties -> CRITICAL override", o["severity"] == "CRITICAL")

# BLEVE always critical
rec = classifier._find_exact("LPG / CNG Tanker Fire \u2013 BLEVE Risk")
check("BLEVE record present", rec is not None)
s = severity.compute(rec, {})
check("BLEVE -> CRITICAL", s.label == "CRITICAL")

# hazmat floors at HIGH
o = engine.assess({"subType": "Side-Swipe Collision"}, {"hazmat": True}, None)
check("hazmat floors >= HIGH", o["severityScore"] >= 3)

# state labeling
o = engine.assess({"subType": "Head-On Collision"}, {}, {"km": 196})
labels = " ".join(a["label"] for a in o["agencies"])
check("Uttarakhand police labeled in Dehradun segment", "Uttarakhand Police" in labels)

# cost: batch of 50, 45 selected + 5 vague, no key -> 0 actual LLM calls (key absent)
calls = 0
for i in range(45):
    if engine.assess({"subType": "Rear-End Collision"}, {}, None)["llmUsed"]:
        calls += 1
for i in range(5):
    if engine.assess({"description": "xyzzy something unclear"}, {}, None)["llmUsed"]:
        calls += 1
check("no spurious LLM calls when key absent", calls == 0)

# local extraction (no LLM) catches fire dispatch from free text alone — the
# original bug: a confidently-matched "Car vs. Car Collision" record with no
# FIRE in its baseline agencies must still get FIRE when the text says so.
o = engine.assess({"description": "Car collided with car and now there is fire"}, {}, {"km": 40})
check("local extraction dispatches FIRE from free text, no LLM needed",
      any(a["code"] == "FIRE" for a in o["agencies"]) and o["llmUsed"] is False)

# negation suppresses false positives on both sides of the hazard word
sig = local_extract.extract_signals_locally(
    "Truck accident, the fire has already been extinguished, no one trapped inside"
)
check("negation suppresses fire and entrapment", sig["fire"] is False and sig["entrapment"] is False)

# common paraphrasing is caught via synonym normalization + TF-IDF blend, not
# just exact keyword overlap
o = engine.assess({"description": "The truck flipped over on the curve near km 60"}, {}, None)
check("paraphrase 'flipped over' classifies as a rollover", "Rollover" in o["subType"])

# hazmat detection from free text alone floors severity at HIGH, same as the
# existing explicit-signal test above, but sourced from the local extractor
o = engine.assess({"description": "Tanker is leaking gas near the bridge, strong toxic smell"}, {}, None)
check("local hazmat detection floors severity >= HIGH", o["severityScore"] >= 3)

# multi-vehicle collisions must always get TOWING + POLICE dispatched, even
# when the matched taxonomy record's own baseline agencies list omits them --
# a real gap: a 4-vehicle collision-with-fire report got AMBULANCE/POLICE/
# FIRE but no TOWING, even though wrecked vehicles blocking the road are the
# norm for any multi-vehicle incident, across every subtype, not an exception.
o = engine.assess({"subType": "Rear-End Collision"}, {"vehiclesInvolved": 4, "fire": True}, None)
check("multi-vehicle collision always gets TOWING dispatched",
      any(a["code"] == "TOWING" for a in o["agencies"]))

# any confirmed casualty implies medical response, regardless of what the
# matched record's baseline agencies say (some property-damage-only subtypes
# omit AMBULANCE by default since none was assumed until reported)
o = engine.assess({"subType": "Mob Blocking Highway / Road Roko"}, {"casualties": 2}, None)
check("any casualty always gets AMBULANCE dispatched",
      any(a["code"] == "AMBULANCE" for a in o["agencies"]))

# classifier.py originally had zero Hindi awareness at all -- any Hindi report
# scored 0 token overlap on every record (Devanagari never matches the
# English-only index) and fell through to an arbitrary placeholder record
# regardless of what was actually described. hindi_glossary.json fixes this;
# these check real understanding survives (non-zero, non-placeholder matches
# across distinct categories), not just that it doesn't crash.
r = classifier.classify({"description": "ड्राइवर को दिल का दौरा पड़ गया है, वह बेहोश है"})
check("Hindi cardiac-arrest description classifies correctly",
      r.record is not None and "Cardiac" in r.record["subType"])

r = classifier.classify({"description": "सड़क पर हाथी आ गया और बाइक से टकरा गया"})
check("Hindi elephant-strike description classifies correctly",
      r.record is not None and "Elephant" in r.record["subType"])

o = engine.assess({"description": "दो गाड़ियों की टक्कर हो गई है और आग लग गई।"}, {}, None)
check("Hindi collision+fire description still dispatches FIRE via local hazard extraction",
      any(a["code"] == "FIRE" for a in o["agencies"]))

# regression test for a real reported bug: "गाड़ी की गाड़ी के साथ टक्कर हो गई और आग लग गई"
# ("a car collided with a car and a fire erupted") classified as "Dhaba / Roadside Shop Fire
# Spreading to Highway" instead of a vehicle-to-vehicle collision -- root cause was translating
# गाड़ी/वाहन to the plural "vehicles" (which only matches Car vs. Car Collision's cause text,
# 1x weight) instead of the singular "car" (which is literally that record's entire subType,
# 2x weight), and टक्कर to the overloaded "struck" (shared across ~8 unrelated "X Struck"
# subtypes) instead of "hit"/"collided" (the record's own cause-text vocabulary). Confirmed via
# testing that the identical failure mode reproduces with an English paraphrase ("Two cars
# collided...now there is a fire" -> also wrong before some phrasings dodge it by luck), so this
# was never Hindi-exclusive -- it was corpus-vocabulary-specific, just consistently exposed by
# the dictionary's original word choices.
r = classifier.classify({"description": "गाड़ी की गाड़ी के साथ टक्कर हो गई और आग लग गई।"})
check("Hindi car-vs-car + fire description classifies as a vehicle collision, not a fire record",
      r.record is not None and "Car" in r.record["subType"] and "Collision" in r.record["subType"])

# Hindi/English parity: the same real-world scenario, described in either language, should land
# on the same subType (or at minimum a record from the same category) -- this is the actual bar
# for "understands all incident types in Hindi", checked automatically rather than by spot check.
PARITY_CASES = [
    ("ड्राइवर को दिल का दौरा पड़ गया है, वह बेहोश है",
     "The driver had a heart attack and is unconscious"),
    ("सड़क पर हाथी आ गया और बाइक से टकरा गया",
     "An elephant came onto the road and hit a motorcycle"),
    ("गाड़ी की गाड़ी के साथ टक्कर हो गई और आग लग गई।",
     "A car hit another car and caught fire"),
]
for hi, en in PARITY_CASES:
    r_hi = classifier.classify({"description": hi})
    r_en = classifier.classify({"description": en})
    st_hi = r_hi.record["subType"] if r_hi.record else None
    st_en = r_en.record["subType"] if r_en.record else None
    check(f"Hindi/English parity: {en!r} -> same subType ({st_hi!r} == {st_en!r})",
          st_hi == st_en)

# Vehicle-pair override in the voice dispatcher's incident search: a caller
# naming both a car and a truck must never be recorded as "Car vs. Car"
# (real reported bug: "मेरी कार की ट्रक से टक्कर हो गई" -> Car vs. Car
# Collision, because that record's keyword-stuffed cause text out-scores the
# correct Truck vs. Car record in plain keyword overlap).
from severity_engine.dispatcher_live import _mentioned_vehicle_types, _find_vehicle_pair_subtype
m = _mentioned_vehicle_types("मेरी कार की ट्रक से टक्कर हो गई।")
check("Hindi car+truck mention detected as two vehicle types", m == {"car", "truck"})
check("car+truck pair resolves to the Truck vs. Car subtype",
      "Truck vs. Car" in (_find_vehicle_pair_subtype(m) or ""))
check("English 'car collided with a truck' detects both types",
      _mentioned_vehicle_types("car collided with a truck") == {"car", "truck"})
check("'सरकार' (government) never false-matches 'कार'",
      _mentioned_vehicle_types("सरकार की मदद चाहिए") == set())
check("'cargo' never false-matches 'car'",
      _mentioned_vehicle_types("cargo truck accident") == {"truck"})

# Same-vehicle-type-twice override (real reported bug: a Hindi caller saying
# "मेरी कार दूसरी कार से टकरा गई" -- car collided with ANOTHER car, same type
# both times -- got asked to confirm the incident type instead of it being
# recorded immediately, because _mentioned_vehicle_types dedupes "car"+"car"
# into a single-element set that the two-distinct-type override above can
# never fire on, so it fell through entirely to classify()'s fuzzy scoring).
from severity_engine.dispatcher_live import (
    _vehicle_type_mention_counts, _mentions_collision, _find_same_type_subtype,
)
check("same type named twice is counted, not deduped",
      _vehicle_type_mention_counts("मेरी कार किसी दूसरी कार से टकरा गई") == {"car": 2})
check("a single passing mention is NOT counted as twice",
      _vehicle_type_mention_counts("मेरी कार खराब हो गई").get("car", 0) == 1)
check("Hindi collision verb (टकरा) detected", _mentions_collision("मेरी कार टकरा गई"))
check("a non-collision phrase (breakdown) has no collision signal",
      not _mentions_collision("मेरी कार खराब हो गई"))
check("taxonomy has a same-type record for car", _find_same_type_subtype("car") == "Car vs. Car Collision")
check("taxonomy has no same-type record for auto-rickshaw (must not invent one)",
      _find_same_type_subtype("auto-rickshaw") is None)

import asyncio
from severity_engine.dispatcher_live import DispatcherSession, DispatcherState

class _FakeWS:
    async def send_json(self, payload):
        pass

async def _search(desc):
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    result = await s._tool_search_incident_type(desc)
    return result, s.state.sub_type

for desc in [
    "मेरी कार किसी दूसरी कार से टकरा गई",
    "एक कार ने मेरी कार को टक्कर मार दी",
    "my car collided with another car",
]:
    result, applied = asyncio.run(_search(desc))
    check(f"Hindi/English same-type override auto-applies Car vs. Car for {desc!r}",
          applied == "Car vs. Car Collision" and result.get("lowConfidence") is False)

result, applied = asyncio.run(_search("मेरी कार की ट्रक से टक्कर हो गई।"))
check("two-distinct-type override (car+truck) still resolves and is unaffected by the same-type override",
      "Truck vs. Car" in applied)

import logging as _logging
from contextlib import asynccontextmanager as _acm
from types import SimpleNamespace

class _FakeLive:
    def __init__(self):
        self.turns = []
        self.closed = False
    async def send_client_content(self, turns=None, turn_complete=True):
        self.turns.append(turns.parts[0].text)
    async def close(self):
        self.closed = True

class _RecordingWS:
    def __init__(self):
        self.sent = []
        self.bytes_sent = []
    async def send_json(self, payload):
        self.sent.append(payload)
    async def send_bytes(self, data):
        self.bytes_sent.append(data)

# ── dispatch_briefing.py: every responder section is mandatory, never omitted ──
# Real reported bug: the spoken English briefing was sometimes incomplete --
# a service missing from the dispatch_update payload (one match failed, or
# it just wasn't relevant) silently OMITTED that entire section instead of
# saying it was unavailable. _responder_facts_en must now ALWAYS return
# exactly 5 lines (ambulance, fire, towing, trauma centre, police, in that
# order), real data or an explicit "currently unavailable" line, never a
# gap. (Hindi's _responder_facts_hi is untouched and still has the old
# omit-if-missing behavior -- this fix is English-only per instruction.)
from severity_engine.dispatch_briefing import _CLOSING_EN, _responder_facts_en

_full_services = {
    "ambulance": {"name": "108 Post — Baraut", "etaMinutes": 26, "distanceKm": 18.2},
    "fire": {"name": "Fire Post — Muzaffarnagar Bypass", "etaMinutes": 36, "distanceKm": 22.0},
    "towing": {"name": "Recovery Post — Shamli", "etaMinutes": 23, "distanceKm": 15.1},
    "hospital": {"name": "Ganga Amrit Hospital", "etaMinutes": 18, "distanceKm": 12.4},
    "police": {"name": "Jorabat PS", "etaMinutes": 12, "distanceKm": 7.0},
}
_full_facts = _responder_facts_en(_full_services)
check("_responder_facts_en returns exactly 5 lines when all services are present",
      len(_full_facts) == 5)
check("_responder_facts_en names every real service's location, in order (ambulance/fire/towing/trauma/police)",
      [loc in _full_facts[i] for i, loc in enumerate(
          ["Baraut", "Muzaffarnagar Bypass", "Shamli", "Ganga Amrit Hospital", "Jorabat PS"])] == [True] * 5)
check("_responder_facts_en calls the hospital section 'trauma centre'",
      "trauma centre" in _full_facts[3].lower())

_partial_facts = _responder_facts_en({
    "ambulance": {"name": "108 Post — Baraut", "etaMinutes": 26, "distanceKm": 18.2},
    # fire, towing, hospital, police all missing
})
check("_responder_facts_en STILL returns exactly 5 lines when only 1 of 5 services is present",
      len(_partial_facts) == 5)
check("_responder_facts_en announces missing services as 'currently unavailable', never omitted",
      [
          "unavailable" in _partial_facts[1].lower(),  # fire
          "unavailable" in _partial_facts[2].lower(),  # towing
          "unavailable" in _partial_facts[3].lower(),  # trauma centre
          "unavailable" in _partial_facts[4].lower(),  # police
      ] == [True, True, True, True])

_no_facts = _responder_facts_en(None)
check("_responder_facts_en returns exactly 5 'currently unavailable' lines when NO dispatch data ever arrived",
      len(_no_facts) == 5 and all("unavailable" in f.lower() for f in _no_facts))

check("_CLOSING_EN (English-only) is trimmed to exactly the 3 mandatory closing sections "
      "(2-hour follow-up, callback-if-missed, polite close)",
      len(_CLOSING_EN) == 3
      and "two hours" in _CLOSING_EN[0].lower()
      and "call this helpline again" in _CLOSING_EN[1].lower()
      and "disconnect" in _CLOSING_EN[2].lower() and "take care" in _CLOSING_EN[2].lower())

# ── english_briefing.py: Gemini Flash script generation ───────────────────────
# Architecture (2026-07): Gemini Live's job now ends at the post-submit
# acknowledgment ("your report has been submitted successfully -- stay on
# the line"). Everything after that is generated by Gemini Flash (plain
# generate_content, a single batch text call -- see english_briefing.py) and
# spoken by Google Cloud TTS, replacing Gemini Live's own native-audio
# delivery (which is why Rounds 1-5's segmentation/reconnect-resume
# machinery for a Gemini-Live-spoken briefing no longer exists at all).
from severity_engine import english_briefing as eb

# Voice/accent matching (real user request): a FEMALE voice, USA/American
# accent to match Gemini Live's own observed accent. Locking in the exact
# default here since this project has already shipped two wrong voice
# defaults in a row (wrong gender, then an unavailable voice tier that
# silently broke the whole briefing) -- a live-verified, en-US, proven-
# available Neural2 voice this time (see dispatcher_live.py/
# english_briefing.py's own comments for the verification trail).
check("ENGLISH_TTS_VOICE_NAME defaults to a FEMALE en-US Neural2 voice (accent-matched, proven-available tier)",
      eb._TTS_VOICE_NAME == "en-US-Neural2-C")
check("ENGLISH_TTS_LANGUAGE_CODE defaults to en-US (not en-IN) to match Gemini Live's observed USA accent",
      eb._TTS_VOICE_LANGUAGE == "en-US")

class _FakeGeminiResponse:
    def __init__(self, text):
        content = SimpleNamespace(parts=[SimpleNamespace(text=text)]) if text is not None else None
        self.candidates = [SimpleNamespace(content=content)]

class _FakeGeminiClient:
    def __init__(self, text=None, raise_exc=None):
        async def generate_content(model=None, contents=None, config=None):
            if raise_exc:
                raise raise_exc
            return _FakeGeminiResponse(text)
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

def _complete_fake_flash_script(services=None):
    # A fully-compliant, naturally-REPHRASED script (not the deterministic
    # wording verbatim) -- covers every anchor group for services=None with
    # a default (no-flags) DispatcherState, so it should be ACCEPTED as-is.
    return (
        "  Great news — your registration for this report went through successfully. "
        "Right now, ambulance details are unavailable, and fire response details are also "
        "unavailable at this moment. Towing support details are unavailable too, along with "
        "trauma centre information, which is unavailable, and police station details, "
        "unavailable as well. Please stay a safe distance from traffic and remain calm. "
        "Within the next two hours our team will follow up, and if you miss that call, ring "
        "this helpline again later.  "
    )

async def _flash_script_returns_stripped_text_when_complete():
    client = _FakeGeminiClient(text=_complete_fake_flash_script())
    script = await eb.generate_dispatch_script(client, DispatcherState(language="en-IN"), None)
    return script == _complete_fake_flash_script().strip()

check("generate_dispatch_script returns Gemini Flash's text, stripped, when it covers every section",
      asyncio.run(_flash_script_returns_stripped_text_when_complete()))

async def _flash_script_rejected_when_missing_a_section():
    # Real reported bug: the spoken briefing was sometimes incomplete
    # because Flash was trusted to include every section on its own
    # judgment. Plausible-sounding but INCOMPLETE Flash output (drops fire/
    # towing/trauma/police from the "unavailable" list) must be discarded
    # entirely in favor of the deterministic fallback, not accepted as-is.
    _logging.disable(_logging.CRITICAL)
    try:
        incomplete = (
            "Your report has been registered successfully. Ambulance details are "
            "currently unavailable. Please stay calm and keep a safe distance. Our "
            "team will call within two hours, or call this helpline again."
        )
        client = _FakeGeminiClient(text=incomplete)
        script = await eb.generate_dispatch_script(client, DispatcherState(language="en-IN"), None)
        # Rejected Flash's text -> fell back to the deterministic script,
        # which explicitly names EVERY unavailable service.
        return (
            script != incomplete
            and "fire service dispatch details are currently unavailable" in script.lower()
            and "towing and recovery service dispatch details are currently unavailable" in script.lower()
            and "trauma centre are currently unavailable" in script.lower()
            and "police station are currently unavailable" in script.lower()
        )
    finally:
        _logging.disable(_logging.NOTSET)

check("generate_dispatch_script discards Flash's output entirely if it omits any required section",
      asyncio.run(_flash_script_rejected_when_missing_a_section()))

# The deterministic fallback (also what the anti-omission Flash prompt is
# built from) must follow the exact required 10-section order: submission
# confirmation, then ambulance/fire/towing/trauma-centre/police, then SOPs,
# then the 3 closing sections.
_order_state = DispatcherState(language="en-IN")
_order_state.flags = {"Heavy bleeding"}
_order_state.flags_discussed = {"Heavy bleeding"}
_order_script = eb._fallback_script(_order_state, _full_services)
_order_positions = [
    _order_script.find("registered successfully"),
    _order_script.find("Baraut"),
    _order_script.find("Muzaffarnagar Bypass"),
    _order_script.find("Shamli"),
    _order_script.find("Ganga Amrit Hospital"),
    _order_script.find("Jorabat PS"),
    _order_script.find("apply firm pressure"),  # bleeding SOP
    _order_script.find("two hours"),
    _order_script.find("call this helpline again"),
    _order_script.find("Take care"),
]
check("_fallback_script's 10 sections appear in the exact required order",
      all(p >= 0 for p in _order_positions) and _order_positions == sorted(_order_positions))

async def _flash_script_falls_back_on_exception():
    _logging.disable(_logging.CRITICAL)
    try:
        client = _FakeGeminiClient(raise_exc=RuntimeError("boom"))
        services = {"ambulance": {"name": "108 Post — X", "etaMinutes": 10, "distanceKm": 5}}
        script = await eb.generate_dispatch_script(client, DispatcherState(language="en-IN"), services)
        return "registered successfully" in script and "10 minutes" in script
    finally:
        _logging.disable(_logging.NOTSET)

check("generate_dispatch_script falls back to a deterministic script if Flash raises",
      asyncio.run(_flash_script_falls_back_on_exception()))

async def _flash_script_falls_back_on_empty_response():
    client = _FakeGeminiClient(text="")
    script = await eb.generate_dispatch_script(client, DispatcherState(language="en-IN"), None)
    # No services data at all -> every one of the 5 responder sections must
    # explicitly say so (never silently omitted -- see _responder_facts_en).
    return "registered successfully" in script and script.lower().count("currently unavailable") == 5

check("generate_dispatch_script falls back to a deterministic script on an empty Flash response",
      asyncio.run(_flash_script_falls_back_on_empty_response()))

# ── english_briefing.py: deterministic SSML wrapping ───────────────────────────
check("_to_ssml wraps the text in <speak> and inserts pauses between sentences",
      eb._to_ssml("Hello there. This is a test!")
      == '<speak>Hello there.<break time="450ms"/>This is a test!</speak>')
check("_to_ssml XML-escapes a literal ampersand", "&amp;" in eb._to_ssml("Fire & Rescue arrived."))

# ── english_briefing.py: Google Cloud TTS (mocked client -- no live API call) ──
# Real reported bug: a live call produced loud static with no intelligible
# speech. Root cause: AudioEncoding.PCM's own proto docstring claims
# headerless output, but the real API's batch synthesize_speech RPC didn't
# honor that the way the frontend's raw-Int16Array playback path assumed --
# switched to AudioEncoding.LINEAR16 (universally supported, always
# WAV-wrapped) with explicit WAV parsing via the stdlib `wave` module
# (_extract_pcm_from_wav), so these fakes must return REAL WAV bytes, not
# arbitrary bytes, to exercise the actual code path.
import io
import struct
import wave

def _make_wav_bytes(pcm_frames: bytes, channels=1, sampwidth=2, framerate=24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)
        wf.writeframes(pcm_frames)
    return buf.getvalue()

_SAMPLE_PCM_FRAMES = struct.pack("<8h", 100, -100, 200, -200, 300, -300, 400, -400)

class _FakeTTSResponse:
    def __init__(self, audio):
        self.audio_content = audio

class _FakeTTSClient:
    def __init__(self, audio=None, raise_exc=None):
        self._audio = _make_wav_bytes(_SAMPLE_PCM_FRAMES) if audio is None else audio
        self._raise = raise_exc
    async def synthesize_speech(self, input=None, voice=None, audio_config=None):
        if self._raise:
            raise self._raise
        return _FakeTTSResponse(self._audio)

async def _tts_returns_raw_audio_bytes():
    orig = eb._get_tts_client
    eb._get_tts_client = lambda: _FakeTTSClient(audio=_make_wav_bytes(_SAMPLE_PCM_FRAMES))
    try:
        return await eb.synthesize_speech("Hello.") == _SAMPLE_PCM_FRAMES
    finally:
        eb._get_tts_client = orig

check("synthesize_speech extracts the raw PCM frames from Google TTS's WAV response",
      asyncio.run(_tts_returns_raw_audio_bytes()))

async def _tts_failure_raises_english_tts_error():
    _logging.disable(_logging.CRITICAL)
    orig = eb._get_tts_client
    eb._get_tts_client = lambda: _FakeTTSClient(raise_exc=RuntimeError("quota exceeded"))
    try:
        await eb.synthesize_speech("Hello.")
        return False
    except eb.EnglishTTSError:
        return True
    finally:
        eb._get_tts_client = orig
        _logging.disable(_logging.NOTSET)

check("synthesize_speech raises EnglishTTSError on any Google TTS failure",
      asyncio.run(_tts_failure_raises_english_tts_error()))

async def _tts_empty_audio_raises():
    _logging.disable(_logging.CRITICAL)
    orig = eb._get_tts_client
    eb._get_tts_client = lambda: _FakeTTSClient(audio=b"")
    try:
        await eb.synthesize_speech("Hello.")
        return False
    except eb.EnglishTTSError:
        return True
    finally:
        eb._get_tts_client = orig
        _logging.disable(_logging.NOTSET)

async def _tts_wrong_sample_rate_raises():
    # The exact class of bug that caused the reported static: audio that
    # doesn't match the mono/16-bit/24kHz shape the frontend expects must
    # be REJECTED (falls back to on-screen text), never handed to the
    # browser to render as noise.
    _logging.disable(_logging.CRITICAL)
    orig = eb._get_tts_client
    wrong_rate_wav = _make_wav_bytes(_SAMPLE_PCM_FRAMES, framerate=16000)
    eb._get_tts_client = lambda: _FakeTTSClient(audio=wrong_rate_wav)
    try:
        await eb.synthesize_speech("Hello.")
        return False
    except eb.EnglishTTSError:
        return True
    finally:
        eb._get_tts_client = orig
        _logging.disable(_logging.NOTSET)

async def _tts_stereo_raises():
    _logging.disable(_logging.CRITICAL)
    orig = eb._get_tts_client
    stereo_wav = _make_wav_bytes(_SAMPLE_PCM_FRAMES, channels=2)
    eb._get_tts_client = lambda: _FakeTTSClient(audio=stereo_wav)
    try:
        await eb.synthesize_speech("Hello.")
        return False
    except eb.EnglishTTSError:
        return True
    finally:
        eb._get_tts_client = orig
        _logging.disable(_logging.NOTSET)

check("synthesize_speech rejects a WAV response at the wrong sample rate instead of playing static",
      asyncio.run(_tts_wrong_sample_rate_raises()))
check("synthesize_speech rejects a stereo WAV response instead of playing static",
      asyncio.run(_tts_stereo_raises()))

check("synthesize_speech raises EnglishTTSError if Google TTS returns empty audio",
      asyncio.run(_tts_empty_audio_raises()))

# Real root cause of "the agent goes completely silent after submission, no
# TTS, no error": _get_tts_client() used to be called OUTSIDE synthesize_
# speech's try block, so a non-EnglishTTSError exception building the TTS
# client (e.g. a credentials/library bug) escaped as its own exception type,
# past both this function's error handling and the caller's narrow
# `except EnglishTTSError` in dispatcher_live.py -- killing the fire-and-
# forget briefing task with nothing ever reaching the frontend. Now the
# client construction is inside the try/except too.
async def _tts_client_construction_failure_still_raises_english_tts_error():
    orig = eb._get_tts_client
    def raise_unrelated():
        raise ValueError("malformed service account credentials")
    eb._get_tts_client = raise_unrelated
    _logging.disable(_logging.CRITICAL)
    try:
        await eb.synthesize_speech("Hello.")
        return False
    except eb.EnglishTTSError:
        return True
    except ValueError:
        return False  # the bug: a raw ValueError escaped instead
    finally:
        eb._get_tts_client = orig
        _logging.disable(_logging.NOTSET)

check("synthesize_speech converts even a client-construction failure to EnglishTTSError",
      asyncio.run(_tts_client_construction_failure_still_raises_english_tts_error()))

async def _flash_script_falls_back_if_prompt_building_itself_raises():
    # Same class of bug as above: _build_flash_prompt used to be called
    # OUTSIDE generate_dispatch_script's try block.
    orig = eb._build_flash_prompt
    def raise_unrelated(state, services):
        raise KeyError("boom")
    eb._build_flash_prompt = raise_unrelated
    _logging.disable(_logging.CRITICAL)
    try:
        client = _FakeGeminiClient(text="unused")
        script = await eb.generate_dispatch_script(client, DispatcherState(language="en-IN"), None)
        return "registered successfully" in script
    finally:
        eb._build_flash_prompt = orig
        _logging.disable(_logging.NOTSET)

check("generate_dispatch_script falls back to a deterministic script even if prompt-building itself raises",
      asyncio.run(_flash_script_falls_back_if_prompt_building_itself_raises()))

# ── dispatcher_live.py: Gemini Live closes right after the post-submit ack ────
# Root cause fixed here (2026-07 redesign): rather than Gemini Live speaking
# the closing briefing itself (Rounds 1-5's whole saga), Gemini Live's job
# now ends at one short acknowledgment. _live_phase_done (Gemini Live's job
# done) and _call_over (the WHOLE call done) are deliberately different
# flags -- see DispatcherSession.__init__'s comment -- so closing Gemini
# Live early can't be mistaken for the call being over while Flash/TTS work
# remains.
class _EmptyReceiveLive(_FakeLive):
    # receive() ends immediately -- exactly what the pump sees when the
    # Gemini connection is closed server-side.
    async def receive(self):
        return
        yield  # pragma: no cover

def _fake_client(session):
    @_acm
    async def _connect(model=None, config=None):
        yield session
    return SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=_connect)))

def _session_for_lifecycle_test(live_phase_done):
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _RecordingWS()
    s.state = DispatcherState(language="en-IN")
    s.state.submitted = True
    s._live_phase_done = live_phase_done
    s._call_over = False
    s._briefing_task = None
    s._model_last_spoke = 0.0
    s._caller_last_spoke = 0.0
    s._session_started = 0.0
    s._nudge_sent_at = 0.0
    return s

async def _session_death_before_ack_confirmed_requests_reconnect():
    _logging.disable(_logging.CRITICAL)
    try:
        s = _session_for_lifecycle_test(live_phase_done=False)
        s._client_task = asyncio.create_task(asyncio.sleep(60))
        try:
            outcome = await s._run_live_session(_fake_client(_EmptyReceiveLive()), "(kickoff)")
        finally:
            s._client_task.cancel()
        return outcome == "reconnect"
    finally:
        _logging.disable(_logging.NOTSET)

async def _session_end_after_intentional_close_is_ended():
    _logging.disable(_logging.CRITICAL)
    try:
        s = _session_for_lifecycle_test(live_phase_done=True)
        s._client_task = asyncio.create_task(asyncio.sleep(60))
        try:
            outcome = await s._run_live_session(_fake_client(_EmptyReceiveLive()), "(kickoff)")
        finally:
            s._client_task.cancel()
        return outcome == "ended"
    finally:
        _logging.disable(_logging.NOTSET)

check("a Gemini session death before the post-submit ack is confirmed delivered requests reconnect",
      asyncio.run(_session_death_before_ack_confirmed_requests_reconnect()))
check("a session end once Gemini Live's job is intentionally done (live_phase_done) returns ended",
      asyncio.run(_session_end_after_intentional_close_is_ended()))

def _reconnect_kickoff_always_holds_the_line_post_submission():
    s = _session_for_lifecycle_test(live_phase_done=False)
    k = s._reconnect_kickoff()
    return "stay on the line" in k and "welcome" in k.lower() and "Do NOT say the welcome line" in k

check("reconnect kickoff post-submission always sends the short holding line (no briefing to resume)",
      _reconnect_kickoff_always_holds_the_line_post_submission())

# Voice matching (real user request): Gemini Live's conversational voice and
# the closing briefing's Google Cloud TTS voice used to sound like two
# different people. English (en-IN) must now be explicitly pinned to a
# named voice rather than left at Gemini Live's unset/undocumented default.
# Switched from "Charon" (MALE) to "Sulafat" (FEMALE, "Warm") per a later
# explicit user request for a female voice.
def _build_config_pins_english_voice_to_sulafat():
    s = DispatcherSession.__new__(DispatcherSession)
    s.state = DispatcherState(language="en-IN")
    config = s._build_config()
    voice_config = config.speech_config.voice_config
    return (
        voice_config is not None
        and voice_config.prebuilt_voice_config.voice_name == "Sulafat"
    )

check("_build_config pins English (en-IN) Gemini Live to the same voice as the TTS briefing (Sulafat)",
      _build_config_pins_english_voice_to_sulafat())

def _build_config_does_not_touch_hindi_voice():
    # Hindi never actually reaches this method (confirmed: dispatcher_hindi.py
    # has its own run() and never references _build_config/_run_live_session),
    # but this locks in that the voice-pinning code path is explicitly gated
    # on language == "en-IN" and does nothing for hi-IN as belt-and-suspenders.
    s = DispatcherSession.__new__(DispatcherSession)
    s.state = DispatcherState(language="hi-IN")
    config = s._build_config()
    return config.speech_config.voice_config is None

check("_build_config leaves Hindi's speech_config untouched (no voice_config set)",
      _build_config_does_not_touch_hindi_voice())

# The mic must NOT reopen after the post-submission "stay on the line"
# acknowledgment turn (real reported bug, predates this redesign but the
# mechanism is unchanged: _pump_gemini_to_client sent {"status":"listening"}
# unconditionally after every turn_complete, including this one -- reopening
# the frontend mic gate for a phase where the caller has nothing left to say
# and Gemini Live never speaks again regardless).
class _FakeLiveSession:
    def __init__(self, events):
        self._events = events
    async def receive(self):
        for e in self._events:
            yield e
    async def send_client_content(self, turns=None, turn_complete=True):
        pass

def _make_turn_complete_event():
    model_turn = SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x00"))])
    sc = SimpleNamespace(
        input_transcription=None, output_transcription=None,
        model_turn=model_turn, interrupted=False, turn_complete=True,
    )
    return SimpleNamespace(tool_call=None, server_content=sc)

async def _mic_stays_closed_after_submission_ack_turn():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _RecordingWS()
    s.state = DispatcherState(language="en-IN")
    s.state.submitted = True
    s._live_phase_done = False
    s._call_over = False
    s._briefing_task = None
    s._model_last_spoke = 0.0
    s._caller_last_spoke = 0.0

    live = _FakeLiveSession([_make_turn_complete_event()])
    s._live_session = live

    async def fake_end_conversation():
        await asyncio.sleep(3600)  # not under test here -- see the handoff tests below
    s._end_conversation_and_deliver_briefing = fake_end_conversation

    call_count = {"n": 0}
    orig_receive = live.receive
    def receive_wrapper():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return orig_receive()
        async def empty():
            return
            yield  # pragma: no cover
        s._live_phase_done = True  # let the pump's outer loop exit instead of re-blocking on receive()
        return empty()
    live.receive = receive_wrapper

    await s._pump_gemini_to_client()
    statuses = [m.get("state") for m in s.websocket.sent if m.get("type") == "status"]
    return "listening" not in statuses and "thinking" in statuses and s._briefing_task is not None

check("mic does not reopen (no 'listening' status) after the post-submission acknowledgment turn",
      asyncio.run(_mic_stays_closed_after_submission_ack_turn()))

# ── dispatcher_live.py: the Flash+TTS handoff itself ───────────────────────────
async def _handoff_happy_path_closes_live_and_delivers_audio():
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    orig_get_client, orig_gen, orig_synth = dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech
    try:
        dl._get_client = lambda: "FAKE-CLIENT"
        async def fake_gen(client, state, services):
            assert client == "FAKE-CLIENT"
            return "The script."
        async def fake_synth(text):
            assert text == "The script."
            return b"\x01" * 20000  # forces multiple 8192-byte chunks
        dl.generate_dispatch_script = fake_gen
        dl.synthesize_speech = fake_synth

        s = DispatcherSession.__new__(DispatcherSession)
        s._ws_send_lock = asyncio.Lock()
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._dispatch_info = {"ambulance": {"name": "108 Post — X", "etaMinutes": 10, "distanceKm": 5}}
        s._dispatch_ready = asyncio.Event()
        s._dispatch_ready.set()
        s._live_phase_done = False
        s._call_over = False
        fake_live = _FakeLive()
        s._live_session = fake_live

        await s._end_conversation_and_deliver_briefing()

        return (
            s._live_phase_done is True
            and fake_live.closed is True
            and s._call_over is True
            and sum(len(c) for c in s.websocket.bytes_sent) == 20000
            and len(s.websocket.bytes_sent) == 3  # 20000 / 8192 -> 3 chunks
            and {"type": "status", "state": "briefing"} in s.websocket.sent
            and {"type": "call_complete"} in s.websocket.sent
        )
    finally:
        dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech = orig_get_client, orig_gen, orig_synth
        _logging.disable(_logging.NOTSET)

check("the Flash+TTS handoff closes Gemini Live, sends chunked audio, and ends the call",
      asyncio.run(_handoff_happy_path_closes_live_and_delivers_audio()))

# Real reported bug: the agent started speaking the closing briefing for
# real, then was shut down abruptly mid-sentence. Root-caused (in part) to
# unsynchronized concurrent writes on the same WebSocket -- _keepalive()
# (every 10s, for the whole call) racing with _send_audio_chunks() (which
# can be 100+ chunks for a full briefing) with nothing preventing both from
# calling send_bytes()/send_json() on the same connection at once. Every
# send now goes through _ws_send_lock via _safe_send_json/_safe_send_bytes
# -- this test proves the lock actually serializes concurrent callers
# rather than just existing unused.
class _ConcurrencyDetectingWS:
    """Fails a send if another send is already in flight on this same fake
    connection -- simulates the real risk (frame interleaving / a
    concurrent-write exception) without needing a real ASGI WebSocket."""
    def __init__(self):
        self.busy = False
        self.violation = False
        self.completed = []
    async def _send(self, kind):
        if self.busy:
            self.violation = True
        self.busy = True
        try:
            await asyncio.sleep(0.02)  # simulate real I/O taking measurable time
        finally:
            self.busy = False
        self.completed.append(kind)
    async def send_json(self, payload):
        await self._send("json")
    async def send_bytes(self, data):
        await self._send("bytes")

async def _concurrent_sends_are_serialized_by_the_lock():
    s = DispatcherSession.__new__(DispatcherSession)
    s.websocket = _ConcurrencyDetectingWS()
    s._ws_send_lock = asyncio.Lock()

    # Fire a keepalive-shaped JSON send and a multi-chunk audio send at the
    # exact same time -- without the lock, these would race on the same
    # fake connection.
    await asyncio.gather(
        s._safe_send_json({"type": "keepalive"}),
        s._send_audio_chunks(b"\x00" * (8192 * 3)),  # 3 chunks
    )
    return not s.websocket.violation and len(s.websocket.completed) == 4  # 1 keepalive + 3 chunks

check("concurrent keepalive + audio-chunk sends are serialized by _ws_send_lock, never interleaved",
      asyncio.run(_concurrent_sends_are_serialized_by_the_lock()))

async def _handoff_tts_failure_falls_back_to_text():
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    orig_get_client, orig_gen, orig_synth = dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech
    try:
        dl._get_client = lambda: "FAKE-CLIENT"
        async def fake_gen(client, state, services):
            return "The script."
        async def fake_synth(text):
            raise dl.EnglishTTSError("boom")
        dl.generate_dispatch_script = fake_gen
        dl.synthesize_speech = fake_synth

        s = DispatcherSession.__new__(DispatcherSession)
        s._ws_send_lock = asyncio.Lock()
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._dispatch_info = None
        s._dispatch_ready = asyncio.Event()
        s._dispatch_ready.set()
        s._live_phase_done = False
        s._call_over = False
        s._live_session = _FakeLive()

        await s._end_conversation_and_deliver_briefing()

        tts_text_events = [m for m in s.websocket.sent if m.get("type") == "tts_text"]
        return (
            s._call_over is True
            and len(tts_text_events) == 1
            and tts_text_events[0]["text"] == "The script."
            and not s.websocket.bytes_sent  # no audio was ever sent on the failure path
            and {"type": "call_complete"} in s.websocket.sent
        )
    finally:
        dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech = orig_get_client, orig_gen, orig_synth
        _logging.disable(_logging.NOTSET)

check("a Google TTS failure falls back to the tts_text event and still ends the call cleanly",
      asyncio.run(_handoff_tts_failure_falls_back_to_text()))

# Real reported bug: after incident submission, the agent went completely
# silent -- no TTS audio, no error, nothing. Root cause: _end_conversation_
# and_deliver_briefing had NO enclosing try/except at all, so an unexpected
# exception ANYWHERE in the pipeline (credentials bug, prompt-building bug,
# anything neither english_briefing.py's own narrower handlers nor the
# `except EnglishTTSError` here were written to catch) killed the
# fire-and-forget task with nothing ever reaching the frontend. Now
# _deliver_briefing_or_raise is allowed to raise ANYTHING, and the outer
# _end_conversation_and_deliver_briefing guarantees a terminal signal
# (tts_text + call_complete) and _call_over=True regardless.
async def _handoff_survives_a_totally_unexpected_exception():
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    orig_get_client, orig_gen, orig_synth = dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech
    try:
        dl._get_client = lambda: "FAKE-CLIENT"
        async def raise_something_unexpected(client, state, services):
            raise AttributeError("some completely unanticipated bug")
        dl.generate_dispatch_script = raise_something_unexpected
        async def fake_synth(text):
            return b"\x00"  # never reached
        dl.synthesize_speech = fake_synth

        s = DispatcherSession.__new__(DispatcherSession)
        s._ws_send_lock = asyncio.Lock()
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._dispatch_info = None
        s._dispatch_ready = asyncio.Event()
        s._dispatch_ready.set()
        s._live_phase_done = False
        s._call_over = False
        s._live_session = _FakeLive()

        await s._end_conversation_and_deliver_briefing()

        return (
            s._call_over is True
            and {"type": "call_complete"} in s.websocket.sent
            and any(m.get("type") == "tts_text" for m in s.websocket.sent)
        )
    finally:
        dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech = orig_get_client, orig_gen, orig_synth
        _logging.disable(_logging.NOTSET)

check("a totally unexpected exception in the handoff still ends the call, never leaves it silent",
      asyncio.run(_handoff_survives_a_totally_unexpected_exception()))

# ── dispatcher_live.py: run() must AWAIT the handoff task, never cancel it,
# purely because Gemini Live itself closed early ───────────────────────────────
# The whole point of closing Gemini Live right after the ack is to free it
# immediately rather than holding it open through the (potentially slower)
# Flash+TTS work -- if run() cancelled _briefing_task the instant
# _run_live_session returned "ended", that would kill the Flash/TTS work
# before it ever got to speak anything. It must only be cancelled if the
# caller has actually hung up (_client_task done).
async def _run_awaits_handoff_task_when_caller_still_connected():
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    orig_get_client = dl._get_client
    try:
        dl._get_client = lambda: "FAKE-CLIENT"
        s = DispatcherSession.__new__(DispatcherSession)
        s._ws_send_lock = asyncio.Lock()
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._pending_location = {}
        s._live_session = None
        s._keepalive_task = None
        s._session_started = 0.0
        s._caller_last_spoke = 0.0
        s._model_last_spoke = 0.0
        s._nudge_sent_at = 0.0
        s._dispatch_info = None
        s._dispatch_ready = asyncio.Event()
        s._live_phase_done = False
        s._call_over = False

        finished = {"v": False}
        async def slow_handoff():
            await asyncio.sleep(0.3)
            finished["v"] = True
        s._briefing_task = asyncio.create_task(slow_handoff())

        async def fake_get_location():
            return {"status": "unavailable"}
        s._tool_get_current_location = fake_get_location
        async def fake_client_pump():
            await asyncio.sleep(3600)  # caller stays connected for the whole test
        s._pump_client_to_gemini = fake_client_pump
        async def fake_keepalive():
            await asyncio.sleep(3600)
        s._keepalive = fake_keepalive
        async def fake_run_live_session(client, kickoff):
            return "ended"
        s._run_live_session = fake_run_live_session

        await s.run()
        return finished["v"] is True
    finally:
        dl._get_client = orig_get_client
        _logging.disable(_logging.NOTSET)

async def _run_cancels_handoff_task_when_caller_disconnected():
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    orig_get_client = dl._get_client
    try:
        dl._get_client = lambda: "FAKE-CLIENT"
        s = DispatcherSession.__new__(DispatcherSession)
        s._ws_send_lock = asyncio.Lock()
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._pending_location = {}
        s._live_session = None
        s._keepalive_task = None
        s._session_started = 0.0
        s._caller_last_spoke = 0.0
        s._model_last_spoke = 0.0
        s._nudge_sent_at = 0.0
        s._dispatch_info = None
        s._dispatch_ready = asyncio.Event()
        s._live_phase_done = False
        s._call_over = False

        finished = {"v": False}
        async def slow_handoff():
            await asyncio.sleep(0.3)
            finished["v"] = True
        s._briefing_task = asyncio.create_task(slow_handoff())

        async def fake_get_location():
            return {"status": "unavailable"}
        s._tool_get_current_location = fake_get_location
        async def fake_client_pump():
            return  # completes immediately -- simulates the caller already gone
        s._pump_client_to_gemini = fake_client_pump
        async def fake_keepalive():
            await asyncio.sleep(3600)
        s._keepalive = fake_keepalive
        async def fake_run_live_session(client, kickoff):
            await asyncio.sleep(0.05)  # give the (already-completing) client task a moment to finish first
            return "ended"
        s._run_live_session = fake_run_live_session

        await s.run()
        return finished["v"] is False
    finally:
        dl._get_client = orig_get_client
        _logging.disable(_logging.NOTSET)

check("run() awaits the Flash+TTS handoff to completion when the caller is still connected",
      asyncio.run(_run_awaits_handoff_task_when_caller_still_connected()))
check("run() cancels the Flash+TTS handoff instead of waiting when the caller has disconnected",
      asyncio.run(_run_cancels_handoff_task_when_caller_disconnected()))

# ── Incident-type transcript backstop + implied vehicle count ─────────────────
# Real reported bug: the caller described a car-on-car collision, the model
# recorded the DESCRIPTION but never called search_incident_type, then asked
# "what kind of incident was it?". Flags and counts already had a rule-first
# transcript backstop for exactly this model-forgetting; incident type now
# has the same one. And when the vehicle overrides fire, the caller has by
# definition named two vehicles -- so the count is recorded too, instead of
# the agent asking "how many vehicles?" right after being told.

async def _backstop_sets_type_and_vehicles():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.caller_transcript = " मेरी कार दूसरी कार से टकरा गई है।"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type == "Car vs. Car Collision" and s.state.vehicles_involved == 2

async def _backstop_ignores_gibberish():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.caller_transcript = " हैलो, सुनिए"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type is None

async def _backstop_never_overrides_existing_type():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.sub_type = "Head-On Collision"
    s.state.category = "Vehicle Collisions"
    s.state.caller_transcript = " मेरी कार दूसरी कार से टकरा गई है।"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type == "Head-On Collision"

async def _implied_count_never_overwrites_caller_number():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.vehicles_involved = 3
    await s._tool_search_incident_type("कार की ट्रक से टक्कर हो गई")
    return s.state.vehicles_involved == 3

check("transcript backstop sets incident type + implied vehicle count without any tool call",
      asyncio.run(_backstop_sets_type_and_vehicles()))
check("transcript backstop stays silent on non-incident chatter",
      asyncio.run(_backstop_ignores_gibberish()))

# Symptom words must never determine incident TYPE (real reported bug: the
# caller answering "चार लोग घायल हैं" -- four people injured -- got the
# incident classified as "Injured Wild Animal on Road – Active Rescue" at
# confidence 0.87, because घायल→"injured" was the only scoring token and that
# record is the only subType containing it; symptoms describe the aftermath
# of ANY incident, so they carry zero type signal and are now stopwords).
for injury_only in ["चार लोग घायल हैं", "4 people injured", "हताहत हुए हैं"]:
    r = classifier.guess(injury_only)
    check(f"injury-only answer {injury_only!r} never classifies confidently",
          r.get("subType") is None or r.get("lowConfidence") is True)
# ...but the records that contained symptom words are still findable by
# their real distinguishing words.
check("wild-animal rescue still classifies from a real animal description",
      classifier.guess("a wild animal is injured on the road and needs rescue")["subType"]
      == "Injured Wild Animal on Road – Active Rescue")
check("dense fog still classifies",
      classifier.guess("dense fog zero visibility on the highway")["subType"]
      == "Dense Fog / Zero Visibility")

async def _symptom_only_tool_call_applies_nothing():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    result = await s._tool_search_incident_type("चार लोग घायल हैं")
    return s.state.sub_type is None and result.get("lowConfidence") is True

async def _backstop_recovers_type_from_full_transcript_after_injury_answer():
    # The exact reported sequence: injury answer classifies nothing, but the
    # full transcript (which contains the actual collision description) does.
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    await s._tool_search_incident_type("चार लोग घायल हैं")
    s.state.caller_transcript = " मेरी कार दूसरी कार से टकरा गई है। हाँ, चार लोग घायल हैं।"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type == "Car vs. Car Collision" and s.state.vehicles_involved == 2

check("search_incident_type on a symptom-only answer applies no type",
      asyncio.run(_symptom_only_tool_call_applies_nothing()))
check("backstop recovers the correct type from the full transcript after an injury answer",
      asyncio.run(_backstop_recovers_type_from_full_transcript_after_injury_answer()))
check("transcript backstop never overrides an already-recorded incident type",
      asyncio.run(_backstop_never_overrides_existing_type()))
check("implied vehicle count never overwrites the caller's own number",
      asyncio.run(_implied_count_never_overwrites_caller_number()))

# ── Hindi single-round fast path ──────────────────────────────────────────────
# Latency: a tool-using Hindi turn used to cost TWO sequential Gemini round
# trips; the fast path composes "model's acknowledgment + code-appended
# canonical question" and skips the second one. The question is appended by
# CODE from the deterministic next_question, so it structurally cannot wander
# off-list -- but only when every guard holds; anything unprovable falls back
# to the second round (see _compose_single_round_reply).
from severity_engine.dispatcher_hindi import (
    _CANONICAL_QUESTIONS,
    HindiDispatcherSession,
)
from severity_engine.dispatcher_live import (
    DEFAULT_REQUIRED_FIELDS,
    REQUIRED_FIELDS,
)

_all_hints = [item for fields in REQUIRED_FIELDS.values() for item in fields] + DEFAULT_REQUIRED_FIELDS
_uncovered = [
    item["hint"] for item in _all_hints
    if item["hint"] not in _CANONICAL_QUESTIONS
]
check(f"every REQUIRED_FIELDS hint has a canonical Hindi question (uncovered: {_uncovered})",
      not _uncovered)

def _fresh_hindi_session():
    s = HindiDispatcherSession.__new__(HindiDispatcherSession)
    HindiDispatcherSession.__init__(s, _FakeWS())
    s.state.sub_type = "Car vs. Car Collision"
    s.state.category = "Vehicle Collisions"
    s.state.description = "Car collided with another car"
    s.state.vehicles_involved = 2
    s.state.location = {"lat": 28.5, "lng": 77.3, "label": "Noida"}
    return s

s = _fresh_hindi_session()
composed = s._compose_single_round_reply("अच्छा... दो लोग घायल हैं", {"update_form_field"})
check("fast path composes ack + canonical question with punctuation added",
      composed == "अच्छा... दो लोग घायल हैं। क्या किसी को चोट लगी है?")
check("fast path refuses when the ack contains its own question",
      s._compose_single_round_reply("ठीक है। क्या कोई फँसा है?", {"update_form_field"}) is None)
check("fast path refuses for tools whose results the model must read (submit/browse/location)",
      s._compose_single_round_reply("ठीक है।", {"update_form_field", "submit_incident"}) is None)
check("fast path refuses with no tool calls at all",
      s._compose_single_round_reply("ठीक है।", set()) is None)
s_done = _fresh_hindi_session()
s_done.state.casualties = 0
s_done.state.flags_discussed = {"Trapped", "Fire"}
check("fast path refuses at the summarize-and-confirm stage (nothing missing)",
      s_done._compose_single_round_reply("ठीक है।", {"update_form_field"}) is None)

class _FakeGeminiClient:
    """Minimal stand-in for the google-genai client: returns canned responses
    and counts calls, so the fast path's one-round-vs-two behavior is
    testable offline."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        outer = self
        class _Models:
            async def generate_content(self, model=None, contents=None, config=None):
                outer.calls += 1
                return outer._responses.pop(0)
        class _Aio:
            models = _Models()
        self.aio = _Aio()

def _model_response(parts):
    from google.genai import types as gtypes
    return SimpleNamespace(candidates=[SimpleNamespace(content=gtypes.Content(role="model", parts=parts))])

async def _reason_uses_single_round():
    from google.genai import types as gtypes
    s = _fresh_hindi_session()
    fake = _FakeGeminiClient([
        _model_response([
            gtypes.Part(text="अच्छा... दो लोग घायल हैं"),
            gtypes.Part(function_call=gtypes.FunctionCall(
                name="update_form_field", args={"field": "casualties", "number_value": 2})),
        ]),
    ])
    reply = await s._reason(fake, "दो लोग घायल हैं")
    last = s._history[-1]
    return (
        fake.calls == 1
        and reply == "अच्छा... दो लोग घायल हैं। क्या कोई गाड़ी के अंदर फँसा हुआ है?"
        and s.state.casualties == 2
        and last.role == "model" and last.parts[0].text == reply
    )

async def _reason_falls_back_without_ack_text():
    from google.genai import types as gtypes
    s = _fresh_hindi_session()
    fake = _FakeGeminiClient([
        _model_response([  # round 0: tool call but NO ack text -> must do round 1
            gtypes.Part(function_call=gtypes.FunctionCall(
                name="update_form_field", args={"field": "casualties", "number_value": 2})),
        ]),
        _model_response([gtypes.Part(text="समझ गया... क्या कोई फँसा हुआ है?")]),
    ])
    reply = await s._reason(fake, "दो लोग घायल हैं")
    return fake.calls == 2 and reply == "समझ गया... क्या कोई फँसा हुआ है?"

check("fast path answers in ONE Gemini round and mirrors the reply into history",
      asyncio.run(_reason_uses_single_round()))
check("missing ack text still falls back to the normal second round",
      asyncio.run(_reason_falls_back_without_ack_text()))

# Reconnect resilience (real reported bug: a call hit "The voice service hit
# a technical problem" after exhausting reconnects; no code-level regression
# was found on investigation, but reconnect budget/backoff was hardened
# regardless -- see the comment above _MAX_RECONNECTS in dispatcher_live.py).
from severity_engine.dispatcher_live import _MAX_RECONNECTS, _RECONNECT_BACKOFF_S

check("at least 4 reconnect attempts before giving up",
      _MAX_RECONNECTS >= 4)
_delays = [_RECONNECT_BACKOFF_S[min(i, len(_RECONNECT_BACKOFF_S) - 1)] for i in range(_MAX_RECONNECTS)]
check("reconnect backoff strictly increases then holds (never flat from the first retry)",
      _delays == sorted(_delays) and _delays[0] < _delays[-1])

# WebSocket keepalive (real reported bug, persisting even after the 3-segment
# split: the call would sometimes go silent mid-briefing with no error shown
# at all -- not a spoken cutoff, a hard connection drop). Root cause: this
# WebSocket had no application-level keepalive, and the post-submission phase
# has genuinely idle stretches (waiting on dispatch_update, and between
# briefing segments while Gemini generates the next one) that a proxy's idle-
# connection timeout could close out from under the call -- which the
# frontend then treats as a normal, silent call end once submitted=True (see
# submittedRef in useVoiceDispatcher.ts). Fixed with a periodic lightweight
# frame sent for the whole call. Shared by both dispatchers (Hindi's own
# dispatch_update wait has the identical risk, even though only English was
# reported so far).
import logging as _logging2

class _KeepaliveWS:
    def __init__(self):
        self.sent = []
    async def send_json(self, payload):
        self.sent.append(payload)

async def _keepalive_fires_periodically_and_cancels_cleanly():
    from severity_engine import dispatcher_live as dl
    old = dl._KEEPALIVE_INTERVAL_S
    dl._KEEPALIVE_INTERVAL_S = 0.2
    _logging2.disable(_logging2.CRITICAL)
    try:
        s = DispatcherSession.__new__(DispatcherSession)
        s._ws_send_lock = asyncio.Lock()
        s.websocket = _KeepaliveWS()
        task = asyncio.create_task(s._keepalive())
        await asyncio.sleep(0.65)  # should fire at least twice
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        keepalives = [m for m in s.websocket.sent if m.get("type") == "keepalive"]
        return len(keepalives) >= 2
    finally:
        dl._KEEPALIVE_INTERVAL_S = old
        _logging2.disable(_logging2.NOTSET)

check("keepalive task sends periodic frames and cancels cleanly (no hang, no exception)",
      asyncio.run(_keepalive_fires_periodically_and_cancels_cleanly()))

print("\nALL TESTS PASSED")
