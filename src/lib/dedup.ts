// Intake deduplication: flag a new incident that falls within RADIUS_M metres
// and TIME_WINDOW_MIN minutes of an existing one.

import { haversineKm } from "./matching";
import type { AccidentReport, EventLogEntry } from "./types";

const RADIUS_M = 500;
const TIME_WINDOW_MIN = 10;

export interface DuplicateMatch {
  existingIncident: AccidentReport;
  distanceM: number;    // rounded to nearest metre
  deltaMinutes: number; // rounded to 1 decimal
}

export function checkDuplicate(
  newLocation: { lat: number; lng: number },
  newTimestamp: string,
  entries: EventLogEntry[]
): DuplicateMatch | null {
  const newMs = new Date(newTimestamp).getTime();

  for (const entry of entries) {
    if (entry.type !== "REPORT_CREATED") continue;
    const existing = entry.payload as AccidentReport;

    const distM = haversineKm(newLocation, existing.location) * 1000;
    const deltaMs = Math.abs(newMs - new Date(existing.timestamp).getTime());
    const deltaMin = deltaMs / 60_000;

    if (distM <= RADIUS_M && deltaMin <= TIME_WINDOW_MIN) {
      return {
        existingIncident: existing,
        distanceM: Math.round(distM),
        deltaMinutes: Math.round(deltaMin * 10) / 10,
      };
    }
  }
  return null;
}
