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

// ── Stop words (mirrors Python classifier) ────────────────────────────────────

const STOP = new Set([
  "the", "a", "an", "of", "on", "to", "in", "by", "or", "and", "with", "at", "for",
  "collision", "crash", "strike", "accident", "situations", "vehicle", "road", "expressway",
  "highway", "vs", "from", "into", "off", "no", "high", "speed",
]);

function tokens(text: string): string[] {
  return (text ?? "")
    .toLowerCase()
    .match(/[a-z0-9]+/g)
    ?.filter((t) => t.length > 2 && !STOP.has(t)) ?? [];
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

// ── Category map: subType → display category ──────────────────────────────────
// We use the `category` field from the JSON directly (the Python classifier uses
// CATEGORY_KEYWORDS to remap; here we use the JSON source of truth).

const CATEGORY_MAP = new Map<string, string>(
  INDEX.map((r) => [r.subType, r.category])
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
  const dt = new Set(tokens(description));
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
