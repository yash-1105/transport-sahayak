# DPG Demo — cheat sheet (keep this open in a terminal during the demo)

## Before the mentor arrives

```bash
# All three services must be up:
curl -s http://localhost:2742/ | head -c 120 ; echo    # Signals DPG → status ok, road_safety domains
curl -s http://localhost:8000/health                   # Python severity engine → {"ok":true,...}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/   # Next.js app → 200

# If any are down:
#   Signals:  cd ~/Desktop/EY/transport-sahayak/signals-dpg && docker compose up -d db redis && pnpm dev:api
#   Engine:   cd ~/Desktop/transport-new && python3 -m uvicorn app:app --port 8000
#   App:      cd ~/Desktop/transport-new && npm run dev

# Load the demo keys into this terminal:
cd ~/Desktop/transport-new
export $(grep -E '^SIGNALS_(API_KEY|AGG_ORG_ID)=' .env.local | xargs)

# Browser tabs to have ready:
#   1. http://localhost:3000            (the app)
#   2. http://localhost:2742/api/reference   (Signals' own API docs — optional wow-tab)
#   3. This terminal + an editor showing signals/road_safety.network.json
```

## Curls used mid-demo (paste in order)

```bash
# [Act 1] Prove the instance serves our network
curl -s http://localhost:2742/ | python3 -m json.tool

# [Act 2] Show the incident as a schema-validated DPG item (run AFTER submitting in the UI)
curl -s "http://localhost:2742/api/v1/item/fetch?item_network=road_safety&item_domain=control_room&item_type=incident_1.0&limit=5" \
  -H "x-api-key: $SIGNALS_API_KEY" | python3 -m json.tool

# [Act 2] Show the dispatch as a cross-domain ACTION record (run AFTER Confirm & Log Alert)
curl -s "http://localhost:2742/api/v1/action/fetch?action_type=dispatch&ownership_role=initiated" \
  -H "x-api-key: $SIGNALS_API_KEY" | python3 -m json.tool

# [Act 4] Schema strictness — the DPG REJECTS anything outside the contract
curl -s -X POST http://localhost:2742/api/v1/item/create \
  -H "content-type: application/json" -H "x-api-key: $SIGNALS_API_KEY" \
  -d '{"item_network":"road_safety","item_domain":"control_room","item_type":"incident_1.0","item_state":{"Incident ID":"X","Reported At":"2026-07-20T12:00:00Z","Report Mode":"TEXT","Location Label":"x","Description":"x","Severity":"UNKNOWN","Bogus Field":"nope"}}'
# → {"error":"INVALID_ITEM_STATE", ...}

# [Act 4] Resilience — kill Signals, show the app is unaffected, then restart
lsof -ti :2742 | xargs kill          # Signals down
# ... demo the app still working, then:
cd ~/Desktop/EY/transport-sahayak/signals-dpg && nohup pnpm dev:api > /tmp/signals.log 2>&1 &
sleep 8 && curl -s http://localhost:2742/ | head -c 80   # back up
```

## Reset to a clean dashboard (run before the demo if there's leftover data)

```bash
docker exec dpg-db psql -U postgres -d postgresdb -c "DELETE FROM action_events WHERE action_id IN (SELECT action_id FROM item_actions); DELETE FROM item_actions; DELETE FROM items WHERE item_domain='control_room'; DELETE FROM item_metrics WHERE item_domain='control_room';"
curl -s "http://localhost:2742/api/v1/aggregator/dashboard?refresh=true&limit=1" \
  -H "x-api-key: $SIGNALS_API_KEY" -H "x-acting-org-id: $SIGNALS_AGG_ORG_ID" > /dev/null
```
