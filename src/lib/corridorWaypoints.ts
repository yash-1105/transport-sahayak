// Guwahati metropolitan area (NH-27 / AT Road urban corridor, Assam) — shared
// anchor points for map center and Places searches. Coordinates verified via
// Google Places; the arc runs Jorabat (east) → Jalukbari (west) through the city.

export interface CorridorWaypoint {
  name: string;
  lat: number;
  lng: number;
}

// 8 anchors used for Google Places fan-out (one Places call per waypoint),
// spanning the Guwahati metro east→west plus the south-western (NH-17) arm.
export const CORRIDOR_WAYPOINTS: CorridorWaypoint[] = [
  { name: "Jorabat (NH-27 / NH-6 junction)", lat: 26.099, lng: 91.862 },
  { name: "Khanapara",                       lat: 26.121, lng: 91.821 },
  { name: "Ganeshguri / Dispur",             lat: 26.154, lng: 91.782 },
  { name: "Ulubari",                         lat: 26.168, lng: 91.754 },
  { name: "Paltan Bazaar",                   lat: 26.181, lng: 91.751 },
  { name: "Maligaon",                        lat: 26.150, lng: 91.696 },
  { name: "Jalukbari (NH-27 junction)",      lat: 26.143, lng: 91.644 },
  { name: "Gorchuk (NH-17)",                 lat: 26.105, lng: 91.713 },
];

// Dense centerline used for the corridor proximity filter — isWithinCorridor
// checks perpendicular distance to this polyline, not just the waypoint circles.
// Follows the main urban NH-27 / GS Road / AT Road spine, Jorabat → Jalukbari.
export const CORRIDOR_POLYLINE: { lat: number; lng: number }[] = [
  { lat: 26.099, lng: 91.862 }, // Jorabat — NH-27 / NH-6 (Shillong road) junction
  { lat: 26.121, lng: 91.821 }, // Khanapara
  { lat: 26.131, lng: 91.804 }, // Six Mile
  { lat: 26.145, lng: 91.790 }, // Beltola / Ganeshguri approach
  { lat: 26.154, lng: 91.782 }, // Ganeshguri / Dispur
  { lat: 26.162, lng: 91.770 }, // GS Road — Bhangagarh
  { lat: 26.168, lng: 91.754 }, // Ulubari
  { lat: 26.181, lng: 91.751 }, // Paltan Bazaar
  { lat: 26.173, lng: 91.730 }, // Bharalumukh
  { lat: 26.162, lng: 91.712 }, // Kamakhya / Maligaon approach (AT Road)
  { lat: 26.150, lng: 91.696 }, // Maligaon
  { lat: 26.143, lng: 91.644 }, // Jalukbari — NH-27 junction (toward Amingaon bridge)
];

// Central Guwahati — default map center.
export const CORRIDOR_CENTER = { lat: 26.15, lng: 91.75 };

// Radius around each waypoint for Google Places searches. City-scale (8 km),
// tighter than an inter-city highway: with 8 overlapping metro anchors this
// covers Guwahati end to end. The corridor-proximity filter (isWithinCorridor)
// is the real inclusion gate; this radius just ensures nothing along the road is missed.
export const CORRIDOR_WAYPOINT_RADIUS_M = 8_000;
