// Route polylines to draw on the map — persists across panel open/close.

import { create } from "zustand";

export interface MapRoute {
  id: string;
  color: string;
  dashArray?: string;
  coords: [number, number][]; // [lat, lng] pairs
  label: string;
}

export type SimulatedVehicleKind = "AMBULANCE" | "FIRE" | "TOWING";

// A cosmetic, clearly-labelled simulated vehicle marker that animates along an
// already-highlighted route. Not a real position feed — see CLAUDE.md hard
// rule 1. Anchored to the same computedAt timestamp as its ETA countdown card
// so both stay in sync and both survive panel remounts.
export interface SimulatedVehicle {
  id: string;
  kind: SimulatedVehicleKind;
  coords: [number, number][]; // same polyline as the drawn route
  startedAt: string; // ISO timestamp — when the estimate was first computed
  durationMin: number; // total simulated travel time
}

interface RoutingState {
  routes: MapRoute[];
  simulatedVehicles: SimulatedVehicle[];
  setRoutes: (routes: MapRoute[]) => void;
  upsertSimulatedVehicle: (v: SimulatedVehicle) => void;
  clearRoutes: () => void;
}

export const useRoutingStore = create<RoutingState>((set) => ({
  routes: [],
  simulatedVehicles: [],
  setRoutes: (routes) => set({ routes }),
  upsertSimulatedVehicle: (v) =>
    set((state) => ({
      simulatedVehicles: [...state.simulatedVehicles.filter((existing) => existing.id !== v.id), v],
    })),
  clearRoutes: () => set({ routes: [], simulatedVehicles: [] }),
}));
