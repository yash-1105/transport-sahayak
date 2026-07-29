# DPG integration — `road_safety` network

Two clearly separated DPG responsibilities on one locally running Signals
instance (the `signals-dpg` repo — Fastify + Postgres + Redis, port 2742):

```
Google Places API ──▶ Aggregator DPG (responder domain) ──▶ POC map + matching
                          ▲   owns ALL responder/service entities
                          │   (curated · synthetic · Google-synced)
Incident Report ──▶ Signals DPG (control_room domain) ──▶ dispatch actions
                          owns the incident lifecycle only;   target Aggregator items
```

- **Responder registry** (`responder` domain): every responder/service entity
  the POC displays or matches against. Per the upstream DPG model, the
  entities are **stored in the Signals instance** (the stack's only system of
  record) and **attributed to the Aggregator organization** — their owner is
  onboarded under the aggregator org (`onboarded_by_org_id`), which is what
  scopes them onto the aggregator dashboard. Synchronized from Google Places
  by `src/lib/aggregator/googleSync.ts` (24h TTL, auto-triggered when stale;
  manual `POST /api/aggregator/sync`); served to the POC exclusively via
  `GET /api/aggregator/responders` (`src/lib/aggregator/client.ts` maps items
  to the app's existing TS interfaces). The POC has NO other responder source
  — no bundled JSON, no direct Google Places consumption.
- **Identity tiers (upstream-faithful, 2026-07 re-attribution):** the
  **Aggregator organization only reads** (dashboard/rollups — matching
  upstream's "no write access to the Signals Stack" MVP rule for
  aggregators). All registry **writes** (bootstrap + Google sync) run under a
  dedicated `network_service`-typed org (`transport-sahayak-ingest`) whose
  service user remains aggregator-onboarded for dashboard attribution. The
  control-room user publishes only its own `control_room` incident items —
  participant-style self-writes, not aggregator writes.
- **Signals DPG** (`control_room` domain): incidents, assessments, dispatch
  actions — published fire-and-forget by `src/lib/signalsPublisher.ts`; an
  outage can only change a badge state, never break the incident flow.
- **Long-term shape:** in a production network, real facilities would be
  onboarded as participants owning their own responder items; the Google sync
  then becomes a discovery/bootstrap aid rather than the source of truth. The
  ingestion-service identity is the stand-in for that today.

## Integration rules (all enforced in code)

- **Dispatch = notification record only** (hard rule 5). A dispatch is mirrored
  as a `dispatch` **action** whose status stays at Signals' initial `created`
  forever — it is never advanced, because a status update may only be made by
  the *target* item's owner, which would fabricate a facility-side
  acknowledgement. Targets are Aggregator-owned responder items resolved live
  by Facility ID (curated `hosp-001`-style ids, Google-synced `gp-<placeId>`).
- **Google Places is ingestion-only** (amended hard rule 6). Places content is
  persisted only in the Aggregator registry, minimum fields, `Last Synced At`
  stamped, 24h re-sync; live open-now status is never persisted; the frontend
  never sees a raw Places response.
- **Sample data labelled** (hard rule 4): synthetic ambulance/fire/towing posts
  carry `"Sample Data": true` in their `item_state`.

## Files

| File | What |
|---|---|
| `road_safety.network.json` | The network contract (committed). Domains `control_room` (`incident_1.0`) + `responder` (`responder_facility_1.0`), action `dispatch`. Signals validates it at boot — a boot failure (`NETWORK_CONFIG_INVALID`) means this file is broken. |
| *(registry-map.json — removed)* | Dispatch targets are now resolved live from the Aggregator by Facility ID (`publish-dispatch` keeps a 5-minute in-memory map). No generated file, nothing to regenerate. |

## Bootstrap runbook (once per Signals DB)

All commands from the `signals-dpg` repo root unless noted.

1. **Point Signals at this network** — in `signals-dpg/.env`:

   ```bash
   SERVED_DOMAINS="road_safety/control_room,road_safety/responder"
   NETWORK_CONFIG_SOURCE="local"
   NETWORK_CONFIG_LOCAL_FILE="/absolute/path/to/transport-new/signals/road_safety.network.json"
   ```

2. **DB + API up**:

   ```bash
   docker compose up -d db redis
   pnpm db:push:api && pnpm db:init:api
   pnpm db:seed:services:api   # prints the network_service org id + sk_signals_ key ONCE — capture both
   pnpm dev:api                # boot success = network.json is valid; check GET http://localhost:2742/
   ```

3. **Aggregator org** (`domains` is a TOP-LEVEL field — putting it in
   `metadata` gets overwritten with `[]` and the dashboard 400s):

   ```bash
   curl -X POST http://localhost:2742/api/v1/admin/aggregator/upsert \
     -H "content-type: application/json" \
     -H "x-api-key: <service key from step 2>" \
     -H "x-acting-org-id: <network_service org id from step 2>" \
     -d '{"external_id":"transport-sahayak","name":"Assam Transport Sahayak",
          "slug":"assam-transport","domains":["control_room","responder"],
          "metadata":{"network":"road_safety","domain":"control_room"}}'
   # → {"org_id":"org_..."}  ← this is SIGNALS_AGG_ORG_ID
   ```

4. **Two participant users** — items published by users onboarded by the
   aggregator org are what the dashboard counts (`onboarded_by_org_id`), and
   non-admin API keys are domain-locked to the first domain they create in, so
   incidents and registry facilities need separate users:

   ```bash
   # x-acting-org-id: the AGG org from step 3, for both calls
   curl -X POST http://localhost:2742/api/v1/admin/participant ... \
     -d '{"email":"control-room@transport-sahayak.local","name":"Assam Transport Control Room",
          "terms_accepted":true,"privacy_accepted":true,"channel":"bulk"}'
   curl -X POST http://localhost:2742/api/v1/admin/participant ... \
     -d '{"email":"responder-registry@transport-sahayak.local","name":"Transport Sahayak Responder Registry",
          "terms_accepted":true,"privacy_accepted":true,"channel":"bulk"}'
   ```

5. **Mint API keys + memberships** — direct SQL against Postgres :5433,
   mirroring `apps/api/scripts/seed_service_users.ts` exactly (better-auth
   stores `base64url(sha256(raw))`, `start = raw[0:6]`, prefix `sk_signals_`,
   `config_id 'default'`; columns are snake_case). Memberships encode the
   identity tiers:
   - control-room user → `member` of the **AGG org** (role `service`) — needed
     for the dashboard's acting-org check. Its key becomes `SIGNALS_API_KEY`.
   - registry/ingestion user → `member` of a dedicated **network_service org**
     (`INSERT INTO organization (id, slug, name, type, created_at) VALUES
     ('org_...', 'transport-sahayak-ingest', 'Transport Sahayak Ingestion
     Service', 'network_service', NOW())`), NOT of the aggregator org — the
     aggregator tier stays read-only, per upstream. Its `onboarded_by_org_id`
     stays the AGG org (dashboard attribution). Its key becomes
     `SIGNALS_REGISTRY_API_KEY`.

6. **Seed the responder registry** (from the transport-new repo root):

   ```bash
   SIGNALS_REGISTRY_API_KEY=sk_signals_... node scripts/bootstrap-aggregator.mjs
   # idempotent upsert of the 58 curated/synthetic fixtures from data/ (ingestion fixtures only —
   # the app never reads data/*.json at runtime). Then populate Google POIs:
   curl -X POST http://localhost:3000/api/aggregator/sync
   # (also runs automatically whenever the responders route sees Google data older than 24h)
   ```

7. **transport-new env** (`.env.local`): `SIGNALS_API_URL`, `SIGNALS_API_KEY`,
   `SIGNALS_AGG_ORG_ID`, `SIGNALS_REGISTRY_API_KEY` — see `.env.example`.
   Restart the dev server.

## What gets published

| Console event | Signals write |
|---|---|
| Incident submitted (all 4 report modes) | `POST /api/v1/item/create` → `incident_1.0`, exact coords in `item_locations` |
| Severity assessed (incl. honest offline stub) | `PATCH /api/v1/item/:id` with the **full** merged state |
| Dispatch alert logged (hospital + police) | `POST /api/v1/action/perform` → `dispatch` action, status `created` |

Deliberately **not** published: HOSPITAL_MATCHED / ROUTE_ESTIMATED (ephemeral
live-traffic numbers — mirroring them as stored facts invites staleness, and
matched lists contain Google names), DUPLICATE_FLAGGED / NOTE_ADDED (local
console UX detail).

## Troubleshooting

- Dashboard 400 `NO_DOMAINS_CONFIGURED` → step 3's `domains` ended up empty
  (see the top-level-field warning there).
- Dashboard shows zero incidents but publishes succeed → items were created by
  a user whose `onboarded_by_org_id` is not the AGG org (step 4 skipped).
- `publish-dispatch` returns `no_registry` → run step 6.
- Dashboard rollups lag fresh publishes → per-org cache (TTL 3600s by
  default); the Network tab's Refresh button passes `?refresh=true`.
- Deleted items still show on the dashboard after Refresh → Signals' metrics
  recompute upserts `item_metrics` rows but never removes rows for hard-deleted
  items (verified live). Purge them directly:
  `DELETE FROM item_metrics WHERE item_id IN (...)` on Postgres :5433 (and the
  corresponding `action_events` / `item_actions` rows if the deleted item had
  dispatch actions), then Refresh again.
- Signals DB reset → repeat steps 2–6 (org/user ids and all item ids change;
  the seed prints new keys).
