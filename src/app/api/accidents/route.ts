import { NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

export async function GET() {
  if (!supabase) {
    return NextResponse.json(
      { error: "Supabase not configured" },
      { status: 503 }
    );
  }
  const { data, error } = await supabase
    .from("reported_accidents")
    .select("*")
    .order("created_at", { ascending: false });

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  // Annotate each accident with its ambiguity-review status (read-path only —
  // the write path is untouched). This lets the PUBLIC density heatmap exclude
  // 'ignored' duplicates and the operator list dim them, WITHOUT any client
  // needing to read the incident_reviews table directly.
  const rows = (data ?? []) as Array<Record<string, unknown>>;
  const { data: reviews } = await supabase.from("incident_reviews").select("incident_id, review_status");
  const statusById = new Map<string, string>();
  for (const r of (reviews ?? []) as Array<{ incident_id: string; review_status: string }>) {
    statusById.set(r.incident_id, r.review_status);
  }
  const annotated = rows.map((a) => ({ ...a, review_status: statusById.get(a.id as string) ?? null }));
  return NextResponse.json(annotated);
}

export async function POST(req: Request) {
  if (!supabase) {
    return NextResponse.json({ error: "Supabase not configured" }, { status: 503 });
  }
  const body = await req.json();
  const { id, lat, lng, location_label, description, severity, report_mode, flags, reported_date } = body;

  if (!id || lat == null || lng == null || !location_label || !report_mode) {
    return NextResponse.json({ error: "Missing required fields" }, { status: 400 });
  }

  const { data, error } = await supabase
    .from("reported_accidents")
    .insert({ id, lat, lng, location_label, description: description ?? null, severity: severity ?? null, report_mode, flags: flags ?? [], reported_date })
    .select()
    .single();

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json(data, { status: 201 });
}
