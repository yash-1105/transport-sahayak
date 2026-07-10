// TypeScript port of severity_engine/classifier.py — runs entirely in Next.js,
// no Python engine needed. Used by /api/categories, /api/categories/subtypes, /api/guess.

import INDEX_RAW from "../../severity_engine/data/accident_index.json";
import CATEGORY_GROUPS_RAW from "../../severity_engine/data/category_groups.json";
import HINDI_GLOSSARY_RAW from "../../severity_engine/data/hindi_glossary.json";

interface IndexRecord {
  category: string;
  subType: string;
  cause: string;
  agencies: string[];
  baseSeverity: number;
}

const INDEX = INDEX_RAW as IndexRecord[];
const CATEGORY_GROUPS = CATEGORY_GROUPS_RAW as unknown as Record<string, string>;

// ── Stop words ────────────────────────────────────────────────────────────────

const STOP = new Set([
  "the", "a", "an", "of", "on", "to", "in", "by", "or", "and", "with", "at", "for",
  "collision", "crash", "strike", "accident", "situations", "vehicle", "road", "expressway",
  "highway", "vs", "from", "into", "off", "no", "high", "speed",
  // Symptom/consequence words never discriminate incident TYPE (any incident
  // can have injured people) — mirrored from classifier.py's _STOP; see the
  // comment there for the real misclassification this fixed.
  "injured", "injury", "injuries", "casualty", "casualties", "hurt", "wounded",
]);

const HINDI_STOP = new Set([
  "की", "का", "के", "में", "से", "पर", "एक", "और", "है", "हैं",
  "था", "थी", "हुआ", "हुई", "को", "ने", "यह", "वह", "कि", "जो", "भी", "तो",
]);

// ── Hindi → English normalization ─────────────────────────────────────────────
// Translates key Hindi accident terms to English equivalents before tokenising,
// so the existing English-indexed scoring still works for Hindi input. Shared
// with severity_engine/classifier.py (Python) via hindi_glossary.json — see
// that file's _meta note for how targets were chosen (must survive the
// stopword filter below AND have real signal in the corpus; e.g. Hindi
// collision verbs map to "struck", not the stopworded "collision").

const HINDI_TO_EN = (HINDI_GLOSSARY_RAW as unknown as { pairs: [string, string][] }).pairs;

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

// ── Category consolidation ─────────────────────────────────────────────────────
// The JSON's raw `category` field already has 50 reasonably balanced values —
// far more reliable than keyword-matching each subType string. severity_engine/
// data/category_groups.json (single source of truth, also read by the Python
// classifier) maps every one of those 50 raw categories onto 11 curated,
// balanced top-level UI categories — every record is assigned, no "Other".

// ── Category map: subType → consolidated display category ─────────────────────

const CATEGORY_MAP = new Map<string, string>(
  INDEX.map((r) => [r.subType, CATEGORY_GROUPS[r.category] ?? r.category])
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

// ── Taxonomy helpers for the AI fallback classifier ───────────────────────────
// The keyword matcher above is English-indexed and has no stemming, so it scores
// 0 on most Hindi/Hinglish input (and on vague English like "vehicle accident").
// When it fails, /api/guess hands the raw description to an LLM which must pick a
// subType from this exact taxonomy — these helpers build and validate that list.

// consolidated category → its subtypes, largest categories first
export function getTaxonomy(): { category: string; subtypes: string[] }[] {
  const grouped = new Map<string, string[]>();
  for (const [subType, cat] of CATEGORY_MAP) {
    if (!grouped.has(cat)) grouped.set(cat, []);
    grouped.get(cat)!.push(subType);
  }
  return [...grouped.entries()]
    .map(([category, subtypes]) => ({ category, subtypes: subtypes.sort() }))
    .sort((a, b) => b.subtypes.length - a.subtypes.length);
}

// lowercased subType → canonical subType, for validating LLM output
const SUBTYPE_CANON = new Map<string, string>();
for (const subType of CATEGORY_MAP.keys()) SUBTYPE_CANON.set(subType.toLowerCase(), subType);

// Resolve an LLM-returned subType back to a real taxonomy entry (exact, then a
// forgiving contains-match). Returns null if it can't be tied to the taxonomy.
export function resolveSubtype(name: string): { subType: string; category: string } | null {
  const key = (name ?? "").trim().toLowerCase();
  if (!key) return null;
  let canon = SUBTYPE_CANON.get(key);
  if (!canon) {
    for (const [low, real] of SUBTYPE_CANON) {
      if (low.includes(key) || key.includes(low)) { canon = real; break; }
    }
  }
  if (!canon) return null;
  return { subType: canon, category: CATEGORY_MAP.get(canon) ?? "Other" };
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
