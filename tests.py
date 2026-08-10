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

# ── #6: widened local extraction (worded counts, zero-fill, vitals) ───────────
_ex = local_extract.extract_signals_locally
check("worded casualty count (EN): 'two people injured' -> 2",
      _ex("two people injured in the crash")["estimatedCasualties"] == 2)
check("worded casualty count (HI): 'दो लोग घायल' -> 2",
      _ex("दो लोग घायल हैं")["estimatedCasualties"] == 2)
check("worded vehicle count (HI): 'तीन गाड़ियाँ' -> 3",
      _ex("तीन गाड़ियाँ आपस में टकरा गईं")["estimatedVehiclesInvolved"] == 3)
check("'a couple of cars' -> 2 vehicles (approximate worded number)",
      _ex("a couple of cars collided")["estimatedVehiclesInvolved"] == 2)
check("bare 'a car'/'a truck' is NOT counted (keeps the two-vehicle override intact)",
      _ex("a car hit a truck")["estimatedVehiclesInvolved"] is None)
check("zero-fill (EN): 'no one is hurt' -> casualties 0 + bleeding ruled out",
      _ex("no one is hurt, just some damage")["estimatedCasualties"] == 0
      and _ex("no one is hurt")["flag_determinations"].get("Heavy bleeding") is False)
check("zero-fill (HI): 'सब ठीक हैं, कोई घायल नहीं' -> casualties 0",
      _ex("सब ठीक हैं, कोई घायल नहीं")["estimatedCasualties"] == 0)
check("negated hazard is DISCUSSED-and-ruled-out: 'there is no fire' -> Fire False (not absent)",
      _ex("there is no fire")["flag_determinations"].get("Fire") is False)
check("Hindi negated fire 'आग नहीं लगी' -> Fire ruled out, fire signal stays False (regression)",
      _ex("आग नहीं लगी")["flag_determinations"].get("Fire") is False
      and _ex("आग नहीं लगी")["fire"] is False)
check("vital: 'the driver is unconscious' -> Conscious False",
      _ex("the driver is unconscious")["flag_determinations"].get("Conscious") is False)
check("vital: 'he is breathing normally' -> Breathing True",
      _ex("he is breathing normally")["flag_determinations"].get("Breathing") is True)
check("vital: 'bleeding heavily' -> Heavy bleeding True; 'no bleeding' -> False",
      _ex("she is bleeding heavily")["flag_determinations"].get("Heavy bleeding") is True
      and _ex("there is no bleeding")["flag_determinations"].get("Heavy bleeding") is False)
check("un-mentioned flags stay absent from determinations (never guessed)",
      "Trapped" not in _ex("a minor scratch on the bumper")["flag_determinations"]
      and "Fire" not in _ex("a minor scratch on the bumper")["flag_determinations"])

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
# ETA phrasing simplified (2026-07): ambulance/fire/towing lines state just the
# estimated time -- no "responds from {location}" origin -- while still being
# "notified" and stating an "approximately N minutes" estimate. Hospital/police
# still name their DESTINATION facility (trauma centre / notified station).
check("_responder_facts_en drops the origin location for ambulance/fire/towing (states just the estimated time)",
      not any(loc in _full_facts[i] for i, loc in enumerate(["Baraut", "Muzaffarnagar Bypass", "Shamli"]))
      and all("notified" in _full_facts[i].lower() and "approximately" in _full_facts[i].lower()
              and str(eta) in _full_facts[i]
              for i, eta in [(0, 26), (1, 36), (2, 23)]))
check("_responder_facts_en still names the destination facility for the trauma centre and police station",
      "trauma centre" in _full_facts[3].lower() and "Ganga Amrit Hospital" in _full_facts[3]
      and "Jorabat PS" in _full_facts[4])

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

# Hindi ETA phrasing simplified the same way (drop "{location} से आएगी" origin);
# Hindi keeps its omit-if-missing behavior (only present services get a line).
from severity_engine.dispatch_briefing import _responder_facts_hi
_hi_facts = _responder_facts_hi(_full_services)
check("_responder_facts_hi drops the origin location for ambulance/fire/towing but keeps 'सूचित' + 'लगभग' estimate",
      len(_hi_facts) == 5
      and "Baraut" not in _hi_facts[0] and "से आएगी" not in _hi_facts[0]
      and "सूचित कर दिया गया है" in _hi_facts[0] and "लगभग" in _hi_facts[0]
      and "Ganga Amrit Hospital" in _hi_facts[3])

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

# #7 regression: after the ETA phrasing dropped the origin location, the
# coverage anchors for PRESENT services became the service label words (no
# longer the facility location). A validly-rephrased script that keeps every
# service label + time must still be ACCEPTED, not falsely rejected.
async def _flash_script_accepted_with_present_services():
    services = {
        "ambulance": {"name": "108 Post — Baraut", "etaMinutes": 26},
        "fire": {"name": "Fire Post — Muzaffarnagar", "etaMinutes": 36},
        "towing": {"name": "Recovery Post — Shamli", "etaMinutes": 23},
        "hospital": {"name": "Ganga Amrit Hospital", "etaMinutes": 18},
        "police": {"name": "Jorabat PS", "etaMinutes": 12},
    }
    rephrased = (
        "Your report has been registered. The nearest ambulance service has been notified, "
        "about 26 minutes away. The fire service is on its way, roughly 36 minutes out. A "
        "towing and recovery team has been notified too, around 23 minutes. The nearest trauma "
        "centre is Ganga Amrit Hospital, about 18 minutes by road. The police station Jorabat PS "
        "has been notified, roughly 12 minutes away. Please keep a safe distance from traffic. "
        "Our team will call within two hours, and if you miss it, call this helpline again."
    )
    client = _FakeGeminiClient(text=rephrased)
    script = await eb.generate_dispatch_script(client, DispatcherState(language="en-IN"), services)
    return script == rephrased  # accepted as-is, not rejected to the deterministic fallback
check("generate_dispatch_script ACCEPTS a rephrased present-services script under the new label anchors (#7)",
      asyncio.run(_flash_script_accepted_with_present_services()))

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
    _order_script.find("ambulance service"),      # ambulance line (origin location no longer present)
    _order_script.find("fire service"),            # fire line
    _order_script.find("towing and recovery"),     # towing line
    _order_script.find("Ganga Amrit Hospital"),    # trauma centre (still names its facility)
    _order_script.find("Jorabat PS"),              # police (still names its station)
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
            # #2: the hold line is synthesized+sent FIRST (distinct byte), then
            # the main briefing script.
            if text == dl._ENGLISH_HOLD_LINE:
                return b"\x02" * 4096   # 1 chunk
            assert text == "The script."
            return b"\x01" * 20000      # forces multiple 8192-byte chunks (3)
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
            # hold-line audio (4096 -> 1 chunk) is sent BEFORE the briefing
            # audio (20000 -> 3 chunks): 4 chunks total, hold first.
            and len(s.websocket.bytes_sent) == 4
            and s.websocket.bytes_sent[0][:1] == b"\x02"        # hold line first
            and sum(len(c) for c in s.websocket.bytes_sent) == 4096 + 20000
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
            # BOTH the hold line and the script fall back to on-screen text when
            # TTS is down: hold line first, then the briefing script.
            and len(tts_text_events) == 2
            and tts_text_events[0]["text"] == dl._ENGLISH_HOLD_LINE
            and tts_text_events[1]["text"] == "The script."
            and not s.websocket.bytes_sent  # no audio was ever sent on the failure path
            and {"type": "call_complete"} in s.websocket.sent
        )
    finally:
        dl._get_client, dl.generate_dispatch_script, dl.synthesize_speech = orig_get_client, orig_gen, orig_synth
        _logging.disable(_logging.NOTSET)

check("a Google TTS failure falls back to the tts_text event and still ends the call cleanly",
      asyncio.run(_handoff_tts_failure_falls_back_to_text()))

# #2 (Hindi): the closing-briefing delivery speaks the fixed hold line FIRST,
# before generating/speaking the main ETA briefing turn, to fill the gap.
async def _hindi_briefing_speaks_hold_line_first():
    from severity_engine import dispatcher_hindi as dh
    s = dh.HindiDispatcherSession.__new__(dh.HindiDispatcherSession)
    s.state = DispatcherState(language="hi-IN")
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _RecordingWS()
    s._dispatch_ready = asyncio.Event(); s._dispatch_ready.set()
    s._dispatch_info = None
    s._ended = asyncio.Event()  # not set -> proceed
    s._turn_stats = {}
    s._briefing_config = None
    order = []
    async def fake_speak(text, *_a, **_kw):
        order.append(("speak", text)); return True
    async def fake_agent_turn(client, instruction, config=None):
        order.append(("agent_turn", instruction))
    s._speak_or_fallback = fake_speak
    s._agent_turn = fake_agent_turn
    await s._deliver_dispatch_briefing(gemini_client=None)
    return (
        len(order) >= 2
        and order[0] == ("speak", dh._HINDI_HOLD_LINE)   # hold line first
        and order[1][0] == "agent_turn"                   # then the briefing turn
        and {"type": "call_complete"} in s.websocket.sent
    )
check("Hindi briefing speaks the hold line first, then the ETA briefing turn (#2)",
      asyncio.run(_hindi_briefing_speaks_hold_line_first()))

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

# #6 backstop integration: zero/negation fill and vitals from the narrative flow
# into state (casualties=0, flags_discussed) so the deterministic next_question
# stops asking about them; and the two-vehicle override still beats a singular
# worded count now that count extraction runs AFTER classification.
async def _backstop_zero_and_negation_drop_questions():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="en-IN")
    s.state.category = "Vehicle Collisions"
    s.state.sub_type = "Car vs. Car Collision"
    s.state.description = "two cars bumped"
    s.state.location = {"lat": 1, "lng": 1, "label": "x"}
    s.state.vehicles_involved = 2
    s.state.caller_transcript = " Two cars bumped, no one is hurt and there is no fire."
    await s._apply_local_signals_from_transcript()
    missing = s._compute_still_missing()
    return (s.state.casualties == 0
            and "Fire" in s.state.flags_discussed and "Fire" not in s.state.flags
            and not any("injured" in m or "fire" in m for m in missing))

async def _backstop_override_beats_singular_worded_count():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s.state.caller_transcript = " एक कार दूसरी कार से टकरा गई।"
    await s._apply_local_signals_from_transcript()
    return s.state.vehicles_involved == 2 and s.state.sub_type == "Car vs. Car Collision"

async def _backstop_single_vehicle_narrative_still_counts():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="en-IN")
    s.state.caller_transcript = " One car broke down on the shoulder."
    await s._apply_local_signals_from_transcript()
    return s.state.vehicles_involved == 1

check("backstop: 'no one hurt / no fire' -> casualties 0 + Fire marked discussed, dropping both questions",
      asyncio.run(_backstop_zero_and_negation_drop_questions()))
check("backstop: 'one car hit another car' -> 2 vehicles (override beats singular 'one', count extracted last)",
      asyncio.run(_backstop_override_beats_singular_worded_count()))
check("backstop: a genuine single-vehicle narrative ('one car broke down') still records 1",
      asyncio.run(_backstop_single_vehicle_narrative_still_counts()))

# ── Hindi single-round fast path ──────────────────────────────────────────────
# Latency: a tool-using Hindi turn used to cost TWO sequential Gemini round
# trips; the fast path composes "model's acknowledgment + code-appended
# canonical question" and skips the second one. The question is appended by
# CODE from the deterministic next_question, so it structurally cannot wander
# off-list -- but only when every guard holds; anything unprovable falls back
# to the second round (see _compose_single_round_reply).
from severity_engine.dispatcher_hindi import (
    _CANONICAL_QUESTIONS,
    _FAST_ACK_NEUTRAL,
    _FAST_ACK_CRITICAL,
    HindiDispatcherSession,
)
from severity_engine.dispatcher_live import (
    DEFAULT_REQUIRED_FIELDS,
    REQUIRED_FIELDS,
)

# Coverage: every hint _compute_still_missing can emit -- both the per-field
# INDIVIDUAL hints (partial-group case) AND the per-group COMBINED hints
# (all-missing case) -- must have a canonical Hindi phrasing, or the Hindi fast
# path silently disables for it. REQUIRED_FIELDS is now a list of GROUPS.
_all_groups = [g for groups in REQUIRED_FIELDS.values() for g in groups] + DEFAULT_REQUIRED_FIELDS
_needed_hints = set()
for _g in _all_groups:
    _needed_hints.update(item["hint"] for item in _g["fields"])
    if "combined" in _g:
        _needed_hints.add(_g["combined"])
_uncovered = sorted(h for h in _needed_hints if h not in _CANONICAL_QUESTIONS)
check(f"every REQUIRED_FIELDS hint (individual + combined) has a canonical Hindi question (uncovered: {_uncovered})",
      not _uncovered)

# #1: combined question when ALL of a group's fields are missing; the group
# dissolves to individual questions (for just the remaining fields) once some
# are already filled -- and it must produce the same sequence in EN and HI.
def _sess_for(category, language="en-IN", **state):
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language=language)
    s.state.category = category
    s.state.sub_type = "X"
    s.state.description = "x"
    s.state.location = {"lat": 1, "lng": 1, "label": "x"}
    for k, v in state.items():
        setattr(s.state, k, v)
    return s

_s_all = _sess_for("Vehicle Collisions")
check("#1 all-missing group -> ONE combined question (count + injury + trapped)",
      _s_all._compute_still_missing()[0]
      == "how many vehicles were involved, and whether anyone is injured or trapped")
check("#1 EN and HI produce the SAME next_question sequence for the same state",
      _sess_for("Vehicle Collisions", language="hi-IN")._compute_still_missing()
      == _s_all._compute_still_missing())
