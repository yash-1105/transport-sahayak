// TypeScript port of severity_engine/classifier.py — runs entirely in Next.js,
// no Python engine needed. Used by /api/categories, /api/categories/subtypes, /api/guess.

import INDEX_RAW from "../../severity_engine/data/accident_index.json";

interface IndexRecord {
  category: string;
  subType: string;
  cause: string;
  agencies: string[];
  baseSeverity: number;
}

const INDEX = INDEX_RAW as IndexRecord[];

// ── Stop words ────────────────────────────────────────────────────────────────

const STOP = new Set([
  "the", "a", "an", "of", "on", "to", "in", "by", "or", "and", "with", "at", "for",
  "collision", "crash", "strike", "accident", "situations", "vehicle", "road", "expressway",
  "highway", "vs", "from", "into", "off", "no", "high", "speed",
]);

const HINDI_STOP = new Set([
  "की", "का", "के", "में", "से", "पर", "एक", "और", "है", "हैं",
  "था", "थी", "हुआ", "हुई", "को", "ने", "यह", "वह", "कि", "जो", "भी", "तो",
]);

// ── Hindi → English normalization ─────────────────────────────────────────────
// Translates key Hindi accident terms to English equivalents before tokenising,
// so the existing English-indexed scoring still works for Hindi input.

const HINDI_TO_EN: [string, string][] = [
  ["टक्कर", "collision"],    ["दुर्घटना", "crash"],    ["हादसा", "accident"],
  ["पलटना", "overturn"],     ["पलट", "overturn"],      ["उलट", "rollover"],
  ["टकराना", "collision"],   ["टकरा", "collision"],
  ["आग", "fire"],             ["जलना", "fire"],         ["विस्फोट", "explosion"],
  ["ईंधन रिसाव", "fuel leak"],
  ["घायल", "injury"],        ["चोट", "injury"],        ["बेहोश", "unconscious"],
  ["फँसा", "trapped"],       ["फंसा", "trapped"],      ["खून", "blood"],
  ["मृत", "dead"],            ["हताहत", "casualty"],   ["जख्मी", "injury"],
  ["गाड़ी", "vehicle"],      ["वाहन", "vehicle"],      ["ट्रक", "truck"],
  ["बस", "bus"],              ["बाइक", "motorcycle"],   ["मोटरसाइकिल", "motorcycle"],
  ["टेंपो", "tempo"],        ["ऑटो", "auto rickshaw"],
  ["खराब", "breakdown"],     ["पंचर", "puncture"],     ["टायर", "tyre"],
  ["ब्रेक", "brake"],        ["इंजन", "engine"],       ["टायर फटा", "tyre burst"],
  ["गड्ढा", "pothole"],      ["बाढ़", "flood"],        ["भूस्खलन", "landslide"],
  ["पेड़ गिरा", "fallen tree"], ["पत्थर गिरा", "rockfall"],
  ["सड़क", "road"],          ["राजमार्ग", "highway"],
];

function normalizeHindi(text: string): string {
  let out = text;
  for (const [hi, en] of HINDI_TO_EN) out = out.replaceAll(hi, " " + en + " ");
  return out;
}

function tokens(text: string): string[] {
  return (text ?? "")
    .toLowerCase()
    .match(/[ऀ-ॿ]+|[a-z0-9]+/g)
    ?.filter((t) => t.length > 1 && !STOP.has(t) && !HINDI_STOP.has(t)) ?? [];
}

// ── Build keyword index once ──────────────────────────────────────────────────

interface KWEntry {
  record: IndexRecord;
  st: Set<string>; // subType tokens
  ct: Set<string>; // cause tokens
}

const KW: KWEntry[] = INDEX.map((rec) => ({
  record: rec,
  st: new Set(tokens(rec.subType)),
  ct: new Set(tokens(rec.cause)),
}));

// ── Category consolidation (mirrors Python CATEGORY_KEYWORDS) ─────────────────
// The JSON has 50 raw categories. These rules collapse them into 14 meaningful
// ones used in the UI. Order matters — first match wins.

