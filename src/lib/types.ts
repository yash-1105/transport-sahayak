// Core domain types for Transport Sahayak

export type SeverityLevel = "CRITICAL" | "SERIOUS" | "MINOR" | "UNKNOWN";

export type DispatchStatus = "NOTIFIED"; // Only status we can confirm — notification sent, nothing more

export interface GeoPoint {
  lat: number;
  lng: number;
}

// ── Map layer types ──────────────────────────────────────────────────────────

export type ServiceLayerType =
  | "HOSPITAL"
  | "AMBULANCE_STATION"
  | "MECHANIC"
  | "POLICE"
  | "PHARMACY"
  | "GAS_STATION";

// ── Google Places (live, not sample) ─────────────────────────────────────────

export type GooglePlaceType =
  | "hospital"
  | "police"
  | "car_repair"
  | "pharmacy"
  | "gas_station";

// Per Google ToS: only place IDs may be persisted; detail fields are query-time only.
export interface GooglePlace {
  id: string;         // place ID — safe to store per ToS
  name: string;
  lat: number;
  lng: number;
  address: string;
  isOpen: boolean | null; // null = opening-hours data unavailable
  placeType: GooglePlaceType;
}

export type AccidentLayerType = "BLACKSPOT" | "POTHOLE";

export type LayerType = ServiceLayerType | AccidentLayerType;

export interface Hospital {
  id: string;
  sample: true;
  name: string;
  shortName: string;
  lat: number;
  lng: number;
  district: string;
  type: string;
  traumaCapable: boolean;
  traumaLevel: 1 | 2 | 3;
  specialty: string[];
  beds: number;
  phone: string;
  emergency: string;
}

export interface AmbulanceStation {
  id: string;
  sample: true;
  name: string;
  lat: number;
  lng: number;
  district: string;
  contactNumber: string;
  ambulanceCount: number;
  types: ("ALS" | "BLS")[];
  operatingHours: string;
  notes: string;
}

export interface Mechanic {
  id: string;
  sample: true;
  name: string;
  lat: number;
  lng: number;
  district: string;
  phone: string;
  services: string[];
  operatingHours: string;
}

export interface PoliceStation {
  id: string;
  sample: true;
  name: string;
  lat: number;
  lng: number;
  district: string;
  circle: string;
  phone: string;
  emergency: string;
}

export interface Blackspot {
  id: string;
  sample: true;
  name: string;
  lat: number;
  lng: number;
  highway: string;
  district: string;
  accidentsLast3Years: number;
  deathsLast3Years: number;
  primaryHazard: string;
  periodOfPeak: string;
}

export interface Pothole {
  id: string;
  sample: true;
  lat: number;
  lng: number;
  road: string;
  district: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  diameterCm: number;
  depthCm: number;
  reportedDate: string;
  status: string;
}

// ── Assessment ───────────────────────────────────────────────────────────────

export type AssessmentSeverity = 1 | 2 | 3 | 4; // 1=LOW 2=MEDIUM 3=HIGH 4=CRITICAL
export type ClassifiedBy = "operator" | "rules" | "llm";

export interface AssessmentResult {
  // Classification
  category?: string;
  subType?: string;
  // Severity — always rule-computed, never from LLM
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"; // string label from engine
  severityScore: AssessmentSeverity;                  // numeric 1–4
  impactNote: string;                                 // replaces rationale
  appliedModifiers: string[];
  // Dispatch
  agencies: { code: string; label: string }[];
  dataGaps: string[];
  // Provenance
  classifiedBy: ClassifiedBy;
  llmUsed: boolean;
  lowConfidence: boolean;
  jurisdictionState?: string;
}

// ── Hospital candidate (hybrid: curated + Google Places) ─────────────────────

// Unified hospital representation used for ranking and dispatch.
// "curated" entries come from the seed dataset (trauma level + specialties known).
// "unverified" entries come from Google Places (real location, capability unknown).
export interface HospitalCandidate {
  id: string;
  name: string;
  shortName: string;
  lat: number;
  lng: number;
  district: string;
  type: string;
  traumaCapable: boolean;
  traumaLevel: 1 | 2 | 3 | null; // null when capability is unverified
  specialty: string[];
  capabilitySource: "curated" | "unverified";
  placeId?: string; // Google Place ID — only present for Google-sourced entries
}

// ── Matching / routing ────────────────────────────────────────────────────────

export interface RankedHospital {
  hospital: HospitalCandidate;
  rank: 1 | 2 | 3;
  straightLineKm: number;
  roadDistanceKm: number | null;  // from Routes API — null until fetched
  roadDurationMin: number | null; // from Routes API, traffic-aware — null until fetched
  matchScore: number;
  specialtyMatches: string[];
  routeCoords: [number, number][] | null; // [lat, lng] pairs
  reasoning: string;
}

export interface NearestPolice {
  station: PoliceStation;
  straightLineKm: number;
  roadDistanceKm: number | null;
  roadDurationMin: number | null;
  routeCoords: [number, number][] | null;
}

// ── Event log ────────────────────────────────────────────────────────────────

export interface AccidentReport {
  id: string;
  timestamp: string; // ISO 8601
  reportMode: "SOS" | "TEXT" | "VOICE";
  location: GeoPoint;
  locationLabel: string;
  vehiclesInvolved: number | null;   // null when unknown (e.g. SOS)
  estimatedCasualties: number | null; // null when unknown
  description: string;
  flags: string[];                    // e.g. ["Trapped", "Heavy bleeding", "SOS"]
  severity: SeverityLevel;            // UNKNOWN until /api/severity is called
  severitySource: "AI" | "RULE_BASED" | null; // null until assessed
}

export interface DispatchRecord {
  id: string;
  reportId: string;
  timestamp: string; // ISO 8601 — when notification was sent
  dispatchedTo: "HOSPITAL" | "AMBULANCE" | "POLICE";
  entityId: string;
  entityName: string;
  status: DispatchStatus; // Always NOTIFIED — we record the send, not the outcome
  routePlanningEstimateKm: number | null; // Routes API traffic-aware estimate, NOT ETA
  messageText: string; // Exact text of the notification that was sent
}

export interface SeverityAssessedPayload {
  incidentId: string;
  assessment: AssessmentResult;
}

export interface HospitalMatchedPayload {
  incidentId: string;
  rankedHospitals: RankedHospital[];
  nearestPolice: NearestPolice;
}

export interface RouteEstimatedPayload {
  incidentId: string;
  entityId: string;
  entityName: string;
  entityType: "HOSPITAL" | "POLICE";
  roadDistanceKm: number;
  roadDurationMin: number;
  disclaimer: "Est. drive time from facility, current traffic — vehicle leaving now. We do not track ambulances.";
}

export interface DuplicateFlaggedPayload {
  // null when the user chose SKIPPED and no incident was created
  newIncidentId: string | null;
  existingIncidentId: string;
  distanceM: number;
  deltaMinutes: number;
  userAction: "SKIPPED" | "PROCEEDED";
}

export interface EventLogEntry {
  id: string;
  timestamp: string;
  type:
    | "REPORT_CREATED"
    | "SEVERITY_ASSESSED"
    | "HOSPITAL_MATCHED"
    | "ROUTE_ESTIMATED"
    | "DISPATCH_SENT"
    | "DUPLICATE_FLAGGED"
    | "NOTE_ADDED";
  payload:
    | AccidentReport
    | DispatchRecord
    | SeverityAssessedPayload
    | HospitalMatchedPayload
    | RouteEstimatedPayload
    | DuplicateFlaggedPayload
    | { note: string };
}
