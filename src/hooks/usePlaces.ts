"use client";

import { useEffect, useState } from "react";
import type { GooglePlace, GooglePlaceType } from "@/lib/types";

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

export function usePlaces(lat: number, lng: number, radiusM: number) {
  const [results, setResults] = useState<PlacesResults>(EMPTY_RESULTS);
  const [loading, setLoading] = useState<PlacesLoading>(ALL_LOADING);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function fetchOne(type: GooglePlaceType) {
      try {
        const res = await fetch(
          `/api/places/nearby?type=${encodeURIComponent(type)}&lat=${lat}&lng=${lng}&radius=${radiusM}`,
          { cache: "no-store" }
        );
        const data = await res.json();
        if (cancelled) return;
        if (!res.ok) throw new Error(data.error ?? res.statusText);
        setResults((prev) => ({ ...prev, [type]: (data.places ?? []) as GooglePlace[] }));
      } catch (err) {
        console.error(`[usePlaces] ${type}:`, err);
        if (!cancelled) setHasError(true);
      } finally {
        if (!cancelled) setLoading((prev) => ({ ...prev, [type]: false }));
      }
    }

    setResults(EMPTY_RESULTS);
    setLoading(ALL_LOADING);
    setHasError(false);

    GOOGLE_PLACE_TYPES.forEach(fetchOne);

    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lng, radiusM]);

  const anyLoading = Object.values(loading).some(Boolean);

  return { results, loading, anyLoading, hasError, allDone: !anyLoading };
}

// ── Layer → place-type mapping ────────────────────────────────────────────────

export const LAYER_TO_PLACE_TYPE: Partial<Record<string, GooglePlaceType>> = {
  HOSPITAL:   "hospital",
  POLICE:     "police",
  MECHANIC:   "car_repair",
  PHARMACY:   "pharmacy",
  GAS_STATION: "gas_station",
};