# casualties=0 (a KNOWN, non-critical value) exercises the partial-group path
# without tripping the #1 injury fast-track -- casualties > 0 would (correctly)
# skip all remaining secondary questions, so it can't be used as a neutral
# "already-filled" field here.
_s_partial = _sess_for("Vehicle Collisions", vehicles_involved=2, casualties=0)
_m_partial = _s_partial._compute_still_missing()
check("#1 partially-filled group -> individual question for the remainder, no re-ask, no combined",
      "how many vehicles were involved, and whether anyone is injured or trapped" not in _m_partial
      and "how many vehicles were involved" not in _m_partial   # vehicles already known -> not re-asked
      and "how many people are injured" not in _m_partial       # casualties already known -> not re-asked
      and "whether anyone is trapped" in _m_partial)
check("#1 fire+hazmat group asks the combined fire/hazmat question when both are undiscussed",
      "whether there is any fire or a hazardous-material leak" in _m_partial)
# #2 pruning: a medical case is never asked for a vehicle count.
check("#2 Medical & Casualty never asks how many vehicles",
      all("vehicle" not in m for m in _sess_for("Medical & Casualty")._compute_still_missing()))
# The combined question flows through the Hindi fast path verbatim from code.
_hi_all = HindiDispatcherSession.__new__(HindiDispatcherSession)
HindiDispatcherSession.__init__(_hi_all, _FakeWS())
_hi_all._accident_mode = True  # Phase 2: the accident sequence is gated on this
_hi_all.state.category = "Vehicle Collisions"
_hi_all.state.sub_type = "X"
_hi_all.state.description = "x"
_hi_all.state.location = {"lat": 1, "lng": 1, "label": "x"}
check("#1 Hindi fast path appends the canonical COMBINED question for an all-missing group",
      _hi_all._compose_single_round_reply("अच्छा...", {"update_form_field"})
      == "अच्छा... कुल कितनी गाड़ियाँ थीं, और क्या किसी को चोट लगी या कोई फँसा हुआ है?")

# #4: severity fast-track. A life-threatening condition skips the routine
# secondary safety/count questions and the summarize-and-confirm gate; the three
# essentials (type/location/description) still gate submission. Stays a
# notification record (no auto-dispatch implied).
check("#4 _is_critical: trapped", _sess_for("Vehicle Collisions", flags={"Trapped"})._is_critical())
check("#4 _is_critical: fire", _sess_for("Vehicle Collisions", flags={"Fire"})._is_critical())
check("#4 _is_critical: heavy bleeding", _sess_for("Vehicle Collisions", flags={"Heavy bleeding"})._is_critical())
check("#4 _is_critical: unconscious (Conscious discussed, not active)",
      _sess_for("Vehicle Collisions", flags_discussed={"Conscious"})._is_critical())
check("#4 _is_critical: not breathing (Breathing discussed, not active)",
      _sess_for("Vehicle Collisions", flags_discussed={"Breathing"})._is_critical())
# #1: an injury (casualties > 0) or a vulnerable victim now also fast-tracks,
# not only the explicit hazard flags.
check("#1 _is_critical: someone injured (casualties > 0)",
      _sess_for("Vehicle Collisions", casualties=1)._is_critical())
check("#1 _is_critical: multiple injured (casualties > 0)",
      _sess_for("Vehicle Collisions", casualties=4)._is_critical())
check("#1 _is_critical: vulnerable victim at risk (child/pregnant/elderly/disabled)",
      _sess_for("Vehicle Collisions", vulnerable_victim=True)._is_critical())
check("#4 not critical when only routine facts are known",
      not _sess_for("Vehicle Collisions", vehicles_involved=2, casualties=0)._is_critical())
check("#1 not critical when casualties is confirmed zero and nothing else",
      not _sess_for("Vehicle Collisions", casualties=0, vulnerable_victim=False)._is_critical())

_crit = _sess_for("Vehicle Collisions", flags={"Trapped"})
check("#4 critical + all essentials known -> no further questions (safety groups skipped)",
      _crit._compute_still_missing() == [])
check("#4 fast_track surfaced in the state block when critical",
      _crit._state_block()["fast_track"] is True)
_crit_no_desc = _sess_for("Vehicle Collisions", flags={"Fire"}, description="")
check("#4 critical still requires the 3 essentials before submit (description still asked)",
      _crit_no_desc._compute_still_missing() == ["a short description of what happened"])
_normal = _sess_for("Vehicle Collisions")
check("#4 non-critical still asks the routine combined safety question first, fast_track False",
      _normal._compute_still_missing()[0]
      == "how many vehicles were involved, and whether anyone is injured or trapped"
      and _normal._state_block()["fast_track"] is False)

# ── #4 Hindi staged / interim dispatch ────────────────────────────────────────
# Hindi (the REAL HindiDispatcherSession, not the base class used above) keeps
# collecting every field even when critical -- urgency is handled by an interim
# dispatch, not by cutting the report short. English is unchanged (asserted
# above via _sess_for, which builds a base DispatcherSession).
class _CapWS:
    def __init__(self): self.sent = []
    async def send_json(self, payload): self.sent.append(payload)

def _hi_staged(**state):
    s = HindiDispatcherSession.__new__(HindiDispatcherSession)
    HindiDispatcherSession.__init__(s, _CapWS())
    s._accident_mode = True  # Phase 2: these tests exercise the accident branch
    s.state.category = "Vehicle Collisions"
    s.state.sub_type = "X"
    s.state.description = "x"
    s.state.location = {"lat": 28.6, "lng": 77.2, "label": "NH-334"}
    for k, v in state.items():
        setattr(s.state, k, v)
    return s

_hi_crit = _hi_staged(flags={"Trapped"})
check("#4 Hindi critical does NOT short-circuit -- keeps collecting the secondary questions",
      _hi_crit._compute_still_missing() != [])
check("#4 Hindi critical still surfaces fast_track True (drives auto-submit-without-confirm)",
      _hi_crit._state_block()["fast_track"] is True)
check("#4 Hindi non-critical secondary-question ORDER matches the shared logic (no regression)",
      _hi_staged()._compute_still_missing()
      == _sess_for("Vehicle Collisions")._compute_still_missing())

# _pending_interim_services: ambulance when critical + location; fire on a fire/
# hazmat flag; nothing before a location is known; deduped once notified.
check("#4 interim: critical + location -> ambulance",
      _hi_staged(flags={"Trapped"})._pending_interim_services() == ["ambulance"])
check("#4 interim: fire flag -> ambulance + fire (fire is itself critical)",
      _hi_staged(flags={"Fire"})._pending_interim_services() == ["ambulance", "fire"])
check("#4 interim: hazmat flag also dispatches fire",
      "fire" in _hi_staged(flags={"Hazardous material"})._pending_interim_services())
_hi_noloc = _hi_staged(flags={"Trapped"}); _hi_noloc.state.location = None
check("#4 interim: NEVER dispatch before a location is known",
      _hi_noloc._pending_interim_services() == [])
check("#4 interim: a non-critical incident dispatches nothing early",
      _hi_staged(casualties=0)._pending_interim_services() == [])

async def _interim_dispatch_sends_frame_and_arms_line():
    s = _hi_staged(flags={"Trapped"})
    await s._maybe_interim_dispatch()
    frame = s.websocket.sent[-1]
    ok_frame = (
        frame["type"] == "interim_dispatch"
        and frame["services"] == ["ambulance"]
        and frame["location"]["label"] == "NH-334"
    )
    # The single deterministic reassurance line is armed EMPATHY-FIRST, then the
    # "help is being arranged" part -- honest wording only (no ETA/minutes), and
    # there is NO model note (the model never announces help itself).
    line = s._pending_interim_spoken or ""
    ok_line = (
        "दुख" in line and "एम्बुलेंस का इंतज़ाम" in line
        and line.index("दुख") < line.index("एम्बुलेंस")   # empathy BEFORE the ambulance line
        and "मिनट" not in line
        and not hasattr(s, "_pending_interim_note")       # note mechanism removed entirely
    )
    # deduped: a second pass with the same state sends nothing new
    before = len(s.websocket.sent)
    await s._maybe_interim_dispatch()
    ok_dedup = len(s.websocket.sent) == before
    return ok_frame and ok_line and ok_dedup and s._dispatched_services == {"ambulance"}

check("#4 interim: _maybe_interim_dispatch sends one frame + arms one empathy-first ETA-free line, and dedups",
      asyncio.run(_interim_dispatch_sends_frame_and_arms_line()))

async def _interim_turn_is_empathy_then_help_then_question():
    # #4: the interim turn is spoken DETERMINISTICALLY in the fixed order
    # empathy -> "help being arranged" -> next question, exactly once. The
    # model's own reply is IGNORED for speech this turn (used only for its tool
    # calls), so it can never reorder, double, or repeat the help announcement.
    s = _hi_staged(flags={"Trapped"})
    await s._maybe_interim_dispatch()
    spoken = []
    async def fake_reason(client, user_text, config=None):
        return "अच्छा... मैं एम्बुलेंस भेज रहा हूँ, क्या कोई फँसा है?"  # model tries to announce help
    async def fake_speak(text, allow_bargein=True, **_kw):
        spoken.append(text); return True
    async def _noop(*a, **k):
        return None
    s._reason = fake_reason
    s._speak_or_fallback = fake_speak
    s._preconnect_tts = _noop
    s._enter_listening = _noop
    s._opening_line_pending = False
    await s._agent_turn(None, "कोई फँसा है")
    out = spoken[0] if spoken else ""
    canonical = "कुल कितनी गाड़ियाँ थीं, और क्या किसी को चोट लगी या कोई फँसा हुआ है?"
    return (
        len(spoken) == 1
        and out.startswith("ओह")                                   # empathy FIRST
        and out.index("दुख") < out.index("एम्बुलेंस का इंतज़ाम")   # empathy before help line
        and out.index("एम्बुलेंस का इंतज़ाम") < out.index(canonical)  # help before the question
        and out.endswith(canonical)                                # ends with the canonical question
        and "अच्छा" not in out                                     # the model's own reply is NOT spoken
        and s._pending_interim_spoken is None                      # consumed (spoken once)
    )

check("#4 interim: the turn is spoken empathy -> help-being-arranged -> question (model reply ignored, no repeat)",
      asyncio.run(_interim_turn_is_empathy_then_help_then_question()))

# Regression guard: the ENGLISH pipeline must be completely unaffected by #4 --
# base DispatcherSession has no staged-dispatch machinery at all.
check("#4 English (base DispatcherSession) has NO interim-dispatch method (Hindi-only change)",
      not hasattr(DispatcherSession, "_maybe_interim_dispatch")
      and hasattr(HindiDispatcherSession, "_maybe_interim_dispatch"))

async def _backstop_unconscious_triggers_fast_track():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="en-IN")
    s.state.caller_transcript = " There's been a crash, the driver is unconscious."
    await s._apply_local_signals_from_transcript()
    return s._is_critical() and "Conscious" in s.state.flags_discussed and "Conscious" not in s.state.flags
check("#4 backstop: 'the driver is unconscious' in the narrative triggers fast_track",
      asyncio.run(_backstop_unconscious_triggers_fast_track()))

async def _backstop_injury_triggers_fast_track():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="en-IN")
    s.state.caller_transcript = " Two cars crashed and two people are injured."
    await s._apply_local_signals_from_transcript()
    return (s.state.casualties or 0) > 0 and s._is_critical()
check("#1 backstop: a stated injury count in the narrative triggers fast_track",
      asyncio.run(_backstop_injury_triggers_fast_track()))

async def _backstop_vulnerable_victim_triggers_fast_track():
    s = DispatcherSession.__new__(DispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="en-IN")
    # A vulnerable person at risk, with no explicit casualty count or hazard flag.
    s.state.caller_transcript = " A small child was hit by a car near the market."
    await s._apply_local_signals_from_transcript()
    return s.state.vulnerable_victim and s._is_critical()
check("#1 backstop: a vulnerable victim (child) in the narrative triggers fast_track",
      asyncio.run(_backstop_vulnerable_victim_triggers_fast_track()))

def _fresh_hindi_session():
    s = HindiDispatcherSession.__new__(HindiDispatcherSession)
    HindiDispatcherSession.__init__(s, _FakeWS())
    s._accident_mode = True  # Phase 2: fast-path tests exercise the accident branch
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
# Vehicle Collisions now groups fire+hazmat, so both must be discussed for the
# form to be complete (nothing missing).
s_done.state.flags_discussed = {"Trapped", "Fire", "Hazardous material"}
check("fast path refuses at the summarize-and-confirm stage (nothing missing)",
      s_done._compose_single_round_reply("ठीक है।", {"update_form_field"}) is None)

# The single biggest Exotel latency finding (2026-08): Sarvam returns tool calls
# with EMPTY content, so the fast path used to bail (not ack) and force a whole
# second reasoning round -> double latency + a verbose free-form reply + the
# round-1 timeout that fell back to Gemini. The fast path must now FIRE with an
# empty ack by substituting a short deterministic ack.
s_empty = _fresh_hindi_session()
s_empty._use_sarvam = True           # empty-ack composing fires whenever Sarvam is PRIMARY...
s_empty._turn_backend = "gemini"     # ...even a round Gemini served as the FALLBACK (Sarvam timed out)
composed_empty = s_empty._compose_single_round_reply("", {"update_form_field"})
check("fast path FIRES with an EMPTY ack in Sarvam-primary mode (incl. Gemini-served fallback round)",
      composed_empty is not None and "?" in composed_empty
      and any(composed_empty.startswith(a) for a in _FAST_ACK_NEUTRAL))
