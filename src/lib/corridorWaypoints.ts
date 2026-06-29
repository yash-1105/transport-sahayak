// Delhi–Dehradun Expressway corridor — shared anchor points for map center and Places searches.

export interface CorridorWaypoint {
  name: string;
  lat: number;
  lng: number;
}

// 8 anchors used for Google Places fan-out (one Places call per waypoint).
export const CORRIDOR_WAYPOINTS: CorridorWaypoint[] = [
  { name: "Delhi (Akshardham / EPE junction)", lat: 28.63,  lng: 77.33  },
  { name: "Baghpat",                           lat: 28.944, lng: 77.218 },
  { name: "Baraut / Shamli",                   lat: 29.30,  lng: 77.31  },
  { name: "Muzaffarnagar / Shamli",            lat: 29.45,  lng: 77.310 },
  { name: "Deoband / Nakur",                   lat: 29.70,  lng: 77.470 },
  { name: "Saharanpur",                        lat: 29.967, lng: 77.546 },
  { name: "Ganeshpur / Roorkee",               lat: 30.15,  lng: 77.87  },
  { name: "Dehradun",                          lat: 30.316, lng: 78.032 },
];

// Dense 24-point centerline (~every 10 km) used for the corridor proximity filter.
// isWithinCorridor checks perpendicular distance to this polyline, not just waypoint circles.
export const CORRIDOR_POLYLINE: { lat: number; lng: number }[] = [
  { lat: 28.632, lng: 77.328 }, // Delhi — EPE/NH-9 junction
  { lat: 28.698, lng: 77.305 }, // Loni / Delhi–UP border
  { lat: 28.760, lng: 77.280 }, // NH-334 heading NNW
  { lat: 28.838, lng: 77.248 }, // approaching Baghpat district
  { lat: 28.944, lng: 77.218 }, // Baghpat
  { lat: 29.020, lng: 77.237 }, // north of Baghpat
  { lat: 29.099, lng: 77.254 }, // Baraut
  { lat: 29.175, lng: 77.268 }, // Baraut–Shamli mid
  { lat: 29.260, lng: 77.283 }, // Muzaffarnagar western bypass
  { lat: 29.355, lng: 77.296 }, // Khatauli area
  { lat: 29.450, lng: 77.310 }, // Shamli
  { lat: 29.550, lng: 77.358 }, // Shamli–Saharanpur mid
  { lat: 29.650, lng: 77.430 }, // Deoband / Nakur area
  { lat: 29.768, lng: 77.500 }, // approaching Saharanpur south
  { lat: 29.868, lng: 77.528 }, // Saharanpur south
  { lat: 29.967, lng: 77.546 }, // Saharanpur
  { lat: 30.020, lng: 77.620 }, // NE of Saharanpur
  { lat: 30.065, lng: 77.700 }, // heading toward Gangoh / Behat
  { lat: 30.110, lng: 77.785 }, // Shivalik foothills approach
  { lat: 30.150, lng: 77.870 }, // Ganeshpur
  { lat: 30.196, lng: 77.928 }, // Mohand approach
  { lat: 30.236, lng: 77.978 }, // Mohand Ghat
  { lat: 30.280, lng: 78.010 }, // Dehradun valley entry
  { lat: 30.316, lng: 78.032 }, // Dehradun
];

// Midpoint of the corridor — default map center.
export const CORRIDOR_CENTER = { lat: 29.6, lng: 77.6 };

// Radius around each waypoint for Google Places searches.
// The corridor-proximity filter (isWithinCorridor) is the real inclusion gate;
// this radius just ensures we don't miss anything along the road.
export const CORRIDOR_WAYPOINT_RADIUS_M = 15_000;
