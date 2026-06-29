# Transport Sahayak — Rule-First Severity Engine (Python + FastAPI)

A deterministic accident severity & dispatch engine for the **Delhi–Dehradun Expressway**.
Rule-first: it classifies the incident, scores severity, and resolves which agencies to
dispatch from a fixed 470-row rule book. **Gemini is consulted only when free-text input is
genuinely ambiguous**, and only to pick a record — severity and agencies are always computed
by rules. Runs **locally alongside your existing POC**; nothing new to deploy.

## Run it (for the demo)

```bash
pip install -r requirements.txt

# 1) Offline demo — no API key, no server. Best for a quick walk-through:
python demo.py

# 2) Guardrail tests (determinism + cost):
python tests.py

# 3) The API your Next.js POC calls:
uvicorn app:app --reload --port 8000
#   GET  http://localhost:8000/health
#   GET  http://localhost:8000/subtypes        -> dropdown options for the UI
#   POST http://localhost:8000/assess          -> the assessment
```

Example request:

```bash
curl -s localhost:8000/assess -H 'content-type: application/json' -d '{
  "incident": {"description": "lpg tanker on fire near km 40"},
  "signals": {"fire": true},
  "location": {"km": 40}
}'
```

## Optional Gemini fallback

```bash
export GEMINI_API_KEY=your_key      # without this, vague input degrades to best rule guess
export GEMINI_MODEL=gemini-2.0-flash
```

The engine works fully without the key — it just won't auto-reclassify ambiguous free text.

## Wiring into your existing POC (no new deployment)

1. Run this engine locally on port 8000 (above).
2. Replace `src/app/api/assess/route.ts` with `poc_integration/assess_route.ts` (calls the
   engine; removes the old Anthropic path).
3. Add `SEVERITY_ENGINE_URL=http://localhost:8000` to the POC's `.env.local`.
4. Add the ambulance-stations layer (`poc_integration/ambulance_stations.ts`) and remove
   Suraksha Mitra (`poc_integration/REMOVE_SURAKSHA_MITRA.md`).

## Files

```
app.py                         FastAPI wrapper (/health /subtypes /assess)
demo.py                        offline live-demo script
tests.py                       determinism + cost guardrail tests
requirements.txt
severity_engine/
  classifier.py                free-text/dropdown -> record (global keyword scoring)
  severity.py                  base + modifiers + hard overrides -> LOW/MED/HIGH/CRITICAL
  dispatch.py                  agency resolution + corridor state-aware labels
  gemini_client.py             OPTIONAL classification-only fallback (graceful if no key)
  engine.py                    orchestrator (rule-first; LLM only on escalation)
  data/accident_index.json     470-row rule book (your Excel, structured)
  data/corridor_profile.json   km segments, wildlife/tunnel zones, state jurisdictions
poc_integration/               drop-in snippets for the existing Next.js POC
```

## Known data note

Your source Excel's **Category** column is row-shifted in places (e.g. "Fire Inside Tunnel"
is tagged "Driver / Passenger Medical"). Each row's own subType, cause, agencies, capture,
and severity are correct — only the category *grouping label* is unreliable. The engine
therefore ranks free text by sub-type keywords, not category, and the UI should show
**subType** as the primary label. If you want clean category labels, re-derive them later;
it does not affect severity or dispatch.
