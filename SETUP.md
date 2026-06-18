# Google Maps Setup — Transport Sahayak

## Required APIs

Enable all four in [Google Cloud Console → APIs & Services → Library](https://console.cloud.google.com/apis/library):

| API | Used for | Status |
|-----|---------|--------|
| **Maps JavaScript API** | Client-side map rendering (`@vis.gl/react-google-maps`) | **Active** |
| **Places API (New)** | Live POI search — hospitals, police, mechanics, pharmacies, fuel | **Active** |
| **Routes API** | Traffic-aware drive times + route polylines (server-side) | **Active** |
| **Geocoding API** | Server-side reverse geocoding (optional, Nominatim is current fallback) | Enable now |

All four are used in the current build. Enable them all before creating keys.

---

## Two API Keys

Create **two separate keys** under APIs & Services → Credentials → Create Credentials → API Key.

### Key 1 — Browser Key (client-side map only)

| Setting | Value |
|---------|-------|
| Name | `transport-sahayak-browser` |
| Application restrictions | **HTTP referrers** |
| Allowed referrers | `localhost:3000/*` · `*.your-production-domain.in/*` |
| API restrictions | Restrict to: **Maps JavaScript API only** |

Copy into `.env.local`:
```
NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY=AIza...
```

This key is included in the browser's JavaScript bundle (`NEXT_PUBLIC_` prefix). The HTTP-referrer restriction and the single-API restriction limit its blast radius if it leaks.

### Key 2 — Server Key (route handlers only)

| Setting | Value |
|---------|-------|
| Name | `transport-sahayak-server` |
| Application restrictions | **IP addresses** (add your server / Cloud Run IP; leave empty for local dev) |
| API restrictions | Restrict to: **Routes API**, **Places API (New)**, **Geocoding API** |

Copy into `.env.local`:
```
GOOGLE_MAPS_SERVER_KEY=AIza...
```

This key is **never sent to the browser**. It is used only inside Next.js route handlers:
- `/api/places/nearby` — Places API (New) Nearby Search
- `/api/routes/matrix` — Routes API Compute Route Matrix
- `/api/routes/single` — Routes API Compute Routes (polyline)

Never add `NEXT_PUBLIC_` to this key. Never import it in a component or hook.

---

## Map ID

The app uses Google's `DEMO_MAP_ID` which enables `AdvancedMarker` without a Cloud Console Map ID. This is fine for development. For production:

1. Google Cloud Console → Google Maps Platform → Map Management → Create Map ID
2. Choose: JavaScript · Roadmap · Raster
3. Add to `.env.local`:
   ```
   NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID=your-map-id
   ```
4. Update `MapView.tsx`: replace `mapId="DEMO_MAP_ID"` with `mapId={process.env.NEXT_PUBLIC_GOOGLE_MAPS_MAP_ID}`.

---

## `.env.local` template

```bash
# Anthropic (severity assessment — optional, falls back to heuristic)
ANTHROPIC_API_KEY=sk-ant-api03-...

# Google Maps — browser key (Maps JavaScript API only, HTTP-referrer restricted)
NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY=AIza...

# Google Maps — server key (Routes + Places + Geocoding, IP-restricted, never sent to browser)
GOOGLE_MAPS_SERVER_KEY=AIza...
```

`.env.local` is gitignored. Copy from `.env.example` — that file is committed and contains only placeholders.

---

## Degraded-mode behaviour

| Missing key | What degrades |
|-------------|---------------|
| `NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY` | Map fails to load (blank tile area); all other features still work |
| `GOOGLE_MAPS_SERVER_KEY` | Places markers fall back to curated seed data only; hospital ranking uses straight-line distance; route polylines are not drawn; a notice is shown in the matching panel |
| `ANTHROPIC_API_KEY` | Severity assessment uses rule-based heuristic; UI shows `source: "HEURISTIC"` |

The app never crashes when keys are absent — it degrades gracefully with notices.

---

## ETA labelling rule (hard rule — never violate)

Google Routes API returns traffic-aware driving times. These **are allowed** in Transport Sahayak, with this exact labelling:

> **Est. drive time from [Facility Name], current traffic — vehicle leaving now**

This phrase describes a hypothetical drive time *if a vehicle left the facility at this moment*. It does NOT imply:
- that an ambulance has been dispatched
- that a vehicle is currently en route
- that we know a vehicle's location

Never write:
- ~~"ambulance arriving in X min"~~ — we do not track ambulances
- ~~"ETA X min"~~ without qualification — implies tracking
- ~~any phrasing implying we know what happens after the alert is sent~~

The dispatch alert records who was notified, what was said, and when. Nothing more.

---

## Google ToS compliance

- Place details (`name`, `address`, `openingHours`) are fetched on demand with `cache: "no-store"` and never written to disk or a database.
- Place IDs are the only field safe to persist (per §3.2.4).
- All route handler responses are not cached by Next.js.
- Attribution: "Service markers: Google Places" is shown in the map bottom bar whenever the Services tab is active.
