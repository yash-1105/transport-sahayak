import { create } from "zustand";
import type {
  EventLogEntry,
  AccidentReport,
  DispatchRecord,
  AssessmentResult,
  RankedHospital,
  NearestPolice,
} from "@/lib/types";

interface EventLogState {
  entries: EventLogEntry[];
  // Append-only — each new fact is a new entry; no updates or deletes
  appendReport: (report: AccidentReport) => void;
  appendAssessment: (incidentId: string, assessment: AssessmentResult) => void;
  appendHospitalMatched: (
    incidentId: string,
    rankedHospitals: RankedHospital[],
    nearestPolice: NearestPolice
  ) => void;
  appendRouteEstimated: (
    incidentId: string,
    entityId: string,
    entityName: string,
    entityType: "HOSPITAL" | "POLICE" | "FIRE" | "TOWING" | "AMBULANCE",
    roadDistanceKm: number,
    roadDurationMin: number
  ) => void;
  appendDispatch: (dispatch: DispatchRecord) => void;
  appendDuplicateFlagged: (
    newIncidentId: string | null,
    existingIncidentId: string,
    distanceM: number,
    deltaMinutes: number,
    userAction: "SKIPPED" | "PROCEEDED"
  ) => void;
  appendNote: (reportId: string, note: string) => void;
}

function makeId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export const useEventLog = create<EventLogState>((set) => ({
  entries: [],

  appendReport: (report) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "REPORT_CREATED",
          payload: report,
        } satisfies EventLogEntry,
      ],
    })),

  appendAssessment: (incidentId, assessment) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "SEVERITY_ASSESSED",
          payload: { incidentId, assessment },
        } satisfies EventLogEntry,
      ],
    })),

  appendHospitalMatched: (incidentId, rankedHospitals, nearestPolice) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "HOSPITAL_MATCHED",
          payload: { incidentId, rankedHospitals, nearestPolice },
        } satisfies EventLogEntry,
      ],
    })),

  appendRouteEstimated: (
    incidentId,
    entityId,
    entityName,
    entityType,
    roadDistanceKm,
    roadDurationMin
  ) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "ROUTE_ESTIMATED",
          payload: {
            incidentId,
            entityId,
            entityName,
            entityType,
            roadDistanceKm,
            roadDurationMin,
            disclaimer:
              "Est. drive time from facility, current traffic — vehicle leaving now. We do not track ambulances.",
          },
        } satisfies EventLogEntry,
      ],
    })),

  appendDispatch: (dispatch) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "DISPATCH_SENT",
          payload: dispatch,
        } satisfies EventLogEntry,
      ],
    })),

  appendDuplicateFlagged: (newIncidentId, existingIncidentId, distanceM, deltaMinutes, userAction) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "DUPLICATE_FLAGGED",
          payload: { newIncidentId, existingIncidentId, distanceM, deltaMinutes, userAction },
        } satisfies EventLogEntry,
      ],
    })),

  appendNote: (_reportId, note) =>
    set((state) => ({
      entries: [
        ...state.entries,
        {
          id: makeId(),
          timestamp: new Date().toISOString(),
          type: "NOTE_ADDED",
          payload: { note },
        } satisfies EventLogEntry,
      ],
    })),
}));
