// Server-only client for the Aggregator DPG layer — the responder/service
// side of the road_safety network. The Aggregator owns every responder
// entity the POC displays or matches against (curated seeds, synthetic
// posts, and Google-Places-synced POIs); the POC never reads responder data
// from anywhere else. Writes use the aggregator/registry identity
// (SIGNALS_REGISTRY_API_KEY); reads use whichever key is available.
//
// Signals (control_room domain) keeps the incident lifecycle only — see
// src/lib/signalsClient.ts. This module is the ONLY place responder items
// are created, updated, or mapped to the app's TypeScript interfaces.

import type {
  AmbulanceStation,
  FireStation,
  GooglePlace,
  GooglePlaceType,
  Hospital,
  PoliceStation,
  TowingStation,
} from "@/lib/types";
import {
  SIGNALS_NETWORK,
  SIGNALS_RESPONDER_DOMAIN,
  SIGNALS_RESPONDER_TYPE,
} from "@/lib/signalsClient";

const TIMEOUT_MS = 5000;

const apiUrl = () => process.env.SIGNALS_API_URL ?? "http://localhost:2742";
const writeKey = () => process.env.SIGNALS_REGISTRY_API_KEY ?? process.env.SIGNALS_API_KEY;

export interface ResponderItem {
  item_id: string;
  item_state: Record<string, unknown>;
  item_locations?: Array<{ lat: number; lng: number; label?: string }>;
}

export function aggregatorEnabled(): boolean {
  return Boolean(writeKey());
}

async function aggregatorFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = writeKey();
  if (!key) throw new Error("aggregator key not configured");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${apiUrl()}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-api-key": key,
        ...(init.headers ?? {}),
      },
      cache: "no-store",
      signal: controller.signal,
    });
    const body = (await res.json().catch(() => ({}))) as T & { error?: string };
    if (!res.ok) throw new Error(`${res.status} ${body?.error ?? ""}`.trim());
    return body;
  } finally {
    clearTimeout(timer);
  }
}

/** Every responder item in the Aggregator, paginated to completion. */
export async function fetchAllResponderItems(): Promise<ResponderItem[]> {
  const items: ResponderItem[] = [];
  for (let offset = 0; ; offset += 100) {
    const q = new URLSearchParams({
      item_network: SIGNALS_NETWORK,
      item_domain: SIGNALS_RESPONDER_DOMAIN,
      item_type: SIGNALS_RESPONDER_TYPE,
      limit: "100",
      offset: String(offset),
    });
    const page = await aggregatorFetch<{ items?: ResponderItem[] }>(`/api/v1/item/fetch?${q}`);
    const batch = page.items ?? [];
    items.push(...batch);
    if (batch.length < 100) break;
  }
  return items;
}

export async function createResponderItem(
  state: Record<string, unknown>,
  locations: Array<{ lat: number; lng: number; label?: string }>,
): Promise<string> {
  const res = await aggregatorFetch<{ item_id: string }>("/api/v1/item/create", {
    method: "POST",
    body: JSON.stringify({
      item_network: SIGNALS_NETWORK,
      item_domain: SIGNALS_RESPONDER_DOMAIN,
      item_type: SIGNALS_RESPONDER_TYPE,
      item_state: state,
      item_locations: locations,
    }),
  });
  return res.item_id;
}

