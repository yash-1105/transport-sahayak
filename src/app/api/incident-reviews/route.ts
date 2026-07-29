import { NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

// Ambiguity / duplicate review store. Operator-only in the UI. GET lists all
// review records; POST upserts one. This is ADVISORY only (Hard Rule 5): a
// review marks a record + de-dups downstream views — it never un-sends an
// already-logged dispatch notification, and never deletes the incident.

export async function GET() {
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 503 });
  const { data, error } = await supabase.from("incident_reviews").select("*");
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json(data ?? []);
}

export async function POST(req: Request) {
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 503 });
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  const incident_id = body.incident_id as string | undefined;
  const review_status = body.review_status as string | undefined; // 'open' | 'ignored' | 'kept'
  if (!incident_id || !review_status) {
    return NextResponse.json({ error: "incident_id and review_status are required" }, { status: 400 });
  }

  const row = {
    incident_id,
    review_status,
    duplicate_of: (body.duplicate_of as string) ?? null,
    reviewed_at: new Date().toISOString(),
  };
  const { data, error } = await supabase
    .from("incident_reviews")
    .upsert(row, { onConflict: "incident_id" })
    .select()
    .single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json(data, { status: 200 });
}
