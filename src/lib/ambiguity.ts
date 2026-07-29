// Ambiguity / duplicate detection for committed incidents. Reuses the
// distance + time-window idea from dedup.ts and adds description similarity, so
// the operator can spot the same event reported multiple times from one area.
// ADVISORY ONLY: this flags clusters; the operator decides. Thresholds are
// constants so they're tunable.

import { haversineKm } from "./matching";
import type { DbAccident } from "./types";

export const AMBIGUITY_DIST_M = 700; // same-area radius
export const AMBIGUITY_TIME_MIN = 45; // close-in-time window
export const AMBIGUITY_JACCARD_MIN = 0.4; // description token-overlap threshold

// Standard English stopwords + a few report-boilerplate words ("caller",
// "reported"…) that appear in almost every description and would otherwise
// inflate similarity. Event nouns (car, collision, injured, fire…) are KEPT.
const STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are", "was", "were", "be",
  "by", "with", "for", "from", "near", "around", "this", "that", "there", "their", "they", "it",
  "has", "have", "had", "as", "but", "not", "no", "some", "any", "one", "another", "other",
  "caller", "call", "report", "reported", "reports", "reporting", "incident", "says", "said",
]);

export function tokenize(text: string): Set<string> {
  return new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9ऀ-ॿ\s]/g, " ") // keep latin + devanagari
      .split(/\s+/)
      .filter((w) => w.length > 1 && !STOPWORDS.has(w))
  );
}

export function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 && b.size === 0) return 0;
  let inter = 0;
  for (const t of a) if (b.has(t)) inter += 1;
  const union = a.size + b.size - inter;
  return union === 0 ? 0 : inter / union;
}

export interface AmbiguityCluster {
  key: string; // stable id (min member id)
  members: DbAccident[]; // sorted oldest → newest
  maxDistM: number;
  spanMin: number;
  avgSimilarity: number;
  why: string;
}

// Union-find over pairwise links (all three conditions must hold). Only
// unreviewed incidents are considered; already ignored/kept ones drop out so
// they stop being re-flagged.
export function findAmbiguousClusters(
  accidents: DbAccident[],
  reviewStatus: Map<string, string>
): AmbiguityCluster[] {
  const pool = accidents.filter((a) => {
    const st = reviewStatus.get(a.id);
    return (!st || st === "open") && typeof a.lat === "number" && typeof a.lng === "number" && a.created_at;
  });

  const tokens = new Map<string, Set<string>>();
  for (const a of pool) tokens.set(a.id, tokenize(a.description ?? ""));

  const parent = new Map<string, string>(pool.map((a) => [a.id, a.id]));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    while (parent.get(x) !== r) { const n = parent.get(x)!; parent.set(x, r); x = n; }
    return r;
  };
  const union = (a: string, b: string) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };

  // Track per-link stats for the "why" summary.
  const linkSims: number[] = [];
  for (let i = 0; i < pool.length; i++) {
    for (let j = i + 1; j < pool.length; j++) {
      const A = pool[i], B = pool[j];
      const distM = haversineKm({ lat: A.lat, lng: A.lng }, { lat: B.lat, lng: B.lng }) * 1000;
      if (distM > AMBIGUITY_DIST_M) continue;
      const dtMin = Math.abs(new Date(A.created_at).getTime() - new Date(B.created_at).getTime()) / 60000;
      if (dtMin > AMBIGUITY_TIME_MIN) continue;
      const sim = jaccard(tokens.get(A.id)!, tokens.get(B.id)!);
      if (sim >= AMBIGUITY_JACCARD_MIN) {
        union(A.id, B.id);
        linkSims.push(sim);
      }
    }
  }

  const groups = new Map<string, DbAccident[]>();
  for (const a of pool) {
    const r = find(a.id);
    const arr = groups.get(r);
    if (arr) arr.push(a);
    else groups.set(r, [a]);
  }

  const clusters: AmbiguityCluster[] = [];
  for (const members of groups.values()) {
    if (members.length < 2) continue;
    members.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

    let maxDistM = 0;
    const sims: number[] = [];
    for (let i = 0; i < members.length; i++) {
      for (let j = i + 1; j < members.length; j++) {
        maxDistM = Math.max(maxDistM, haversineKm({ lat: members[i].lat, lng: members[i].lng }, { lat: members[j].lat, lng: members[j].lng }) * 1000);
        sims.push(jaccard(tokens.get(members[i].id)!, tokens.get(members[j].id)!));
      }
    }
    const spanMin = Math.round(
      (new Date(members[members.length - 1].created_at).getTime() - new Date(members[0].created_at).getTime()) / 60000
    );
    const avgSimilarity = sims.length ? sims.reduce((a, b) => a + b, 0) / sims.length : 0;

    clusters.push({
      key: members.map((m) => m.id).sort()[0],
      members,
      maxDistM: Math.round(maxDistM),
      spanMin,
      avgSimilarity,
      why: `Same area (≤${Math.round(maxDistM)} m) · within ${spanMin} min · similar wording (${Math.round(avgSimilarity * 100)}% overlap)`,
    });
  }

  // Newest-activity clusters first.
  clusters.sort((a, b) => new Date(b.members[b.members.length - 1].created_at).getTime() - new Date(a.members[a.members.length - 1].created_at).getTime());
  return clusters;
}