export async function updateResponderItem(
  itemId: string,
  state: Record<string, unknown>,
  locations?: Array<{ lat: number; lng: number; label?: string }>,
): Promise<void> {
  await aggregatorFetch(`/api/v1/item/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify({ item_state: state, ...(locations ? { item_locations: locations } : {}) }),
  });
}

// ── Item → app-interface mapping ─────────────────────────────────────────────
// The rest of the app keeps its existing TypeScript interfaces; only the data
// source changed. Attributes carries the type-specific fields verbatim.

const s = (v: unknown): string => (typeof v === "string" ? v : "");
const attrs = (it: ResponderItem): Record<string, unknown> =>
  (it.item_state["Attributes"] as Record<string, unknown> | undefined) ?? {};
const loc = (it: ResponderItem) => it.item_locations?.[0] ?? { lat: 0, lng: 0 };

export function itemFacilityType(it: ResponderItem): string {
  return s(it.item_state["Facility Type"]);
}
export function itemFacilityId(it: ResponderItem): string {
  return s(it.item_state["Facility ID"]);
}
export function itemDataOrigin(it: ResponderItem): string {
  return s(it.item_state["Data Origin"]) || s(it.item_state["Capability Source"]);
}

export function toHospital(it: ResponderItem): Hospital {
  const a = attrs(it);
  const name = s(it.item_state["Facility Name"]);
  return {
    id: itemFacilityId(it),
    name,
    shortName: s(a.shortName) || (name.length > 25 ? name.slice(0, 23) + "…" : name),
    lat: loc(it).lat,
    lng: loc(it).lng,
    district: s(it.item_state["District"]),
    type: s(a.type) || "Hospital",
    traumaCapable: a.traumaCapable === true,
    traumaLevel: (it.item_state["Trauma Level"] as 1 | 2 | 3 | undefined) ?? 3,
    specialty: (it.item_state["Specialties"] as string[] | undefined) ?? [],
    beds: typeof a.beds === "number" ? a.beds : 0,
    emergency: s(a.emergency),
    phone: s(it.item_state["Contact Number"]) || s(a.phone),
    // The interfaces pin `sample: true` (hard rule 4) — every registry-served
    // record keeps that labelling contract regardless of origin.
    sample: true,
  } satisfies Hospital;
}

export function toPoliceStation(it: ResponderItem): PoliceStation {
  const a = attrs(it);
  return {
    id: itemFacilityId(it),
    name: s(it.item_state["Facility Name"]),
    lat: loc(it).lat,
    lng: loc(it).lng,
    district: s(it.item_state["District"]),
    circle: s(a.circle),
    emergency: s(a.emergency),
    phone: s(it.item_state["Contact Number"]) || s(a.phone),
    sample: true,
  } satisfies PoliceStation;
}

function toStationBase(it: ResponderItem) {
  const a = attrs(it);
  return {
    id: itemFacilityId(it),
    name: s(it.item_state["Facility Name"]),
    lat: loc(it).lat,
    lng: loc(it).lng,
    district: s(it.item_state["District"]),
    contactNumber: s(it.item_state["Contact Number"]) || s(a.phone),
    operatingHours: s(a.operatingHours) || "24x7",
    notes: s(a.notes),
    sample: true as const,
  };
}

export function toAmbulanceStation(it: ResponderItem): AmbulanceStation {
  const a = attrs(it);
  return {
    ...toStationBase(it),
    ambulanceCount: typeof a.ambulanceCount === "number" ? a.ambulanceCount : 1,
    types: (a.types as string[] | undefined) ?? ["BLS"],
  } as AmbulanceStation;
}

export function toFireStation(it: ResponderItem): FireStation {
  const a = attrs(it);
  return {
    ...toStationBase(it),
    vehicleTypes: (a.vehicleTypes as string[] | undefined) ?? [],
  } as FireStation;
}

export function toTowingStation(it: ResponderItem): TowingStation {
  const a = attrs(it);
  return {
    ...toStationBase(it),
    vehicleTypes: (a.vehicleTypes as string[] | undefined) ?? [],
  } as TowingStation;
}

/** Google-synced item → the GooglePlace shape the map layers already render.
 *  isOpen is always null — open-now status is a live signal we deliberately
 *  do not persist in the Aggregator, so the popup simply omits that line. */
export function toGooglePlace(it: ResponderItem, placeType: GooglePlaceType): GooglePlace {
  const a = attrs(it);
  return {
    id: s(it.item_state["Place ID"]) || itemFacilityId(it),
    name: s(it.item_state["Facility Name"]),
    lat: loc(it).lat,
    lng: loc(it).lng,
    address: s(a.address),
    isOpen: null,
    phone: s(a.phone) || null,
    placeType,
    distanceToCorridorKm:
      typeof a.distanceToCorridorKm === "number" ? a.distanceToCorridorKm : 0,
  } as GooglePlace;
}

// Facility Type ⇄ GooglePlaceType mapping (one canonical place per layer).
export const FACILITY_TYPE_TO_PLACE_TYPE: Record<string, GooglePlaceType> = {
  HOSPITAL: "hospital",
  POLICE: "police",
  MECHANIC: "car_repair",
  PHARMACY: "pharmacy",
  FUEL_STATION: "gas_station",
};
export const PLACE_TYPE_TO_FACILITY_TYPE: Record<GooglePlaceType, string> = {
  hospital: "HOSPITAL",
  police: "POLICE",
  car_repair: "MECHANIC",
  pharmacy: "PHARMACY",
  gas_station: "FUEL_STATION",
};
