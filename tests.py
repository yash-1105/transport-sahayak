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
        self.closed = False
    async def send_client_content(self, turns=None, turn_complete=True):
        self.turns.append(turns.parts[0].text)
    async def close(self):
        self.closed = True

class _RecordingWS:
    def __init__(self):
        self.sent = []
    async def send_json(self, payload):
        self.sent.append(payload)
    async def send_bytes(self, data):
        pass

async def _briefing_survives_a_long_active_reply():
    # _brief_and_close only ever sends the FIRST segment itself (see
    # build_briefing_segments/_send_next_briefing_segment) -- the pump sends
    # the rest as each one's own turn_complete arrives (covered separately
    # below). This test covers the stall failsafe around that first send.
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
        s._briefing_segments = None
        s._briefing_segment_started_at = 0.0
        s._model_last_spoke = 0.0
        fake_live = _FakeLive()
        s._live_session = fake_live

        async def simulate_20s_of_speech():
            for _ in range(66):  # ~20s of chunks arriving every 0.3s
                await asyncio.sleep(0.3)
                s._model_last_spoke = _time.monotonic()
            s._call_over = True  # normal completion, as the real pump would set it

        await asyncio.gather(s._brief_and_close(), simulate_20s_of_speech())
        return len(fake_live.turns) == 1 and {"type": "call_complete"} not in s.websocket.sent

    finally:
        dl._BRIEFING_STALL_TIMEOUT_S = old_timeout
        _logging.disable(_logging.NOTSET)

# A genuine stall no longer force-ends the call (an earlier version did,
# silently dropping every undelivered segment -- the same caller-facing
# symptom as the mid-briefing session death this round's fix addresses).
# Instead it must CLOSE the wedged Gemini session -- which ends the pump and
# hands control to run()'s reconnect loop, which resumes the briefing from
# the in-flight segment -- and must never send call_complete itself.
# Termination is still guaranteed by run()'s _MAX_RECONNECTS budget plus its
# finally-block cancelling this task.
async def _briefing_stall_closes_session_for_reconnect_resume():
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
        s._briefing_segments = None
        s._briefing_segment_started_at = 0.0
        s._model_last_spoke = 0.0
        fake_live = _FakeLive()
        s._live_session = fake_live

        task = asyncio.create_task(s._brief_and_close())
        await asyncio.sleep(2.5)  # long enough for the 0.5s stall to trip
        closed_on_stall = fake_live.closed
        premature_end = {"type": "call_complete"} in s.websocket.sent
        s._call_over = True  # as run()'s reconnect/give-up path would resolve it
        await task
        return closed_on_stall and not premature_end
    finally:
        dl._BRIEFING_STALL_TIMEOUT_S = old_timeout
        _logging.disable(_logging.NOTSET)

check("closing briefing survives ~20s of continuously-active speech without a premature cutoff",
      asyncio.run(_briefing_survives_a_long_active_reply()))
check("a genuine briefing stall closes the Gemini session for reconnect-resume, never call_complete",
      asyncio.run(_briefing_stall_closes_session_for_reconnect_resume()))

# ── Segmented closing-briefing delivery (English/Gemini Live) ─────────────────
# Real reported bug, in three rounds: agent stopped after ambulance; after a
# first fix (3 segments: facts/SOPs/closing) it got through fire before
# stopping; after strengthening the facts segment it got through towing
# before stopping. Each fix bought a little more content, never a full fix --
# confirmed live this isn't a fixed length threshold (a single 5-sentence
# facts turn worked 6/6 times in isolated short-context test sessions, even
# with a real ~25s idle gap injected matching production's dispatch_update
# wait), so generation reliability must be degrading with how much a REAL
# call has already accumulated in a way isolated testing can't reproduce.
# The only defensible fix at that point is maximum granularity: build_briefing
# _segments now returns ONE micro-turn per fact/SOP-line/closing-line -- never
# more than a single short sentence asked of the model in any one turn, so
# there is nothing left within a turn that COULD be dropped or cut short.
from severity_engine.dispatch_briefing import build_briefing_segments

def _segments_for_test():
    st = DispatcherState(language="en-IN")
    st.flags = {"Heavy bleeding", "Trapped"}
    st.flags_discussed = {"Heavy bleeding", "Trapped"}
    services = {"ambulance": {"name": "108 Post — Baraut", "etaMinutes": 26, "distanceKm": 18.2}}
    return build_briefing_segments(st, services, "en-IN")

