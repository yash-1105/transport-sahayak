"""Curated highway-helpline knowledge base — scheme / legal / what-to-do facts.

Language-agnostic, reusable data (Hindi + English text per topic) for the
multi-intent helpline assistant. The Hindi dispatcher's `answer_info_question`
tool reads from this; the English pipeline may adopt it later. It is imported as
DATA only — no dispatcher/session state here.

HONESTY (project Hard Rules): every fact below is transcribed from an official
source (cited in `source`), NOT invented by an LLM. Amounts/eligibility must
never be fabricated — the model may rephrase the `hi`/`en` text conversationally
but must not add figures or legal claims that aren't here. Every scheme entry
carries a conservative `note_*` telling the caller to confirm current details
via 1033 / the official source, because scheme rules and amounts change.

Figures verified 2026-08 against MoRTH / NHA / PIB / official notifications:
- Cashless Treatment of Road Accident Victims Scheme, 2025 ("PM-RAHAT"):
  MoRTH notification 5 May 2025, under Motor Vehicles Act §162; ₹1.5 lakh /
  victim, up to 7 days; implemented by the National Health Authority.
- Rah-Veer Yojana: MoRTH, in force 21 Apr 2025; ₹25,000 reward per incident.
- Good Samaritan protection: Motor Vehicles Act §134A; rules GSR 594(E),
  29 Sep 2020.
- Compensation to Victims of Hit-and-Run Motor Accidents Scheme, 2022: MoRTH
  notification 25 Feb 2022, effective 1 Apr 2022; ₹2,00,000 death / ₹50,000
  grievous injury, from the Motor Vehicle Accident Fund.
- Ayushman Bharat PM-JAY: NHA; ₹5 lakh / family / year; Cabinet Sept 2024
  extended cover to all citizens aged 70+ (Ayushman Vay Vandana Card).
"""

from typing import Optional

# Shared conservative disclaimers appended to scheme answers (amounts/eligibility
# can change). Safety topics don't carry a scheme disclaimer.
_SCHEME_NOTE_HI = (
    "योजना के नियम और राशि समय-समय पर बदल सकते हैं — ताज़ा और सटीक जानकारी के लिए "
    "1033 हेल्पलाइन या आधिकारिक स्रोत से पुष्टि ज़रूर करें।"
)
_SCHEME_NOTE_EN = (
    "Scheme rules and amounts can change over time — please confirm the current, "
    "exact details via the 1033 helpline or the official source."
)

