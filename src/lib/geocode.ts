export async function reverseGeocode(lat: number, lng: number): Promise<string> {
  const url = `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}&zoom=16&addressdetails=1`;
  const res = await fetch(url, {
    headers: { "Accept-Language": "en-IN", "User-Agent": "TransportSahayak/PoC (proof-of-concept)" },
  });
  if (!res.ok) throw new Error("Geocode request failed");
  const data = await res.json();
  const a = data.address ?? {};
  const parts = [
    a.road ?? a.neighbourhood ?? a.suburb,
    a.city ?? a.town ?? a.village ?? a.county,
    a.state_district ?? a.state,
  ].filter(Boolean);
  return parts.length
    ? parts.join(", ")
    : (data.display_name ?? `${lat.toFixed(5)}, ${lng.toFixed(5)}`);
}
