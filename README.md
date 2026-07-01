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

## Wiring into your existing POC (local dev)

1. Run this engine locally on port 8000 (above).
2. Add `SEVERITY_ENGINE_URL=http://localhost:8000` to the Next.js app's `.env.local`.

Local dev only — `http://localhost:8000` is your own machine, not reachable from a Vercel
deployment. See below for production.

## Deploying to production (Railway)

The Next.js app on Vercel calls this engine over HTTP via `SEVERITY_ENGINE_URL` — it needs
its own always-on host, since Vercel can't reach `localhost` on your machine. This repo is
already set up for Railway:

1. **Deploy this repo to Railway** — [railway.app](https://railway.app) → New Project →
   Deploy from GitHub repo → select this repo. Railway auto-detects the `Procfile`
   (`web: uvicorn app:app --host 0.0.0.0 --port $PORT`) and `requirements.txt`.
   `nixpacks.toml` at the repo root forces a Python-only build so Railway ignores the
   sibling Next.js `package.json` in the same repo.
2. In the Railway service → **Settings → Networking**, click **Generate Domain** to get a
   public URL (Railway services aren't public by default). Copy it, e.g.
   `https://transport-sahayak-severity.up.railway.app`.
3. Verify it: `curl https://<your-railway-domain>/health` → `{"ok":true,"records":471}`.
4. (Optional) Add `GEMINI_API_KEY` as a Railway environment variable to enable the
   ambiguous-free-text fallback — the engine works fully without it.
5. In your **Vercel** project → Settings → Environment Variables, set
   `SEVERITY_ENGINE_URL` = the Railway URL from step 2 (no trailing slash), for Production
   (and Preview if you want PR previews to hit the same engine). Redeploy.
6. `/api/assess` in the Next.js app will now reach the real engine instead of falling back
   to the "severity engine unreachable — treat as HIGH" stub.

Note: Railway redeploys this service whenever anything in the repo changes, including
Next.js–only commits — harmless (same build, no code difference for this service) but worth
knowing if you see redeploys you didn't expect.

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