s_gem = _fresh_hindi_session()
s_gem._use_sarvam = False            # pure Gemini-PRIMARY: an empty ack is the "split" case -> must bail
check("empty ack in Gemini-PRIMARY mode still bails (split-across-rounds preserved, English/Gemini untouched)",
      s_gem._compose_single_round_reply("", {"update_form_field"}) is None)
s_crit = _fresh_hindi_session()
s_crit._use_sarvam = True
s_crit._ambulance_requested = True   # -> _is_critical() True -> warmer ack pool
composed_crit = s_crit._compose_single_round_reply("", {"update_form_field"})
check("fast path empty-ack uses the WARMER pool for a critical incident",
      composed_crit is not None and any(composed_crit.startswith(a) for a in _FAST_ACK_CRITICAL))
check("empty-ack fast path is DISABLED once submitted (guard unchanged)",
      s_crit.state.__setattr__("submitted", True) or
      s_crit._compose_single_round_reply("", {"update_form_field"}) is None)

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
    s.state.vehicles_involved = 2   # already known
    fake = _FakeGeminiClient([
        _model_response([
            gtypes.Part(text="अच्छा... किसी को चोट नहीं आई"),
            gtypes.Part(function_call=gtypes.FunctionCall(
                name="update_form_field", args={"field": "casualties", "number_value": 0})),
        ]),
    ])
    reply = await s._reason(fake, "किसी को चोट नहीं आई")
    last = s._history[-1]
    # vehicles(2) + casualties(0) now known, so the count/injury/trapped group is
    # partial -> the individual trapped question (new shorter canonical), not the
    # combined one. (In Hindi, casualties > 0 no longer nulls next_question -- see
    # _reason_injury_continues_collecting -- so both 0 and >0 take this fast path
    # now; 0 is kept here as the plain non-critical case.)
    return (
        fake.calls == 1
        and reply == "अच्छा... किसी को चोट नहीं आई। क्या कोई फँसा हुआ है?"
        and s.state.casualties == 0
        and last.role == "model" and last.parts[0].text == reply
    )

async def _reason_injury_continues_collecting():
    # #4 (Hindi staged dispatch): when the caller reports an injury
    # (casualties > 0), the turn becomes critical but -- unlike the shared
    # English fast-track -- Hindi's overridden _compute_still_missing does NOT
    # short-circuit: next_question stays present (still collecting the remaining
    # secondary facts), so the single-round fast path still fires (ONE round),
    # appending the next canonical question. The "help is being arranged"
    # reassurance is NOT delivered here -- it rides the interim-dispatch
    # SYSTEM UPDATE note injected in run() (see _maybe_interim_dispatch), tested
    # separately. Here vehicles(2) is already known, so the next topic is the
    # individual "trapped" question.
    from google.genai import types as gtypes
    s = _fresh_hindi_session()
    fake = _FakeGeminiClient([
        _model_response([
            gtypes.Part(text="ओह... मैं समझ सकता हूँ"),
            gtypes.Part(function_call=gtypes.FunctionCall(
                name="update_form_field", args={"field": "casualties", "number_value": 2})),
        ]),
    ])
    reply = await s._reason(fake, "दो लोग घायल हैं")
    return (
        fake.calls == 1               # fast path fired, canonical question appended
        and reply == "ओह... मैं समझ सकता हूँ। क्या कोई फँसा हुआ है?"
        and s.state.casualties == 2
        and s._is_critical()          # still flagged critical (drives auto-submit + interim dispatch)
    )

async def _reason_composes_with_empty_ack():
    # 2026-08 Exotel fix, END-TO-END on the real SARVAM path: Sarvam returns a
    # tool call with EMPTY content every tool turn. That now composes in ONE
    # round (default ack + canonical question) instead of forcing a second
    # reasoning round -- the round that doubled latency, ran long, and timed out.
    from severity_engine import dispatcher_hindi as dh
    from severity_engine.sarvam_reasoning import NormalizedResult, NormalizedToolCall
    s = _fresh_hindi_session()   # _use_sarvam True (default backend is 'sarvam')
    calls = {"n": 0}
    async def _fake_sarvam(messages, tools, **k):
        calls["n"] += 1
        return NormalizedResult("", [NormalizedToolCall(
            "call_0", "update_form_field", {"field": "casualties", "number_value": 2})])
    orig = dh.sarvam_generate
    dh.sarvam_generate = _fake_sarvam
    try:
        reply = await s._reason(None, "दो लोग घायल हैं")
    finally:
        dh.sarvam_generate = orig
    return (calls["n"] == 1 and s._turn_backend == "sarvam"
            and s.state.casualties == 2
            and any(reply.endswith(q) for q in _CANONICAL_QUESTIONS.values())
            and any(reply.startswith(a) for a in _FAST_ACK_NEUTRAL + _FAST_ACK_CRITICAL))

check("fast path answers in ONE Gemini round and mirrors the reply into history",
      asyncio.run(_reason_uses_single_round()))
check("#4 Hindi injury report keeps collecting (fast path fires with the next question, not fast-track submit)",
      asyncio.run(_reason_injury_continues_collecting()))
check("empty round-0 ack on SARVAM now composes in ONE round (no second round), end-to-end",
      asyncio.run(_reason_composes_with_empty_ack()))

async def _reason_composes_after_split_update_round():
    # Issue 2 (Gemini-PRIMARY mode only): the model splits its work -- round 0 has
    # a tool call but NO ack, round 1 has a tool call WITH a question-free ack. In
    # this mode an empty round-0 ack must NOT compose (it would skip round 1's tool
    # call); the fast path composes after round 1 (2 rounds), not a 3rd round. Only
    # 2 fake responses are provided, so fake.calls==2 proves no 3rd round.
    from google.genai import types as gtypes
    s = _fresh_hindi_session()
    s._use_sarvam = False   # Gemini-PRIMARY: the split-across-rounds path this test pins
    fake = _FakeGeminiClient([
        _model_response([  # round 0: tool call, NO ack
            gtypes.Part(function_call=gtypes.FunctionCall(
                name="update_form_field", args={"field": "vehiclesInvolved", "number_value": 2})),
        ]),
        _model_response([  # round 1: tool call + question-free ack
            gtypes.Part(text="अच्छा... किसी को चोट नहीं आई"),
            gtypes.Part(function_call=gtypes.FunctionCall(
                name="update_form_field", args={"field": "casualties", "number_value": 0})),
        ]),
    ])
    reply = await s._reason(fake, "दो गाड़ियाँ टकराईं, किसी को चोट नहीं")
    return (
        fake.calls == 2
        and reply == "अच्छा... किसी को चोट नहीं आई। क्या कोई फँसा हुआ है?"
        and s.state.vehicles_involved == 2
        and s.state.casualties == 0
    )
check("#2 fast path composes after a split classify/update round (2 rounds, not 3)",
      asyncio.run(_reason_composes_after_split_update_round()))

# Issue 3: BulbulStream splits a long utterance (the closing briefing) into
# sentence-level pieces so no single synthesis is long enough to drift off the
# configured voice; short replies stay one piece.
from severity_engine.sarvam_speech import _split_for_synthesis, _MAX_SYNTH_CHARS
check("Bulbul split: short text stays one piece",
      _split_for_synthesis("नमस्ते, क्या हुआ बताइए?") == ["नमस्ते, क्या हुआ बताइए?"])
check("Bulbul split: blank -> []", _split_for_synthesis("   ") == [])
_lg = " ".join(f"यह वाक्य संख्या {i} है।" for i in range(1, 40))
_pcs = _split_for_synthesis(_lg)
check("Bulbul split: a long utterance -> multiple pieces", len(_pcs) > 1)
check("Bulbul split: every piece within the char cap", all(len(p) <= _MAX_SYNTH_CHARS for p in _pcs))
check("Bulbul split: content preserved across pieces",
      "".join(_pcs).replace(" ", "") == _lg.replace(" ", ""))

async def _bulbul_speak_splits_long_utterance():
    from severity_engine import sarvam_speech as ss
    b = ss.BulbulStream.__new__(ss.BulbulStream)
    b._closed = False
    calls = []
    async def fake_speak_one(text):
        calls.append(text)
        yield b"\x00\x00"  # one dummy PCM frame per piece
    b._speak_one = fake_speak_one
    long_text = " ".join(f"वाक्य {i} यहाँ लिखा है।" for i in range(1, 40))
    out = [c async for c in b.speak(long_text)]
    # one _speak_one synthesis per piece, all yielded as one continuous stream
    return len(calls) > 1 and len(out) == len(calls)
check("Bulbul speak() synthesizes a long utterance as several config'd pieces (#3)",
      asyncio.run(_bulbul_speak_splits_long_utterance()))

# Real reported bug: the Hindi agent spoke internal tool-result fields aloud
# ("tone reminder", ...). _strip_meta_leak (run in _render_for_speech) removes
# leaked tokens + parenthesized system notes; natural Hindi speech is untouched.
from severity_engine.dispatcher_hindi import _strip_meta_leak, _HINDI_OPENING_LINE
check("meta strip: leaves clean Hindi untouched",
      _strip_meta_leak("ओह... मुझे यह सुनकर दुख हुआ। क्या कोई फँसा हुआ है?")
      == "ओह... मुझे यह सुनकर दुख हुआ। क्या कोई फँसा हुआ है?")
check("meta strip: removes a snake_case token word",
      "tone_reminder" not in _strip_meta_leak("ठीक है। tone_reminder क्या हुआ बताइए?"))
check("meta strip: removes 'tone reminder' (spaced) and 'next question'",
      _strip_meta_leak("tone reminder और next question मत बोलो।").find("reminder") == -1)
check("meta strip: drops a parenthesized system note entirely",
      "SYSTEM UPDATE" not in _strip_meta_leak("(SYSTEM UPDATE — internal) मैं मदद भेज रहा हूँ।")
      and "मदद" in _strip_meta_leak("(SYSTEM UPDATE — internal) मैं मदद भेज रहा हूँ।"))
check("meta strip: strips a leaked label:value clause but keeps the real reply",
      "fast_track" not in _strip_meta_leak("(fast_track: true) घबराइए मत... मदद आ रही है।")
      and "घबराइए मत" in _strip_meta_leak("(fast_track: true) घबराइए मत... मदद आ रही है।"))

def _render_strips_meta_leak():
    from severity_engine import dispatcher_hindi as dh
    s = dh.HindiDispatcherSession.__new__(dh.HindiDispatcherSession)
    s._last_opener = None
    out = s._render_for_speech("ठीक है। (tone_reminder: caller injured) क्या हुआ बताइए?")
    return "tone_reminder" not in out and "क्या हुआ" in out
check("_render_for_speech strips leaked meta before Bulbul",
      _render_strips_meta_leak())

def _compose_strips_meta_from_ack():
    # A leaked-meta ack composes cleanly (fast path preserved), meta removed.
    s = _fresh_hindi_session()
    composed = s._compose_single_round_reply(
        "अच्छा... दो लोग घायल हैं (tone_reminder: be gentle)", {"update_form_field"})
    return composed is not None and "tone_reminder" not in composed
check("_compose_single_round_reply strips leaked meta from the ack",
      _compose_strips_meta_from_ack())

# Issue 2: an explicit ambulance request from the caller makes the Hindi
# fast-track fire (critical), so the model stops asking routine secondary
# questions and submits once the essentials are in. Hindi-scoped -- the shared
# English _is_critical is untouched.
from severity_engine.dispatcher_hindi import _AMBULANCE_REQUEST_RE
check("ambulance regex matches Devanagari + Latin spellings, not unrelated text",
      all(_AMBULANCE_REQUEST_RE.search(t) for t in
          ["एम्बुलेंस भेजो", "एंबुलेंस जल्दी चाहिए", "please send an ambulance asap"])
      and not _AMBULANCE_REQUEST_RE.search("दो गाड़ियाँ आपस में टकराईं"))

