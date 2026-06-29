"""
gemini_client.py — OPTIONAL, classification-only fallback.

Used by the engine ONLY when rules can't confidently classify free text. It does ONE cheap
job: pick the best {category, subType} from a shortlist. It NEVER computes severity or picks
agencies. Degrades gracefully: if no GEMINI_API_KEY or the SDK is absent, returns None and the
engine falls back to the best rules candidate.
"""
import json
import os

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def classify_with_gemini(description: str, candidates: list, all_categories: list):
    """Returns {'subType':..., 'category':...} or None. Cheap, JSON-only, short timeout."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        import google.generativeai as genai
    except Exception:
        return None

    if candidates:
        shortlist = [{"category": c["category"], "subType": c["subType"]} for c in candidates]
        options_text = json.dumps(shortlist, ensure_ascii=False)
        instruction = (
            "Pick the single best matching option for this road-incident description. "
            "Reply with ONLY JSON: {\"subType\":\"...\",\"category\":\"...\"} chosen exactly "
            "from the options. No other text.\n"
            f"Options: {options_text}\nDescription: {description[:400]}"
        )
    else:
        instruction = (
            "Classify this road-incident description into one category from the list. "
            "Reply with ONLY JSON: {\"category\":\"...\"}. No other text.\n"
            f"Categories: {json.dumps(all_categories, ensure_ascii=False)}\n"
            f"Description: {description[:400]}"
        )

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL)
        resp = model.generate_content(
            instruction,
            generation_config={"max_output_tokens": 80, "temperature": 0},
            request_options={"timeout": 4},
        )
        text = (resp.text or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception:
        return None
