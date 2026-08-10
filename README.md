# Transport Sahayak

A **road-accident first-response** proof-of-concept for the Guwahati metropolitan area, Assam
(the NH-27 / AT Road urban corridor), built for the Assam Transport Department. It is a live map + incident console that a
control-room operator, a citizen, or a community volunteer can use to **report a crash, assess its
severity, find the nearest hospital/police with real traffic-aware drive times, and log a dispatch
notification** — plus a conversational AI call-taker that can take the whole report by voice in
English or Hindi.

> **Honesty over impressiveness.** This is a POC and it never fakes real-time data. There is no live
> ambulance GPS, no invented ETAs, no "dispatched & tracked" status. Drive times are clearly labelled
> as *estimates* ("vehicle leaving now"), a dispatch is only ever a *notification record*, and all
> synthetic/sample data is labelled as such in the UI. See [Ground rules](#ground-rules) and
> [`CLAUDE.md`](./CLAUDE.md) for the full contract.

---

## What's in the app

**Map & incident reporting**
- Interactive Google map of the corridor with responder/service layers (hospitals, police, ambulance
  posts, fire, towing, mechanics, pharmacies, fuel) and accident layers (blackspots, potholes,
  citizen-reported accidents).
- Report an incident three ways — **SOS** (one tap + geolocation), **Text** (pin on map + describe),
  or **Voice** (speech-to-text into the description).
- **Rule-first severity assessment** and **hospital + police matching** with **traffic-aware drive
  times** (Google Routes API), route polylines, and a printable incident record.
- Real-time incident-classification hints as you type, duplicate detection, and an append-only event
  timeline.

**AI voice dispatcher** (a separate Python service)
- A full conversational call-taker (`WS /ws/dispatcher`) that asks questions, fills the incident form,
  and submits only after the caller confirms — in **English** (Gemini Live native-audio) or **Hindi**
  (Sarvam Saaras STT → Gemini Flash reasoning → Sarvam Bulbul TTS).

**Accounts & roles**
- **Citizens** get an email/password account with a personal safety profile.
- **Suraksha Mitra volunteers** register as community first-responders, set a **base location** (GPS or
  map pin) and a **coverage radius**, and appear as a map layer (personal details stay private —
  operators see full detail, the public sees only first name + coverage + first-aid status).
- **Operators** (an email allowlist) get an extra **Network** tab: the Signals/Aggregator dispatch
  dashboard, **Post-Call Analytics** for the voice dispatcher, and an **Ambiguity review** that flags
  likely-duplicate reports with a side-by-side compare (descriptions, locations, and call transcripts)
  so the operator can judge before de-duping.

**Data backbone (Signals DPG)**
- All responder/service entities are served from a **Signals DPG** instance (an open Digital Public
  Good). Google Places feeds it via an ingestion sync; the app also *mirrors* incidents, dispatches,
  and volunteer registrations into it. The app degrades gracefully with Signals down — the map just
  shows empty layers, nothing breaks.

---

## Architecture

Three deployable pieces plus two managed services:

```
                    ┌─────────────────────────────┐
   Browser  ───────▶│  Next.js app  (Vercel)      │
                    │  map · reporting · matching  │
                    │  operator dashboard · accts  │
                    └──┬───────────┬───────────┬───┘
                       │           │           │
        server routes  │           │ WebSocket │  browser client
                       ▼           ▼           ▼
       ┌───────────────────┐ ┌───────────┐ ┌──────────────────┐
       │ Signals DPG        │ │ Python    │ │ Supabase          │
       │ (Railway)          │ │ voice     │ │ auth + Postgres   │
       │ responders +       │ │ backend   │ │ (accounts,        │
       │ incident/dispatch  │ │ (Railway) │ │  potholes,        │
       │ mirror             │ │ dispatcher│ │  accidents,       │
       └────────┬───────────┘ │ + STT +   │ │  metrics)         │
                │             │ severity  │ └──────────────────┘
        Google Places ingest  └─────┬─────┘
                                     │ Vertex (Gemini Live/Flash),
                                     │ Sarvam (Hindi STT/TTS),
                                     │ Google Speech-to-Text (Chirp)
```

- **Next.js frontend** — the main app; runs on Vercel (or locally). This repo.
- **Python voice backend** (`app.py` + `severity_engine/`) — the conversational dispatcher, live
  speech-to-text, and the rule-first severity engine. Runs on Railway (Vercel can't hold WebSockets).
- **Signals DPG** — a separate TypeScript service (Fastify + Postgres + Redis) that stores responders
  and mirrors incidents/dispatches. Runs on Railway. Bootstrap + deploy: [`signals/README.md`](./signals/README.md).
- **Supabase** — auth + Postgres for accounts, potholes, citizen accidents, and voice-call metrics.
- **Google APIs** — Maps JS (map tiles), Routes (drive times), Places (POI ingestion), Speech-to-Text.

---

## Tech stack

| Concern | Choice |
|---|---|
| Frontend | Next.js 16 (App Router) · TypeScript · Tailwind CSS v4 |
| Map | `@vis.gl/react-google-maps` + Google Maps JS API |
| Routing / drive times | Google Routes API (`computeRouteMatrix` / `computeRoutes`, `TRAFFIC_AWARE`) |
| Responder data | Signals DPG (Aggregator) — Google Places synced in |
| Severity | Python rule-first engine (`severity_engine/`), optional Gemini second opinion, heuristic fallback |
| Voice (English) | Gemini Live native-audio (Vertex AI) |
| Voice (Hindi) | Sarvam Saaras (STT) → Gemini Flash → Sarvam Bulbul (TTS) |
| Speech-to-text | Google Cloud Speech-to-Text V2 (Chirp) |
| Accounts / DB | Supabase (auth + Postgres, client-side RLS) |
| State | Zustand · i18n via a flat EN/HI/AS string map |

---

## Running it locally

### Prerequisites
- **Node.js 20+** and **npm** (this repo). Next.js 16.
- A **Google Maps API key** (see below) — the only thing you strictly need to see the map.
- Optional, for full functionality: a Supabase project, a running Signals DPG, and the Python voice
  backend (each degrades gracefully if absent).

### 1. Install & run the frontend
```bash
npm install
cp .env.example .env.local     # then fill in the keys you have (see below)
npm run dev                    # http://localhost:3000
```

### 2. Minimum env to get going
Put these in `.env.local`. With just the browser key you get the map and can click around; the rest
unlock more.

```bash
# Map tiles (required for the map to render at all) — browser-safe, referrer-restricted
NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY=AIza...

# Routes + Places (server-side only, never NEXT_PUBLIC_). Enables real drive times + POI ingestion.
GOOGLE_MAPS_SERVER_KEY=AIza...

# Accounts (optional) — without these the app runs, just with no login/accounts
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
NEXT_PUBLIC_OPERATOR_EMAILS=you@example.com   # who sees the operator Network tab

# Responder data (optional) — point at a running Signals DPG (see signals/README.md).
# Without these, service/responder map layers are empty but the app still works.
SIGNALS_API_URL=http://localhost:2742
SIGNALS_API_KEY=sk_signals_...
SIGNALS_AGG_ORG_ID=org_...
SIGNALS_REGISTRY_API_KEY=sk_signals_...
```

> `.env.example` is the full annotated reference (also lists the Python backend + Sarvam/Vertex vars).
> `.env` and `.env.local` are gitignored — never commit real keys.

### Degraded modes (all intentional)
| Missing | Behaviour |
|---|---|
| Browser Maps key | Map area is blank; everything else still works |
| Server Maps key | POI/responder layers hidden; hospital ranking uses straight-line distance; no polylines |
| Supabase keys | No accounts/login; guest reporting still works via anon API routes |
| Signals keys / DPG down | Responder layers empty; incident/volunteer mirror is a no-op; app unaffected |
| Severity engine unreachable | Severity falls back to a rule-based heuristic (amber "Heuristic fallback" card) |

### 3. (Optional) the Python voice backend
The voice dispatcher, live speech-to-text, and severity engine live in `app.py` + `severity_engine/`.
```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000        # http://localhost:8000/health
```
Point the frontend at it via `SEVERITY_ENGINE_URL` (severity) and the `NEXT_PUBLIC_*_WS_URL` vars
(voice). Full setup — Google service-account credentials, Vertex/Gemini, Sarvam, GCP one-time enables
— is documented inline in `.env.example` and in this file's git history / the module docstrings.

### 4. (Optional) the Signals DPG
Responder data comes from a Signals DPG instance (Fastify + Postgres + Redis, port 2742). Local
bootstrap + the Railway deploy runbook are in [`signals/README.md`](./signals/README.md).

---

## Deployment

- **Frontend → Vercel.** Set the same env vars in the Vercel project (server keys stay non-`NEXT_PUBLIC_`).
- **Python voice backend → Railway.** Vercel can't hold WebSockets open; Railway hosts the always-on
  service. Needs the Google service-account JSON + Vertex/Sarvam keys.
- **Signals DPG → Railway.** Fastify + managed Postgres + Redis. Deploy + bootstrap runbook in
  [`signals/README.md`](./signals/README.md) and the "Production deployment" section of `CLAUDE.md`.
- **Supabase** is already hosted; apply migrations from `supabase/migrations/`.

---

## Repo layout

```
src/
  app/                       Next.js App Router
    page.tsx · layout.tsx
    api/                     server route handlers
      assess/                → rule-first severity engine (proxy, heuristic fallback)
      aggregator/            → responder data + Google Places sync (Signals)
      routes/                → Google Routes (drive-time matrix + polylines)
      signals/               → publish incidents/dispatches/volunteers to Signals
      call-metrics/          → voice-dispatcher analytics (+ per-incident lookup)
      accidents/ potholes/ incident-reviews/  → Supabase-backed data
  components/
    MapView.tsx              the map: all layers, markers, popups, panels
    report/                  incident report + hospital/police matching
    auth/                    accounts, Suraksha Mitra volunteer form + map picker
    operator/                Network dashboard, call analytics, ambiguity review
    map/ signals/            clustered layers, Signals dashboard panel
  hooks/ lib/ store/ i18n/   data hooks, domain logic, Zustand stores, EN/HI/AS strings

severity_engine/  app.py     Python: rule-first severity + voice dispatcher (English/Hindi) + Chirp STT
signals/                     Signals DPG network contract + bootstrap/deploy runbook
supabase/migrations/         accounts, volunteer location/radius, operator analytics/reviews
data/                        labelled sample seed data (hospitals, ambulance posts, blackspots, …)
CLAUDE.md                    the authoritative deep-dive on every subsystem + the hard rules
```

---

## Ground rules

These are enforced throughout the code and UI:

1. **No fake real-time data.** No live vehicle GPS, no invented ETAs, no auto-escalation timers. (A
   clearly-labelled *simulated* ambulance marker may animate along a highlighted route as a visual aid.)
2. **Drive times are estimates**, labelled `"Est. drive time from [Facility], current traffic — vehicle
   leaving now."` — never "ambulance arriving in X." We do not track vehicles.
3. **No phantom infrastructure.** If a feature would need field equipment that doesn't exist, it isn't built.
4. **Sample data is labelled** — in code and with an amber banner in the UI.
5. **A dispatch is a notification record only** — who was notified, what, when. No acknowledgement or
   en-route status is ever implied.

---

## More detail

- **[`CLAUDE.md`](./CLAUDE.md)** — the authoritative, exhaustive context: every subsystem, the voice
  pipelines, the Signals integration, the API-key architecture, and the production-deployment notes.
- **[`signals/README.md`](./signals/README.md)** — the Signals DPG integration + bootstrap/deploy runbook.
