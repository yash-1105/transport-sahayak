import { NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

// One call's full metrics INCLUDING the transcript (operator-only drill-down).
// Surfaced only in the operator dashboard UI.
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 503 });
  const { id } = await params;
  const { data, error } = await supabase.from("voice_call_metrics").select("*").eq("id", id).maybeSingle();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  if (!data) return NextResponse.json({ error: "Not found" }, { status: 404 });
  return NextResponse.json(data);
}
