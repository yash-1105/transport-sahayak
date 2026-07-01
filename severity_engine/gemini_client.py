"""
gemini_client.py — OPTIONAL classification + hazard-signal extraction. Both functions read
free text only; NEITHER ever computes severity or picks agencies — that stays 100% rule-based
in severity.py/dispatch.py. Both degrade gracefully: if no GEMINI_API_KEY, the SDK is absent,
or the call fails/times out, they return None and the caller proceeds with whatever it already
had (client-provided signals, best rules candidate) — never fabricates, never blocks.
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


def extract_hazard_signals(description: str):
    """
    Reads a free-text incident description and extracts hazard/casualty signals
    ONLY (fire, hazmat, road-blocked, entrapment, vulnerable victim, rough
    casualty/vehicle counts) — no taxonomy lookup, that's classify_with_gemini's
    job. Runs on every request with free text, independent of how confidently
    the rule-based classifier matched a record: a clearly-matched "Car vs. Car
    Collision" can still mention a fire the classifier's static agency list has
    no way to know about. Returns a dict or None (never fabricates: the caller
    only ever ORs these into whatever the client already sent, and every value
    here is explicitly instructed to be conservative).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        import google.generativeai as genai
    except Exception:
        return None

    instruction = (
        "Read this road-incident description and extract ONLY facts explicitly stated in the "
        "text — never guess, infer beyond what's written, or assume worst case. "
        "Reply with ONLY this JSON shape, no other text, no markdown fences:\n"
        '{"fire": bool, "hazmat": bool, "roadBlocked": bool, "entrapment": bool, '
        '"vulnerableVictim": bool, "estimatedCasualties": int_or_null, '
        '"estimatedVehiclesInvolved": int_or_null}\n'
        f"Description: {description[:600]}"
    )

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL)
        resp = model.generate_content(
            instruction,
            generation_config={"max_output_tokens": 150, "temperature": 0},
            request_options={"timeout": 5},
        )
        text = (resp.text or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def gemini_health_check():
    """
    Diagnostic only — makes one trivial call and returns (ok, detail) so a
    misconfigured/quota-exhausted key surfaces its REAL error instead of the
    silent None every other function here returns by design. Not used by the
    assess() pipeline itself; wired to a debug endpoint in app.py.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return False, "GEMINI_API_KEY not set in this process's environment"
    try:
        import google.generativeai as genai
    except Exception as e:
        return False, f"SDK import failed: {e}"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL)
        resp = model.generate_content(
            'Reply with only this exact JSON: {"ok": true}',
            generation_config={"max_output_tokens": 20, "temperature": 0},
            request_options={"timeout": 8},
        )
        return True, (resp.text or "").strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
