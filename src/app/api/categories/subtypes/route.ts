import { NextRequest, NextResponse } from "next/server";
import { getSubtypes } from "@/lib/incidentClassifier";

export async function GET(req: NextRequest) {
  const name = new URL(req.url).searchParams.get("name") ?? "";
  if (!name) return NextResponse.json({ error: "name param required" }, { status: 400 });
  return NextResponse.json(getSubtypes(name));
}
