import { NextRequest, NextResponse } from "next/server";
import { guess } from "@/lib/incidentClassifier";

export async function POST(req: NextRequest) {
  const { description } = await req.json();
  if (!description || typeof description !== "string") {
    return NextResponse.json({ error: "description required" }, { status: 400 });
  }
  return NextResponse.json(guess(description));
}
