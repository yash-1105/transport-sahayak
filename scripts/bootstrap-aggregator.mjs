/**
 * Bootstrap the Aggregator DPG's responder registry from the ingestion
 * fixtures in data/ (curated hospitals/police + synthetic ambulance/fire/
 * towing posts). Google-Places POIs are ingested separately by the sync
 * pipeline (src/lib/aggregator/googleSync.ts / POST /api/aggregator/sync).
 *
 * Run:  SIGNALS_REGISTRY_API_KEY=sk_signals_... node scripts/bootstrap-aggregator.mjs
 *
 * Idempotent upsert — items are matched by "Facility ID"; existing items are
 * PATCHed with the full fixture state (so schema additions propagate), new
 * ones are created. The data/*.json files are INGESTION FIXTURES ONLY: the
 * application never reads them at runtime — the Aggregator DPG is the sole
 * source of responder data.
 */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const API_URL = process.env.SIGNALS_API_URL ?? "http://localhost:2742";
const API_KEY = process.env.SIGNALS_REGISTRY_API_KEY;
if (!API_KEY) {
  console.error("SIGNALS_REGISTRY_API_KEY is required (the aggregator/registry user's key)");
  process.exit(1);
}

const NETWORK = "road_safety";
const DOMAIN = "responder";
const ITEM_TYPE = "responder_facility_1.0";

const readSeed = (file, key) => JSON.parse(readFileSync(join(ROOT, "data", file), "utf8"))[key];

const facilities = [];

for (const h of readSeed("hospitals.json", "hospitals")) {
  facilities.push({
    id: h.id,
    state: {
      "Facility ID": h.id,
      "Facility Name": h.name,
      "Facility Type": "HOSPITAL",
      "District": h.district,
      ...(h.phone ? { "Contact Number": h.phone } : {}),
      ...(h.traumaLevel ? { "Trauma Level": h.traumaLevel } : {}),
      ...(Array.isArray(h.specialty) && h.specialty.length ? { "Specialties": h.specialty } : {}),
      "Capability Source": "curated",
      "Data Origin": "curated",
      "Sample Data": h.sample === true,
      "Attributes": {
        shortName: h.shortName,
        type: h.type,
        traumaCapable: h.traumaCapable === true,
        beds: h.beds ?? 0,
        emergency: h.emergency ?? "",
      },
    },
    locations: [{ lat: h.lat, lng: h.lng, label: h.name }],
  });
}

for (const p of readSeed("police-stations.json", "policeStations")) {
  facilities.push({
    id: p.id,
    state: {
      "Facility ID": p.id,
      "Facility Name": p.name,
      "Facility Type": "POLICE",
      "District": p.district,
      ...(p.phone ? { "Contact Number": p.phone } : {}),
      "Capability Source": "curated",
      "Data Origin": "curated",
      "Sample Data": p.sample === true,
      "Attributes": { circle: p.circle ?? "", emergency: p.emergency ?? "" },
    },
    locations: [{ lat: p.lat, lng: p.lng, label: p.name }],
  });
}

const syntheticLayers = [
  ["ambulance-stations.json", "ambulanceStations", "AMBULANCE_STATION",
    (s) => ({ ambulanceCount: s.ambulanceCount ?? 1, types: s.types ?? ["BLS"], operatingHours: s.operatingHours ?? "24x7", notes: s.notes ?? "" })],
  ["fire-stations.json", "fireStations", "FIRE_STATION",
    (s) => ({ vehicleTypes: s.vehicleTypes ?? [], operatingHours: s.operatingHours ?? "24x7", notes: s.notes ?? "" })],
  ["towing-stations.json", "towingStations", "TOWING_STATION",
    (s) => ({ vehicleTypes: s.vehicleTypes ?? [], operatingHours: s.operatingHours ?? "24x7", notes: s.notes ?? "" })],
];

for (const [file, key, type, attrsOf] of syntheticLayers) {
  for (const st of readSeed(file, key)) {
    facilities.push({
      id: st.id,
      state: {
        "Facility ID": st.id,
        "Facility Name": st.name,
        "Facility Type": type,
        "District": st.district,
        ...(st.contactNumber ? { "Contact Number": st.contactNumber } : {}),
        "Capability Source": "synthetic",
        "Data Origin": "synthetic",
        "Sample Data": true,
        "Attributes": attrsOf(st),
      },
      locations: [{ lat: st.lat, lng: st.lng, label: st.name }],
    });
  }
}

const api = async (path, init = {}) => {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", "x-api-key": API_KEY, ...(init.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${init.method ?? "GET"} ${path} → ${res.status}: ${JSON.stringify(body)}`);
  return body;
};

// Existing items keyed by Facility ID (idempotent upsert).
const existing = new Map();
for (let offset = 0; ; offset += 100) {
  const q = new URLSearchParams({ item_network: NETWORK, item_domain: DOMAIN, item_type: ITEM_TYPE, limit: "100", offset: String(offset) });
  const page = await api(`/api/v1/item/fetch?${q}`);
  const items = page.items ?? [];
  for (const it of items) {
    const fid = it.item_state?.["Facility ID"];
    if (fid) existing.set(fid, it.item_id);
  }
  if (items.length < 100) break;
}
console.log(`found ${existing.size} existing responder items`);

let created = 0;
let updated = 0;
for (const f of facilities) {
  const itemId = existing.get(f.id);
  if (itemId) {
    await api(`/api/v1/item/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify({ item_state: f.state, item_locations: f.locations }),
    });
    updated += 1;
  } else {
    await api("/api/v1/item/create", {
      method: "POST",
      body: JSON.stringify({ item_network: NETWORK, item_domain: DOMAIN, item_type: ITEM_TYPE, item_state: f.state, item_locations: f.locations }),
    });
    created += 1;
  }
}
console.log(`done: ${created} created, ${updated} updated (fixtures: ${facilities.length})`);
