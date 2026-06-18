// Route polylines to draw on the map — persists across panel open/close.

import { create } from "zustand";

export interface MapRoute {
  id: string;
  color: string;
  dashArray?: string;
  coords: [number, number][]; // [lat, lng] pairs
  label: string;
}

interface RoutingState {
  routes: MapRoute[];
  setRoutes: (routes: MapRoute[]) => void;
  clearRoutes: () => void;
}

export const useRoutingStore = create<RoutingState>((set) => ({
  routes: [],
  setRoutes: (routes) => set({ routes }),
  clearRoutes: () => set({ routes: [] }),
}));
