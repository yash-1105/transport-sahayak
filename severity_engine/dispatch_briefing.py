"""
dispatch_briefing.py — the post-submission closing sequence shared by BOTH
voice dispatchers (English Gemini Live in dispatcher_live.py, and the Hindi
Saaras→Gemini→Bulbul pipeline in dispatcher_hindi.py).

After submit_incident, the browser dashboard runs its EXISTING matching flow
(MatchingPanel.tsx: Places + Routes API + the ambulance/fire/towing station
ETA cards) and sends the same numbers it is already displaying back over the
dispatcher WebSocket as one {"type": "dispatch_update", "services": {...}}
frame. Nothing in this module computes an ETA — it only turns those
already-displayed values, plus the incident conditions already collected in
DispatcherState, into ONE final synthetic model turn: responder ETAs → SOP
safety guidance → follow-up-call script → goodbye.

Honesty rules (CLAUDE.md hard rules — do not weaken when editing):
  - Every time is spoken as an ESTIMATE ("estimated arrival, approximately N
    minutes"), matching the dashboard's own "Calculated estimate — not live
    tracking" cards. Never phrased as tracked fact.
  - Services are said to be NOTIFIED / responding-from, never "dispatched and
    being tracked" — the dispatch record is a notification record only, and
    the system tracks no vehicle.
  - The model is instructed to use these exact names and numbers verbatim and
    never to invent or adjust one.
"""
from typing import Optional

# ── SOP safety guidance ────────────────────────────────────────────────────────
# Deterministic mapping from the flags already collected in DispatcherState to
# fixed, vetted first-response instructions — the model chooses HOW to say
# them naturally, never WHICH apply (same rule-first pattern as next_question).
# Ordered life-threat first; at most _MAX_SPECIFIC_SOPS specific instructions
# are given plus the general one, so a caller is never read a long checklist.

_MAX_SPECIFIC_SOPS = 3

_SOPS = [
    {
        "key": "bleeding",
        "applies": lambda st: "Heavy bleeding" in st.flags,
        "en": "If someone is bleeding heavily, apply firm pressure using a clean cloth, and do not remove the cloth once pressure has been applied.",
        "hi": "यदि किसी व्यक्ति को बहुत अधिक खून बह रहा है, तो साफ कपड़े से लगातार दबाव बनाए रखें। कपड़ा बार-बार न हटाएँ।",
    },
    {
        "key": "fire",
        "applies": lambda st: "Fire" in st.flags,
        "en": "Move everyone away from the vehicle immediately, and do not attempt to extinguish a large fire yourselves.",
        "hi": "सभी लोगों को वाहन से तुरंत सुरक्षित दूरी पर ले जाएँ। यदि आग बड़ी है तो उसे खुद बुझाने का प्रयास न करें।",
    },
    {
        # Covers both "confirmed unconscious" and "not breathing normally" —
        # flag polarity: Conscious/Breathing in flags_discussed but NOT in
        # flags means the caller confirmed the bad state (see
        # dispatcher_live._tool_update_form_field).
        "key": "unconscious",
        "applies": lambda st: (
            ("Conscious" in st.flags_discussed and "Conscious" not in st.flags)
            or ("Breathing" in st.flags_discussed and "Breathing" not in st.flags)
        ),
        "en": "Do not move the injured person unless there is immediate danger such as fire, and keep their head and neck as still as possible.",
        "hi": "यदि तत्काल खतरा न हो, तो घायल व्यक्ति को बिल्कुल न हिलाएँ, और उसके सिर और गर्दन को जितना हो सके स्थिर रखें।",
    },
    {
        "key": "trapped",
        "applies": lambda st: "Trapped" in st.flags,
        "en": "Do not try to force open crushed doors or pull a trapped person out unless there is an immediate threat.",
        "hi": "यदि तत्काल खतरा न हो, तो फँसे हुए व्यक्ति को जबरन बाहर निकालने या दबे दरवाज़े ज़बरदस्ती खोलने का प्रयास न करें।",
    },
    {
        "key": "hazmat",
        "applies": lambda st: "Hazardous material" in st.flags,
        "en": "Keep everyone well away from any spilled fuel or chemicals, and do not touch or go near the material.",
        "hi": "गिरे हुए ईंधन या केमिकल से सभी लोगों को दूर रखें, और उसे छूने या उसके पास जाने की कोशिश न करें।",
    },
]