async def _hindi_ambulance_request_is_critical():
    from severity_engine import dispatcher_hindi as dh
    s = dh.HindiDispatcherSession.__new__(dh.HindiDispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s._ambulance_requested = False
    s._accident_mode = False   # Phase 2: bare ambulance latch still drives _is_critical
    before = s._is_critical()                     # no ambulance, no injury -> not critical
    s.state.caller_transcript = " एक्सीडेंट हुआ है, जल्दी एम्बुलेंस भेजो!"
    await s._apply_local_signals_from_transcript()
    return (
        before is False
        and s._ambulance_requested is True
        and s._is_critical() is True
        and s._state_block()["fast_track"] is True
    )
check("#2 an ambulance request makes the Hindi fast-track fire (critical)",
      asyncio.run(_hindi_ambulance_request_is_critical()))

def _english_is_critical_unaffected_by_ambulance():
    # The SHARED English _is_critical has no ambulance override -> a fresh
    # (non-injury) English session is not critical, i.e. Hindi's change didn't
    # leak into dispatcher_live.
    from severity_engine.dispatcher_live import DispatcherSession
    s = DispatcherSession.__new__(DispatcherSession)
    s.state = DispatcherState(language="en-IN")
    return s._is_critical() is False and not hasattr(s, "_ambulance_requested")
check("English _is_critical is unaffected by the Hindi ambulance override",
      _english_is_critical_unaffected_by_ambulance())

# Real reported bug: the Hindi model skipped the fixed 1033 welcome greeting and
# opened straight with the location question. The greeting is now spoken
# deterministically as a prefix on the FIRST agent turn only (never left to the
# model, never repeated on later turns).
def _fresh_opening_session():
    from severity_engine import dispatcher_hindi as dh
    s = dh.HindiDispatcherSession.__new__(dh.HindiDispatcherSession)
    s._ws_send_lock = asyncio.Lock()
    s.websocket = _FakeWS()
    s.state = DispatcherState(language="hi-IN")
    s._opening_line_pending = True
    s._ended = asyncio.Event()
    s._last_opener = None
    s._turn_stats = {}
    s._turn_backend = None             # reasoning backend: mirrors __init__ (helper skips it)
    s._quota_hits = 0
    s._turn_index = 0
    s._call_turns = []
    s._fp_skip = None
    s._pending_interim_spoken = None   # #4: mirrors __init__ (helper skips it)
    return s

async def _hindi_opening_turn_greeting_one_utterance():
    # Issue 1 (fixed): the 1033 greeting + the model's opening reply are spoken
    # as ONE UNINTERRUPTIBLE utterance in _agent_turn -- so no between-turn
    # "interrupted"/flush can drop the greeting (the failure a separate greeting
    # utterance had). Later turns speak only the model reply.
    from severity_engine import dispatcher_hindi as dh
    s = _fresh_opening_session()
    spoken = []
    async def fake_reason(client, user_text, config=None):
        return "क्या यह लोकेशन सही है? क्या हुआ, बताइए?"
    async def fake_speak(text, allow_bargein=True, **_kw):
        spoken.append((text, allow_bargein)); return True
    async def fake_noop(*a, **k):
        return None
    s._reason = fake_reason
    s._speak_or_fallback = fake_speak
    s._preconnect_tts = fake_noop
    s._enter_listening = fake_noop

    await s._agent_turn(None, "(opening)")
    first_ok = (
        len(spoken) == 1
        and spoken[0][0].startswith(dh._HINDI_OPENING_LINE)  # greeting first, same utterance
        and "क्या हुआ" in spoken[0][0]                         # ...then the model's question
        and spoken[0][1] is False                            # uninterruptible
        and s._opening_line_pending is False
    )
    spoken.clear()
    await s._agent_turn(None, "दो गाड़ियाँ टकराईं")             # a later turn
    later_ok = len(spoken) == 1 and not spoken[0][0].startswith(dh._HINDI_OPENING_LINE)
    return first_ok and later_ok
check("Hindi 1033 greeting + first reply are ONE uninterruptible utterance; later turns don't repeat it",
      asyncio.run(_hindi_opening_turn_greeting_one_utterance()))

async def _hindi_greeting_still_spoken_when_model_silent():
    # Even if the model returns nothing on the opening turn, the greeting (+ a
    # fallback question) is still spoken.
    from severity_engine import dispatcher_hindi as dh
    s = _fresh_opening_session()
    spoken = []
    async def fake_reason(client, user_text, config=None):
        return ""                       # model produced nothing usable
    async def fake_speak(text, allow_bargein=True, **_kw):
        spoken.append(text); return True
    async def fake_noop(*a, **k):
        return None
    s._reason = fake_reason
    s._speak_or_fallback = fake_speak
    s._preconnect_tts = fake_noop
    s._enter_listening = fake_noop
    await s._agent_turn(None, "(opening)")
    return (
        len(spoken) == 1
        and spoken[0].startswith(dh._HINDI_OPENING_LINE)
        and dh._OPENING_FALLBACK_QUESTION in spoken[0]
    )
check("Hindi 1033 greeting is spoken even if the model returns nothing (fallback question)",
      asyncio.run(_hindi_greeting_still_spoken_when_model_silent()))

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

# Real reported bug: the agent asked the same question twice / repeated
# itself. Root cause: _watchdog's RESPONSE_TIMEOUT branch only tracked
# _model_last_spoke (audio-only, deliberately) and _caller_last_spoke --
# a caller statement needing several sequential tool calls (search_
# incident_type, more than one update_form_field) plus real network/Vertex
# latency could exceed the timeout with neither of those having moved,
# causing the watchdog to inject a synthetic "respond now" turn WHILE the
# model's real response was still in flight -- Gemini Live is reactive, so
# this produced a genuine duplicate/overlapping response to one caller
# statement. Fixed with _tool_activity_at, updated on every individual
# tool-call receipt, folded into the watchdog's waiting_since via max().
import time as _wdtime

class _FakeLiveSessionForWatchdog:
    def __init__(self):
        self.sent_turns = []
    async def send_client_content(self, turns=None, turn_complete=True):
        self.sent_turns.append(turns.parts[0].text)

async def _watchdog_does_not_nudge_during_ongoing_tool_activity():
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    old_timeout = dl._RESPONSE_TIMEOUT_S
    dl._RESPONSE_TIMEOUT_S = 1.0  # shrunk only for test speed (poll interval is a fixed 2s)
    try:
        s = DispatcherSession.__new__(DispatcherSession)
        s.state = DispatcherState(language="en-IN")
        s._live_session = _FakeLiveSessionForWatchdog()
        now = _wdtime.monotonic()
        s._session_started = now
        s._model_last_spoke = now  # model's last (previous) reply, e.g. the greeting
        s._caller_last_spoke = now + 0.01  # caller then spoke, prompting a multi-tool-call turn
        s._tool_activity_at = now + 0.01
        s._nudge_sent_at = 0.0

        watchdog_task = asyncio.create_task(s._watchdog())
        try:
            # Simulate a slow multi-tool-call turn: keep bumping
            # tool_activity_at every 0.5s for 4s total -- well past the
            # shrunk 1s timeout with no single bump, but each one should
            # keep resetting the clock so the watchdog never sees a real
            # timeout-length gap.
            for _ in range(8):
                await asyncio.sleep(0.5)
                s._tool_activity_at = _wdtime.monotonic()
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

        return len(s._live_session.sent_turns) == 0
    finally:
        dl._RESPONSE_TIMEOUT_S = old_timeout
        _logging.disable(_logging.NOTSET)

check("watchdog does NOT nudge while tool-call activity is ongoing (the actual repeated-question fix)",
      asyncio.run(_watchdog_does_not_nudge_during_ongoing_tool_activity()))

async def _watchdog_still_nudges_on_genuine_silence():
    # Safety net must survive the fix above: if there is NO tool activity
    # and NO speech for the full timeout, the watchdog must still nudge
    # (never leave the caller hanging on a genuinely wedged session).
    from severity_engine import dispatcher_live as dl
    _logging.disable(_logging.CRITICAL)
    old_timeout = dl._RESPONSE_TIMEOUT_S
    dl._RESPONSE_TIMEOUT_S = 0.5
    try:
        s = DispatcherSession.__new__(DispatcherSession)
        s.state = DispatcherState(language="en-IN")
        s._live_session = _FakeLiveSessionForWatchdog()
        now = _wdtime.monotonic()
        s._session_started = now
        s._model_last_spoke = now  # model's last (previous) reply
        s._caller_last_spoke = now + 0.01  # caller spoke AFTER that -- now genuinely waiting on a reply
        s._tool_activity_at = 0.0  # no tool activity at all
        s._nudge_sent_at = 0.0

        watchdog_task = asyncio.create_task(s._watchdog())
        try:
            await asyncio.sleep(2.7)  # past one 2s poll tick, well past the shrunk 0.5s timeout
        finally:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

        return len(s._live_session.sent_turns) >= 1
    finally:
        dl._RESPONSE_TIMEOUT_S = old_timeout
        _logging.disable(_logging.NOTSET)

check("watchdog still nudges on genuine silence (no tool activity, no speech) -- safety net preserved",
      asyncio.run(_watchdog_still_nudges_on_genuine_silence()))

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

# ── Multi-intent helpline: curated knowledge base (Phase 1) ───────────────────
# The scheme/legal/what-to-do answers must come from this curated, sourced KB --
# never LLM invention. These tests lock the verified figures + structure so a
# future edit can't silently drop a source, a note, or a key fact.
from severity_engine.knowledge_base import (
    KNOWLEDGE_BASE, INFO_TOPICS, lookup_info, describe_topics,
)

_EXPECTED_TOPICS = {
    "golden_hour_cashless", "rah_veer_good_samaritan", "hit_and_run_compensation",
    "ayushman_bharat", "insurance_claim", "move_vehicle", "night_safety",
}
check("KB has the expected helpline topics",
      set(INFO_TOPICS) == _EXPECTED_TOPICS and set(KNOWLEDGE_BASE) == _EXPECTED_TOPICS)

# Every entry is well-formed: non-empty Hindi + English answer + a source.
_kb_ok = all(
    e.get("hi", "").strip() and e.get("en", "").strip() and e.get("source", "").strip()
    and isinstance(e.get("labels"), list) and e["labels"]
    for e in KNOWLEDGE_BASE.values()
)
check("every KB entry has non-empty hi/en text, labels, and a source", _kb_ok)

# Scheme entries carry the conservative "confirm current details" disclaimer.
for _k in ("golden_hour_cashless", "rah_veer_good_samaritan", "hit_and_run_compensation",
           "ayushman_bharat", "insurance_claim"):
    _e = KNOWLEDGE_BASE[_k]
    check(f"KB scheme entry {_k!r} carries a hi + en disclaimer note",
          bool(_e.get("note_hi", "").strip()) and bool(_e.get("note_en", "").strip()))

# Verified figures are present verbatim (guards against silent corruption).
check("KB golden-hour/PM-RAHAT states ₹1.5 lakh + 7 days + §162",
      "डेढ़ लाख" in KNOWLEDGE_BASE["golden_hour_cashless"]["hi"]
      and "1.5 lakh" in KNOWLEDGE_BASE["golden_hour_cashless"]["en"]
      and "सात दिन" in KNOWLEDGE_BASE["golden_hour_cashless"]["hi"]
      and "162" in KNOWLEDGE_BASE["golden_hour_cashless"]["en"])
check("KB Rah-Veer/Good-Samaritan states ₹25,000 + §134A",
      "पच्चीस हज़ार" in KNOWLEDGE_BASE["rah_veer_good_samaritan"]["hi"]
      and "25,000" in KNOWLEDGE_BASE["rah_veer_good_samaritan"]["en"]
      and "134A" in KNOWLEDGE_BASE["rah_veer_good_samaritan"]["en"])
check("KB hit-and-run states ₹2 lakh death / ₹50,000 grievous injury",
      "दो लाख" in KNOWLEDGE_BASE["hit_and_run_compensation"]["hi"]
      and "पचास हज़ार" in KNOWLEDGE_BASE["hit_and_run_compensation"]["hi"]
      and "2,00,000" in KNOWLEDGE_BASE["hit_and_run_compensation"]["en"]
      and "50,000" in KNOWLEDGE_BASE["hit_and_run_compensation"]["en"])
check("KB Ayushman states ₹5 lakh + 70+ Vay Vandana",
      "पाँच लाख" in KNOWLEDGE_BASE["ayushman_bharat"]["hi"]
      and "5 lakh" in KNOWLEDGE_BASE["ayushman_bharat"]["en"]
      and "वय वंदना" in KNOWLEDGE_BASE["ayushman_bharat"]["hi"])

# lookup_info + describe_topics behave.
check("lookup_info returns an entry for a valid topic and None for an unknown one",
      lookup_info("golden_hour_cashless") is KNOWLEDGE_BASE["golden_hour_cashless"]
      and lookup_info("does_not_exist") is None and lookup_info("") is None)
check("describe_topics lists every topic key for the prompt",
      all(k in describe_topics() for k in _EXPECTED_TOPICS))

# ── Multi-intent helpline: backend routing + accident gating (Phase 2) ────────
# The accident-collection flow is now ONE branch, entered only on an accident/
# injury. General calls (facility/info/complaint/breakdown) must never be forced
# into the incident form, and English must be byte-identical.
from severity_engine.helpline_tools import HELPLINE_TOOL_DECLARATIONS, HelplineToolsMixin
from severity_engine.dispatcher_live import _TOOL_DECLARATIONS

def _hi_general():
    s = HindiDispatcherSession.__new__(HindiDispatcherSession)
    HindiDispatcherSession.__init__(s, _CapWS())
    return s

# Combined tool set: Hindi has the accident tools PLUS the 3 helpline tools;
# English's shared _TOOL_DECLARATIONS is NOT mutated.
_hi_tool_names = {d["name"] for d in _TOOL_DECLARATIONS} | {d["name"] for d in HELPLINE_TOOL_DECLARATIONS}
check("#P2 Hindi tool set adds find_nearest_facility / answer_info_question / lodge_complaint",
      {"find_nearest_facility", "answer_info_question", "lodge_complaint"} <= _hi_tool_names)
check("#P2 the shared accident-tool declarations were NOT mutated (English-safe)",
      all(d["name"] not in {"find_nearest_facility", "answer_info_question", "lodge_complaint"}
          for d in _TOOL_DECLARATIONS)
      and HelplineToolsMixin not in DispatcherSession.__mro__)

check("#P2 general helpline mode imposes no accident next_question",
      _hi_general()._accident_mode is False and _hi_general()._compute_still_missing() == [])

async def _p2_interim_suppressed_in_general():
    s = _hi_general(); s.state.location = {"lat": 28.6, "lng": 77.2, "label": "Delhi"}
    s._ambulance_requested = True  # 'ambulance ka number' style -- must NOT dispatch
    before = len(s.websocket.sent)
    await s._maybe_interim_dispatch()
    return len(s.websocket.sent) == before
check("#P2 interim dispatch is suppressed in general mode (facility mention != emergency)",
      asyncio.run(_p2_interim_suppressed_in_general()))

async def _p2_accident_tool_enters_accident_mode():
    s = _hi_general()
    await s._dispatch_tool("search_incident_categories", {})
    return s._accident_mode is True
check("#P2 calling an accident tool enters accident mode",
      asyncio.run(_p2_accident_tool_enters_accident_mode()))

async def _p2_backstop_injury_enters_accident_mode():
    inj = _hi_general(); inj.state.caller_transcript = " मेरी पत्नी घायल है, बहुत खून बह रहा है "
    await inj._apply_local_signals_from_transcript()
    gen = _hi_general(); gen.state.caller_transcript = " पास में कौन सा पेट्रोल पंप है "
    await gen._apply_local_signals_from_transcript()
    return inj._accident_mode is True and gen._accident_mode is False
check("#P2 backstop: an injury transcript enters accident mode; a facility query stays general",
      asyncio.run(_p2_backstop_injury_enters_accident_mode()))

async def _p2_answer_info_question():
    s = _hi_general()
    good = await s._dispatch_tool("answer_info_question", {"topic": "golden_hour_cashless"})
    miss = await s._dispatch_tool("answer_info_question", {"topic": "unknown_topic"})
    return (good["ok"] and good["found"] and "डेढ़ लाख" in good["answer_hi"]
            and miss["found"] is False and "known_topics" in miss
            and s._accident_mode is False)   # an info query never enters accident mode
check("#P2 answer_info_question returns KB facts, honest miss, and never enters accident mode",
      asyncio.run(_p2_answer_info_question()))

async def _p2_find_nearest_facility_roundtrip():
    s = _hi_general(); s.state.location = {"lat": 28.6, "lng": 77.2, "label": "Delhi"}
    task = asyncio.create_task(s._dispatch_tool("find_nearest_facility", {"facility_type": "hospital"}))
    await asyncio.sleep(0.02)
    frame = s.websocket.sent[-1]
    ok_req = frame["type"] == "request_facility" and frame["facilityType"] == "hospital"
    s._resolve_helpline_client_message("facility_result", {"requestId": frame["requestId"],
        "facility": {"name": "AIIMS", "contactNumber": "011-1", "distanceKm": 3.2, "etaMinutes": 9}})
    res = await task
    # no-location path returns needs_location and sends no frame
    s2 = _hi_general()
    noloc = await s2._dispatch_tool("find_nearest_facility", {"facility_type": "mechanic"})
    return (ok_req and res["ok"] and res["facility"]["name"] == "AIIMS"
            and noloc.get("needs_location") is True and s._accident_mode is False)
check("#P2 find_nearest_facility does the request/resolve round-trip (needs_location without GPS)",
      asyncio.run(_p2_find_nearest_facility_roundtrip()))

async def _p2_lodge_complaint_roundtrip():
    s = _hi_general(); s.state.location = {"lat": 28.6, "lng": 77.2, "label": "NH-334"}
    task = asyncio.create_task(s._dispatch_tool("lodge_complaint",
        {"description": "large pothole", "complaint_type": "pothole"}))
    await asyncio.sleep(0.02)
    frame = s.websocket.sent[-1]
    ok_req = frame["type"] == "request_complaint" and frame["complaintType"] == "pothole"
    s._resolve_helpline_client_message("complaint_result",
        {"requestId": frame["requestId"], "referenceId": "RPOT-ABCD"})
    res = await task
    return ok_req and res["ok"] and res["reference_id"] == "RPOT-ABCD" and s._accident_mode is False
check("#P2 lodge_complaint does the request/resolve round-trip and returns a real reference id",
      asyncio.run(_p2_lodge_complaint_roundtrip()))

# English pipeline must be completely untouched by the multi-intent work.
check("#P2 English (base DispatcherSession) has NO helpline tools and NO accident_mode",
      not hasattr(DispatcherSession, "_tool_find_nearest_facility")
      and not hasattr(DispatcherSession, "_tool_answer_info_question")
      and not hasattr(DispatcherSession, "_dispatch_helpline_tool" if False else "_maybe_dispatch_helpline_tool"))
_en_probe = DispatcherSession.__new__(DispatcherSession)
_en_probe.state = DispatcherState(language="en-IN")
_en_probe.state.category = "Vehicle Collisions"; _en_probe.state.sub_type = "X"
_en_probe.state.description = "x"; _en_probe.state.location = {"lat": 1, "lng": 1, "label": "x"}
_en_probe.state.flags = {"Trapped"}
check("#P2 English fast-track short-circuit is unchanged (critical -> no further questions)",
      _en_probe._compute_still_missing() == [] and not hasattr(_en_probe, "_accident_mode"))

# call_intent classification for Post-Call Analytics: a general helpline call is
# emitted as "information" (so it's logged with outcome "information" and kept out
# of the accident completion rate); an accident call as "accident"; accident
# outranks information and is emitted once per level.
async def _p2_call_intent_classification():
    def intents(sent):
        return [f["intent"] for f in sent if f.get("type") == "call_intent"]

    async def fac(facility_type="", capability=""):
        return {"ok": True, "facility": {"name": "H"}, "needs_location": False}

    async def comp(description="", complaint_type="road_defect"):
        return {"ok": True, "reference_id": "HD-1"}

    info = _hi_general(); info.state.location = {"lat": 28.6, "lng": 77.2, "label": "D"}
    info._tool_find_nearest_facility = fac; info._tool_lodge_complaint = comp
    await info._dispatch_tool("find_nearest_facility", {"facility_type": "hospital"})
    await info._dispatch_tool("answer_info_question", {"topic": "golden_hour_cashless"})

    acc = _hi_general()
    await acc._dispatch_tool("search_incident_categories", {})

    upg = _hi_general(); upg.state.location = {"lat": 28.6, "lng": 77.2, "label": "D"}
    upg._tool_find_nearest_facility = fac
    await upg._dispatch_tool("find_nearest_facility", {"facility_type": "tow"})
    await upg._dispatch_tool("search_incident_type", {"description": "crash"})

    return (intents(info.websocket.sent) == ["information"]
            and intents(acc.websocket.sent) == ["accident"]
            and intents(upg.websocket.sent) == ["information", "accident"])
check("#P2 call_intent: general -> 'information' (once), accident -> 'accident', info upgrades to accident",
      asyncio.run(_p2_call_intent_classification()))

# ── Exotel telephony integration (transport adapter only) ─────────────────────
# The adapter translates Exotel AgentStream <-> the browser protocol and runs the
# SAME HindiDispatcherSession; ExotelHindiSession overrides ONLY location
# acquisition (a composed GeocodeLocationProvider: geocode-from-speech + retry, no
# default). Data/ETA/complaint reuse the app's real endpoints via services.py. The
# browser pipeline must stay unchanged.
import base64 as _b64
import json as _json
import integrations.exotel.services as _EXSV
from integrations.exotel.session import (
    ExotelWebSocketAdapter, ExotelHindiSession, ExotelEnglishSession, make_exotel_session,
)
from integrations.exotel.location import GeocodeLocationProvider

class _FakeExotelWS:
    def __init__(self, inbound): self._in = list(inbound); self.sent = []
    async def receive(self):
        if self._in: return self._in.pop(0)
        await asyncio.sleep(0.005); return {"type": "websocket.disconnect"}
    async def send_json(self, f): self.sent.append(f)
    async def close(self): pass

def _ex_msg(d): return {"type": "websocket.receive", "text": _json.dumps(d)}

async def _exotel_adapter_translates():
    pcm = b"\x11\x00" * 160
    ex = _FakeExotelWS([
        _ex_msg({"event": "start", "stream_sid": "SS", "start": {"call_sid": "C1", "from": "+9199", "media_format": {"sample_rate": 16000}}}),
        _ex_msg({"event": "media", "media": {"payload": _b64.b64encode(pcm).decode()}}),
        _ex_msg({"event": "stop"}),
    ])
    ad = ExotelWebSocketAdapter(ex, exotel_rate=16000); ad.start()
    m1 = await ad.receive(); m2 = await ad.receive()
    await ad.send_bytes(b"\x22\x00" * 240)
    await ad.send_json({"type": "interrupted"})
    await ad.send_json({"type": "transcript", "role": "user", "text": "बागपत"})
    return (m1.get("bytes") == pcm and m2["type"] == "websocket.disconnect"
            and ad.call_sid == "C1" and ad.from_number == "+9199"
            and ex.sent[-2]["event"] == "media" and ex.sent[-1]["event"] == "clear"
            and ad.last_caller_utterance == "बागपत")
check("#EX adapter translates media->16k / stop->disconnect / Bulbul->media / interrupted->clear / transcript",
      asyncio.run(_exotel_adapter_translates()))

# ── IVR locale routing (Phase 2): ?locale= -> the right pipeline, single source ──
def _mk_ad(locale="hi-IN", gate=False):
    return ExotelWebSocketAdapter(_FakeExotelWS([]), 8000, gate_caller_audio=gate, locale=locale)
_route_en = make_exotel_session("en-IN", _mk_ad("en-IN", True))
_route_hi = make_exotel_session("hi-IN", _mk_ad())
_route_missing = make_exotel_session("", _mk_ad())       # missing -> Hindi (default) + warn
_route_unknown = make_exotel_session("fr-FR", _mk_ad())  # unrecognized -> Hindi (default) + warn
check("#EX locale routing: en-IN->English, hi-IN/missing/unknown->Hindi (phone defaults to Hindi)",
      isinstance(_route_en, ExotelEnglishSession) and isinstance(_route_hi, ExotelHindiSession)
      and isinstance(_route_missing, ExotelHindiSession) and isinstance(_route_unknown, ExotelHindiSession))

async def _exotel_locale_from_custom_params():
    # Exotel carries the IVR's DTMF locale in the `start` event's custom_parameters
    # when it's NOT in the URL query string -> the router reads it there and
    # configures the en-IN echo gate.
    ex = _FakeExotelWS([
        _ex_msg({"event": "start", "stream_sid": "SS", "start": {"call_sid": "C", "from": "+91",
                 "media_format": {"sample_rate": 8000}, "custom_parameters": {"locale": "en-IN"}}}),
        _ex_msg({"event": "stop"}),
    ])
    ad = ExotelWebSocketAdapter(ex, exotel_rate=8000)
    ad.start()
    await ad.wait_for_start(2.0)
    locale = str(ad.custom_parameters.get("locale") or "").strip()
    ad.configure_for_locale(locale)
    sess = make_exotel_session(locale, ad)
    return (locale == "en-IN" and ad._gate_caller_audio is True
            and ad.query_params["locale"] == "en-IN" and isinstance(sess, ExotelEnglishSession))
check("#EX locale from start custom_parameters (fallback when not in the URL) -> en-IN gate + English session",
      asyncio.run(_exotel_locale_from_custom_params()))

# ── Echo guard (Phase 3): English drops caller audio while the agent speaks ──
# (replaces the browser mic-gate that a phone line doesn't have; Hindi keeps barge-in)
async def _echo_gate_media(gate, speaking, locale="en-IN"):
    pcm = b"\x11\x00" * 160
    ex = _FakeExotelWS([
        _ex_msg({"event": "start", "stream_sid": "SS", "start": {"call_sid": "C", "from": "+91", "media_format": {"sample_rate": 8000}}}),
        _ex_msg({"event": "media", "media": {"payload": _b64.b64encode(pcm).decode()}}),
        _ex_msg({"event": "stop"}),
    ])
    ad = ExotelWebSocketAdapter(ex, exotel_rate=8000, gate_caller_audio=gate, locale=locale)
    ad._agent_speaking = speaking  # set before the read loop processes the media frame
    ad.start()
    return await ad.receive()  # first inbound: the caller bytes if forwarded, else the disconnect
async def _echo_gate_tests():
    dropped = await _echo_gate_media(gate=True, speaking=True)              # English + speaking -> drop
    forwarded = await _echo_gate_media(gate=True, speaking=False)          # English + listening -> forward
    hindi = await _echo_gate_media(gate=False, speaking=True, locale="hi-IN")  # Hindi -> never gates
    # status + playback-tail tracking drive _agent_is_speaking:
    ad = ExotelWebSocketAdapter(_FakeExotelWS([]), 8000, gate_caller_audio=True, locale="en-IN")
    await ad.send_json({"type": "status", "state": "speaking"});  spk = ad._agent_is_speaking()
    await ad.send_json({"type": "status", "state": "listening"}); lst = ad._agent_is_speaking()
    await ad.send_bytes(b"\x00\x00" * 24000)                      # ~1s of 24k audio -> tail keeps gate on
    tail = ad._agent_is_speaking()
    return (dropped["type"] == "websocket.disconnect" and forwarded.get("bytes") is not None
            and hindi.get("bytes") is not None and spk is True and lst is False and tail is True)
check("#EX echo gate: English drops caller media while speaking (status+playback tail), forwards while listening; Hindi never gates",
      asyncio.run(_echo_gate_tests()))

def _english_phone_prompt_rewrite():
    sess = ExotelEnglishSession(_mk_ad("en-IN", True), "en-IN")
    text = sess._build_config().system_instruction.parts[0].text
    return ("PHONE-CALL LOCATION RULE" in text
            and "use the map-pin button to mark their location" not in text
            and sess.state.language == "en-IN")
check("#EX English _build_config rewrites the phone location prompt (map-pin instruction removed, phone rule added)",
      _english_phone_prompt_rewrite())

def _ex_session():
    return ExotelHindiSession(ExotelWebSocketAdapter(_FakeExotelWS([]), 16000))

async def _exotel_location_geocode_retry():
    _orig_geo = _EXSV.geocode_landmark  # restore after — the provider late-binds to this
    async def _ok(_t): return {"lat": 28.9, "lng": 77.2, "label": "Baghpat"}
    async def _fail(_t): return None
    try:
        _EXSV.geocode_landmark = _ok
        s = _ex_session(); s.websocket.last_caller_utterance = "NH-334 Baghpat"
        ok = (await s._tool_get_current_location())["status"] == "ok" and s.state.location["label"] == "Baghpat"
        _EXSV.geocode_landmark = _fail
        s = _ex_session(); s.websocket.last_caller_utterance = "कुछ पता नहीं"
        r1 = await s._tool_get_current_location(); r2 = await s._tool_get_current_location(); r3 = await s._tool_get_current_location()
        retry_then_terminate = ("next_step" in r1 and s.state.location is None and "call back" in r3["next_step"])
        up = _ex_session()  # upfront: no caller speech -> silent unavailable, no prompt
        silent = ("next_step" not in (await up._tool_get_current_location()))
        fac = _ex_session(); fac.websocket.last_caller_utterance = "मुझे हॉस्पिटल चाहिए"
        facneeds = (await fac._tool_find_nearest_facility(facility_type="hospital"))["needs_location"] is True
        return ok and retry_then_terminate and silent and facneeds
    finally:
        _EXSV.geocode_landmark = _orig_geo
check("#EX location: geocode success sets it; failure asks for a landmark, then terminates; upfront stays silent; no default",
      asyncio.run(_exotel_location_geocode_retry()))

# Composition: GeocodeLocationProvider is a standalone, injectable unit (the
# geocode/retry/terminate policy lives here once, not duplicated in the session).
async def _exotel_location_provider_composition():
    async def _ok(_t): return {"lat": 28.9, "lng": 77.2, "label": "Baghpat"}
    async def _fail(_t): return None
    p_ok = GeocodeLocationProvider(lambda: "NH-334", geocode=_ok)
    o1 = await p_ok.acquire()
    p_silent = GeocodeLocationProvider(lambda: "", geocode=_ok)
    o2 = await p_silent.acquire()
    p_fail = GeocodeLocationProvider(lambda: "somewhere", geocode=_fail, max_attempts=3)
    a = await p_fail.acquire(); b = await p_fail.acquire(); c = await p_fail.acquire()
    return (o1.ok and o1.location["label"] == "Baghpat"
            and o2.silent and not o2.ok and o2.next_step is None
            and a.next_step and not a.terminate and not b.terminate and c.terminate)
check("#EX GeocodeLocationProvider (composition): success / silent-when-empty / ask x2 then terminate — injectable, no default",
      asyncio.run(_exotel_location_provider_composition()))

# Location BACKSTOP (2026-08): the model doesn't reliably re-call
# get_current_location after the caller names a place (live bug: stuck asking for
# location for 5 turns after "Malviya Nagar near KFC"). try_opportunistic resolves
# it from the caller's own words, but ONLY for a place-looking utterance and ONLY a
# verified hit -- an accident description must never set a bogus location.
from integrations.exotel.location import looks_like_location, label_verifies
check("#EX looks_like_location: place text (Latin token / Devanagari cue) True; accident description False",
      looks_like_location("malviya nagar near kfc") and looks_like_location("मालवीय नगर के पास")
      and looks_like_location("सेक्टर 62") and not looks_like_location("दो कारें आपस में टकराई हैं")
      and not looks_like_location("तीन लोग घायल हैं") and not looks_like_location("हाँ ठीक है"))
check("#EX label_verifies: Latin query needs a token in the label; mismatch rejected; Devanagari-only trusted",
      label_verifies("malviya nagar kfc", "Old Market, Malviya Nagar, New Delhi")
      and not label_verifies("ambulance please", "Max Super Speciality Hospital, Saket")
      and label_verifies("मालवीय नगर के पास", "Malviya Nagar, New Delhi"))

async def _exotel_backstop_opportunistic():
    async def _geo(text):  # a place resolves; a non-place returns Google's bogus business
        if "malviya" in text.lower() or "मालवीय" in text:
            return {"lat": 28.53, "lng": 77.21, "label": "Malviya Nagar, New Delhi"}
        return {"lat": 28.6, "lng": 77.3, "label": "Some Random Shop, Noida"}  # bogus
    resolved = GeocodeLocationProvider(lambda: "मालवीय नगर के पास", geocode=_geo)
    accident = GeocodeLocationProvider(lambda: "दो कारें आपस में टकराई हैं", geocode=_geo)
    latin_nonplace = GeocodeLocationProvider(lambda: "ambulance please", geocode=_geo)
    r1 = await resolved.try_opportunistic()
    r2 = await accident.try_opportunistic()      # not place-looking -> never geocoded
    r3 = await latin_nonplace.try_opportunistic()  # geocoded but label fails verification
    return (r1 and r1["label"].startswith("Malviya") and r2 is None and r3 is None
            and resolved._attempts == 0)  # opportunistic must NOT touch the ask/terminate budget
check("#EX backstop try_opportunistic: resolves a place, no-ops an accident description / unverified match, never counts an attempt",
      asyncio.run(_exotel_backstop_opportunistic()))

async def _exotel_services_facility_roundtrip():
    async def _resp(): return {"hospitals": [{"name": "AIIMS", "lat": 28.91, "lng": 77.21, "phone": "011"}]}
    async def _no_routes(_o, _d): return None  # force the haversine fallback (hermetic, no HTTP)
    _EXSV.fetch_responders = _resp
    _EXSV.route_eta_minutes = _no_routes
    a = ExotelWebSocketAdapter(_FakeExotelWS([]), 16000)
    await a.send_json({"type": "request_facility", "requestId": "r1", "facilityType": "hospital", "location": {"lat": 28.9, "lng": 77.2}})
    got = await asyncio.wait_for(a._inbound.get(), 2); p = _json.loads(got["text"])
    return (p["type"] == "facility_result" and p["facility"]["name"] == "AIIMS"
            and isinstance(p["facility"]["etaMinutes"], int))
check("#EX request_facility is answered server-side (reuses responder data + ETA) and injected as facility_result",
      asyncio.run(_exotel_services_facility_roundtrip()))

# ETA reuse: hospital/police use the app's REAL Google-Routes ETA when available;
# ambulance/fire/tow mirror matching.ts per-type haversine (tow 50 vs ambulance 40).
async def _exotel_eta_reuses_app_logic():
    resp = {
        "hospitals": [{"name": "H", "lat": 28.6, "lng": 77.2, "phone": "1", "traumaLevel": 1}],
        "ambulanceStations": [{"name": "A", "lat": 28.6, "lng": 77.2, "contactNumber": "2"}],
        "towingStations": [{"name": "T", "lat": 28.6, "lng": 77.2, "contactNumber": "3"}],
        "policeStations": [{"name": "P", "lat": 28.6, "lng": 77.2, "phone": "4"}],
    }
    pt = (28.7, 77.2)  # ~11 km north
    async def _routes(_o, _d): return 42  # the app's real Routes ETA for hospital/police
    _EXSV.route_eta_minutes = _routes
    hosp = await _EXSV.nearest_facility(resp, "hospital", pt)
    amb = await _EXSV.nearest_facility(resp, "ambulance", pt)
    tow = await _EXSV.nearest_facility(resp, "tow", pt)
    # same straight-line distance, but tow (50 km/h) must be FASTER than ambulance (40 km/h)
    hospital_uses_routes = hosp["etaMinutes"] == 42
    haversine_per_type = amb["etaMinutes"] > tow["etaMinutes"]
    svc = await _EXSV.build_dispatch_update(resp, pt, set())
    no_fire_without_flag = "fire" not in svc and "ambulance" in svc and "hospital" in svc
    svc_fire = await _EXSV.build_dispatch_update(resp, pt, {"Fire"})
    return hospital_uses_routes and haversine_per_type and no_fire_without_flag and ("hospital" in svc_fire)
check("#EX ETA reuses the app's logic: Google-Routes for hospital/police, matching.ts per-type haversine for ambulance/tow",
      asyncio.run(_exotel_eta_reuses_app_logic()))

check("#EX browser HindiDispatcherSession is UNCHANGED (base GPS location tool); geocode override is isolated to ExotelHindiSession",
      HindiDispatcherSession._tool_get_current_location is DispatcherSession._tool_get_current_location
      and ExotelHindiSession._tool_get_current_location is not DispatcherSession._tool_get_current_location)

# Startup validation: passes on defaults, raises ExotelConfigError on hard misconfig.
import integrations.exotel.config as _EXCFG
def _exotel_config_validation():
    ok_default = True
    try:
        _EXCFG.validate()
    except _EXCFG.ExotelConfigError:
        ok_default = False
    _orig_rate = _EXCFG._RAW_SAMPLE_RATE
    _EXCFG._RAW_SAMPLE_RATE = "abc"
    raised_rate = False
    try:
        _EXCFG.validate()
    except _EXCFG.ExotelConfigError:
        raised_rate = True
    _EXCFG._RAW_SAMPLE_RATE = _orig_rate
    _orig_path = _EXCFG.EXOTEL_WS_PATH
    _EXCFG.EXOTEL_WS_PATH = "exotel/ws"  # missing leading slash
    raised_path = False
    try:
        _EXCFG.validate()
    except _EXCFG.ExotelConfigError:
        raised_path = True
    _EXCFG.EXOTEL_WS_PATH = _orig_path
    return ok_default and raised_rate and raised_path
check("#EX startup config validation passes on defaults; raises on bad EXOTEL_SAMPLE_RATE / EXOTEL_WS_PATH",
      _exotel_config_validation())

# /exotel/health payload reflects enabled + validation state.
from integrations.exotel.websocket import health_payload as _health_payload
def _exotel_health():
    d = _health_payload()  # disabled by default in the test env
    disabled_ok = (d["status"] == "disabled" and d["ok"] is False
                   and {"errors", "warnings", "config"} <= set(d))
    _orig_en = _EXCFG.EXOTEL_ENABLED
    _EXCFG.EXOTEL_ENABLED = True
    enabled_ok = (_health_payload()["status"] == "ok")
    _orig_rate = _EXCFG._RAW_SAMPLE_RATE
    _EXCFG._RAW_SAMPLE_RATE = "abc"
    m = _health_payload()
    misconf = (m["status"] == "misconfigured" and m["ok"] is False and bool(m["errors"]))
    _EXCFG._RAW_SAMPLE_RATE = _orig_rate
    _EXCFG.EXOTEL_ENABLED = _orig_en
    return disabled_ok and enabled_ok and misconf
check("#EX /exotel/health reports disabled / ok / misconfigured per enabled+validation state",
      _exotel_health())

# Every external call has a timeout + bounded retries: transient failures retry,
# 4xx client errors do not.
import types as _types
async def _exotel_http_retry():
    calls = {"n": 0}
    class _R:
        def __init__(self, s, p=None): self.status_code = s; self._p = p
        def json(self): return self._p
    class _CTransient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")   # fail attempts 1 & 2
            return _R(200, {"ok": True})           # succeed on attempt 3
    class _C4xx:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kw):
            calls["n"] += 1
            return _R(404, None)
    _orig_httpx = _EXSV.httpx
    _orig_backoff = _EXSV.config.HTTP_BACKOFF
    _orig_retries = _EXSV.config.HTTP_RETRIES
    _EXSV.config.HTTP_BACKOFF = (0, 0, 0)  # no real sleeps in tests
    _EXSV.config.HTTP_RETRIES = 2          # 3 attempts total
    try:
        _EXSV.httpx = _types.SimpleNamespace(AsyncClient=_CTransient)
        got = await _EXSV._send("GET", "http://x", label="t")
        retried_ok = (got == {"ok": True} and calls["n"] == 3)
        calls["n"] = 0
        _EXSV.httpx = _types.SimpleNamespace(AsyncClient=_C4xx)
        got4 = await _EXSV._send("GET", "http://x", label="t")
        no_retry_4xx = (got4 is None and calls["n"] == 1)
    finally:
        _EXSV.httpx = _orig_httpx
        _EXSV.config.HTTP_BACKOFF = _orig_backoff
        _EXSV.config.HTTP_RETRIES = _orig_retries
    return retried_ok and no_retry_4xx