_segs = _segments_for_test()
# 1 fact + 3 SOPs (bleeding, trapped, general) + 6 closing lines = 10 turns
check("build_briefing_segments returns one micro-turn per fact/SOP-line/closing-line",
      len(_segs) == 10)
check("the fact segment carries the responder fact", "26 minutes" in _segs[0] and "Baraut" in _segs[0])
check("a SOP segment carries the bleeding instruction",
      any("bleeding" in s.lower() for s in _segs[1:4]))
check("the last segment carries the final closing line",
      "disconnect" not in _segs[-1].lower() and "safe" in _segs[-1].lower())
check("only the LAST segment is marked as the final turn",
      all("the FINAL turn" not in s for s in _segs[:-1]) and "the FINAL turn" in _segs[-1])
check("only the LAST segment tells the model the call ends here",
      all("the call ends here" not in s for s in _segs[:-1]) and "the call ends here" in _segs[-1])
check("every non-final segment tells the model not to add anything from later in the sequence",
      all("more is coming right after this" in s for s in _segs[:-1]))
check("every segment says it is the ONLY point in that turn",
      all("ONLY point in this turn" in s for s in _segs))

# Multi-service case: exactly one micro-turn per service, in order, each with
# its own exact name and number -- nothing pre-emptively trimmed or merged.
_multi_services = {
    "ambulance": {"name": "108 Post — Baraut", "etaMinutes": 26, "distanceKm": 18.2},
    "fire": {"name": "Fire Post — Muzaffarnagar Bypass", "etaMinutes": 36, "distanceKm": 22.0},
    "towing": {"name": "Recovery Post — Shamli", "etaMinutes": 23, "distanceKm": 15.1},
    "hospital": {"name": "Ganga Amrit Hospital", "etaMinutes": 18, "distanceKm": 12.4},
    "police": {"name": "Jorabat PS", "etaMinutes": 12, "distanceKm": 7.0},
}
_multi_segs = build_briefing_segments(DispatcherState(language="en-IN"), _multi_services, "en-IN")
# 5 facts + 1 general SOP (no flags set) + 6 closing lines = 12 turns
check("multi-service case still yields exactly one segment per fact (5) + SOPs + closing",
      len(_multi_segs) == 12)
check("the 5 fact segments each carry exactly one service's real name/location, in order",
      # _facility_location() strips everything before "—" (station names
      # follow "<Label> — <Location>"), so only the location half is spoken.
      [loc in _multi_segs[i] for i, loc in enumerate(
          ["Baraut", "Muzaffarnagar Bypass", "Shamli", "Ganga Amrit Hospital", "Jorabat PS"])] == [True] * 5)
check("a fact segment does not also contain a DIFFERENT service's name (true one-per-turn)",
      "Muzaffarnagar Bypass" not in _multi_segs[0] and "Baraut" not in _multi_segs[1])

# dispatch_update never arrived / matching failed -- must never invent a
# number, but must still produce a valid (non-empty) segment sequence.
_no_data_segs = build_briefing_segments(DispatcherState(language="en-IN"), None, "en-IN")
check("no-dispatch-data case still produces a valid, non-empty segment sequence",
      len(_no_data_segs) >= 1 and "notified" in _no_data_segs[0].lower())
check("no-dispatch-data fact segment invents no specific ETA minute count",
      not any(f" {n} minute" in _no_data_segs[0] for n in range(1, 200)))

async def _send_next_segment_sequence_ends_only_after_all_sent():
    s = DispatcherSession.__new__(DispatcherSession)
    s.websocket = _RecordingWS()
    s.state = DispatcherState(language="en-IN")
    s._call_over = False
    segments = _segments_for_test()
    n = len(segments)
    s._briefing_segments = segments
    s._briefing_total = n
    s._briefing_segment_started_at = 0.0
    live = _FakeLive()
    s._live_session = live

    results = []
    for _ in range(n + 1):  # every real segment + 1 call after they're exhausted
        await s._send_next_briefing_segment()
        results.append((len(live.turns), s._call_over))

    expected = [(i, False) for i in range(1, n + 1)] + [(n, True)]
    return results == expected and {"type": "call_complete"} in s.websocket.sent

check("segments are sent one at a time in order, call ends only once all are exhausted",
      asyncio.run(_send_next_segment_sequence_ends_only_after_all_sent()))