_GENERAL_SOP = {
    "key": "general",
    "en": "Please stay calm, and keep everyone at a safe distance from moving traffic. Help is on the way.",
    "hi": "कृपया शांत रहें। सभी लोगों को यातायात से सुरक्षित दूरी पर रखें। सहायता रास्ते में है।",
}


def select_sops(state) -> list:
    """The SOP instructions applicable to this incident, life-threat first,
    capped so the caller is never overwhelmed. Always ends with the general
    stay-calm/stay-clear-of-traffic instruction."""
    chosen = [s for s in _SOPS if s["applies"](state)][:_MAX_SPECIFIC_SOPS]
    chosen.append(_GENERAL_SOP)
    return chosen


# ── Hindi number rendering ─────────────────────────────────────────────────────
# Bulbul reads bare digits unreliably mid-sentence (same reason the opening
# line spells out 1033 — see dispatcher_hindi.py), so ETA minutes are handed
# to the model already rendered as Hindi words. Hindi numbers 1–99 are
# irregular, so a full table is the standard approach.

_HINDI_NUMBERS = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस "
    "ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
    "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस "
    "इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस "
    "इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
    "इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ "
    "इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर "
    "इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी "
    "इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे "
    "इक्यानवे बानवे तिरानवे चौरानवे पंचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे सौ"
).split()


def hindi_minutes(minutes: int) -> str:
    """ETA minutes as spoken Hindi, e.g. 26 -> "छब्बीस मिनट"."""
    minutes = max(1, int(round(minutes)))
    if minutes <= 100:
        return f"{_HINDI_NUMBERS[minutes]} मिनट"
    hours = minutes // 60
    return f"{_HINDI_NUMBERS[hours]} घंटे से ज़्यादा"


# ── Responder lines from the dashboard's dispatch_update payload ──────────────

def _facility_location(name: str) -> str:
    """Station names in the seed data follow "<Facility label> — <Location>"
    (e.g. "108 Post — Baraut") — same convention MatchingPanel's own ETA cards
    use: what matters to the caller is the location, not the internal label."""
    idx = (name or "").find("—")
    return name[idx + 1:].strip() if idx >= 0 else (name or "").strip()


def _service(services: Optional[dict], key: str) -> Optional[dict]:
    entry = (services or {}).get(key)
    if not entry or not entry.get("name"):
        return None
    return entry


def _eta_min(entry: dict) -> Optional[int]:
    eta = entry.get("etaMinutes")
    if eta is None:
        return None
    try:
        return max(1, int(round(float(eta))))
    except (TypeError, ValueError):
        return None


def _responder_facts_en(services: Optional[dict]) -> list:
    facts = []
    amb = _service(services, "ambulance")
    if amb:
        eta = _eta_min(amb)
        facts.append(
            f"The nearest ambulance service has been notified — it responds from {_facility_location(amb['name'])}"
            + (f", estimated arrival at the caller's location in approximately {eta} minutes." if eta else ".")
        )
    fire = _service(services, "fire")
    if fire:
        eta = _eta_min(fire)
        facts.append(
            f"The fire service has been notified — it responds from {_facility_location(fire['name'])}"
            + (f", estimated arrival in approximately {eta} minutes." if eta else ".")
        )
    tow = _service(services, "towing")
    if tow:
        eta = _eta_min(tow)
        facts.append(
            f"A towing and recovery service has been notified — it responds from {_facility_location(tow['name'])}"
            + (f", estimated to reach the caller in approximately {eta} minutes." if eta else ".")
        )
    hos = _service(services, "hospital")
    if hos:
        eta = _eta_min(hos)
        facts.append(
            f"The nearest suitable hospital is {hos['name']}"
            + (f", an estimated {eta} minutes away by road." if eta else ".")
        )
    pol = _service(services, "police")
    if pol:
        eta = _eta_min(pol)
        facts.append(
            f"The nearest police station, {pol['name']}, has also been notified"
            + (f" — an estimated {eta} minutes away." if eta else ".")
        )
    return facts


