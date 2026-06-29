import { NextResponse } from "next/server";
import { getCategories } from "@/lib/incidentClassifier";

export async function GET() {
  return NextResponse.json(getCategories());
}
