"""
classifier.py — map an incident (dropdown subType OR free-text description) to a
record in the accident index. Deterministic. No network, no LLM here.
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _SKLEARN_OK = True
except Exception:
    _SKLEARN_OK = False

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
with open(os.path.join(_DATA_DIR, "accident_index.json"), encoding="utf-8") as _f:
    INDEX = json.load(_f)
with open(os.path.join(_DATA_DIR, "category_groups.json"), encoding="utf-8") as _f:
    _CATEGORY_GROUPS = json.load(_f)
    _CATEGORY_GROUPS.pop("_meta", None)
with open(os.path.join(_DATA_DIR, "hindi_glossary.json"), encoding="utf-8") as _f:
    _HINDI_GLOSSARY = json.load(_f)["pairs"]

# ---- build a keyword index once at import ----
_STOP = {
    "the", "a", "an", "of", "on", "to", "in", "by", "or", "and", "with", "at", "for",
    "collision", "crash", "strike", "accident", "situations", "vehicle", "road", "expressway",
    "highway", "vs", "from", "into", "off", "no", "of.", "-", "high", "speed", "high-speed",
}
_word_re = re.compile(r"[a-z0-9]+")

# ── Synonym / paraphrase normalization ────────────────────────────────────────
# Applied before tokenizing so common paraphrasing ("rammed into", "flipped
# over") maps onto the same vocabulary the index is scored against — the raw
# tokenizer alone has zero synonym awareness. Order matters: longer/more
# specific phrases first so they're substituted before a shorter one could
# partially match inside them.
_SYNONYMS = [
    ("burst into flames", "fire"), ("went up in flames", "fire"),
    ("caught on fire", "fire"), ("caught fire", "fire"),
    ("rammed into", "collision"), ("slammed into", "collision"),
    ("crashed into", "collision"), ("smashed into", "collision"),
    ("ran into", "collision"), ("collided with", "collision"),
    ("flipped over", "rollover"), ("turned over", "rollover"), ("toppled over", "rollover"),
    ("broke down", "breakdown"), ("stopped working", "breakdown"), ("wont start", "breakdown"),
    ("won't start", "breakdown"),
    ("skidded off", "skid"), ("lost control", "skid"),
    ("fell off", "fall"), ("fell down", "fall"),
    ("hit by", "struck"), ("struck by", "struck"),
]


# ── Hindi → English normalization ─────────────────────────────────────────────
# Applied before the English synonym pass and before lowercasing (Hindi has no
# case, so order relative to .lower() doesn't matter for these substitutions
# themselves, but they must run before _word_re only captures [a-z0-9]).
# Shared with src/lib/incidentClassifier.ts (the /api/guess picker UI) via
# hindi_glossary.json — see that file's _meta note for how targets were
# chosen. Previously this classifier had zero Hindi awareness: any Hindi
# report scored 0 token overlap on every record and fell through to whatever
# arbitrary placeholder record classify() defaults to, regardless of what was
# actually described.
def _translate_hindi(text: str) -> str:
    out = text or ""
    for hi, en in _HINDI_GLOSSARY:
        out = out.replace(hi, f" {en} ")
    return out


def _normalize(text: str) -> str:
    out = _translate_hindi(text).lower()
    for phrase, canonical in _SYNONYMS:
        out = out.replace(phrase, f" {canonical} ")
    return out


def _tokens(text: str):
    return [t for t in _word_re.findall(_normalize(text)) if t not in _STOP and len(t) > 2]


_KW = []  # list of (record, set_subtype_tokens, set_cause_tokens)
for _rec in INDEX:
    _KW.append((_rec, set(_tokens(_rec["subType"])), set(_tokens(_rec["cause"]))))

# ── TF-IDF similarity — a second, complementary scorer ────────────────────────
# Raw token overlap treats every word equally, so a rare/informative word like
# "bleve" counts the same as a common one. TF-IDF naturally weights rare terms
# higher and catches semantic closeness plain overlap misses. Fit once at
# import over the corpus (no model download, no network — pure math over the
# existing 471-row index), degrades to token-only scoring if sklearn is
# unavailable for any reason.
if _SKLEARN_OK:
    _CORPUS_TEXTS = [" ".join(_tokens(_rec["subType"] + " " + _rec["cause"])) for _rec in INDEX]
    try:
        _VECTORIZER = TfidfVectorizer()
        _TFIDF_MATRIX = _VECTORIZER.fit_transform(_CORPUS_TEXTS)
    except Exception:
        _SKLEARN_OK = False


def _tfidf_similarities(desc_token_str: str):
    """Returns {record_index: cosine_similarity} or {} if unavailable."""
    if not _SKLEARN_OK or not desc_token_str.strip():
        return {}
    try:
        q_vec = _VECTORIZER.transform([desc_token_str])
        sims = cosine_similarity(q_vec, _TFIDF_MATRIX)[0]
        return {i: float(sims[i]) for i in range(len(sims))}
    except Exception:
        return {}


# How much a perfect (1.0) TF-IDF cosine match contributes, in the same units
# as the token-overlap score below — tuned so a strong TF-IDF match is roughly
# on par with a solid token-overlap hit (comparable to the acceptance
# thresholds further down), without ever being able to REDUCE a record's
# score relative to today's token-only behavior (TF-IDF only ever adds).
_TFIDF_SCALE = 6.0

# Hard-override tokens -> category that should win regardless of weak scores.
# Each rule: (predicate on token set, category name).
_OVERRIDES = [
    (lambda t: "bleve" in t or (("lpg" in t or "cng" in t) and "fire" in t), "Fire Situations"),
    (lambda t: ("tanker" in t and bool(t & {"spill", "leak", "chemical", "acid", "hazmat"})) or "radioactive" in t, "Hazardous Material"),
    (lambda t: bool(t & {"explosion", "exploded", "ied", "bomb", "blast"}), "Hazardous Material"),
    (lambda t: "collapse" in t and bool(t & {"bridge", "flyover"}), "Structural / Catastrophic"),
    (lambda t: "tunnel" in t and bool(t & {"fire", "smoke", "trapped", "dark", "stuck", "blocked"}), "Tunnel Incidents"),
    (lambda t: bool(t & {"flood", "submerged", "swept", "drowning", "waterlogged"}), "Flood / Water Emergency"),
    (lambda t: bool(t & {"landslide", "rockfall", "boulder", "mudslide"}), "Landslide / Cliff Fall"),
    (lambda t: bool(t & {"elephant", "leopard", "cattle", "cow", "buffalo", "nilgai", "boar", "animal", "deer", "monkey"}), "Vehicle to Animal"),
    (lambda t: bool(t & {"pedestrian", "cyclist", "walking", "crossing"}), "Vehicle to Person"),
    (lambda t: bool(t & {"cardiac", "stroke", "childbirth", "anaphylaxis", "overdose", "seizure", "unconscious"}), "Driver / Passenger Medical"),
]


@dataclass
class ClassificationResult:
    record: Optional[dict]
    confidence: float
    source: str  # "operator" | "rules" | "needs_llm"
    candidates: list = field(default_factory=list)


def _find_exact(sub_type: str):
    if not sub_type:
        return None
    s = sub_type.strip().lower()
    for rec in INDEX:
        if rec["subType"].strip().lower() == s:
            return rec
    return None


def classify(incident: dict) -> ClassificationResult:
    # 1. exact dropdown selection -> zero ambiguity, zero LLM
    rec = _find_exact(incident.get("subType", ""))
    if rec:
        return ClassificationResult(record=rec, confidence=1.0, source="operator")

    desc = incident.get("description", "") or ""
    if not desc.strip():
        return ClassificationResult(record=None, confidence=0.0, source="needs_llm")

    desc_tokens = _tokens(desc)
    dt = set(desc_tokens)

    # NOTE: the source spreadsheet's Category column is row-shifted in places, so we do NOT
    # filter by category. We score every record globally by sub-type/cause keyword overlap
    # PLUS TF-IDF cosine similarity (a second, complementary scorer that catches semantic
    # closeness plain overlap misses — see _tfidf_similarities above); sub-type tokens
    # (weighted 2x) carry most of the overlap signal. Overrides are used only by the engine's
    # conflict guard, not as a hard category filter here.
    tfidf_sims = _tfidf_similarities(" ".join(desc_tokens))
    scored = []
    for i, (rec, st, ct) in enumerate(_KW):
        token_score = 2 * len(dt & st) + 1 * len(dt & ct)
        combined = token_score + tfidf_sims.get(i, 0.0) * _TFIDF_SCALE
        if combined > 0:
            scored.append((combined, rec))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return ClassificationResult(record=None, confidence=0.0, source="needs_llm")

    top = scored[0][0]
    runner = scored[1][0] if len(scored) > 1 else 0
    confidence = top / (top + runner + 1)
    best = scored[0][1]
    candidates = [r for _, r in scored[:3]]

    # accept when there's a clear, meaningful sub-type hit; else escalate
    if top >= 4 and confidence >= 0.45:
        return ClassificationResult(record=best, confidence=round(confidence, 2),
                                    source="rules", candidates=candidates)
    if top >= 2 and confidence >= 0.6:
        return ClassificationResult(record=best, confidence=round(confidence, 2),
                                    source="rules", candidates=candidates)
    return ClassificationResult(record=best, confidence=round(confidence, 2),
                                source="needs_llm", candidates=candidates)


# ── Category consolidation ─────────────────────────────────────────────────────
# The source spreadsheet's raw `category` field already has 50 reasonably
# balanced values (1-47 records each) — far better than deriving categories by
# keyword-matching each subType string. category_groups.json (single source of
# truth, also read by the TS port in src/lib/incidentClassifier.ts) maps every
# one of those 50 raw categories onto 11 curated, balanced top-level UI
# categories — every record is assigned, no leftover "Other" catch-all.

_CATEGORY_MAP: dict[str, str] = {
    rec["subType"]: _CATEGORY_GROUPS.get(rec["category"], rec["category"]) for rec in INDEX
}


def get_categories() -> list[dict]:
    """Return [{category, count}] sorted descending by count."""
    counts: dict[str, int] = {}
    for cat in _CATEGORY_MAP.values():
        counts[cat] = counts.get(cat, 0) + 1
    return sorted(
        [{"category": c, "count": n} for c, n in counts.items()],
        key=lambda x: -x["count"],
    )


def get_subtypes_for(category: str) -> list[str]:
    """Return sorted list of subType strings belonging to this category."""
    return sorted(sub for sub, cat in _CATEGORY_MAP.items() if cat == category)


def guess(description: str) -> dict:
    """
    Classify a free-text description and return a confirm-card payload for the UI.
    """
    result = classify({"description": description})
    if not result.record:
        return {
            "subType": None,
            "category": None,
            "confidence": 0.0,
            "lowConfidence": True,
            "candidates": [],
        }
    top_cat = _CATEGORY_MAP.get(result.record["subType"], "Other")
    candidates = [
        {
            "subType": r["subType"],
            "category": _CATEGORY_MAP.get(r["subType"], "Other"),
        }
        for r in (result.candidates or [])
    ]
    return {
        "subType": result.record["subType"],
        "category": top_cat,
        "confidence": result.confidence,
        "lowConfidence": result.confidence < 0.5 or result.source == "needs_llm",
        "candidates": candidates,
    }
