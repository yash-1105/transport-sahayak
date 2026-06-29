import { NextRequest, NextResponse } from "next/server";
import { getAmbulanceStations } from "@/lib/ambulanceStations";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const lat = parseFloat(searchParams.get("lat") ?? "29.6");
  const lng = parseFloat(searchParams.get("lng") ?? "77.6");
  const radius = parseInt(searchParams.get("radius") ?? "15000", 10);

  const stations = await getAmbulanceStations(lat, lng, radius);
  return NextResponse.json(stations, { headers: { "Cache-Control": "no-store" } });
}