check("#EX external calls have timeout+retries: transient failures retry to success, 4xx does not retry",
      asyncio.run(_exotel_http_retry()))

# Structured logging tags each message with the per-call id (contextvar-scoped).
from integrations.exotel.logging_utils import get_logger as _ex_get_logger, set_call_id as _ex_set_call_id
def _exotel_call_id_logging():
    import logging as _lg
    cap = []
    class _H(_lg.Handler):
        def emit(self, r): cap.append(self.format(r))
    base = _lg.getLogger("exotel.test.cid"); h = _H(); h.setFormatter(_lg.Formatter("%(message)s"))
    base.addHandler(h); base.setLevel(_lg.INFO)
    log = _ex_get_logger("exotel.test.cid")
    _ex_set_call_id("cafef00d"); log.info("started")
    tagged = any("[call=cafef00d] started" in m for m in cap)
    cap.clear(); _ex_set_call_id("-"); log.info("untagged")
    untagged = any(m == "untagged" for m in cap)
    return tagged and untagged
check("#EX structured logging tags each message with the per-call id (and nothing when unset)",
      _exotel_call_id_logging())

# Protocol conformance: parse the EXACT documented Exotel AgentStream frames.
from integrations.exotel import protocol as _EXP
def _exotel_protocol_matches_spec():
    _b = _b64
    start = _EXP.parse_event({
        "event": "start", "sequence_number": "1", "stream_sid": "MZ1",
        "start": {"stream_sid": "MZ1", "call_sid": "CA1", "account_sid": "AC1",
                  "from": "+919876543210", "to": "+918000", "custom_parameters": {"k": "v"},
                  "media_format": {"encoding": "audio/x-raw", "sample_rate": "8000", "bit_rate": "16"}}})
    media = _EXP.parse_event({
        "event": "media", "sequence_number": "3", "stream_sid": "MZ1",
        "media": {"chunk": "2", "timestamp": "200", "payload": _b.b64encode(b"\x01\x02").decode()}})
    stop = _EXP.parse_event({"event": "stop", "stream_sid": "MZ1", "stop": {"reason": "callended"}})
    outm = _EXP.media_frame("MZ1", b"\x01\x02")
    return (start.kind == "start" and start.stream_sid == "MZ1" and start.call_sid == "CA1"
            and start.from_number == "+919876543210" and start.to_number == "+918000"
            and start.sample_rate == 8000 and start.custom_parameters == {"k": "v"}
            and media.kind == "media" and media.audio == b"\x01\x02" and stop.kind == "stop"
            and outm == {"event": "media", "media": {"payload": _b.b64encode(b"\x01\x02").decode()}, "stream_sid": "MZ1"})
