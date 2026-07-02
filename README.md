# Transport Sahayak — Rule-First Severity Engine (Python + FastAPI)

A deterministic accident severity & dispatch engine for the **Delhi–Dehradun Expressway**.
Rule-first: it classifies the incident, scores severity, and resolves which agencies to
dispatch from a fixed 470-row rule book — severity and agencies are **always** computed by
rules, never by an LLM.

Reading free text for hazard signals and taxonomy hints happens in two layers, in order:
1. **Local NLP (primary, always on)** — `severity_engine/local_extract.py` extracts
   fire/hazmat/road-blocked/entrapment/vulnerable-victim/casualty signals from the description
   using a curated phrase lexicon with negation detection ("fire was extinguished" correctly
   does NOT set `fire: true`), and `severity_engine/classifier.py` matches free text to a
   taxonomy record via token overlap + synonym normalization + a TF-IDF similarity blend
   (catches paraphrasing like "rammed into"/"flipped over" that raw keyword matching misses).
   Deterministic, no network, no API key, no external dependency beyond `scikit-learn`
   (small, pure-math, no model download) — this is what correctness depends on.
2. **Gemini (optional bonus)** — `severity_engine/gemini_client.py`'s `classify_with_gemini`
   (record escalation when local classification is still uncertain) and
   `extract_hazard_signals` (a second opinion on hazard signals) layer on top when available.
   Both already fail closed to `None` on any error (no key, quota, timeout, bad output) and
   only ever ADD to what local extraction already found — never required, never overrides.

An operator's explicit signal (quick-flag in the UI) always wins over either extraction layer;
extraction only ever adds. See `severity_engine/engine.py`'s `_merge_signals()`. Runs **locally
alongside your existing POC** for dev; see "Deploying to production" below for the real
deployment.

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

## Optional Gemini extraction (bonus layer only)

```bash
export GEMINI_API_KEY=your_key
export GEMINI_MODEL=gemini-2.0-flash
```

The engine works fully — including hazard-signal detection and paraphrase-tolerant
classification — without this key; local NLP (`local_extract.py` + `classifier.py`'s
TF-IDF blend) is the primary path, not a fallback. Gemini only adds a second opinion on top when
configured and reachable. Free-tier Gemini API keys sometimes have a 0-request quota until
billing is linked — that fails closed the same way as a missing key, not silently broken (check
`llmUsed` in the response: `true` only when Gemini specifically contributed, regardless of
whether local extraction also caught the same signal). `GET /debug/gemini` makes one trivial
call and reports the real error (bad key, quota, billing) if you need to diagnose it.

## Voice streaming (Google Cloud Speech-to-Text V2 / Chirp)

`WS /ws/voice?locale=en-IN|hi-IN` — replaces the old browser Web Speech API entirely. The
browser streams raw PCM16/16kHz/mono microphone audio directly to this WebSocket (see
`src/hooks/useVoiceInput.ts` on the Next.js side); `severity_engine/voice_stream.py` forwards
it to Speech-to-Text V2's `StreamingRecognize` (Chirp 2, automatic punctuation) and relays
`{"type":"interim"|"final","text":...}` events back as they arrive. Vercel serverless functions
can't hold a WebSocket open, so the browser connects to this service directly — see
`NEXT_PUBLIC_VOICE_STREAM_URL` in `.env.example`.

**Language is selected per recording session, not simultaneously.** English and Hindi are the
only two supported languages (matching the `locale` query param), but Chirp 2 doesn't support
recognizing both at once in a single request on this project: multi-language mode is only
available in the `eu`/`global`/`us` multi-region locations, and `chirp_2` isn't deployed to any
of them there — only to specific single regions like `us-central1` (both confirmed via real
400s while building this). So the reporter's selected language (the existing "English"/"हिंदी"
toggle already in the UI) applies to the whole recording, same as the old browser API's
per-session `rec.lang`.

**Client protocol:** after the handshake, send binary PCM16 audio frames while recording; send
any text frame (the client sends `"__end__"`) to signal "no more audio" *without* closing the
socket — the server needs a moment to flush the final transcript before the connection goes
away, so it closes the socket itself once done. Closing from the client immediately (skipping
the text signal) is handled as a normal disconnect, but risks losing an in-flight final result —
this raced and silently dropped the last utterance during testing, which is why the explicit
signal exists instead of relying on close alone.

