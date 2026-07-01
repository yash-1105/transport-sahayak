// Route polylines to draw on the map — persists across panel open/close.

import { create } from "zustand";

export interface MapRoute {
  id: string;
  color: string;
  dashArray?: string;
  coords: [number, number][]; // [lat, lng] pairs
  label: string;
}

// A cosmetic, clearly-labelled simulated ambulance marker that animates along
// an already-highlighted route. Not a real position feed — see CLAUDE.md hard
// rule 1. Anchored to the same computedAt timestamp as the ETA countdown so
// both stay in sync and both survive panel remounts.
export interface SimulatedAmbulance {
  id: string;
  coords: [number, number][]; // same polyline as the drawn ambulance route
  startedAt: string; // ISO timestamp — when the estimate was first computed
  durationMin: number; // total simulated travel time
}

interface RoutingState {
  routes: MapRoute[];
  simulatedAmbulance: SimulatedAmbulance | null;
  setRoutes: (routes: MapRoute[]) => void;
  setSimulatedAmbulance: (sim: SimulatedAmbulance | null) => void;
  clearRoutes: () => void;
}

export const useRoutingStore = create<RoutingState>((set) => ({
  routes: [],
  simulatedAmbulance: null,
  setRoutes: (routes) => set({ routes }),
  setSimulatedAmbulance: (sim) => set({ simulatedAmbulance: sim }),
  clearRoutes: () => set({ routes: [], simulatedAmbulance: null }),
}));
