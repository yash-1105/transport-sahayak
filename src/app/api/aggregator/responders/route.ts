// GET: the POC's single source of responder/service data — served from the
// Aggregator DPG. Curated hospitals/police, synthetic ambulance/fire/towing
// posts, and Google-Places-synced POIs all come back mapped to the exact
// TypeScript shapes the app already renders, so components are agnostic to
// the storage change.
//
// Query:
//   for_matching=1  → hospital list for emergency matching: skips the
//                     corridor display filter and drops specialty clinics
//                     by name (same rules the old Places route applied).
//
// Degradation: if the Aggregator is unreachable, the last successful
// payload (module cache) is served with source:"aggregator_cached"; with no
// cache yet, empty lists + source:"unavailable" — the map renders empty
// layers and incident reporting is unaffected. A stale Google sync
// (>24 h old or never run) triggers a background re-sync, fire-and-forget.

import { NextRequest, NextResponse } from "next/server";
import {
  FACILITY_TYPE_TO_PLACE_TYPE,
  aggregatorEnabled,
  fetchAllResponderItems,
  itemDataOrigin,
  itemFacilityType,
  toAmbulanceStation,
  toFireStation,
  toGooglePlace,
  toHospital,
  toPoliceStation,
  toSurakshaMitra,
  toTowingStation,
  type ResponderItem,
} from "@/lib/aggregator/client";
import { isSyncStale, runGoogleSync } from "@/lib/aggregator/googleSync";
import type { GooglePlace, GooglePlaceType } from "@/lib/types";

const CORRIDOR_MAX_KM = 15;
const SPECIALTY_EXCLUDE_RE =
  /\b(eye|ophthal|dental|teeth|skin|derma|hair|ivf|fertility|cosmetic|vision|maternity)\b/i;

interface RespondersPayload {
  source: string;
  hospitals: ReturnType<typeof toHospital>[];
  policeStations: ReturnType<typeof toPoliceStation>[];
  ambulanceStations: ReturnType<typeof toAmbulanceStation>[];
  fireStations: ReturnType<typeof toFireStation>[];
  towingStations: ReturnType<typeof toTowingStation>[];
  surakshaMitras: ReturnType<typeof toSurakshaMitra>[];
  places: Record<GooglePlaceType, GooglePlace[]>;
}

const EMPTY_PLACES: Record<GooglePlaceType, GooglePlace[]> = {
  hospital: [],
  police: [],
  car_repair: [],
  pharmacy: [],
  gas_station: [],
};

// Last-good payload so a brief Aggregator outage doesn't blank the map.
let lastGood: { payload: RespondersPayload; at: number } | null = null;

function buildPayload(items: ResponderItem[], forMatching: boolean): RespondersPayload {
  const payload: RespondersPayload = {
    source: "aggregator",
    hospitals: [],
    policeStations: [],
    ambulanceStations: [],
    fireStations: [],
    towingStations: [],
    surakshaMitras: [],
    places: {
      hospital: [],
      police: [],
      car_repair: [],
      pharmacy: [],
      gas_station: [],
    },
  };

  for (const it of items) {
    const type = itemFacilityType(it);
    const origin = itemDataOrigin(it);

    if (origin === "google_places" || origin === "unverified") {
      const placeType = FACILITY_TYPE_TO_PLACE_TYPE[type];
      if (!placeType) continue;
      payload.places[placeType].push(toGooglePlace(it, placeType));
      continue;
    }

    switch (type) {
      case "HOSPITAL": payload.hospitals.push(toHospital(it)); break;
      case "POLICE": payload.policeStations.push(toPoliceStation(it)); break;
      case "AMBULANCE_STATION": payload.ambulanceStations.push(toAmbulanceStation(it)); break;
      case "FIRE_STATION": payload.fireStations.push(toFireStation(it)); break;
      case "TOWING_STATION": payload.towingStations.push(toTowingStation(it)); break;
      case "SURAKSHA_MITRA": payload.surakshaMitras.push(toSurakshaMitra(it)); break;
    }
  }

  // Same presentation rules the old Places route applied.
  for (const key of Object.keys(payload.places) as GooglePlaceType[]) {
    if (forMatching && key === "hospital") {
      payload.places.hospital = payload.places.hospital.filter(
        (p) => !SPECIALTY_EXCLUDE_RE.test(p.name),
      );
    } else {
      payload.places[key] = payload.places[key]
        .filter((p) => (p.distanceToCorridorKm ?? 0) <= CORRIDOR_MAX_KM)
        .sort((a, b) => (a.distanceToCorridorKm ?? 0) - (b.distanceToCorridorKm ?? 0));
    }
  }

  return payload;
}

export async function GET(req: NextRequest) {
  const forMatching = req.nextUrl.searchParams.get("for_matching") === "1";

  if (!aggregatorEnabled()) {
    return NextResponse.json({
      source: "no_key",
      hospitals: [], policeStations: [], ambulanceStations: [], fireStations: [], towingStations: [], surakshaMitras: [],
      places: EMPTY_PLACES,
    });
  }

  try {
    const items = await fetchAllResponderItems();
    // Keep Google-sourced content fresh without blocking the response.
    if (isSyncStale(items)) void runGoogleSync().catch(() => {});
    const payload = buildPayload(items, forMatching);
    if (!forMatching) lastGood = { payload, at: Date.now() };
    return NextResponse.json(payload);
  } catch {
    if (lastGood && !forMatching) {
      return NextResponse.json({ ...lastGood.payload, source: "aggregator_cached" });
    }
    return NextResponse.json({
      source: "unavailable",
      hospitals: [], policeStations: [], ambulanceStations: [], fireStations: [], towingStations: [], surakshaMitras: [],
      places: EMPTY_PLACES,
    });
  }
}