# Each entry: a stable key (also the tool's `topic` enum value), a set of
# `labels` describing what it covers (used in the tool description + loose
# matching), Hindi + English answer text (facts only), an optional note, and the
# source. Keep answers concise (a few sentences) so they read naturally aloud.
KNOWLEDGE_BASE: dict[str, dict] = {
    "golden_hour_cashless": {
        "labels": [
            "golden hour", "cashless treatment", "free treatment for accident",
            "PM-RAHAT", "no money for treatment", "who pays for hospital",
        ],
        "hi": (
            "सड़क दुर्घटना पीड़ितों के लिए 'कैशलेस इलाज योजना, 2025' (जिसे पीएम-राहत भी कहा जाता है) "
            "के तहत, किसी भी सार्वजनिक सड़क पर मोटर वाहन से हुई दुर्घटना में घायल किसी भी व्यक्ति को "
            "प्रति पीड़ित डेढ़ लाख रुपये तक का कैशलेस इलाज, दुर्घटना की तारीख़ से सात दिन तक मिल सकता है। "
            "यह ख़ास तौर पर 'गोल्डन आवर' — चोट के बाद के पहले घंटे — के लिए है। इलाज नामित अस्पतालों में "
            "होता है, जिनमें आयुष्मान भारत से जुड़े अस्पताल भी शामिल हैं; और अगर किसी ग़ैर-नामित अस्पताल में "
            "ले जाया जाए, तब भी शुरुआती स्थिरीकरण (stabilisation) का ख़र्च इसमें शामिल है। यह मोटर वाहन "
            "अधिनियम की धारा 162 के तहत राष्ट्रीय स्वास्थ्य प्राधिकरण द्वारा चलाई जाती है। इसके लिए पैसे "
            "अभी पास होना ज़रूरी नहीं — पहले इलाज कराएँ।"
        ),
        "en": (
            "Under the Cashless Treatment of Road Accident Victims Scheme, 2025 (also called "
            "PM-RAHAT), any person injured in a road accident involving a motor vehicle on any public "
            "road can get cashless treatment up to ₹1.5 lakh per victim, for up to 7 days from the "
            "accident. It is meant for the 'golden hour' — the first hour after injury. Treatment is "
            "at designated hospitals, including Ayushman Bharat-empanelled ones; even at a "
            "non-designated hospital, the initial stabilisation cost is covered. It runs under Section "
            "162 of the Motor Vehicles Act and is implemented by the National Health Authority. You do "
            "not need money in hand to start treatment."
        ),
        "note_hi": _SCHEME_NOTE_HI,
        "note_en": _SCHEME_NOTE_EN,
        "source": "MoRTH — Cashless Treatment of Road Accident Victims Scheme, 2025 (notified 5 May 2025, MV Act §162); NHA.",
    },
    "rah_veer_good_samaritan": {
        "labels": [
            "good samaritan", "rah-veer", "reward for helping", "will I get in trouble for helping",
            "legal protection helper", "should I take injured to hospital",
        ],
        "hi": (
            "अगर आप नेकनीयती से किसी सड़क दुर्घटना पीड़ित की मदद करते हैं, तो 'नेक व्यक्ति क़ानून' "
            "(मोटर वाहन अधिनियम की धारा 134A; 29 सितंबर 2020 के नियम) आपकी रक्षा करता है: आपकी मदद से "
            "हुए किसी नुक़सान के लिए आप पर कोई सिविल या आपराधिक देनदारी नहीं आती, कोई आपको अपना नाम या "
            "पता बताने के लिए मजबूर नहीं कर सकता, और अस्पताल-पुलिस को आपसे सम्मान से पेश आना होगा। "
            "इसके अलावा, 'राह-वीर योजना' (2025) के तहत किसी गंभीर सड़क दुर्घटना में जान बचाने वाली "
            "मदद करने पर प्रति घटना पच्चीस हज़ार रुपये (एक से ज़्यादा मददगार हों तो बराबर बँटता है) का "
            "इनाम और प्रशस्ति-पत्र मिल सकता है। इसलिए घायल की मदद करने या उसे अस्पताल पहुँचाने से न घबराएँ।"
        ),
        "en": (
            "If you help a road-accident victim in good faith, the Good Samaritan Law (Section 134A of "
            "the Motor Vehicles Act; rules dated 29 Sept 2020) protects you: you cannot be held civilly "
            "or criminally liable for any harm resulting from your help, no one can force you to reveal "
            "your name or address, and the hospital and police must treat you respectfully. Separately, "
            "under the Rah-Veer Yojana (2025), a person who gives life-saving help during a serious road "
            "accident can receive a ₹25,000 reward per incident (shared equally if there is more than one "
            "helper) plus a certificate. So do not hesitate to help an injured person or take them to a "
            "hospital."
        ),
        "note_hi": _SCHEME_NOTE_HI,
        "note_en": _SCHEME_NOTE_EN,
        "source": "Motor Vehicles Act §134A + rules GSR 594(E) (29 Sep 2020); MoRTH Rah-Veer Yojana (in force 21 Apr 2025).",
    },
    "hit_and_run_compensation": {
        "labels": [
            "hit and run", "car fled", "vehicle ran away", "unknown vehicle compensation",
            "compensation if driver escaped",
        ],
        "hi": (
            "अगर कोई वाहन दुर्घटना करके भाग जाए और दोषी वाहन की पहचान न हो पाए, तो 'हिट-एंड-रन मोटर "
            "दुर्घटना पीड़ित मुआवज़ा योजना, 2022' के तहत मौत पर दो लाख रुपये और गंभीर चोट पर पचास हज़ार "
            "रुपये का मुआवज़ा, मोटर वाहन दुर्घटना कोष से दिया जाता है। मौत की स्थिति में पीड़ित का परिवार, "
            "या घायल व्यक्ति ख़ुद, आवेदन कर सकता है। इसके लिए पुलिस में दुर्घटना की रिपोर्ट (FIR) दर्ज होना "
            "ज़रूरी है; दावा पुलिस/ज़िला प्रशासन और जनरल इंश्योरेंस काउंसिल के ज़रिए आगे बढ़ता है।"
        ),
        "en": (
            "If a vehicle causes a road accident and then flees, and the offending vehicle cannot be "
            "identified, the Compensation to Victims of Hit-and-Run Motor Accidents Scheme, 2022 "
            "provides ₹2,00,000 for a death and ₹50,000 for a grievous injury, paid from the Motor "
            "Vehicle Accident Fund. The victim's family (in case of death) or the injured person can "
            "apply. A police report (FIR) of the accident is needed; the claim is processed through the "
            "police / district administration and the General Insurance Council."
        ),
        "note_hi": _SCHEME_NOTE_HI,
        "note_en": _SCHEME_NOTE_EN,
        "source": "MoRTH — Compensation to Victims of Hit-and-Run Motor Accidents Scheme, 2022 (effective 1 Apr 2022); Motor Vehicle Accident Fund.",
    },
    "ayushman_bharat": {
        "labels": [
            "ayushman bharat", "ayushman card", "pm-jay", "5 lakh health cover",
            "free hospital treatment", "senior citizen health cover",
        ],
        "hi": (
            "आयुष्मान भारत पीएम-जय के तहत पात्र परिवारों को नामित सरकारी और निजी अस्पतालों में प्रति "
            "परिवार, प्रति वर्ष पाँच लाख रुपये तक का कैशलेस इलाज मिलता है। यह आर्थिक रूप से कमज़ोर परिवारों "
            "(सरकार की पात्रता सूची के अनुसार) को कवर करता है, और 2024 से — आय चाहे जो भी हो — 70 वर्ष और "
            "उससे अधिक उम्र के सभी बुज़ुर्गों को 'आयुष्मान वय वंदना कार्ड' के ज़रिए कवर करता है। अपनी पात्रता "
            "और नज़दीकी नामित अस्पताल आप पीएम-जय पोर्टल पर या 14555 पर कॉल करके देख सकते हैं। ध्यान दें — "
            "सड़क दुर्घटना के इलाज के लिए ऊपर बताई कैशलेस/पीएम-राहत योजना अलग है और उसके लिए आयुष्मान कार्ड "
            "ज़रूरी नहीं।"
        ),
        "en": (
            "Ayushman Bharat PM-JAY gives eligible families cashless hospital treatment up to ₹5 lakh "
            "per family per year at empanelled government and private hospitals. It covers economically "
            "weaker families (as per the government's eligibility list) and — since 2024 — ALL citizens "
            "aged 70 and above regardless of income, through the Ayushman Vay Vandana Card. You can check "
            "your eligibility and find empanelled hospitals on the PM-JAY portal or by calling 14555. "
            "Note: the cashless / PM-RAHAT road-accident scheme above is separate, and an Ayushman card "
            "is not required for it."
        ),
        "note_hi": _SCHEME_NOTE_HI,
        "note_en": _SCHEME_NOTE_EN,
        "source": "National Health Authority — Ayushman Bharat PM-JAY; Cabinet decision Sept 2024 (70+ Ayushman Vay Vandana).",
    },
    "insurance_claim": {
        "labels": [
            "insurance claim", "how to claim insurance", "bima claim", "should I file FIR",
            "car damage claim", "cashless garage",
        ],
        "hi": (
            "दुर्घटना के बाद मोटर-बीमा दावे के लिए: (1) जल्द से जल्द, आम तौर पर 24–48 घंटे के भीतर अपनी बीमा "
            "कंपनी को सूचित करें; (2) किसी भी गंभीर दुर्घटना — जिसमें चोट, मौत, या तीसरे पक्ष/संपत्ति का "
            "नुक़सान हो — के लिए पुलिस में एफ़आईआर दर्ज कराएँ (सिर्फ़ अपनी गाड़ी की मामूली खरोंच के लिए शायद "
            "ज़रूरत न पड़े); (3) घटनास्थल और नुक़सान की तस्वीरें लें, और दूसरी गाड़ी का नंबर नोट करें; (4) दावा "
            "फ़ॉर्म के साथ पॉलिसी, लाइसेंस, आरसी और एफ़आईआर जमा करें। आप नेटवर्क गैराज में कैशलेस मरम्मत चुन "
            "सकते हैं, या ख़ुद भुगतान करके बाद में रीइम्बर्समेंट का दावा कर सकते हैं; एक सर्वेयर नुक़सान का आकलन "
            "करेगा। मौक़े पर किसकी ग़लती थी — इस बारे में कोई काग़ज़ न लिखें, न ही ज़बानी मान लें; इसका आकलन "
            "पुलिस और बीमा कंपनी को करने दें।"
        ),
        "en": (
            "For a motor-insurance claim after an accident: (1) inform your insurer as soon as possible, "
            "generally within 24–48 hours; (2) file a police FIR for any serious accident — one involving "
            "injury, death, or third-party / property damage (a minor own-damage scratch may not need "
            "one); (3) take photos of the scene and the damage, and note the other vehicle's number; "
            "(4) submit the claim form with your policy, licence, RC and the FIR. You can choose a "
            "cashless repair at a network garage, or pay yourself and claim reimbursement later; a "
            "surveyor will assess the damage. Do not sign anything or admit who was at fault on the spot "
            "— let the police and insurer assess it."
        ),
        "note_hi": "अपनी पॉलिसी की शर्तें अलग हो सकती हैं — ठीक-ठीक प्रक्रिया अपनी बीमा कंपनी से पुष्टि करें।",
        "note_en": "Your policy terms may differ — confirm the exact process with your own insurer.",
        "source": "General IRDAI / insurer motor-claim guidance.",
    },
    "move_vehicle": {
        "labels": [
            "should I move the vehicle", "move the car", "move injured person",
            "what to do after accident", "is it safe to move",
        ],
        "hi": (
            "सबसे पहले जगह को सुरक्षित करें: हैज़र्ड लाइटें चालू करें और अगर हो तो एक चेतावनी त्रिकोण "
            "(warning triangle) गाड़ी से काफ़ी पीछे रखें। जो व्यक्ति गंभीर रूप से घायल या बेहोश है, उसे तब "
            "तक न हिलाएँ जब तक कोई तुरंत ख़तरा (जैसे आग) न हो — हिलाने से रीढ़ की चोट बिगड़ सकती है; "
            "एम्बुलेंस का इंतज़ार करें। गाड़ी के लिए: अगर मामूली दुर्घटना है और कोई घायल नहीं है, तो गाड़ी "
            "को सड़क किनारे कर लें ताकि ट्रैफ़िक न रुके या ख़तरा न बने — पर पहले बीमा के लिए उसकी स्थिति की "
            "कुछ तस्वीरें ले लें। अगर चोटें या भारी नुक़सान है, तो गाड़ियों को वहीं रहने दें और पुलिस को "
            "घटनास्थल दर्ज करने दें।"
        ),
        "en": (
            "First make the scene safe: turn on the hazard lights and, if you have one, place a warning "
            "triangle well behind the vehicle. Do NOT move anyone who is seriously injured or unconscious "
            "unless they are in immediate danger (like fire) — moving them can worsen a spinal injury; "
            "wait for the ambulance. For the vehicle: if it is only a minor accident with no injuries, "
            "move it to the roadside so it does not block or endanger traffic — but first take a few "
            "photos of the position for insurance. If there are injuries or serious damage, leave the "
            "vehicles where they are and let the police document the scene."
        ),
        "note_hi": "",
        "note_en": "",
        "source": "General road-safety guidance (MoRTH / traffic-police advisories).",
    },
    "night_safety": {
        "labels": [
            "accident at night", "breakdown at night", "who to call at night",
            "stuck on highway at night", "night safety",
        ],
        "hi": (
            "अगर रात में हाईवे पर दुर्घटना या गाड़ी ख़राब हो जाए: तुरंत हैज़र्ड लाइटें चालू करें, और अगर हो "
            "सके तो गाड़ी से लगभग 15–20 मीटर पीछे एक रिफ़्लेक्टिव चेतावनी त्रिकोण रखें। ख़ुद को और बाक़ी लोगों "
            "को सड़क से हटाकर, क्रैश बैरियर के पीछे, चलते ट्रैफ़िक से दूर ले जाएँ — कभी ट्रैफ़िक लेन में खड़े न "
            "हों। अँधेरे में सड़क पर बड़ी मरम्मत की कोशिश न करें। मदद के लिए 1033 हाईवे हेल्पलाइन पर कॉल करें; "
            "अगर कोई घायल है तो एम्बुलेंस माँगें। अपने फ़ोन की लोकेशन चालू रखें ताकि मदद आप तक जल्दी पहुँच सके।"
        ),
        "en": (
            "If you have an accident or a breakdown at night on the highway: switch on the hazard lights "
            "immediately, and if you can, place a reflective warning triangle about 15–20 metres behind "
            "the vehicle. Move yourself and everyone else off the carriageway, behind the crash barrier, "
            "away from moving traffic — never stand in a traffic lane. Do not attempt major repairs on "
            "the road in the dark. Call the 1033 highway helpline for help; if anyone is injured, ask for "
            "an ambulance. Keep your phone's location on so help can reach you quickly."
        ),
        "note_hi": "",
        "note_en": "",
        "source": "General highway-safety guidance (1033 helpline / NHAI advisories).",
    },
}

# Stable ordering for the tool `topic` enum + the system-prompt topic list.
INFO_TOPICS: list[str] = list(KNOWLEDGE_BASE.keys())


def lookup_info(topic: str) -> Optional[dict]:
    """Return the KB entry for a topic key, or None if unknown. Never fabricates."""
    if not topic:
        return None
    return KNOWLEDGE_BASE.get(topic.strip())


def describe_topics() -> str:
    """One-line 'key — what it covers' summary per topic, for the system prompt."""
    return "\n".join(
        f"- {key}: {', '.join(entry['labels'][:3])}"
        for key, entry in KNOWLEDGE_BASE.items()
    )
