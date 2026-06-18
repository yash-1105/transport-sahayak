# Transport Sahayak
**Assam Transport Department — Road Accident First Response (Proof of Concept)**

---

## Honesty Rules — hard constraints, never violate

| # | Rule |
|---|------|
| 1 | **No fake real-time data.** No live ambulance GPS tracking, animated moving vehicles, fake ETAs from invented traffic, or auto-escalation timers. Assam does not have this field infrastructure yet. |
| 2 | **Google traffic ETAs are allowed — with the correct label.** Drive times from the Routes API are labelled *"Est. drive time from [Facility], current traffic — vehicle leaving now."* Never as *"ambulance arriving in X."* We do not track ambulances. |
| 3 | **No phantom infrastructure.** If a feature would need field equipment that does not exist today (GPS-equipped vehicles, in-vehicle terminals), do not build it. |
| 4 | **Sample data must be labelled in code AND the UI.** Every synthetic record in `/data` carries `"sample": true`. The sample-data banner is visible on every map screen for the four synthetic layers: ambulance stations, Suraksha Mitras, accident hotspots, potholes. |
| 5 | **Dispatch alert = notification record only.** The alert records who was notified, what was sent, and when. No acknowledgement, crew status, or "en-route" status is ever implied. |
| 6 | **Google Places data is not persisted beyond place IDs** (Google ToS §3.2.4). All place details are fetched on demand with `cache: "no-store"`. |

---

## What is built

| Turn | Feature |
|------|---------|
| 1 | Project scaffold — Next.js App Router, Tailwind CSS, typed seed JSON, Zustand event log, i18n string map (EN/HI/AS), env template |
| 2 | Full-page map centred on Guwahati; Services/Accidents tab toggle; coloured marker layers with popups; persistent sample-data banner |
| 3 | Three-mode incident report: SOS (geolocation), Text (map-pin + form), Voice (Web Speech API, `en-IN` / `hi-IN` / `as-IN`) |
| 4 | Severity assessment — POST `/api/assess` calling `claude-sonnet-4-6`; heuristic fallback when API key absent or AI call fails; coloured severity card with rationale + recommended response |
| 5 | Hospital ranking (proximity + trauma level + specialty match) and police station matching; "Awaiting hospital capacity feed" placeholder for beds |
| 6 | Dispatch alert — Preview → Confirm flow with exact SMS-format message text; "Sent notifications" panel; status always "Awaiting acknowledgement" |
| 7 | Orchestration timeline — live timestamped event log showing only real system steps; intake deduplication (500 m / 10 min) with SKIPPED / PROCEEDED user choice |
| 8 | Language toggle (EN / हि / অ) in header, session-persisted; consolidated Incident Record overlay with all case data, printable and exportable as plain text |
| 9 | Migrated from react-leaflet + OSM to `@vis.gl/react-google-maps`; two-key architecture (browser key for Maps JS, server key for Routes/Places/Geocoding); centred on Guwahati, location unrestricted for testing |
| 10 | Google Places API (New) for live hospital, police, mechanic, pharmacy, and fuel markers; curated synthetic layers (ambulance stations, Suraksha Mitras, hotspots, potholes) kept with sample-data label |
| 11 | Traffic-aware hospital selection via Routes API (New): hybrid curated + Google Places candidate list, deduped by proximity/name; nearest-10 shortlist → one Route Matrix batch call (TRAFFIC_AWARE); ranked by drive time + trauma/specialty; route polylines for #1 hospital and nearest police |

---

## Tech Stack

| Concern | Technology |
|---------|-----------|
| Framework | Next.js 16 (App Router) + TypeScript |
| Styling | Tailwind CSS v4 |
| Map | `@vis.gl/react-google-maps` + Google Maps JavaScript API (`ssr: false` dynamic import) |
| POI data | Google Places API (New) — live hospitals, police, mechanics, pharmacies, fuel |
| Routing / drive times | Google Routes API (New) — `computeRouteMatrix` + `computeRoutes`, `TRAFFIC_AWARE` |
| Reverse geocoding | Nominatim / OpenStreetMap |
| Voice input | Browser Web Speech API (`en-IN`, `hi-IN`, `as-IN`) |
| AI severity | Anthropic Messages API — `claude-sonnet-4-6` via Next.js Route Handler |
| State | Zustand — append-only in-memory event log |
| i18n | Flat string map (`src/i18n/strings.ts`) — EN / HI / AS, session store via `sessionStorage` |

---

## Getting Started

```bash
cp .env.example .env.local
# Edit .env.local — add your keys (see SETUP.md)

npm install
npm run dev
```

Open `http://localhost:3000`.