check("#EX protocol parses the documented Exotel frames (start/media/stop, string sample_rate) + builds spec media out",
      _exotel_protocol_matches_spec())

# Exotel's default stream rate is 8 kHz (the start frame can still override it).
import integrations.exotel.audio_adapter as _EXAA
def _exotel_default_sample_rate():
    return (_EXAA.AudioAdapter().exotel_rate == 8000
            and ExotelWebSocketAdapter(_FakeExotelWS([]))._audio.exotel_rate == 8000
            and _EXCFG.summary()["sample_rate"] == "8000")
check("#EX default Exotel sample rate is 8000 Hz (Exotel's default; start frame overrides)",
      _exotel_default_sample_rate())

# Outbound audio must be framed to 320-byte multiples; sub-320 remainder buffers
# until more arrives, then is padded + flushed on call teardown.
async def _exotel_outbound_framing():
    ex = _FakeExotelWS([])
    a = ExotelWebSocketAdapter(ex, exotel_rate=24000)  # 24k == Bulbul rate => passthrough, no resample
    a.stream_sid = "SS"
    await a.send_bytes(b"\x01\x02" * 300)  # 600B -> emit 320, buffer 280
    await a.send_bytes(b"\x03\x04" * 60)   # +120 -> 400 -> emit 320, buffer 80
    await a.send_bytes(b"\x05\x06" * 50)   # +100 -> 180 -> no full frame
    mid = [f for f in ex.sent if f["event"] == "media"]
    await a.close()                        # final flush pads the 180B remainder to 320
    media = [f for f in ex.sent if f["event"] == "media"]
    all_aligned = all(len(_b64.b64decode(f["media"]["payload"])) % 320 == 0 for f in media)
    return all_aligned and len(mid) == 2 and len(media) == 3