def _responder_facts_hi(services: Optional[dict]) -> list:
    facts = []
    amb = _service(services, "ambulance")
    if amb:
        eta = _eta_min(amb)
        facts.append(
            f"एम्बुलेंस सेवा को सूचित कर दिया गया है — यह {_facility_location(amb['name'])} से आएगी"
            + (f", अनुमानित समय लगभग {hindi_minutes(eta)}।" if eta else "।")
        )
    fire = _service(services, "fire")
    if fire:
        eta = _eta_min(fire)
        facts.append(
            f"फायर ब्रिगेड को सूचित कर दिया गया है — यह {_facility_location(fire['name'])} से आएगी"
            + (f", अनुमानित समय लगभग {hindi_minutes(eta)}।" if eta else "।")
        )
    tow = _service(services, "towing")
    if tow:
        eta = _eta_min(tow)
        facts.append(
            f"टो करने वाली गाड़ी (रिकवरी सेवा) को सूचित कर दिया गया है — यह {_facility_location(tow['name'])} से आएगी"
            + (f", अनुमानित समय लगभग {hindi_minutes(eta)}।" if eta else "।")
        )
    hos = _service(services, "hospital")
    if hos:
        eta = _eta_min(hos)
        facts.append(
            f"सबसे नज़दीकी उपयुक्त अस्पताल {hos['name']} है"
            + (f" — सड़क से अनुमानित लगभग {hindi_minutes(eta)} की दूरी पर।" if eta else "।")
        )
    pol = _service(services, "police")
    if pol:
        eta = _eta_min(pol)
        facts.append(
            f"सबसे नज़दीकी पुलिस थाने ({pol['name']}) को भी सूचित कर दिया गया है"
            + (f" — अनुमानित लगभग {hindi_minutes(eta)} की दूरी पर।" if eta else "।")
        )
    return facts


# ── Closing script (follow-up call information) ────────────────────────────────

_CLOSING_EN = [
    "Your incident has been successfully registered.",
    "All the necessary emergency teams have been informed.",
    "You may now safely disconnect this call.",
    "Our team will contact you within the next two hours to confirm that help has reached you and to check on the situation.",
    "If for any reason you do not receive that follow-up call, please call this helpline again after emergency services have arrived, or after a few hours, so that we can close the incident.",
    "Take care, and we hope everyone remains safe.",
]

_CLOSING_HI = [
    "आपकी घटना सफलतापूर्वक दर्ज कर ली गई है।",
    "सभी आवश्यक आपातकालीन सेवाओं को सूचित कर दिया गया है।",
    "अब आप यह कॉल समाप्त कर सकते हैं।",
    "अगले दो घंटों के भीतर हमारी टीम आपसे दोबारा संपर्क करेगी, ताकि यह पुष्टि की जा सके कि सहायता आपके पास पहुँच गई है और स्थिति कैसी है।",
    "यदि किसी कारणवश आपको यह फ़ॉलो-अप कॉल न मिले, तो कृपया सहायता पहुँचने के बाद, या कुछ घंटों बाद, इस हेल्पलाइन पर दोबारा कॉल करें, ताकि हम घटना को बंद कर सकें।",
    "अपना ध्यान रखिए... हमें उम्मीद है कि सभी सुरक्षित रहेंगे।",
]


