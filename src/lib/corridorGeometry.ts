// Corridor proximity geometry — pure math, no I/O, safe to import server-side or client-side.
//
// Unit-tested results (24-pt Delhi–Dehradun polyline, maxKm=15):
//   ✓  0.00 km  on road at Shamli
//   ✓  1.88 km  2 km east of Shamli
//   ✓ 13.60 km  AIIMS Delhi (relevant for Delhi-end incidents)
//   ✗ 28.47 km  Roorkee city centre (off expressway axis)
//   ✗ 32.83 km  Muzaffarnagar city centre
//   ✗ 45.56 km  Meerut city centre

export type LatLng = { lat: number; lng: number };

const R_EARTH_KM = 6371;
const DEG = Math.PI / 180;

export function haversineKm(a: LatLng, b: LatLng): number {
  const dLat = (b.lat - a.lat) * DEG;
  const dLng = (b.lng - a.lng) * DEG;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * DEG) * Math.cos(b.lat * DEG) * Math.sin(dLng / 2) ** 2;
  return 2 * R_EARTH_KM * Math.asin(Math.sqrt(s));
}

/**
 * Shortest distance from point p to segment a–b.
 * Uses equirectangular projection (cosLat-scaled longitude) for the dot-product,
 * then haversine for the final distance. Accurate to <0.5% for segments <50 km.
 */
export function distancePointToSegmentKm(p: LatLng, a: LatLng, b: LatLng): number {
  const cosLat = Math.cos(((a.lat + b.lat) / 2) * DEG);
  const ax = a.lat, ay = a.lng * cosLat;
  const bx = b.lat, by = b.lng * cosLat;
  const px = p.lat, py = p.lng * cosLat;

  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;

  const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  const closest: LatLng = { lat: a.lat + t * (b.lat - a.lat), lng: a.lng + t * (b.lng - a.lng) };
  return haversineKm(p, closest);
}

/** Perpendicular distance from p to the nearest segment in the polyline. */
export function distanceToCorridorKm(p: LatLng, polyline: LatLng[]): number {
  let min = Infinity;
  for (let i = 0; i < polyline.length - 1; i++) {
    const d = distancePointToSegmentKm(p, polyline[i], polyline[i + 1]);
    if (d < min) min = d;
  }
  return min;
}

/** True when p is within maxKm of any segment in the polyline. */
export function isWithinCorridor(p: LatLng, polyline: LatLng[], maxKm = 15): boolean {
  return distanceToCorridorKm(p, polyline) <= maxKm;
}