Credentials (checked in this order, first match wins — see `voice_stream.py` for the exact
logic):
1. `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` — base64-encoded service account JSON. **Use this in
   production (Railway)**: `base64 -i path/to/key.json | tr -d '\n'`, paste the output as a
   Railway environment variable.
2. `GOOGLE_SERVICE_ACCOUNT_JSON` — the same JSON unencoded, if base64 is inconvenient.
3. A local file (default `~/Downloads/trans-sahayak-8f5e1c61e87e.json`, override via
   `GOOGLE_SERVICE_ACCOUNT_LOCAL_PATH`) — **local dev only**, never set in production.

This is deliberately explicit rather than bare `gcloud auth login` / ambient ADC discovery —
credentials are either configured or they're not, with a clear error either way, not a silent
"works on my machine" surprise.

**Required GCP setup** (one-time, on whichever project the service account belongs to):
```bash
gcloud services enable speech.googleapis.com --project=<your-project-id>
gcloud projects add-iam-policy-binding <your-project-id> \
  --member="serviceAccount:<your-service-account-email>" \
  --role="roles/speech.client"
```
(or the Console equivalents: APIs & Services → Library → enable "Cloud Speech-to-Text API";
IAM & Admin → IAM → find the service account → add the "Cloud Speech Client" role.) Chirp 2 is
a regional model — this integration talks to the `us-central1` regional endpoint by default
(`GOOGLE_SPEECH_LOCATION` to change it — `chirp_2` is only deployed to specific regions, not
`eu`/`global`/`us`, see "Voice streaming" above), which requires enabling the API and granting
the role before any request will succeed — a `403 PERMISSION_DENIED` naming
`speech.recognizers.recognize` means this step hasn't been done yet on that project.

Speech-to-Text V2/Chirp is a paid, metered API — separate billing/quota from the Gemini setup
above, even if you reuse the same GCP project.

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
4. (Optional) Add `GEMINI_API_KEY` as a Railway environment variable for a bonus second opinion
   on classification/hazard signals — not required. Local NLP already handles FIRE/hazmat/
   road-blocked dispatch from free text with no key configured at all.
5. Add `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` as a Railway environment variable (see "Voice
   streaming" above) — **required** for voice input to work at all in production; unlike
   Gemini, there's no local fallback for speech recognition.
6. In your **Vercel** project → Settings → Environment Variables, set:
   - `SEVERITY_ENGINE_URL` = the Railway URL from step 2 (no trailing slash), `https://...`.
   - `NEXT_PUBLIC_VOICE_STREAM_URL` = the same Railway domain with `wss://` and `/ws/voice`,
     e.g. `wss://transport-sahayak-severity.up.railway.app/ws/voice`.
   Set both for Production (and Preview if you want PR previews to hit the same engine), then
   redeploy.
7. `/api/assess` in the Next.js app will now reach the real engine instead of falling back
   to the "severity engine unreachable — treat as HIGH" stub, and the Voice report mode will
   stream to Speech-to-Text V2 instead of the browser's (now-removed) built-in recognizer.

Note: Railway redeploys this service whenever anything in the repo changes, including
Next.js–only commits — harmless (same build, no code difference for this service) but worth
knowing if you see redeploys you didn't expect.

## Files

```
app.py                         FastAPI wrapper (/health /subtypes /assess /ws/voice)
demo.py                        offline live-demo script
tests.py                       determinism + cost guardrail tests
requirements.txt
severity_engine/
  classifier.py                free-text/dropdown -> record: synonym normalization + token
                                overlap + TF-IDF similarity blend (primary, no LLM)
  local_extract.py             free-text -> hazard signals: curated phrase lexicon + negation
                                detection (primary, no LLM)
  severity.py                  base + modifiers + hard overrides -> LOW/MED/HIGH/CRITICAL
  dispatch.py                  agency resolution + corridor state-aware labels
  gemini_client.py             OPTIONAL bonus layer: classify_with_gemini (record escalation) +
                                extract_hazard_signals (free-text hazard extraction) — both
                                graceful if no key/failure, never required
  voice_stream.py              Speech-to-Text V2 (Chirp) streaming bridge for /ws/voice —
                                credential loading + StreamingRecognize forwarding
  engine.py                    orchestrator (rule-first; local NLP primary, LLM only reads as
                                a bonus, neither ever decides severity/dispatch)
  data/accident_index.json     470-row rule book (your Excel, structured)
  data/category_groups.json    raw category (50) -> curated top-level UI category (11) —
                                single source of truth, also read by src/lib/incidentClassifier.ts
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