# ── Segmented delivery (English / Gemini Live only) ────────────────────────────
# Real reported bug: the English agent spoke the ambulance ETA and then just
# stopped -- never reaching fire, hospital, police, SOPs, or the closing
# script. The single combined instruction below worked reliably in isolated
# testing (a fresh session, short history, 6/6 live runs completed the full
# ~40s monologue), but a REAL call reaches this point only after a full
# multi-turn conversation's worth of accumulated context, which isolated
# testing can't perfectly replicate -- and asking Gemini Live's native-audio
# model for one continuous ~40+ second turn is inherently more fragile than
# asking for several shorter ones, regardless of the exact mechanism. Hindi
# has no equivalent risk (build_briefing_instruction, used unchanged below):
# its text is fully generated up front by plain generate_content, then handed
# to Bulbul TTS as one already-complete string with its own internal chunked
# streaming -- there is no live per-turn audio-generation step that could
# stop partway. English has no such separation (Gemini Live generates audio
# directly, turn by turn), so the safety measure here is to keep every
# individual turn short: split the SAME content into 3 sequential turns along
# the natural 1/2/3 boundaries already present in the single-instruction
# design (responders -> SOPs -> closing), sent back-to-back by the caller
# (dispatcher_live._brief_and_close / _pump_gemini_to_client), each with its
# own stall failsafe. If one segment is ever cut short, the remaining
# segments still get their own independent chance to be delivered in full --
# strictly more robust than a single giant turn where any interruption loses
# everything after it.
def build_briefing_segments(state, services: Optional[dict], language_code: str) -> list:
    """Same content as build_briefing_instruction, as 3 separate synthetic
    turns instead of one. Each is self-contained (states it may not be the
    final turn) so the model doesn't try to also produce the other segments'
    content or say goodbye early."""
    hindi = language_code == "hi-IN"
    facts = _responder_facts_hi(services) if hindi else _responder_facts_en(services)
    sops = select_sops(state)
    sop_lines = [s["hi"] if hindi else s["en"] for s in sops]
    closing = _CLOSING_HI if hindi else _CLOSING_EN

    lang_note = (
        "Speak in simple, natural spoken Hindi as before (the material below is already in Hindi — "
        "deliver it faithfully; any facility or hospital name written in English letters must be "
        "spoken with natural Hindi pronunciation, and numbers are already written out as words). "
        if hindi
        else "Speak in natural, warm English as before. "
    )
    def preface(n: int, final: bool) -> str:
        position = f"turn {n} of 3 — the FINAL turn" if final else f"turn {n} of 3"
        return (
            "(SYSTEM UPDATE — not the caller speaking. The incident report was submitted successfully "
            "and the response dashboard has now matched the responding services. You are delivering the "
            "closing of this call in a FEW SHORT BACK-TO-BACK TURNS instead of one long one — this is "
            f"{position}. " + lang_note
        )
    # NOTE on "MUST mention all N" / "do not summarize or shorten": added
    # after live-testing the segments above -- confirmed on real Gemini Live
    # (2026-07) that without this, the model reliably UNDER-delivered each
    # segment (e.g. mentioning ambulance+fire but silently dropping towing,
    # hospital, and police from the SAME facts list every single run; dropping
    # the "call back if you don't hear from us" closing line most runs). Root
    # cause: this project's base system prompt (dispatcher_live._system_
    # instruction) elsewhere trains the model hard to keep every reply to 1-2
    # short sentences for natural conversation -- exactly the right instinct
    # for the rest of the call, but it was winning out over "say everything
    # in this list" once framed as "turn N of 3" (a short, lighter-feeling
    # conversational beat) instead of "the one shot to say it all". This is
    # NOT a repeat of the earlier premature-cutoff bug (that was the pump
    # forcing the call to end mid-generation; this is the model choosing, on
    # its own, to speak a shorter reply than asked) -- so the fix is a
    # stronger instruction, not a timing change. Re-verify live if this list
    # is ever restructured.
    common_rules = (
        "Speak naturally and conversationally, like a caring human operator, never a robot reading a "
        "checklist — BUT this specific turn is an exception to your usual habit of keeping replies to "
        "1-2 short sentences: this turn must be as long as it needs to be to include EVERY item listed "
        "below, by name, none skipped, none summarized away, none merged into a vague generic phrase. "
        "Do not ask the caller any question, do not call any tool, and do not say goodbye yet unless "
        "this is explicitly the final turn (see below) — more information is coming right after this. "
        "After speaking, say nothing further and wait.)"
    )

    if facts:
        facts_block = (
            f"Announce ALL {len(facts)} of these responding services to the caller now — every single "
            f"one below, by name, in one continuous reply; do not stop after the first one or two and do "
            f"not silently drop any of the rest. Use these exact names and numbers, word for word — NEVER "
            "invent, round differently, or change any time or name. Every time is an estimate and must "
            "sound like one (\"estimated\", \"approximately\" / \"अनुमानित\", \"लगभग\"):\n"
            + "\n".join(f"   - {f}" for f in facts)
            + f"\n\nBefore moving on, double check silently: did you name all {len(facts)} of the "
            "services above? If not, go back and include the ones you missed."
        )
    else:
        facts_block = (
            "No estimated times are available right now. Simply tell the caller the emergency services "
            "have been notified and are being arranged. Do NOT invent any arrival time, facility name, "
            "or number."
        )
    turn1 = preface(1, final=False) + "\n\n" + facts_block + "\n\n" + common_rules

    turn2 = (
        preface(2, final=False)
        + f"\n\nGive the caller ALL {len(sop_lines)} of these safety instructions while help is on the "
        "way — exactly these, briefly and clearly, in this order, none skipped:\n"
        + "\n".join(f"   - {line}" for line in sop_lines)
        + "\n\n" + common_rules
    )

    turn3 = (
        preface(3, final=True)
        + f"\n\nThis IS the final turn: finish the call with ALL {len(closing)} of these points, in this "
        "exact order, none skipped or merged together — this includes the instruction about calling "
        "back if the caller does NOT receive the follow-up call, which is easy to accidentally leave "
        "out but must be said:\n"
        + "\n".join(f"   - {line}" for line in closing)
        + "\n\nDo not ask the caller any further question, do not call any tool, and after this reply "
        "say nothing more — the call ends here.)"
    )
    return [turn1, turn2, turn3]


