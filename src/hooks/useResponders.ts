"use client";

// Fetches ALL responder/service data from the Aggregator DPG — the single
// source of responder truth (curated + synthetic + Google-synced POIs).
// Replaces the old usePlaces hook (direct Google Places consumption) and the
// bundled data/*.json imports; output shapes are unchanged so map layers and
// matching components render exactly as before.

import { useEffect, useState } from "react";
import type {
  AmbulanceStation,
  FireStation,
  GooglePlace,
  GooglePlaceType,
  Hospital,
  PoliceStation,
  TowingStation,
} from "@/lib/types";

export const GOOGLE_PLACE_TYPES = [
  "hospital",
  "police",
  "car_repair",
  "pharmacy",
  "gas_station",
] as const satisfies readonly GooglePlaceType[];

export type PlacesResults = Record<GooglePlaceType, GooglePlace[]>;
export type PlacesLoading = Record<GooglePlaceType, boolean>;

const EMPTY_RESULTS: PlacesResults = {
  hospital: [],
  police: [],
  car_repair: [],
  pharmacy: [],
  gas_station: [],
};

const ALL_LOADING: PlacesLoading = {
  hospital: true,
  police: true,
  car_repair: true,
  pharmacy: true,
  gas_station: true,
};

const ALL_DONE: PlacesLoading = {
  hospital: false,
  police: false,
  car_repair: false,
  pharmacy: false,
  gas_station: false,
};

export interface RespondersData {
  hospitals: Hospital[];
  policeStations: PoliceStation[];
  ambulanceStations: AmbulanceStation[];
  fireStations: FireStation[];
  towingStations: TowingStation[];
}

const EMPTY_DATA: RespondersData = {
  hospitals: [],
  policeStations: [],
  ambulanceStations: [],
  fireStations: [],
  towingStations: [],
};

export function useResponders() {
  const [results, setResults] = useState<PlacesResults>(EMPTY_RESULTS);
  const [data, setData] = useState<RespondersData>(EMPTY_DATA);
  const [loading, setLoading] = useState<PlacesLoading>(ALL_LOADING);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch("/api/aggregator/responders", { cache: "no-store" });
        const payload = await res.json();
        if (cancelled) return;
        if (!res.ok) throw new Error(payload.error ?? res.statusText);
        setResults((payload.places ?? EMPTY_RESULTS) as PlacesResults);
        setData({
          hospitals: payload.hospitals ?? [],
          policeStations: payload.policeStations ?? [],
          ambulanceStations: payload.ambulanceStations ?? [],
          fireStations: payload.fireStations ?? [],
          towingStations: payload.towingStations ?? [],
        });
        if (payload.source === "unavailable" || payload.source === "no_key") setHasError(true);
      } catch (err) {
        console.error("[useResponders]", err);
        if (!cancelled) setHasError(true);
      } finally {
        if (!cancelled) setLoading(ALL_DONE);
      }
    }

    void load();
    return () => { cancelled = true; };
  }, []);

  const anyLoading = Object.values(loading).some(Boolean);

  return { results, data, loading, anyLoading, hasError, allDone: !anyLoading };
}

// ── Layer → place-type mapping (unchanged) ───────────────────────────────────

export const LAYER_TO_PLACE_TYPE: Partial<Record<string, GooglePlaceType>> = {
  HOSPITAL:   "hospital",
  POLICE:     "police",
  MECHANIC:   "car_repair",
  PHARMACY:   "pharmacy",
  GAS_STATION: "gas_station",
};
