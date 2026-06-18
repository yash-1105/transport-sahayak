// OSRM public demo server client.
// Returns free-flow driving estimates — NOT live traffic, NOT ETAs.
// For production, run a self-hosted OSRM instance with Assam road data.

export interface OsrmRoute {
  distanceM: number;    // metres
  durationSec: number;  // seconds, free-flow — no live traffic
  // Coordinates as [lat, lng] pairs (converted from OSRM's [lng, lat] GeoJSON)
  coords: [number, number][];
}

export async function getOsrmRoute(
  from: { lat: number; lng: number },
  to: { lat: number; lng: number }
): Promise<OsrmRoute> {
  // OSRM uses [lng, lat] order
  const url =
    `http://router.project-osrm.org/route/v1/driving/` +
    `${from.lng},${from.lat};${to.lng},${to.lat}` +
    `?overview=full&geometries=geojson&steps=false`;

  const res = await fetch(url, {
    headers: { "Accept": "application/json" },
    // 10 s timeout via AbortController
    signal: AbortSignal.timeout(10_000),
  });

  if (!res.ok) throw new Error(`OSRM HTTP ${res.status}`);

  const data = await res.json();

  if (data.code !== "Ok" || !data.routes?.length) {
    throw new Error(`OSRM: ${data.code ?? "no routes"}`);
  }

  const route = data.routes[0];
  // Convert GeoJSON [lng, lat] → Leaflet [lat, lng]
  const coords: [number, number][] = (
    route.geometry.coordinates as [number, number][]
  ).map(([lng, lat]) => [lat, lng]);

  return {
    distanceM: route.distance,
    durationSec: route.duration,
    coords,
  };
}
