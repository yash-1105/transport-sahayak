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
  return NextResponse.json(data ?? []);
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
