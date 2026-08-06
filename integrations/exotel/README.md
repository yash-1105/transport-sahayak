# Exotel AgentStream ↔ Hindi Voice Dispatcher

Telephony transport for the **existing** Hindi voice assistant. A phone call placed
to an Exotel ExoPhone is streamed over Exotel's **Voicebot / AgentStream**
bidirectional WebSocket into this backend, which runs the **exact same**
`HindiDispatcherSession` the browser uses (Sarvam Saaras STT → Gemini 2.5 Flash →
Sarvam Bulbul TTS, plus the same emergency workflow, SOP, dispatch and helpline
logic). Nothing in the browser path changes.

> **Scope (v1):** Hindi only. English (Gemini Live) is intentionally **not** wired
> to telephony yet. The integration is **off by default** (`EXOTEL_ENABLED=false`)
> — with it unset, this backend behaves byte-for-byte as it does today.

---

## What was added / changed

| File | New? | Purpose |
|------|------|---------|
| `integrations/exotel/protocol.py` | new | Parse AgentStream events (`connected`/`start`/`media`/`dtmf`/`stop`); build outbound `media`/`clear`/`mark` frames. |
| `integrations/exotel/audio_adapter.py` | new | Resample Exotel PCM16 ⇄ pipeline rates (`audioop.ratecv`): Exotel→16 kHz for Saaras, 24 kHz Bulbul→Exotel. |
| `integrations/exotel/services.py` | new | **Server-side** answers to the browser round-trips, all reusing the app's OWN real endpoints (there is no Python-internal implementation — see *Reuse* below): responder lookup via `/api/aggregator/responders`, **traffic-aware ETA via `/api/routes/matrix`** (the same Google-Routes source the browser uses for hospital/police), complaint via `/api/potholes`, Nominatim forward-geocode. Ambulance/fire/tow ETAs mirror `src/lib/matching.ts`'s per-type haversine by value. |
| `integrations/exotel/location.py` | new | `GeocodeLocationProvider` — transport-agnostic, injectable location acquisition (geocode caller's landmark → retry → terminate, no default). **Composed** by the session. |
| `integrations/exotel/session.py` | new | `ExotelWebSocketAdapter` (a FastAPI-WebSocket look-alike = the "Exotel transport") + `ExotelHindiSession` (thin subclass that **composes** the location provider and overrides **only** the 3 location-gated tools). |
| `integrations/exotel/config.py` | new | Centralised env config + **startup validation** (`validate()` raises on hard misconfig; soft issues are warnings) + `health_payload` inputs. |
| `integrations/exotel/logging_utils.py` | new | **Structured per-call logging** — a `contextvars`-scoped call id auto-tagged onto every Exotel log line (`[call=<id>]`), no threading. |
| `integrations/exotel/websocket.py` | new | The `/exotel/ws` endpoint (mounted only when enabled **and** valid) + the always-on `/exotel/health` probe; `register(app)`. |
| `app.py` | **edited** | **Only** additive change: `register(app)` mount (guarded, try/except). This is the sole module that owns the FastAPI `app` and all WS route registrations — there is no other mount point. |
| `requirements.txt` | **edited** | Added `httpx>=0.27` (async client for the server-side reuse of the app's APIs + Nominatim). |
| `tests.py` | **edited** | Added `#EX` offline tests: adapter translation, geocode-retry-then-terminate, the `GeocodeLocationProvider` in isolation, server-side facility round-trip, **ETA-reuse** (Routes for hospital/police, per-type haversine for ambulance/tow), and the browser-session-unchanged assertion. |

**Not touched (per the constraints):** Gemini prompts, Hindi prompts, Sarvam/Bulbul
integration, emergency workflow, service lookup, SOP/severity/dispatch logic, the
browser WebSocket (`/ws/dispatcher`), and the entire frontend.

---

## Architecture

```
                         ┌──────────────────────────── FastAPI app.py (Railway) ────────────────────────────┐
                         │                                                                                    │
 ☎  Caller ──dial──▶ Exotel ──Voicebot/AgentStream WSS──▶  /exotel/ws  (websocket.py)                        │
                     ExoPhone   (JSON: connected/start/          │                                            │
                                 media/dtmf/stop  +              │  ExotelWebSocketAdapter                    │
                                 media/clear/mark out)           │  ── quacks like a FastAPI WebSocket ──     │
                                                                 │   • start  → capture call_sid/from/rate    │
                                                                 │   • media  → audio_adapter → 16 kHz PCM ──┐│
                                                                 │   • Bulbul 24 kHz PCM → audio_adapter ────┼┼─▶ media out
                                                                 │   • interrupted → clear (barge-in)        ││
                                                                 │   • request_facility/complaint/submitted  ││
                                                                 │        → services.py (server-side) ───────┘│
                                                                 │                                            │
                                                                 ▼                                            │
                                    ExotelHindiSession(HindiDispatcherSession)                                │
                                    overrides ONLY the 3 location tools → COMPOSED GeocodeLocationProvider     │
                                    (location.py: geocode landmark → retry → terminate, never a default)       │
                                                                 │                                            │
                                              ─── inherited, UNCHANGED ───────────────────────────────       │
                                              run() · Saaras STT · Gemini 2.5 Flash + tools · Bulbul TTS ·    │
                                              dispatch briefing · SOP · helpline (find facility / complaint)  │
                         └────────────────────────────────────────────┬───────────────────────────────────────┘
                          services.py reuses the app's OWN endpoints (no Python-internal impl exists)
              ┌──────────────────────┬──────────────────────────┬─────────────────────┬─────────────────────┐
        GET /api/aggregator/   POST /api/routes/matrix    POST /api/potholes     Nominatim forward-geocode
            responders          (traffic-aware ETA,        (complaint record)    (landmark → coordinates)
        (responder data)         hospital/police)

  Browser path (UNCHANGED):  Browser ──WSS── /ws/dispatcher ──▶ HindiDispatcherSession  (same pipeline, GPS location)
```

Both transports terminate in the **same** `HindiDispatcherSession`. The only
difference is how location is obtained (phone: geocoded speech; browser: device GPS)
and how the browser round-trips are answered (phone: server-side in `services.py`;
browser: by the frontend).

---

## Call sequence (Hindi accident call over the phone)

```
Caller        Exotel            /exotel/ws (adapter)        ExotelHindiSession           services.py / app APIs
  │  dial ─────▶│                      │                            │                             │
  │             │─ connected ─────────▶│ (ignored)                  │                             │
  │             │─ start (call_sid,───▶│ capture meta,              │                             │
  │             │   from, rate)        │ reconfigure AudioAdapter   │                             │
  │             │                      │─ run() ───────────────────▶│ opening Hindi line          │
  │             │◀── media (Bulbul) ───│◀── send_bytes (24k→rate) ──│ (नमस्ते… 1033…)            │
  │ ◀ audio ────│                      │                            │                             │
  │ speaks ────▶│─ media (caller) ────▶│ audio→16k→Saaras STT       │                             │
  │  "एक्सीडेंट…"│                     │─── transcript ────────────▶│ Gemini + tools              │
  │             │                      │ (adapter records last      │  search_incident_type…      │
  │             │                      │  caller utterance)         │  get_current_location ─────▶│ geocode(landmark)
  │             │                      │                            │◀── {lat,lng,label} ─────────│
  │             │◀── media (reply) ────│◀── send_bytes ─────────────│ empathetic ack + question   │
  │  … dialogue continues (casualties / trapped / fire / conscious / breathing) …                 │
  │             │                      │ submit_incident            │                             │
  │             │                      │─ "submitted" ─────────────▶│ _service_dispatch ─────────▶│ responders + ETAs
  │             │                      │◀ dispatch_update (services)│                             │
  │             │◀── media (briefing)──│◀── staged dispatch briefing│ ambulance notified, SOP,    │
  │  ◀ audio ───│                      │   (ambulance ETA, safety)  │ 2-hour follow-up            │
  │             │                      │─ call_complete ───────────▶│ (adapter sets flag)         │
  │             │◀── (drain + hang up) │ run() returns → close()    │                             │
  │  ends ──────│                      │                            │                             │
```

**Location retry (no silent default):** if `geocode(landmark)` returns nothing,
`_acquire_location()` returns a `next_step` telling the model to warmly ask for one
specific landmark (NH number, toll plaza, petrol pump, town). After
`_MAX_GEOCODE_ATTEMPTS` (3) failures it returns a terminate guidance (apologise, ask
them to call back with a clearer landmark, end). A default location is **never**
substituted.

---

## Environment variables

Set on the **Railway service that runs `app.py`** (same service as the browser
dispatcher). All optional — with `EXOTEL_ENABLED` unset the endpoint isn't mounted.

```bash
EXOTEL_ENABLED=true               # master switch (default false => WS endpoint not mounted)
EXOTEL_WS_PATH=/exotel/ws         # WS endpoint path (default /exotel/ws)
EXOTEL_HEALTH_PATH=/exotel/health # health endpoint path (default /exotel/health)
EXOTEL_SAMPLE_RATE=16000          # Exotel stream PCM16 rate; auto-corrected from the `start` frame if it differs

# services.py — server-side reuse of the app's own APIs for phone calls
APP_BASE_URL=https://transport-sahayak.vercel.app   # where /api/aggregator/responders, /api/routes/matrix & /api/potholes live (default http://localhost:3000)
NOMINATIM_URL=https://nominatim.openstreetmap.org   # forward-geocoder (default public endpoint)

# HTTP resilience for every external call in services.py
EXOTEL_HTTP_TIMEOUT=8             # seconds, per attempt (default 8)
EXOTEL_HTTP_RETRIES=2             # extra attempts after the first, transient failures only (default 2)
```

**Startup validation:** when `EXOTEL_ENABLED=true`, `config.validate()` runs at mount
time. A **hard** misconfig (bad `EXOTEL_WS_PATH`, non-numeric/zero `EXOTEL_SAMPLE_RATE`,
malformed `APP_BASE_URL`, nonsensical HTTP settings) → the WS endpoint is **not**
mounted (the browser service stays up; `/exotel/health` reports `misconfigured`).
**Soft** issues (localhost `APP_BASE_URL`, missing `SARVAM_API_KEY` / Google creds the
Hindi pipeline needs) are logged as warnings.

Existing Sarvam/Gemini/Vertex vars (`SARVAM_API_KEY`, `GEMINI_TEXT_MODEL`,
`GOOGLE_SERVICE_ACCOUNT_JSON_BASE64`, …) are **reused unchanged** — the Hindi
pipeline is identical.

---

## Exact WSS URL

The endpoint is on the **same** Railway host as the browser dispatcher
(`NEXT_PUBLIC_DISPATCHER_WS_URL` → `.../ws/dispatcher`), just a different path:

```
wss://<your-app.py-railway-domain>/exotel/ws
```

- **Local dev:** `ws://localhost:8000/exotel/ws`
- **Production:** `wss://<your-railway-domain>/exotel/ws`

Locale is fixed to Hindi server-side (`ExotelHindiSession`), so **no `?locale=`
query param is required** (unlike the browser `/ws/dispatcher?locale=hi-IN`).

**Health probe:** `GET https://<your-railway-domain>/exotel/health` →
`{status: "disabled" | "misconfigured" | "ok", ok, errors[], warnings[], config{}}`.
Always mounted (even when disabled), returns no secrets — safe for uptime checks.

---

## Exotel Voicebot configuration

In the Exotel dashboard, build an **App Bazaar flow** for the ExoPhone:

1. **Voicebot applet** (a.k.a. AgentStream / bidirectional streaming) as the flow's
   first/primary applet.
2. **WebSocket URL:** `wss://<your-railway-domain>/exotel/ws`
3. **Streaming format:** raw **PCM16, 16 kHz, mono** (match `EXOTEL_SAMPLE_RATE`).
   The adapter also honours whatever `sample_rate` arrives in the `start` frame, so
   8 kHz works too — but keep the applet and the env var in agreement.
4. **Bidirectional:** enabled (the bot must be able to send audio back, not just
   receive).
5. Assign the applet to the trial **ExoPhone**; point the ExoPhone's incoming-call
   flow at this Voicebot flow.
6. (Optional) pass `custom_parameters` in the applet — they arrive on the `start`
   frame and are captured, though v1 doesn't require any.

No DTMF handling is required for v1 (digits are parsed but ignored).

---

## Deployment steps

1. **Deploy the backend** (this repo's `app.py`) to Railway as usual. The Exotel
   files ship with it; nothing else in the deploy changes.
2. **Set env vars** on that Railway service: `EXOTEL_ENABLED=true` and
   `APP_BASE_URL=https://transport-sahayak.vercel.app` (so server-side responder /
   complaint lookups hit the live app). `EXOTEL_WS_PATH` / `EXOTEL_SAMPLE_RATE` only
   if you deviate from defaults.
3. **Confirm the WSS URL** is reachable: `wss://<railway-domain>/exotel/ws`.
4. **Configure the Exotel Voicebot flow** (above) with that URL and bind it to the
   trial ExoPhone.
5. **Place a test call** to the ExoPhone and walk the checklist below.
6. Leave the browser app untouched — it keeps using `/ws/dispatcher`.

---

## Testing checklist

**Offline (run now, no telephony):**

- [x] `python3 tests.py` — full suite green, incl. the `#EX` tests.
- [x] Adapter translates `media`→16 kHz PCM, `stop`→disconnect, Bulbul 24 kHz→Exotel
      `media`, `interrupted`→`clear`, caller transcript captured.
- [x] Location: geocode success sets `state.location`; failure asks for a landmark;
      3 failures → terminate; upfront (no speech yet) stays silent; **no default**.
- [x] `GeocodeLocationProvider` in isolation (composition): success / silent-when-empty
      / ask ×2 then terminate — injectable geocoder, no default.
- [x] `request_facility` answered **server-side** (reuses responder data + ETA) and
      injected back as `facility_result`.
- [x] ETA reuses the app's logic: **Google-Routes** ETA for hospital/police,
      `matching.ts` per-type haversine for ambulance/tow (tow 50 vs ambulance 40 km/h);
      `fire` included only when a fire/hazmat flag is set.
- [x] Browser `HindiDispatcherSession` is **unchanged** (still uses the base GPS
      location tool); the geocode override lives only in `ExotelHindiSession`.
- [x] Startup config validation passes on defaults; raises on bad `EXOTEL_SAMPLE_RATE`
      / `EXOTEL_WS_PATH`.
- [x] `/exotel/health` reports `disabled` / `ok` / `misconfigured` per the
      enabled + validation state.
- [x] External calls have timeout + retries: transient failures retry to success;
      a 4xx does not retry.
- [x] Structured logging tags each message with the per-call id (nothing when unset).

**Live (trial ExoPhone):**

- [ ] Call connects; hear the Hindi opening line (helpline number read digit-by-digit).
- [ ] Speak an accident description in Hindi → incident type is inferred; the bot
      acknowledges with empathy, then asks exactly one follow-up.
- [ ] When asked for location, say a landmark (e.g. "NH-27 के पास, बागपत") → it's
      geocoded and accepted.
- [ ] Give a vague/ungeocodable location → the bot asks for a clearer landmark and
      retries (does not invent a location).
- [ ] Barge-in: talk over the bot → its audio stops (clear frame) and it listens.
- [ ] Complete the flow → hear the staged dispatch briefing (ambulance notified +
      approx ETA, SOP safety guidance, two-hour follow-up), then the call ends.
- [ ] Hang up mid-call → server logs show a clean teardown (`adapter.close()`), no
      stack trace escaping the endpoint.
- [ ] **Regression:** open the browser app, run a Hindi voice dispatch → behaves
      exactly as before (unchanged).

---

## Design notes / invariants

### Composition vs. inheritance (why each seam is what it is)

- **Transport is composition, via duck typing — zero base change.** The session
  depends only on the FastAPI-WebSocket *surface* (`receive` / `send_json` /
  `send_bytes` / `close`). `ExotelWebSocketAdapter` implements that surface, so it
  **is** the "Exotel transport" the same way the real FastAPI WebSocket is the
  "browser transport." The dispatcher already accepts any WS-shaped object, so no
  transport interface had to be added to `dispatcher_hindi.py`/`dispatcher_live.py`.
- **Location is composition too** — `GeocodeLocationProvider` (in `location.py`) is a
  standalone, injectable unit that owns the geocode/retry/terminate policy *once*.
  `ExotelHindiSession` **holds** one and delegates to it. The dispatcher doesn't know
  or care that a phone's location came from speech instead of GPS.
- **The subclass is the minimal inheritance seam, and only because the base can't be
  touched.** The location tools live on the base session and gate on
  `self.state.location`. Injecting a provider into the *base* would mean editing
  `dispatcher_live.py`/`dispatcher_hindi.py` — whose browser GPS round-trip is woven
  through the core `run()`/`_pump_client` loop — which the constraints forbid. So
  `ExotelHindiSession` overrides *only* the three location-gated tools
  (`_tool_get_current_location`, `_tool_find_nearest_facility`,
  `_tool_lodge_complaint`), each a one-line delegation to the composed provider.
  Reasoning, STT, TTS, dispatch, SOP and helpline logic are inherited verbatim. No
  emergency workflow is duplicated.

### Reuse (HTTP is the *only* reuse path, not a shortcut)

- **The Python voice backend has no responder / geocode / ETA / complaint logic of
  its own** — verified by search. `dispatch.py` only resolves *which agency labels*
  to notify; `dispatch_briefing.py` only *formats text* from an externally-supplied
  `dispatch_update`; the browser path answers facility/complaint/location by
  round-tripping to the **frontend**. All of that logic lives in the Next.js app (a
  **separate deployment** — Railway vs. Vercel), reachable from Python only over HTTP.
- So `services.py` calls the app's **own real endpoints** — `/api/aggregator/responders`,
  `/api/routes/matrix` (the same traffic-aware Google-Routes ETA the browser uses for
  hospital/police), `/api/potholes` — rather than reimplementing any of them. The only
  local computation is nearest-by-distance selection + the app's *own* per-type
  haversine formula for the synthetic ambulance/fire/tow posts (which have no route to
  compute — the browser falls back to the identical formula there too). Those speed/
  buffer constants **mirror `src/lib/matching.ts` by value** and must be kept in sync.
- **The adapter is transport-only.** It never makes an emergency decision; it moves
  audio and answers the browser round-trips server-side via `services.py`. Any
  responder/ETA/complaint change in the app automatically applies to phone calls too.
- **Fail cleanly, always.** Invalid packets are dropped (call stays alive);
  STT/Gemini/TTS failures are handled inside the inherited `run()`; a disconnect or
  any escaping error is caught in the endpoint and `adapter.close()` runs in
  `finally`.

### Production hardening

- **Startup validation** (`config.validate()`, run at mount when enabled): hard
  misconfig → skip the WS mount (browser service untouched, `/exotel/health` shows
  `misconfigured`); soft issues → warnings. See the env-vars section above.
- **Structured per-call logging** (`logging_utils.py`): each connection gets an
  8-hex call id set once via a `contextvars` var, and every Exotel log line across
  all modules is auto-tagged `[call=<id>]` — the read loop and per-round-trip
  service tasks inherit it because `asyncio.create_task` copies the context. Nothing
  is threaded through function signatures; no global logging config is changed.
- **HTTP timeouts + retries** (`services._send`): every external call (responders,
  routes/ETA, complaint, geocode) uses a per-attempt timeout (`EXOTEL_HTTP_TIMEOUT`)
  and bounded retries with backoff (`EXOTEL_HTTP_RETRIES`). Transient failures
  (network errors, timeouts, 5xx) retry; a 4xx client error does not. On give-up it
  returns `None` and the caller degrades honestly (haversine ETA fallback, "services
  notified" without an invented number, complaint ref still issued).
- **`/exotel/health`**: always-mounted read-only status probe (no secrets).
- **Zero blast radius when off.** `EXOTEL_ENABLED=false` (default) → `register()` is
  a no-op → the endpoint isn't mounted → the backend is unchanged. The mount itself
  is wrapped in try/except so even an import problem can't take down the browser
  service.
```
