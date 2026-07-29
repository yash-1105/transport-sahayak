import { create } from "zustand";

// Per-incident Signals publish status, session-scoped (matches the
// session-scoped event log). Drives the small honesty badge only —
// "published" is set exclusively after Signals acknowledged the create.
export type SignalsSyncState = "pending" | "published" | "unavailable" | "disabled";

interface SignalsSyncEntry {
  state: SignalsSyncState;
  itemId?: string;
}

interface SignalsSyncStore {
  byIncident: Record<string, SignalsSyncEntry>;
  // Bumped after every completed publish (incident create, assessment PATCH,
  // dispatch action). The Network dashboard panel subscribes to this and
  // re-fetches with ?refresh=true — event-driven freshness, no polling.
  activitySeq: number;
  setSync: (incidentId: string, entry: SignalsSyncEntry) => void;
  bumpActivity: () => void;
}

export const useSignalsSync = create<SignalsSyncStore>((set) => ({
  byIncident: {},
  activitySeq: 0,
  setSync: (incidentId, entry) =>
    set((state) => ({
      byIncident: { ...state.byIncident, [incidentId]: entry },
    })),
  bumpActivity: () => set((state) => ({ activitySeq: state.activitySeq + 1 })),
}));