check("#EX outbound audio is framed to 320-byte multiples (remainder buffered, then padded+flushed on close)",
      asyncio.run(_exotel_outbound_framing()))

async def _exotel_bargein_drops_buffer():
    ex = _FakeExotelWS([])
    a = ExotelWebSocketAdapter(ex, exotel_rate=24000); a.stream_sid = "SS"
    await a.send_bytes(b"\x01\x02" * 50)         # 100B buffered, below one frame
    await a.send_json({"type": "interrupted"})   # barge-in: drop buffer + send clear
    buf_cleared = len(a._out_buf) == 0
    clear_sent = any(f["event"] == "clear" for f in ex.sent)
    await a.close()
    no_media = not any(f["event"] == "media" for f in ex.sent)
    return buf_cleared and clear_sent and no_media
check("#EX barge-in (interrupted) drops buffered outbound audio and sends a clear frame",
      asyncio.run(_exotel_bargein_drops_buffer()))

# A latency summary is logged when the call ends.
async def _exotel_latency_summary():
    import logging as _lg
    cap = []
    class _H(_lg.Handler):
        def emit(self, r): cap.append(self.format(r))
    base = _lg.getLogger("exotel.session"); h = _H(); h.setFormatter(_lg.Formatter("%(message)s"))
    base.addHandler(h); base.setLevel(_lg.INFO)
    try:
        a = ExotelWebSocketAdapter(_FakeExotelWS([]))
        await a.close()
    finally:
        base.removeHandler(h)
    return any("[latency-summary]" in m for m in cap)
check("#EX a latency summary line is logged when the call ends",
      asyncio.run(_exotel_latency_summary()))

# Phone-call location: the geocoder extracts the place from a full spoken sentence
# (Nominatim can't), and the Exotel prompt drops the browser's map-pin instruction
# for accepting the caller's APPROXIMATE spoken location — browser prompt untouched.
from integrations.exotel.services import _clean_landmark as _EX_clean
from integrations.exotel.session import _phone_location_prompt as _EX_phoneprompt
from severity_engine.dispatcher_hindi import _hindi_system_prompt as _EX_hiprompt
def _exotel_location_extraction_and_prompt():
    clean_ok = (_EX_clean("I am injured near Malviya Nagar").lower() == "malviya nagar"
                and _EX_clean("मैं मालवीय नगर के पास घायल हूँ") == "मालवीय नगर"
                and _EX_clean("Malviya Nagar").lower() == "malviya nagar")  # clean input preserved
    base = _EX_hiprompt()
    phone = _EX_phoneprompt(base)
    prompt_ok = ("मैप-पिन बटन" in base                    # browser base still instructs map-pin
                 and "मैप-पिन बटन" not in phone           # phone path: instruction removed
                 and "यह एक असली फ़ोन कॉल है" in phone      # phone override present
                 and "अनुमानित लोकेशन को स्वीकार" in phone)  # accept approximate
    sess = _ex_session()  # wiring: the session applies the phone prompt to its gen configs
    wired = ("मैप-पिन बटन" not in sess._gen_config.system_instruction
             and "यह एक असली फ़ोन कॉल है" in sess._briefing_config.system_instruction)
    return clean_ok and prompt_ok and wired
check("#EX phone location: geocoder extracts place from a sentence; prompt drops map-pin for approximate (browser unchanged)",
      _exotel_location_extraction_and_prompt())

# Geocoding tries Google Places Text Search first (best for Indian landmarks/
# businesses), and falls back to Nominatim when there's no key.
async def _exotel_google_geocode_first():
    async def fake_send(method, url, **kw):
        if "places.googleapis" in url:
            return {"places": [{"location": {"latitude": 28.5369, "longitude": 77.2119},
                                "formattedAddress": "KFC, Old Market, Malviya Nagar"}]}
        return [{"lat": "28.5300", "lon": "77.2000", "display_name": "Malviya Nagar (Nominatim)"}]
    _orig_send = _EXSV._send
    _orig_key = _EXSV.config.GOOGLE_MAPS_SERVER_KEY
    _EXSV._send = fake_send
    try:
        _EXSV.config.GOOGLE_MAPS_SERVER_KEY = "test-key"
        r = await _EXSV.geocode_landmark("near Malviya Nagar KFC")
        google_first = bool(r and abs(r["lat"] - 28.5369) < 1e-6 and "KFC" in r["label"])
        _EXSV.config.GOOGLE_MAPS_SERVER_KEY = ""  # no key -> Nominatim fallback
        r2 = await _EXSV.geocode_landmark("Malviya Nagar")
        nominatim_fallback = bool(r2 and abs(r2["lat"] - 28.53) < 1e-6 and "Nominatim" in r2["label"])
    finally:
        _EXSV._send = _orig_send
        _EXSV.config.GOOGLE_MAPS_SERVER_KEY = _orig_key
    return google_first and nominatim_fallback
check("#EX geocoding uses Google Places Text Search first (key set), falls back to Nominatim (no key)",
      asyncio.run(_exotel_google_geocode_first()))

# Latency/UX: the opening line skips Gemini entirely (instant welcome, no 429 wait),
# and slow lookups speak a holding line so the caller never hears dead air.
async def _exotel_instant_opening_and_filler():
    s = _ex_session()
    s._opening_line_pending = True
    opening_skips_gemini = (await s._reason(None, "(the call just connected)")) is None
    spoken = []
    async def _fake_speak(text, allow_bargein=True, **_kw):
        spoken.append((text, allow_bargein)); return True
    s._speak_or_fallback = _fake_speak
    async def _fake_lookup():
        return {"ok": True, "name": "AIIMS Trauma Centre"}
    result = await s._speak_filler_during("एक पल... देख रहा हूँ।", _fake_lookup())
    filler_ok = (result == {"ok": True, "name": "AIIMS Trauma Centre"}
                 and spoken == [("एक पल... देख रहा हूँ।", False)])  # spoken, uninterruptible
    distinct_fillers = (_EXsession_fillers("hospital") != _EXsession_fillers("mechanic")
                        and "अस्पताल" in _EXsession_fillers("hospital"))
    return opening_skips_gemini and filler_ok and distinct_fillers
from integrations.exotel.session import _FACILITY_FILLERS as _EX_FILLERS, _FACILITY_FILLER_DEFAULT as _EX_FILLER_DEF
def _EXsession_fillers(k): return _EX_FILLERS.get(k, _EX_FILLER_DEF)
check("#EX instant opening (no Gemini on the first turn) + spoken filler during slow lookups (no dead air)",
      asyncio.run(_exotel_instant_opening_and_filler()))

# ── Change 3: filler acknowledgment (rotation + cached playback + mutual excl.) ─
import severity_engine.dispatcher_hindi as _HI

class _FillerWS:
    def __init__(self): self.sent = []; self.query_params = {"locale": "hi-IN"}
    async def send_json(self, f): pass
    async def send_bytes(self, b): self.sent.append(b)
    async def receive(self): return {"type": "websocket.disconnect"}
    async def close(self): pass

async def _hi_filler_rotation_and_play():
    _HI._filler_pcm_cache.clear()
    for i, t in enumerate(_HI._FILLER_TEXTS):
        _HI._filler_pcm_cache[t] = b"\x01\x00" * (100 * (i + 1))  # fake cached PCM
    s = _HI.HindiDispatcherSession(_FillerWS())
    picks = [s._next_filler_text() for _ in range(6)]
    no_repeat = all(picks[i] != picks[i + 1] for i in range(len(picks) - 1))
    all_valid = all(p in _HI._FILLER_TEXTS for p in picks)
    before = len(s.websocket.sent)
    await s._play_filler()                     # streams cached PCM (never reads self._stt)
    played = len(s.websocket.sent) > before
    _HI._filler_pcm_cache.clear()
    return no_repeat and all_valid and played
check("#HI Change 3: filler rotates without immediate repeat + plays cached PCM (no per-turn synth, no STT read)",
      asyncio.run(_hi_filler_rotation_and_play()))

async def _hi_filler_synth_caches_once():
    # _synthesize_fillers hits Bulbul only for uncached texts; a mocked stream proves
    # the once-per-process caching (no live API).
    _HI._filler_pcm_cache.clear()
    calls = []
    class _FakeBulbul:
        async def speak(self, text):
            calls.append(text)
            yield b"\x02\x00" * 50
    await _HI._synthesize_fillers(_FakeBulbul())
    first = sorted(calls)
    await _HI._synthesize_fillers(_FakeBulbul())     # cache warm -> no more synths
    cached_all = all(t in _HI._filler_pcm_cache for t in _HI._FILLER_TEXTS)
    _HI._filler_pcm_cache.clear()
    return first == sorted(_HI._FILLER_TEXTS) and len(calls) == len(_HI._FILLER_TEXTS) and cached_all
check("#HI Change 3: fillers synthesized once per process and cached (second call hits no API)",
      asyncio.run(_hi_filler_synth_caches_once()))

async def _ex_filler_mutual_exclusion():
    a = ExotelWebSocketAdapter(_FakeExotelWS([]), 16000)
    s = ExotelHindiSession(a)
    spoke = []
    async def _fake_speak(t, allow_bargein=True): spoke.append(t); return True
    s._speak_or_fallback = _fake_speak
    async def _coro(): return {"ok": True}
    s._ack_filler_active = False
    r1 = await s._speak_filler_during("lookup filler", _coro())   # thinking filler NOT active -> speaks
    s._ack_filler_active = True
    r2 = await s._speak_filler_during("lookup filler", _coro())   # thinking filler active -> defers
    return r1 == {"ok": True} and r2 == {"ok": True} and spoke == ["lookup filler"]
check("#EX Change 3: Exotel lookup filler defers to the thinking-gap filler (at most one filler/turn)",
      asyncio.run(_ex_filler_mutual_exclusion()))

# ── Change 2: stream/pipeline the fast-path reply's sentences to Bulbul ────────
from severity_engine.sarvam_speech import _split_for_synthesis as _C2_split
def _change2_force_split():
    short = "ओह, समझ गया। कुल कितनी गाड़ियाँ थीं?"
    normal = _C2_split(short)                          # short -> one synthesis (unchanged)
    forced = _C2_split(short, force_sentences=True)    # pipelined -> one piece per sentence
    long_ = "क ख ग। " * 80                             # > cap -> splits either way (voice consistency)
    return normal == [short] and len(forced) == 2 and forced[0].startswith("ओह") and len(_C2_split(long_)) > 1
check("#HI Change 2: force_split pipelines a short reply into per-sentence pieces (non-force unchanged)",
      _change2_force_split())

async def _change2_pipeline_plumbing():
    s = _HI.HindiDispatcherSession(_FillerWS())
    seen = {}
    async def _fake_speak(text, force_split=False):
        seen["force_split"] = force_split
        yield b"\x00\x00" * 240
    s._tts.speak = _fake_speak
    await s._speak_or_fallback("जी। ठीक है।", allow_bargein=False, pipeline=True)
    fast = seen.get("force_split")
    seen.clear()
    await s._speak_or_fallback("जी।", allow_bargein=False, pipeline=False)
    slow = seen.get("force_split")
    return fast is True and slow is False
check("#HI Change 2: _speak_or_fallback forwards pipeline -> Bulbul force_split (fast path only)",
      asyncio.run(_change2_pipeline_plumbing()))

