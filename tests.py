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

# Post-submission closing briefing must never cut a long reply off mid-speech
# (real reported bug: caller heard the ambulance ETA, then got cut off
# partway into the fire service ETA). Root cause: _brief_and_close's failsafe
# used to be a single flat asyncio.sleep(45) "total time budget" from when the
# briefing turn was sent -- but the full briefing (multiple responder ETAs +
# up to 4 SOP lines + a 6-line closing script) can legitimately take well
# over a minute of continuous speech, so the failsafe fired mid-reply and
# told the frontend the call was over while the backend was still actively
# streaming audio for it. Fixed by polling for genuine STALLING (time since
# the last audio chunk) instead of a flat total budget -- verified here with
# a simulated ~20s of continuously-arriving audio chunks (representative of
# a long real briefing) that must never trigger a premature call_complete,
# and a genuine no-audio-ever stall that must still end the call (no hang).
import time as _time
import logging as _logging

class _FakeLive:
    def __init__(self):
        self.turns = []
    async def send_client_content(self, turns=None, turn_complete=True):
        self.turns.append(turns.parts[0].text)

class _RecordingWS:
    def __init__(self):
        self.sent = []
    async def send_json(self, payload):
        self.sent.append(payload)
    async def send_bytes(self, data):
        pass

async def _briefing_survives_a_long_active_reply():
    from severity_engine import dispatcher_live as dl
    old_timeout = dl._BRIEFING_STALL_TIMEOUT_S
    dl._BRIEFING_STALL_TIMEOUT_S = 2.0  # shrunk only for test speed
    _logging.disable(_logging.CRITICAL)
    try:
        s = DispatcherSession.__new__(DispatcherSession)
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._dispatch_info = {"ambulance": {"name": "108 Post — X", "etaMinutes": 10, "distanceKm": 5}}
        s._dispatch_ready = asyncio.Event()
        s._dispatch_ready.set()
        s._call_over = False
        s._briefing_sent = False
        s._model_last_spoke = 0.0
        fake_live = _FakeLive()

        async def simulate_20s_of_speech():
            for _ in range(66):  # ~20s of chunks arriving every 0.3s
                await asyncio.sleep(0.3)
                s._model_last_spoke = _time.monotonic()
            s._call_over = True  # normal completion, as the real pump would set it

        await asyncio.gather(s._brief_and_close(fake_live), simulate_20s_of_speech())
        return len(fake_live.turns) == 1 and {"type": "call_complete"} not in s.websocket.sent

    finally:
        dl._BRIEFING_STALL_TIMEOUT_S = old_timeout
        _logging.disable(_logging.NOTSET)

async def _briefing_still_ends_on_genuine_stall():
    from severity_engine import dispatcher_live as dl
    old_timeout = dl._BRIEFING_STALL_TIMEOUT_S
    dl._BRIEFING_STALL_TIMEOUT_S = 0.5
    _logging.disable(_logging.CRITICAL)
    try:
        s = DispatcherSession.__new__(DispatcherSession)
        s.websocket = _RecordingWS()
        s.state = DispatcherState(language="en-IN")
        s._dispatch_info = None
        s._dispatch_ready = asyncio.Event()
        s._dispatch_ready.set()
        s._call_over = False
        s._briefing_sent = False
        s._model_last_spoke = 0.0
        await s._brief_and_close(_FakeLive())
        return {"type": "call_complete"} in s.websocket.sent
    finally:
        dl._BRIEFING_STALL_TIMEOUT_S = old_timeout
        _logging.disable(_logging.NOTSET)

check("closing briefing survives ~20s of continuously-active speech without a premature cutoff",
      asyncio.run(_briefing_survives_a_long_active_reply()))
check("closing briefing still force-ends the call on genuine silence (no hang)",
      asyncio.run(_briefing_still_ends_on_genuine_stall()))

# The mic must NOT reopen after the post-submission "stay on the line"
# acknowledgment turn (real reported bug: the English agent started speaking
# automatically and repeatedly, asking the same thing over and over without
# waiting for a real reply). Root cause: _pump_gemini_to_client sent
# {"status":"listening"} unconditionally after every turn_complete, including
# the acknowledgment turn right after submit_incident -- reopening the
# frontend mic gate (useVoiceDispatcher.ts only opens the mic on "listening"
# for en-IN) for the entire up-to-30s dispatch_update wait in
# _brief_and_close, even though the caller has nothing left to say at that
# point in the call. Any caller utterance or background noise picked up
# during that window was treated by Gemini Live (which is reactive -- it
# only speaks in response to input) as a fresh turn, and with no new
# information to report yet, the model just repeated its "please hold on"
# line every time it heard anything at all.
from types import SimpleNamespace

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
    s.websocket = _RecordingWS()
    s.state = DispatcherState(language="en-IN")
    s.state.submitted = True
    s._call_over = False
    s._briefing_sent = False
    s._spoke_after_briefing = False
    s._briefing_task = None
    s._model_last_spoke = 0.0
    s._caller_last_spoke = 0.0

    live = _FakeLiveSession([_make_turn_complete_event()])
    s._live_session = live

    async def fake_brief_and_close(live_session):
        await asyncio.sleep(3600)  # not under test here -- see the briefing tests above
    s._brief_and_close = fake_brief_and_close

    call_count = {"n": 0}
    orig_receive = live.receive
    def receive_wrapper():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return orig_receive()
        async def empty():
            return
            yield  # pragma: no cover
        s._call_over = True  # let the pump's outer loop exit instead of re-blocking on receive()
        return empty()
    live.receive = receive_wrapper

    await s._pump_gemini_to_client()
    statuses = [m.get("state") for m in s.websocket.sent if m.get("type") == "status"]
    return "listening" not in statuses and "thinking" in statuses and s._briefing_task is not None

check("mic does not reopen (no 'listening' status) after the post-submission acknowledgment turn",
      asyncio.run(_mic_stays_closed_after_submission_ack_turn()))

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
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.caller_transcript = " मेरी कार दूसरी कार से टकरा गई है।"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type == "Car vs. Car Collision" and s.state.vehicles_involved == 2

async def _backstop_ignores_gibberish():
    s = DispatcherSession.__new__(DispatcherSession)
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.caller_transcript = " हैलो, सुनिए"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type is None

async def _backstop_never_overrides_existing_type():
    s = DispatcherSession.__new__(DispatcherSession)
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.sub_type = "Head-On Collision"
    s.state.category = "Vehicle Collisions"
    s.state.caller_transcript = " मेरी कार दूसरी कार से टकरा गई है।"
    await s._apply_local_signals_from_transcript()
    return s.state.sub_type == "Head-On Collision"

async def _implied_count_never_overwrites_caller_number():
    s = DispatcherSession.__new__(DispatcherSession)
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
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    result = await s._tool_search_incident_type("चार लोग घायल हैं")
    return s.state.sub_type is None and result.get("lowConfidence") is True

async def _backstop_recovers_type_from_full_transcript_after_injury_answer():
    # The exact reported sequence: injury answer classifies nothing, but the
    # full transcript (which contains the actual collision description) does.
    s = DispatcherSession.__new__(DispatcherSession)
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

print("\nALL TESTS PASSED")