# ── The single combined turn (Hindi only — see build_briefing_segments above
# for why English uses the segmented version instead) ──────────────────────────

def build_briefing_instruction(state, services: Optional[dict], language_code: str) -> str:
    """One synthetic model turn (same parenthesized-system-note convention as
    the kickoff/reconnect turns) that carries everything the agent must
    deliver before the call ends. `services` is the browser's dispatch_update
    payload — the exact values the dashboard is displaying — or None if it
    never arrived (the ETA section is then skipped honestly, never invented)."""
    hindi = language_code == "hi-IN"
    facts = _responder_facts_hi(services) if hindi else _responder_facts_en(services)
    sops = select_sops(state)
    sop_lines = [s["hi"] if hindi else s["en"] for s in sops]
    closing = _CLOSING_HI if hindi else _CLOSING_EN

    if facts:
        facts_block = (
            "1. RESPONDING SERVICES — announce these to the caller. Use these exact names and "
            "numbers, word for word — NEVER invent, round differently, or change any time or name. "
            "Every time is an estimate and must sound like one (\"estimated\", \"approximately\" / "
            "\"अनुमानित\", \"लगभग\"):\n"
            + "\n".join(f"   - {f}" for f in facts)
        )
    else:
        facts_block = (
            "1. RESPONDING SERVICES — no estimated times are available right now. Simply tell the "
            "caller the emergency services have been notified and are being arranged. Do NOT invent "
            "any arrival time, facility name, or number."
        )

    lang_note = (
        "Speak in simple, natural spoken Hindi as before (the material below is already in Hindi — "
        "deliver it faithfully; any facility or hospital name written in English letters must be "
        "spoken with natural Hindi pronunciation, and numbers are already written out as words). "
        if hindi
        else "Speak in natural, warm English as before. "
    )

    return (
        "(SYSTEM UPDATE — not the caller speaking. The incident report was submitted successfully "
        "and the response dashboard has now matched the responding services. This is your FINAL "
        "turn of the call. "
        + lang_note
        + "Deliver ALL of the following as one continuous, natural, conversational reply — a "
        "caring human operator wrapping up an emergency call, never a robot reading a checklist. "
        "Use brief natural connectors between parts, and keep the pace calm:\n\n"
        + facts_block
        + "\n\n2. SAFETY INSTRUCTIONS while help is on the way — give exactly these, briefly and "
        "clearly, in this order:\n"
        + "\n".join(f"   - {line}" for line in sop_lines)
        + "\n\n3. CLOSING — finish the call with all of these points, in this order:\n"
        + "\n".join(f"   - {line}" for line in closing)
        + "\n\nDo not ask the caller any further question, do not call any tool, and after this "
        "reply say nothing more — the call ends here.)"
    )
