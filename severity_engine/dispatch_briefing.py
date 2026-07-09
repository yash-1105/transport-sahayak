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


# ── The final synthetic turn ───────────────────────────────────────────────────

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