**Minimum to run:** `NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY` (map tiles). Without `GOOGLE_MAPS_SERVER_KEY` the app falls back to straight-line distance ranking and hides route polylines, showing a notice in the matching panel. Without `ANTHROPIC_API_KEY` severity falls back to a rule-based heuristic automatically.

See **SETUP.md** for how to create and restrict both Google Maps keys.

---

## Security — API key architecture

| Key | Env var | Exposed to browser? | Restrictions |
|-----|---------|---------------------|--------------|
| Browser key | `NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY` | Yes (Maps JS bundle) | HTTP referrers + Maps JavaScript API only |
| Server key | `GOOGLE_MAPS_SERVER_KEY` | **Never** | IP addresses + Routes API, Places API (New), Geocoding API |

The server key is used only inside Next.js route handlers (`/api/routes/*`, `/api/places/*`). It is never included in any component, hook, or client-side module. `NEXT_PUBLIC_` is reserved for the browser key only.

`.env.local` is gitignored. `.env.example` (committed) contains only placeholder values.

---

## What still needs real integrations to go live

These are the four data gaps that make the app a PoC and not an operational system.

### 1. Ambulance station dataset (108 network)
- **Gap:** The app plots 10 synthetic ambulance stations with invented locations, staffing, and vehicle counts.
- **What is needed:** The 108 Emergency Response Service operator (GVK EMRI or successor) provides the live station roster — location, vehicle count, ALS/BLS split, and contact number for each. This is the authoritative source; the synthetic data must be replaced before any operational use.

### 2. Suraksha Mitra roster
- **Gap:** 5 synthetic highway patrollers with invented patrol stretches and contact numbers.
- **What is needed:** The Assam Transport Department / NHAI provides the current roster of deployed Suraksha Mitras with their GPS-assigned patrol zones, shift schedules, and mobile numbers. Updated whenever the roster changes.

### 3. Accident-record feed (iRAD / police FIR data)
- **Gap:** Accident hotspots and potholes are synthetic.
- **What is needed:**
  - **Hotspots:** Historical accident data from the Integrated Road Accident Database (iRAD / MoRTH) or Assam Police FIR records, aggregated per road segment to identify high-risk locations.
  - **Potholes:** Automated ingestion from the Assam PWD pothole-reporting portal, or crowdsourced via citizen reporting with moderation.

### 4. Hospital capacity feed
- **Gap:** Bed counts show "Awaiting hospital capacity feed" for every hospital.
- **What is needed:** Each participating hospital exposes a real-time endpoint (FHIR `Location` resource, or a simple REST feed) returning available ICU, trauma, and general beds. The State Health Mission or NHM aggregates this. Until the feed exists, the field remains greyed out — it is never fabricated.

---

## Other production gaps

| Item | Current PoC state | Production requirement |
|------|-------------------|----------------------|
| **In-memory state** | Zustand store — lost on refresh | PostgreSQL + append-only events table |
| **Dispatch delivery** | Generates message text and logs it — no actual send | SMS gateway (BSNL / TRAI sender ID) + push notifications; delivery callback updates acknowledgement status |
| **Nominatim geocoding** | Public Nominatim (1 req/s policy) | Self-hosted instance with Assam OSM extract, or Google Geocoding API (server key already in place) |
| **Assamese STT** | `as-IN` Web Speech locale — experimental, browser support sparse | Bhashini (MeitY) ASR API or Google Cloud Speech-to-Text `as-IN` model, called server-side |
| **GPS fleet tracking** | Deliberately omitted — no field infrastructure exists | In-vehicle GPS terminals on each ambulance and police vehicle + backend receiving periodic location fixes. Only then can the system honestly show crew movement and on-scene arrival |
| **Authentication** | None — app is entirely open | Staff accounts with role-based access (operator / supervisor / read-only) |
| **Multi-user / concurrent ops** | Single-session, no conflict resolution | Control-room model with optimistic locking on dispatch to prevent double-dispatch |
| **Severity heuristic** | Rule-based scoring on flags, keywords, person count | Calibrated against historical Assam accident data before operational use |
| **Map tiles (offline)** | Google Maps — requires network | PMTiles or State-run tile server for connectivity-gap areas |

---

## Data Attribution

| Source | Licence |
|--------|---------|
| Map tiles | © Google Maps |
| POI data | © Google Places |
| Drive times | © Google Routes API |
| Geocoding | Nominatim / © OpenStreetMap contributors (ODbL) |
| Hospital, police, ambulance, Suraksha Mitra data | **Sample data only** — replace with official Assam govt / MoRTH / GVK EMRI records before any operational use |
| Hotspot / pothole data | **Sample data only** — replace with iRAD / Assam PWD records |