def _change2_guards_unchanged():
    s = _HI.HindiDispatcherSession(_FillerWS())
    non_fast_tool = s._compose_single_round_reply("ओह ठीक है", {"submit_incident"})  # tool guard -> None
    ack_has_question = s._compose_single_round_reply("क्या हुआ?", {"update_form_field"})  # '?' guard -> None
    # NOTE: an empty ack ALONE no longer bails (2026-08 Sarvam fix -- see the
    # empty-ack fast-path tests above). This session returns None only because a
    # non-accident session has nothing missing; the tool + '?' guards are what
    # this test pins.
    nothing_missing = s._compose_single_round_reply("ओह ठीक है", {"update_form_field"})  # no accident mode -> None
    return non_fast_tool is None and ack_has_question is None and nothing_missing is None
check("#HI Change 2: fast-path guards unchanged (non-fast tool / ack-with-'?' / empty ack all still fall back)",
      _change2_guards_unchanged())

# ── Change 1: per-call Hindi region selection ─────────────────────────────────
check("#HI Change 1: Hindi Gemini region is asia-south1 by default, separate from English's us-central1",
      _HI._HINDI_TEXT_LOCATION == "asia-south1"
      and __import__("severity_engine.dispatcher_live", fromlist=["_LOCATION"])._LOCATION == "us-central1")

# ── Sarvam reasoning backend (primary) + Gemini fallback ──────────────────────
import os as _os
import json as _json2
import severity_engine.sarvam_reasoning as _SR
from google.genai import types as _T

def _sarvam_tool_mapping():
    decls = [{"name": "update_form_field", "description": "D", "parameters": {
        "type": "OBJECT", "properties": {
            "field": {"type": "STRING", "enum": ["a", "b"]},
            "n": {"type": "INTEGER"},
            "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": ["field"]}}]
    t = _SR.gemini_tools_to_openai(decls)[0]
    p = t["function"]["parameters"]
    return (t["type"] == "function" and t["function"]["name"] == "update_form_field"
            and p["type"] == "object"
            and p["properties"]["field"]["type"] == "string" and p["properties"]["field"]["enum"] == ["a", "b"]
            and p["properties"]["n"]["type"] == "integer"
            and p["properties"]["tags"]["type"] == "array" and p["properties"]["tags"]["items"]["type"] == "string"
            and p["required"] == ["field"])
check("#HI Sarvam tool mapping: Gemini decl dicts -> OpenAI tools (types lowercased, nesting preserved)",
      _sarvam_tool_mapping())

def _sarvam_history_mapping():
    hist = [
        _T.Content(role="user", parts=[_T.Part(text="मेरी कार टकरा गई")]),
        _T.Content(role="model", parts=[
            _T.Part(text="ओह"),
            _T.Part(function_call=_T.FunctionCall(id="c1", name="update_form_field", args={"field": "casualties", "value": "2"}))]),
        _T.Content(role="user", parts=[
            _T.Part(function_response=_T.FunctionResponse(id="c1", name="update_form_field", response={"ok": True, "next_question": "whether anyone is trapped"}))]),
    ]
    m = _SR.gemini_history_to_openai_messages(hist, "SYS")
    return (m[0] == {"role": "system", "content": "SYS"}
            and m[1] == {"role": "user", "content": "मेरी कार टकरा गई"}
            and m[2]["role"] == "assistant" and m[2]["tool_calls"][0]["id"] == "c1"
            and m[2]["tool_calls"][0]["function"]["name"] == "update_form_field"
            and _json2.loads(m[2]["tool_calls"][0]["function"]["arguments"]) == {"field": "casualties", "value": "2"}
            and m[3]["role"] == "tool" and m[3]["tool_call_id"] == "c1"
            and _json2.loads(m[3]["content"])["next_question"] == "whether anyone is trapped")
check("#HI Sarvam history mapping: types.Content -> OpenAI messages (assistant tool_calls + tool msgs, ids matched)",
      _sarvam_history_mapping())

async def _sarvam_stream_aggregation():
    _os.environ.setdefault("SARVAM_API_KEY", "test-key")
    lines = [
        "data: " + _json2.dumps({"choices": [{"delta": {"content": "ओह"}}]}),
        "data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "update_form_field", "arguments": ""}}]}}]}),
        "data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"field": "casualties", '}}]}}]}),
        "data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"value": "2"}'}}]}}]}),
        "data: [DONE]",
    ]
    class _Resp:
        status_code = 200
        async def aiter_lines(self):
            for l in lines: yield l
        async def aread(self): return b""
    class _Ctx:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False
    class _Client:
        def stream(self, *a, **k): return _Ctx()
        async def aclose(self): pass
    res = await _SR.sarvam_generate([{"role": "user", "content": "x"}], [], client=_Client())
    tc = res.tool_calls[0]
    return (res.text == "ओह" and tc.name == "update_form_field" and tc.id == "c1"
            and tc.args == {"field": "casualties", "value": "2"} and res.ok)
check("#HI Sarvam streaming aggregation: content + split-argument tool_calls -> normalized result",
      asyncio.run(_sarvam_stream_aggregation()))

async def _sarvam_stream_malformed_raises():
    _os.environ.setdefault("SARVAM_API_KEY", "test-key")
    lines = ["data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "update_form_field", "arguments": "{not json"}}]}}]}), "data: [DONE]"]
    class _Resp:
        status_code = 200
        async def aiter_lines(self):
            for l in lines: yield l
        async def aread(self): return b""
    class _Ctx:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False
    class _Client:
        def stream(self, *a, **k): return _Ctx()
        async def aclose(self): pass
    try:
        await _SR.sarvam_generate([{"role": "user", "content": "x"}], [], client=_Client())
        return False
    except _SR.SarvamReasoningError:
        return True
check("#HI Sarvam malformed tool arguments -> SarvamReasoningError (so the dispatcher can fall back)",
      asyncio.run(_sarvam_stream_malformed_raises()))

async def _sarvam_stream_dup_empty_args():
    _os.environ.setdefault("SARVAM_API_KEY", "test-key")
    # sarvam-105b streams a NO-ARG tool's "{}" TWICE ("{}{}") -- must be tolerated.
    lines = [
        "data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "submit_incident", "arguments": ""}}]}}]}),
        "data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]}),
        "data: " + _json2.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]}),
        "data: [DONE]",
    ]
    class _Resp:
        status_code = 200
        async def aiter_lines(self):
            for l in lines: yield l
        async def aread(self): return b""
    class _Ctx:
        async def __aenter__(self): return _Resp()
        async def __aexit__(self, *a): return False
    class _Client:
        def stream(self, *a, **k): return _Ctx()
        async def aclose(self): pass
    res = await _SR.sarvam_generate([{"role": "user", "content": "x"}], [], client=_Client())
    return len(res.tool_calls) == 1 and res.tool_calls[0].name == "submit_incident" and res.tool_calls[0].args == {}
check("#HI Sarvam tolerates the duplicated-'{}' streaming quirk for no-arg tools (submit_incident)",
      asyncio.run(_sarvam_stream_dup_empty_args()))

def _mk_reason_session(use_sarvam):
    from severity_engine import dispatcher_hindi as dh
    s = dh.HindiDispatcherSession.__new__(dh.HindiDispatcherSession)
    s._history = [_T.Content(role="user", parts=[_T.Part(text="hi")])]
    s._turn_stats = {}; s._turn_backend = None; s._quota_hits = 0; s._sarvam_http = None
    s._use_sarvam = use_sarvam; s._openai_tools = []
    class _Cfg: system_instruction = "SYS"
    s._gen_config = _Cfg(); s._briefing_config = object()
    return s, dh

def _gemini_response(text):
    return _T.GenerateContentResponse(candidates=[_T.Candidate(content=_T.Content(role="model", parts=[_T.Part(text=text)]))])

async def _sarvam_retry_then_fallback():
    s, dh = _mk_reason_session(use_sarvam=True)
    orig = dh.sarvam_generate
    calls = {"sarvam": 0, "fast": None}
    async def _boom(*a, **k): calls["sarvam"] += 1; raise dh.SarvamReasoningError("boom")
    dh.sarvam_generate = _boom
    async def _fake_gemini(gemini_client=None, config=None, fast=False):
        calls["fast"] = fast
        return _gemini_response("जी")
    s._generate_gemini = _fake_gemini
    try:
        text, fcs, parts = await s._reason_round(None, None)
    finally:
        dh.sarvam_generate = orig
    # Sarvam is retried (HINDI_SARVAM_ATTEMPTS=2) BEFORE falling back, and the
    # Gemini fallback is the FAIL-FAST path (fast=True) -> no 15s stall possible.
    return (text == "जी" and s._turn_backend == "gemini" and not fcs
            and "sarvam_fail" in s._turn_stats
            and calls["sarvam"] == dh._SARVAM_ATTEMPTS and calls["fast"] is True)
check("#HI Sarvam retried N× then FAIL-FAST Gemini fallback (fast=True) -> a 15s stall is impossible",
      asyncio.run(_sarvam_retry_then_fallback()))

async def _sarvam_timeout_no_retry():
    s, dh = _mk_reason_session(use_sarvam=True)
    orig = dh.sarvam_generate
    calls = {"sarvam": 0, "fast": None}
    async def _hang(*a, **k):
        calls["sarvam"] += 1
        raise asyncio.TimeoutError()      # simulate wait_for's timeout firing
    dh.sarvam_generate = _hang
    async def _fake_gemini(gemini_client=None, config=None, fast=False):
        calls["fast"] = fast
        return _gemini_response("जी")
    s._generate_gemini = _fake_gemini
    try:
        text, fcs, parts = await s._reason_round(None, None)
    finally:
        dh.sarvam_generate = orig
    # A timeout must NOT be retried (that stacked a second 5s wait -> the 10s
    # stall seen on a real call) -- Sarvam called exactly once, then fail-fast.
    return (text == "जी" and s._turn_backend == "gemini"
            and calls["sarvam"] == 1 and calls["fast"] is True)
check("#HI Sarvam TIMEOUT is not retried (1 attempt) -> straight to fail-fast Gemini (no stacked 10s wait)",
      asyncio.run(_sarvam_timeout_no_retry()))

async def _briefing_fallback_is_generous():
    # Regression: the closing briefing is a ~700-token generation. When Sarvam
    # times out on it, the fallback MUST use the generous Gemini path (fast=False
    # -> _BRIEFING_TIMEOUT_S, 2 attempts), NOT the 3.5s fail-fast -- else a long
    # briefing can't finish and the caller gets "तकनीकी समस्या" with no ETAs/SOPs.
    s, dh = _mk_reason_session(use_sarvam=True)
    class _BCfg: system_instruction = "BRIEFING SYS"
    s._briefing_config = _BCfg()
    calls = {"fast": None}
    async def _boom(messages, tools, **k): raise asyncio.TimeoutError()
    orig = dh.sarvam_generate; dh.sarvam_generate = _boom
    async def _fake_gemini(gemini_client=None, config=None, fast=False):
        calls["fast"] = fast
        return _gemini_response("आपातकालीन ब्रीफिंग")
    s._generate_gemini = _fake_gemini
    try:
        text, fcs, parts = await s._reason_round(None, s._briefing_config)
    finally:
        dh.sarvam_generate = orig
    return text == "आपातकालीन ब्रीफिंग" and calls["fast"] is False
check("#HI briefing turn: Sarvam timeout -> GENEROUS Gemini fallback (fast=False), not the 3.5s fail-fast",
      asyncio.run(_briefing_fallback_is_generous()))

async def _backend_env_gemini_skips_sarvam():
    s, dh = _mk_reason_session(use_sarvam=False)   # HINDI_REASONING_BACKEND=gemini equivalent
    orig = dh.sarvam_generate
    called = {"sarvam": False}
    async def _spy(*a, **k): called["sarvam"] = True; raise dh.SarvamReasoningError("should not be called")
    dh.sarvam_generate = _spy
    async def _fake_gemini(gemini_client=None, config=None, fast=False):
        return _gemini_response("जी")
    s._generate_gemini = _fake_gemini
    try:
        text, fcs, parts = await s._reason_round(None, None)
    finally:
        dh.sarvam_generate = orig
    return text == "जी" and s._turn_backend == "gemini" and called["sarvam"] is False
check("#HI backend env var: _use_sarvam False skips Sarvam entirely and uses Gemini (default constant is 'sarvam')",
      asyncio.run(_backend_env_gemini_skips_sarvam())
      and _SR and __import__("severity_engine.dispatcher_hindi", fromlist=["_HINDI_REASONING_BACKEND"])._HINDI_REASONING_BACKEND == "sarvam"
      and __import__("severity_engine.dispatcher_hindi", fromlist=["_MAX_OUTPUT_TOKENS"])._MAX_OUTPUT_TOKENS <= 200)

def _fastpath_backend_agnostic():
    from severity_engine import dispatcher_hindi as dh
    def mk(use_sarvam):
        s = dh.HindiDispatcherSession.__new__(dh.HindiDispatcherSession)
        s.state = DispatcherState(language="hi-IN")
        s.state.location = {"lat": 28.6, "lng": 77.2, "label": "x"}
        s.state.description = "car crash"
        s.state.sub_type = "Car vs. Car Collision"   # incident type filled -> next missing has a canonical Q
        s._accident_mode = True
        s._use_sarvam = use_sarvam
        return s
    a = mk(True)._compose_single_round_reply("ओह ठीक है", {"update_form_field"})
    b = mk(False)._compose_single_round_reply("ओह ठीक है", {"update_form_field"})
    # identical regardless of backend, and it actually composes (a canonical
    # next-question exists once location + description are filled)
    return a is not None and a == b and "?" in a
check("#HI fast-path guards are backend-agnostic: _compose_single_round_reply is identical for Sarvam vs Gemini",
      _fastpath_backend_agnostic())

print("\nALL TESTS PASSED")