const CATEGORY_KEYWORDS: [string, string[]][] = [
  ["Fire / Explosion",          ["fire", "bleve", "explosion", "flame", "ignit", "blast", "burning", "arson", "conflagration"]],
  ["Hazardous Material",        ["hazmat", "chemical", "acid", "radioactive", "toxic", "corrosive", "ammonia", "pesticide", "chlorine", "cryogenic", "biohazard", "lpg tank", "cng tank", "tanker spill", "gas leak", "carbon monoxide"]],
  ["Tunnel Incident",           ["tunnel"]],
  ["Medical Emergency",         ["cardiac", "stroke", "childbirth", "anaphylaxis", "overdose", "medical emergency", "seizure", "heart attack", "driver medical"]],
  ["Flood / Water",             ["flood", "waterlogged", "submerged", "water ingress", "drowning", "swept"]],
  ["Landslide / Rockfall",      ["landslide", "rockfall", "boulder", "mudslide", "cliff fall", "scree"]],
  ["Animal on Road",            ["animal", "cattle", "elephant", "leopard", "nilgai", "buffalo", "camel", "deer", "boar", "monkey", "dog on", "stray"]],
  ["Mechanical / Breakdown",    ["breakdown", "tyre burst", "tyre blowout", "brake fail", "engine fail", "stall", "puncture", "tow truck", "mechanical"]],
  ["Skid / Traction Loss",      ["skid", "aquaplaning", "black ice", "oil slick", "hydroplane"]],
  ["Crime / Security",          ["robbery", "theft", "carjack", "road rage assault", "terrorist", "brawl", "shooting", "murder", "hijack"]],
  ["Weather / Visibility",      ["fog", "dust storm", "hailstorm", "wildfire", "sun glare", "low visibility", "rain", "cyclone"]],
  ["Infrastructure / Structural", ["pothole", "crash barrier", "guardrail", "atms", "vms", "bridge collapse", "flyover collapse", "road surface"]],
  ["Pedestrian / Person on Road", ["pedestrian", "cyclist", "wrong-way", "suicide", "walker", "jogger"]],
  ["Vehicle Collision",         ["collision", "crash", "rear-end", "head-on", "side-swipe", "t-bone", "overturn", "rollover", "pile-up", "pileup"]],
];

function assignCategory(subType: string): string {
  const s = subType.toLowerCase();
  for (const [cat, keywords] of CATEGORY_KEYWORDS) {
    if (keywords.some((kw) => s.includes(kw))) return cat;
  }
  return "Other";
}

// ── Category map: subType → consolidated display category ─────────────────────

const CATEGORY_MAP = new Map<string, string>(
  INDEX.map((r) => [r.subType, assignCategory(r.subType)])
);

// ── Public API ────────────────────────────────────────────────────────────────

export function getCategories(): { category: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const cat of CATEGORY_MAP.values()) {
    counts.set(cat, (counts.get(cat) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([category, count]) => ({ category, count }))
    .sort((a, b) => b.count - a.count);
}

export function getSubtypes(category: string): string[] {
  return [...CATEGORY_MAP.entries()]
    .filter(([, cat]) => cat === category)
    .map(([subType]) => subType)
    .sort();
}

export interface GuessResult {
  subType: string | null;
  category: string | null;
  confidence: number;
  lowConfidence: boolean;
  candidates: { subType: string; category: string }[];
}

export function guess(description: string): GuessResult {
  const dt = new Set(tokens(normalizeHindi(description)));
  if (dt.size === 0) {
    return { subType: null, category: null, confidence: 0, lowConfidence: true, candidates: [] };
  }

  const scored: { score: number; record: IndexRecord }[] = [];
  for (const { record, st, ct } of KW) {
    const score = 2 * intersect(dt, st) + intersect(dt, ct);
    if (score > 0) scored.push({ score, record });
  }

  if (scored.length === 0) {
    return { subType: null, category: null, confidence: 0, lowConfidence: true, candidates: [] };
  }

  scored.sort((a, b) => b.score - a.score);
  const top = scored[0].score;
  const runner = scored[1]?.score ?? 0;
  const confidence = top / (top + runner + 1);
  const best = scored[0].record;
  const candidates = scored.slice(0, 3).map((s) => ({
    subType: s.record.subType,
    category: CATEGORY_MAP.get(s.record.subType) ?? "Other",
  }));

  return {
    subType: best.subType,
    category: CATEGORY_MAP.get(best.subType) ?? "Other",
    confidence: Math.round(confidence * 100) / 100,
    lowConfidence: confidence < 0.5,
    candidates,
  };
}

function intersect(a: Set<string>, b: Set<string>): number {
  let n = 0;
  for (const v of a) if (b.has(v)) n++;
  return n;
}
