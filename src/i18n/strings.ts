// i18n string map for Transport Sahayak
// Languages: EN (English), HI (Hindi / Devanagari)

export type Locale = "EN" | "HI";

export const strings = {
  // ── App shell ────────────────────────────────────────────────────────────────
  appName:    { EN: "Transport Sahayak",     HI: "ट्रांसपोर्ट सहायक",        AS: "ট্ৰান্সপ'ৰ্ট সহায়ক" },
  appTagline: { EN: "Delhi–Dehradun Expressway — Road Accident First Response",
                HI: "दिल्ली–देहरादून एक्सप्रेसवे — सड़क दुर्घटना प्रथम प्रतिक्रिया",
                AS: "দিল্লী–দেহৰাদূন এক্সপ্ৰেছৱে — পথ দুৰ্ঘটনা প্ৰথম সাড়া" },
  mapSources: { EN: "Map: Google Maps · POI: Google Places · Routing: Google Routes API",
                HI: "मानचित्र: Google Maps · POI: Google Places · मार्ग: Google Routes API",
                AS: "মানচিত্ৰ: Google Maps · POI: Google Places · পথ: Google Routes API" },

  // ── Banners ──────────────────────────────────────────────────────────────────
  sampleDataBanner: {
    EN: "⚠ Ambulance stations · Hotspots · Potholes: sample data — replace with official dataset.",
    HI: "⚠ एम्बुलेंस · हॉटस्पॉट · गड्ढे: नमूना डेटा — आधिकारिक डेटासेट से बदलें।",
    AS: "⚠ এম্বুলেন্স · হটস্পট · গাঁত: নমুনা তথ্য — চৰকাৰী তথ্যসমূহেৰে সলনি কৰক।",
  },
  googlePoweredNote: {
    EN: "Service markers: Google Places",
    HI: "सेवा मार्कर: Google Places",
    AS: "সেৱা মাৰ্কাৰ: Google Places",
  },
  placesLoadError: {
    EN: "Google Places unavailable — check GOOGLE_MAPS_SERVER_KEY",
    HI: "Google Places अनुपलब्ध — GOOGLE_MAPS_SERVER_KEY जांचें",
    AS: "Google Places অনুপলব্ধ — GOOGLE_MAPS_SERVER_KEY পৰীক্ষা কৰক",
  },

  // ── Map tabs ─────────────────────────────────────────────────────────────────
  tabServices:  { EN: "Services",   HI: "सेवाएं",        AS: "সেৱাসমূহ" },
  tabAccidents: { EN: "Accidents",  HI: "दुर्घटनाएं",    AS: "দুৰ্ঘটনাসমূহ" },

  // ── Layer filter chips ───────────────────────────────────────────────────────
  layerHospitals:   { EN: "Hospitals",           HI: "अस्पताल",              AS: "চিকিৎসালয়" },
  layerAmbulance:   { EN: "Ambulance Stations",  HI: "एम्बुलेंस केंद्र",    AS: "এম্বুলেন্স কেন্দ্ৰ" },
  layerMechanics:   { EN: "Mechanics",           HI: "मैकेनिक",              AS: "মেকানিক" },
  layerPolice:      { EN: "Police Stations",     HI: "पुलिस थाने",           AS: "আৰক্ষী থানা" },
  layerPharmacy:    { EN: "Pharmacies",          HI: "फ़ार्मेसी",             AS: "ঔষধালয়" },
  layerFuel:        { EN: "Fuel Stations",       HI: "ईंधन केंद्र",           AS: "ইন্ধন কেন্দ্ৰ" },
  layerBlackspots:  { EN: "Accident Hotspots",   HI: "दुर्घटना क्षेत्र",    AS: "দুৰ্ঘটনা অঞ্চল" },
  layerPotholes:    { EN: "Road Defects / Potholes", HI: "सड़क दोष / गड्ढे", AS: "পথৰ ত্ৰুটি / গাঁত" },
  layerReportedAccidents: { EN: "Reported Accidents", HI: "रिपोर्ट की गई दुर्घटनाएं", AS: "প্ৰতিবেদিত দুৰ্ঘটনা" },

  // ── Report panel ─────────────────────────────────────────────────────────────
  reportTitle:      { EN: "Report Incident",           HI: "घटना रिपोर्ट करें",       AS: "ঘটনা প্ৰতিবেদন দিয়ক" },
  reportModeSOS:    { EN: "🚨 SOS",                    HI: "🚨 SOS",                   AS: "🚨 SOS" },
  reportModeText:   { EN: "📝 Text",                   HI: "📝 लिखित",                 AS: "📝 লিখন" },
  reportModeVoice:  { EN: "🎙 Voice",                  HI: "🎙 मौखिक",                 AS: "🎙 কণ্ঠ" },
  reportLocation:   { EN: "Incident Location",         HI: "घटना स्थान",               AS: "ঘটনাৰ স্থান" },
  reportLocRequired:{ EN: "Tap here, then tap map to set location",
                      HI: "यहाँ टैप करें, फिर मानचित्र पर स्थान चुनें",
                      AS: "ইয়াত টেপ কৰক, তাৰপিছত মানচিত্ৰত স্থান নিৰ্বাচন কৰক" },
  reportDescription:{ EN: "Description",              HI: "विवरण",                    AS: "বিৱৰণ" },
  reportDescPlaceholder: {
    EN: "Describe the incident — vehicles, visible injuries, road conditions…",
    HI: "घटना का विवरण दें — वाहन, दृश्यमान चोटें, सड़क की स्थिति…",
    AS: "ঘটনাৰ বিৱৰণ দিয়ক — যানবাহন, দৃশ্যমান আঘাত, পথৰ অৱস্থা…",
  },
  reportPersons:    { EN: "Estimated Persons Involved", HI: "अनुमानित व्यक्ति",        AS: "আনুমানিক ব্যক্তি" },
  reportConditions: { EN: "Observed Conditions",        HI: "देखी गई स्थितियां",       AS: "পৰ্যবেক্ষিত অৱস্থা" },
  reportSubmit:     { EN: "Submit & Assess Severity",   HI: "सबमिट करें",              AS: "দাখিল কৰক" },
  reportNeedPin:    { EN: "Set location to submit",     HI: "सबमिट करने के लिए स्थान सेट करें", AS: "দাখিলৰ বাবে স্থান নিৰ্ধাৰণ কৰক" },

  // Quick flags
  flagConscious:    { EN: "Conscious",       HI: "होश में",              AS: "সচেতন" },
  flagBreathing:    { EN: "Breathing",       HI: "सांस ले रहा है",       AS: "শ্বাস লৈছে" },
  flagTrapped:      { EN: "Trapped",         HI: "फंसा हुआ",             AS: "আবদ্ধ" },
  flagBleeding:     { EN: "Heavy bleeding",  HI: "अत्यधिक रक्तस्राव",   AS: "অত্যধিক ৰক্তক্ষৰণ" },

  // SOS
  sosWhatDoes:       { EN: "What SOS does",       HI: "SOS क्या करता है",      AS: "SOS-এ কি কৰে" },
  sosWhatDoesNot:    { EN: "What SOS does NOT do", HI: "SOS क्या नहीं करता",   AS: "SOS-এ কি নকৰে" },
  sosSendButton:     { EN: "Send SOS",             HI: "SOS भेजें",             AS: "SOS পঠাওক" },

  // Voice
  voiceLanguage:     { EN: "Recognition Language", HI: "भाषा",                  AS: "ভাষা" },
  voiceRecording:    { EN: "Recording — tap to stop", HI: "रिकॉर्डिंग — रोकने के लिए टैप करें", AS: "ৰেকৰ্ডিং — বন্ধৰ বাবে টেপ কৰক" },
  voiceTapToStart:   { EN: "Tap to start recording",  HI: "रिकॉर्डिंग शुरू करने के लिए टैप करें", AS: "ৰেকৰ্ডিং আৰম্ভৰ বাবে টেপ কৰক" },
  voiceTranscript:   { EN: "Live Transcript",     HI: "लाइव ट्रांसक्रिप्ट",   AS: "লাইভ ট্ৰান্সক্ৰিপ্ট" },
  voiceClear:        { EN: "Clear transcript",    HI: "ट्रांसक्रिप्ट साफ करें", AS: "ট্ৰান্সক্ৰিপ্ট মচক" },

  // ── Assessment ───────────────────────────────────────────────────────────────
  assessTitle:      { EN: "Severity Assessment",    HI: "गंभीरता मूल्यांकन",     AS: "গুৰুত্ব নিৰ্ধাৰণ" },
  assessAssessing:  { EN: "Assessing severity…",   HI: "गंभीरता का आकलन…",      AS: "গুৰুত্ব নিৰ্ধাৰণ হৈছে…" },
  assessRationale:  { EN: "Rationale",             HI: "कारण",                   AS: "যুক্তি" },
  assessRecommended:{ EN: "Recommended Response",  HI: "अनुशंसित प्रतिक्रिया",   AS: "পৰামৰ্শিত সাড়া" },
  assessSourceAI:   { EN: "AI assessment",         HI: "AI मूल्यांकन",           AS: "AI নিৰ্ধাৰণ" },
  assessSourceHeuristic: { EN: "Heuristic fallback", HI: "नियम-आधारित",         AS: "নিয়ম-আধাৰিত" },
  assessVerify:     { EN: "Operator should verify before acting",
                      HI: "कार्रवाई से पहले ऑपरेटर को सत्यापित करना चाहिए",
                      AS: "কাৰ্য গ্ৰহণৰ আগতে অপাৰেটৰে যাচাই কৰিব লাগিব" },
  assessCreated:    { EN: "Incident",               HI: "घटना",                   AS: "ঘটনা" },
  assessCreatedSuffix: { EN: "created and logged", HI: "दर्ज और लॉग किया",       AS: "সৃষ্টি আৰু লগ কৰা হৈছে" },

  // Severity labels
  sev1: { EN: "Minor",    HI: "मामूली",     AS: "সামান্য" },
  sev2: { EN: "Low",      HI: "कम",         AS: "কম" },
  sev3: { EN: "Moderate", HI: "मध्यम",      AS: "মধ্যম" },
  sev4: { EN: "Serious",  HI: "गंभीर",      AS: "গুৰুতৰ" },
  sev5: { EN: "Critical", HI: "अति गंभीर",  AS: "অতি গুৰুতৰ" },

  // Priority labels
  priLow:      { EN: "LOW PRIORITY",      HI: "कम प्राथमिकता",         AS: "কম অগ্ৰাধিকাৰ" },
  priMedium:   { EN: "MEDIUM PRIORITY",   HI: "मध्यम प्राथमिकता",      AS: "মধ্যম অগ্ৰাধিকাৰ" },
  priHigh:     { EN: "HIGH PRIORITY",     HI: "उच्च प्राथमिकता",       AS: "উচ্চ অগ্ৰাধিকাৰ" },
  priCritical: { EN: "CRITICAL PRIORITY", HI: "अति उच्च प्राथमिकता",   AS: "অতি উচ্চ অগ্ৰাধিকাৰ" },

  // ── Matching ─────────────────────────────────────────────────────────────────
  matchHospitals:   { EN: "Matched Hospitals",          HI: "मिलान अस्पताल",               AS: "মিলোৱা চিকিৎসালয়" },
  matchRankSubtitle:{ EN: "proximity + trauma + specialty", HI: "निकटता + ट्रॉमा + विशेषज्ञता", AS: "নিকটতা + ট্ৰমা + বিশেষজ্ঞতা" },
  matchPolice:      { EN: "Nearest Police Station",     HI: "निकटतम पुलिस थाना",            AS: "নিকটতম আৰক্ষী থানা" },
  matchBeds:        { EN: "Beds available: Awaiting hospital capacity feed",
                      HI: "उपलब्ध बेड: अस्पताल क्षमता डेटा की प्रतीक्षा",
                      AS: "উপলব্ধ বিছনা: চিকিৎসালয়ৰ ক্ষমতাৰ তথ্যৰ অপেক্ষা" },
  matchRoutesOnMap: { EN: "Routes on map",              HI: "मानचित्र पर मार्ग",             AS: "মানচিত্ৰত পথ" },
  matchFreeFlow:    { EN: "Est. drive time from facility, current traffic — vehicle leaving now. We do not track ambulances.",
                      HI: "सुविधा से अनुमानित ड्राइव समय, वर्तमान ट्रैफ़िक — अभी निकलने वाला वाहन। हम एम्बुलेंस ट्रैक नहीं करते।",
                      AS: "সুবিধাৰ পৰা আনুমানিক ড্ৰাইভ সময়, বৰ্তমান ট্ৰেফিক — এতিয়াই ওলোৱা বাহন। আমি এম্বুলেন্স ট্ৰেক নকৰো।" },
  matchFreeFlowShort: { EN: "Current traffic · vehicle leaving now · we do not track ambulances",
                         HI: "वर्तमान ट्रैफ़िक · अभी निकलने वाला वाहन · हम एम्बुलेंस ट्रैक नहीं करते",
                         AS: "বৰ্তমান ট্ৰেফিক · এতিয়াই ওলোৱা বাহন · এম্বুলেন্স ট্ৰেক নকৰো" },
  matchOsrmLoading: { EN: "Computing traffic-aware drive times (Routes API)…",
                      HI: "ट्रैफ़िक-जागरूक ड्राइव समय की गणना हो रही है (Routes API)…",
                      AS: "ট্ৰাফিক-সচেতন ড্ৰাইভ সময় গণনা কৰা হৈছে (Routes API)…" },
  matchIncidentAt:  { EN: "Incident location",  HI: "घटना स्थान",    AS: "ঘটনাৰ স্থান" },
  matchNearestPS:   { EN: "Nearest PS",         HI: "निकटतम थाना",   AS: "নিকটতম থানা" },

  // ── Dispatch ─────────────────────────────────────────────────────────────────
  dispatchTitle:    { EN: "Dispatch Alert",          HI: "डिस्पैच अलर्ट",               AS: "প্ৰেৰণ সতৰ্কতা" },
  dispatchPreview:  { EN: "Preview & Send Alert",    HI: "पूर्वावलोकन और भेजें",         AS: "পৰ্যালোচনা আৰু পঠাওক" },
  dispatchConfirm:  { EN: "Confirm & Log Alert",     HI: "पुष्टि करें और लॉग करें",      AS: "নিশ্চিত কৰক আৰু লগ কৰক" },
  dispatchCancel:   { EN: "Cancel",                  HI: "रद्द करें",                    AS: "বাতিল কৰক" },
  dispatchBack:     { EN: "← Back",                  HI: "← वापस",                       AS: "← উভতি যাওক" },
  dispatchSent:     { EN: "2 notifications logged",  HI: "2 सूचनाएं लॉग की गईं",         AS: "2 জাননী লগ কৰা হৈছে" },
  dispatchAck:      { EN: "Awaiting acknowledgement", HI: "पावती की प्रतीक्षा",           AS: "স্বীকৃতিৰ অপেক্ষা" },
  dispatchAckBody:  {
    EN: "this field is filled by the deployed production system when the recipient responds. No acknowledgement has been received or simulated.",
    HI: "यह फ़ील्ड तैनात प्रोडक्शन सिस्टम द्वारा भरी जाती है जब प्राप्तकर्ता प्रतिक्रिया देता है।",
    AS: "এই ক্ষেত্ৰখন মোতায়েন প্ৰডাকচন চিষ্টেমে পূৰণ কৰে যেতিয়া প্ৰাপকে সাড়া দিয়ে।",
  },
  dispatchChannel:  { EN: "SMS / Push Notification", HI: "SMS / पुश नोटिफिकेशन",        AS: "SMS / পুছ জাননী" },
  dispatchHospital: { EN: "Hospital alert",          HI: "अस्पताल अलर्ट",               AS: "চিকিৎসালয় সতৰ্কতা" },
  dispatchPolice:   { EN: "Police alert",            HI: "पुलिस अलर्ट",                 AS: "আৰক্ষী সতৰ্কতা" },
  dispatchShowMsg:  { EN: "Show exact message text", HI: "संदेश टेक्स्ट देखें",          AS: "বাৰ্তাৰ লিখন চাওক" },
  dispatchHideMsg:  { EN: "Hide message text",       HI: "संदेश टेक्स्ट छुपाएं",         AS: "বাৰ্তাৰ লিখন লুকুৱাওক" },
  dispatchHospitalNotified: { EN: "Hospital notified", HI: "अस्पताल को सूचित किया",     AS: "চিকিৎসালয়ক জনোৱা হৈছে" },
  dispatchPoliceNotified:   { EN: "Police notified",   HI: "पुलिस को सूचित किया",        AS: "আৰক্ষীক জনোৱা হৈছে" },

  // ── Deduplication ────────────────────────────────────────────────────────────
  dedupTitle:       { EN: "Possible duplicate",      HI: "संभावित डुप्लिकेट",           AS: "সম্ভাব্য পুনৰাবৃত্তি" },
  dedupExisting:    { EN: "Existing incident",       HI: "मौजूदा घटना",                 AS: "বিদ্যমান ঘটনা" },
  dedupUseExisting: { EN: "Use existing incident",   HI: "मौजूदा घटना का उपयोग करें",   AS: "বিদ্যমান ঘটনা ব্যৱহাৰ কৰক" },
  dedupProceed:     { EN: "It's a different event — log separately",
                      HI: "यह एक अलग घटना है — अलग से दर्ज करें",
                      AS: "এইটো পৃথক ঘটনা — পৃথকে লগ কৰক" },

  // ── Timeline ─────────────────────────────────────────────────────────────────
  timelineTitle:    { EN: "Orchestration Timeline",  HI: "समन्वय समयरेखा",              AS: "সমন্বয় সময়ৰেখা" },
  timelineSubtitle: { EN: "Actions this system actually performs, in order",
                      HI: "वे कार्य जो यह सिस्टम वास्तव में करता है, क्रम में",
                      AS: "এই ব্যৱস্থাই প্ৰকৃততে কৰা কাৰ্যসমূহ, ক্ৰমে" },
  timelineEmpty:    { EN: "No events logged yet",    HI: "अभी कोई ईवेंट लॉग नहीं",     AS: "এতিয়ালৈকে কোনো ঘটনা লগ কৰা হোৱা নাই" },
  timelineEmptyDesc:{ EN: "Submit a report using the button on the map. Steps will appear here in real time.",
                      HI: "मानचित्र पर बटन का उपयोग करके रिपोर्ट सबमिट करें। चरण यहाँ वास्तविक समय में दिखाई देंगे।",
                      AS: "মানচিত্ৰৰ বুটামেৰে প্ৰতিবেদন দাখিল কৰক। পদক্ষেপসমূহ ইয়াত ৰিয়েল টাইমত দেখা যাব।" },
  timelineSteps:    { EN: "steps",   HI: "चरण",      AS: "পদক্ষেপ" },
  timelineViewRecord:    { EN: "View record",          HI: "रिकॉर्ड देखें",              AS: "তথ্য চাওক" },
  timelineSysBoundary:  { EN: "System boundary",       HI: "सिस्टम सीमा",               AS: "ব্যৱস্থাৰ সীমা" },
  timelineBoundaryBody: {
    EN: "Each step above is an action this system actually performs. Field steps — ambulance dispatch confirmation, crew movement, on-scene arrival — are not shown because they require field infrastructure (GPS-equipped vehicles, in-vehicle terminals) not yet in place.",
    HI: "ऊपर प्रत्येक चरण एक वास्तविक कार्य है। फील्ड चरण — एम्बुलेंस डिस्पैच पुष्टि, दल आवाजाही, घटनास्थल पर आगमन — नहीं दिखाए जाते क्योंकि उनके लिए बुनियादी ढांचा अभी उपलब्ध नहीं है।",
    AS: "ওপৰৰ প্ৰতিটো পদক্ষেপ এই ব্যৱস্থাই কৰা প্ৰকৃত কাৰ্য। ক্ষেত্ৰৰ পদক্ষেপ — এম্বুলেন্স প্ৰেৰণ নিশ্চিতকৰণ, দলৰ গতিবিধি, স্থলত আগমন — দেখুওৱা নহয় কাৰণ সেইবোৰৰ বাবে প্ৰয়োজনীয় আধাৰভূমি এতিয়াও নাই।",
  },
  timelineAlertNote: {
    EN: "\"Alert sent\" means a notification record was created and the message text was generated. Production delivery is via SMS gateway or push notification. No acknowledgement or crew status is implied.",
    HI: "\"अलर्ट भेजा\" का अर्थ है कि एक अधिसूचना रिकॉर्ड बनाया गया। प्रोडक्शन डिलीवरी SMS गेटवे या पुश नोटिफिकेशन के माध्यम से होती है।",
    AS: "\"সতৰ্কতা পঠোৱা\" মানে এটা জাননী তথ্য সৃষ্টি কৰা হৈছে। প্ৰডাকচন ডেলিভাৰী SMS গেটৱে বা পুছ জাননীৰ জৰিয়তে।",
  },

  // Step labels
  stepCreated:    { EN: "Incident created",   HI: "घटना दर्ज",            AS: "ঘটনা সৃষ্টি" },
  stepAssessed:   { EN: "Severity assessed",  HI: "गंभीरता आकलन",         AS: "গুৰুত্ব নিৰ্ধাৰিত" },
  stepMatched:    { EN: "Hospital matched",   HI: "अस्पताल मिलाया",       AS: "চিকিৎসালয় মিলোৱা" },
  stepRouted:     { EN: "Route estimated",    HI: "मार्ग अनुमानित",       AS: "পথ অনুমান" },
  stepAlerted:    { EN: "Alert sent",         HI: "अलर्ट भेजा",           AS: "সতৰ্কতা পঠোৱা" },
  stepDuplicate:  { EN: "Duplicate check",    HI: "डुप्लिकेट जांच",       AS: "পুনৰাবৃত্তি পৰীক্ষা" },
  dupSkippedLabel:{ EN: "Duplicate — skipped", HI: "डुप्लिकेट — छोड़ा",  AS: "পুনৰাবৃত্তি — এৰা" },

  // ── Incident record view ──────────────────────────────────────────────────────
  recordTitle:      { EN: "Incident Record",    HI: "घटना रिकॉर्ड",           AS: "ঘটনাৰ তথ্য" },
  recordGenerated:  { EN: "Generated",          HI: "उत्पन्न",                 AS: "সৃষ্টি" },
  recordPrint:      { EN: "Print",              HI: "प्रिंट",                  AS: "প্ৰিন্ট" },
  recordExport:     { EN: "Export text",        HI: "टेक्स्ट निर्यात",         AS: "লিখন ৰপ্তানি" },
  recordViewFull:   { EN: "View full record",   HI: "पूरा रिकॉर्ड देखें",      AS: "সম্পূৰ্ণ তথ্য চাওক" },
  recordSectionLocation:   { EN: "Location",           HI: "स्थान",             AS: "স্থান" },
  recordSectionReport:     { EN: "Report",             HI: "रिपोर्ट",           AS: "প্ৰতিবেদন" },
  recordSectionAssessment: { EN: "Severity Assessment", HI: "गंभीरता मूल्यांकन", AS: "গুৰুত্ব নিৰ্ধাৰণ" },
  recordSectionHospital:   { EN: "Matched Hospital",   HI: "मिलान अस्पताल",    AS: "মিলোৱা চিকিৎসালয়" },
  recordSectionPolice:     { EN: "Nearest Police Station", HI: "निकटतम पुलिस थाना", AS: "নিকটতম আৰক্ষী থানা" },
  recordSectionRoutes:     { EN: "Route Estimates",    HI: "मार्ग अनुमान",      AS: "পথ অনুমান" },
  recordSectionAlerts:     { EN: "Alerts Sent",        HI: "भेजे गए अलर्ट",    AS: "পঠোৱা সতৰ্কতা" },
  recordSectionEventLog:   { EN: "Event Log",          HI: "ईवेंट लॉग",         AS: "ঘটনা লগ" },
  recordSectionNotes:      { EN: "System Notes",       HI: "सिस्टम नोट्स",      AS: "ব্যৱস্থাৰ টোকা" },
  recordMode:     { EN: "Mode",           HI: "मोड",            AS: "পদ্ধতি" },
  recordReported: { EN: "Reported",       HI: "रिपोर्ट किया",   AS: "প্ৰতিবেদন" },
  recordPersons:  { EN: "Persons",        HI: "व्यक्ति",         AS: "ব্যক্তি" },
  recordFlags:    { EN: "Flags",          HI: "झंडे",            AS: "চিহ্ন" },
  recordGPS:      { EN: "GPS",            HI: "GPS",             AS: "GPS" },
  recordEstimate: { EN: "Est. road distance", HI: "अनुमानित दूरी", AS: "আনুমানিক দূৰত্ব" },
  recordDriveTime:{ EN: "Est. drive time",    HI: "अनुमानित समय",  AS: "আনুমানিক সময়" },
  recordNotAvail: { EN: "Not reported",   HI: "रिपोर्ट नहीं",   AS: "প্ৰতিবেদন নাই" },
  recordNoneYet:  { EN: "Not yet available", HI: "अभी उपलब्ध नहीं", AS: "এতিয়ালৈকে উপলব্ধ নহয়" },
  recordSystemNotes: {
    EN: "Generated by Transport Sahayak (Proof of Concept). Drive times are traffic-aware estimates from Google Routes API (vehicle leaving facility now) — not a tracked ETA. We do not track ambulances or police vehicles. Bed availability requires a live hospital capacity feed. Acknowledgement status is an open field filled by the production system. Field steps (crew movement, on-scene arrival) are not tracked — requires GPS-equipped vehicle infrastructure not yet deployed.",
    HI: "Transport Sahayak (PoC) द्वारा उत्पन्न। ड्राइव समय Google Routes API से ट्रैफ़िक-जागरूक अनुमान हैं — ट्रैक किया हुआ ETA नहीं। बेड उपलब्धता के लिए लाइव हॉस्पिटल फीड आवश्यक है।",
    AS: "Transport Sahayak (PoC)-ৰ দ্বাৰা সৃষ্টি। ড্ৰাইভ সময় Google Routes API-ৰ পৰা ট্ৰাফিক-সচেতন অনুমান — ট্ৰেক কৰা ETA নহয়। বিছনাৰ উপলব্ধতাৰ বাবে লাইভ চিকিৎসালয়ৰ তথ্য প্ৰয়োজন।",
  },

  // ── Common ───────────────────────────────────────────────────────────────────
  close:          { EN: "Close",          HI: "बंद करें",         AS: "বন্ধ কৰক" },
  cancel:         { EN: "Cancel",         HI: "रद्द करें",        AS: "বাতিল কৰক" },
  loading:        { EN: "Loading…",       HI: "लोड हो रहा है…",  AS: "লোড হৈছে…" },
  tryAgain:       { EN: "Try Again",      HI: "पुनः प्रयास करें", AS: "পুনৰ চেষ্টা কৰক" },
  freeFlow:       { EN: "current traffic", HI: "वर्तमान ट्रैफ़िक",  AS: "বৰ্তমান ট্ৰেফিক" },
  min:            { EN: "min",            HI: "मिनट",             AS: "মিনিট" },
  km:             { EN: "km",             HI: "किमी",             AS: "কিমি" },
  road:           { EN: "road",           HI: "सड़क",             AS: "পথ" },

  // Language names (shown in toggle)
  langEN:  { EN: "EN", HI: "EN", AS: "EN" },
  langHI:  { EN: "हि", HI: "हि", AS: "হি" },
  langAS:  { EN: "অ",  HI: "অ",  AS: "অ" },

  // Legacy keys (kept for backward compatibility)
  sampleDataLabel:     { EN: "Sample data — replace with official dataset.",
                         HI: "नमूना डेटा — आधिकारिक डेटासेट से बदलें।",
                         AS: "নমুনা তথ্য — চৰকাৰী তথ্যসমূহেৰে সলনি কৰক।" },
  routeEstimateDisclaimer: {
    EN: "Est. drive time from facility, current traffic — vehicle leaving now. We do not track ambulances or police vehicles.",
    HI: "सुविधा से अनुमानित ड्राइव समय, वर्तमान ट्रैफ़िक — अभी निकलने वाला वाहन। हम एम्बुलेंस या पुलिस वाहन ट्रैक नहीं करते।",
    AS: "সুবিধাৰ পৰা আনুমানিক ড্ৰাইভ সময়, বৰ্তমান ট্ৰেফিক — এতিয়াই ওলোৱা বাহন। আমি এম্বুলেন্স বা পুলিছ বাহন ট্ৰেক নকৰো।",
  },
} as const;

export type StringKey = keyof typeof strings;

// Pure function (for server components or utility use)
export function t(key: StringKey, locale: Locale = "EN"): string {
  const entry = strings[key] as Record<string, string>;
  return entry[locale] ?? entry["EN"] ?? key;
}