# ── Mid-briefing reconnect-and-resume (English/Gemini Live) ───────────────────
# 4th report of "the agent stops partway through the closing briefing", and
# the actual root cause of the whole saga: _run_live_session used to return
# "ended" whenever state.submitted was True -- making the reconnect loop
# structurally unreachable for the entire post-submission phase, so ANY
# Gemini Live session death mid-briefing (connection lifetime limits /
# GoAway / transient failures -- likeliest at the END of a long call, which
# is exactly where the briefing sits) silently hung up on the caller: run()
# returned, app.py closed the socket, and the frontend treats any
# post-submission close as a normal call end. The segment-granularity work
# of earlier rounds only ever changed WHERE the death landed. Now a session
# death with the briefing undelivered returns "reconnect", and
# _reconnect_kickoff resumes from the exact in-flight segment.
from contextlib import asynccontextmanager as _acm
from types import SimpleNamespace

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

def _session_for_lifecycle_test(call_over):
    s = DispatcherSession.__new__(DispatcherSession)
    s.websocket = _RecordingWS()
    s.state = DispatcherState(language="en-IN")
    s.state.submitted = True
    s._call_over = call_over
    s._briefing_task = None
    s._briefing_segments = ["SEG-B", "SEG-C"]
    s._current_briefing_segment = "SEG-A"
    s._briefing_total = 3
    s._briefing_segment_started_at = 0.0
    s._model_last_spoke = 0.0
    s._caller_last_spoke = 0.0
    s._session_started = 0.0
    s._nudge_sent_at = 0.0
    return s

async def _session_death_mid_briefing_requests_reconnect():
    _logging.disable(_logging.CRITICAL)
    try:
        s = _session_for_lifecycle_test(call_over=False)
        s._client_task = asyncio.create_task(asyncio.sleep(60))
        try:
            outcome = await s._run_live_session(_fake_client(_EmptyReceiveLive()), "(kickoff)")
        finally:
            s._client_task.cancel()
        return outcome == "reconnect"
    finally:
        _logging.disable(_logging.NOTSET)

async def _session_end_after_briefing_complete_is_ended():
    _logging.disable(_logging.CRITICAL)
    try:
        s = _session_for_lifecycle_test(call_over=True)
        s._client_task = asyncio.create_task(asyncio.sleep(60))
        try:
            outcome = await s._run_live_session(_fake_client(_EmptyReceiveLive()), "(kickoff)")
        finally:
            s._client_task.cancel()
        return outcome == "ended"
    finally:
        _logging.disable(_logging.NOTSET)

check("a Gemini session death mid-briefing now requests reconnect, never a silent call end",
      asyncio.run(_session_death_mid_briefing_requests_reconnect()))
check("a session end after the briefing fully delivered (call_over) still ends the call",
      asyncio.run(_session_end_after_briefing_complete_is_ended()))

# _reconnect_kickoff must resume the briefing from the exact in-flight
# segment: the one sent but never turn-completed is re-delivered in full
# (repeating one short line beats silently losing it), and calling it again
# (a second death before the resume ever got sent) is idempotent.
def _resume_kickoff_replays_in_flight_segment():
    s = _session_for_lifecycle_test(call_over=False)
    k1 = s._reconnect_kickoff()
    first = (k1, list(s._briefing_segments), s._current_briefing_segment)
    k2 = s._reconnect_kickoff()  # died again before the resume was delivered
    second = (k2, list(s._briefing_segments), s._current_briefing_segment)
    expected = ("SEG-A", ["SEG-B", "SEG-C"], "SEG-A")
    return first == expected and second == expected

check("reconnect kickoff resumes the briefing from the in-flight segment, idempotently",
      _resume_kickoff_replays_in_flight_segment())

def _resume_kickoff_before_briefing_holds_the_line():
    s = _session_for_lifecycle_test(call_over=False)
    s._briefing_segments = None
    s._current_briefing_segment = None
    k = s._reconnect_kickoff()
    return "stay on the line" in k and "welcome" in k.lower() and "Do NOT say the welcome line" in k

check("reconnect kickoff before briefing delivery sends the holding line, never the greeting",
      _resume_kickoff_before_briefing_holds_the_line())

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
    s._briefing_segments = None
    s._briefing_task = None
    s._model_last_spoke = 0.0
    s._caller_last_spoke = 0.0

    live = _FakeLiveSession([_make_turn_complete_event()])
    s._live_session = live

    async def fake_brief_and_close():
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
