import { NextResponse } from "next/server";
import { supabase } from "@/lib/supabase";

// Post-Call Analytics for the voice dispatcher. POST inserts one call's metrics
// (from ANY caller — capture is global; RLS allows anon insert). GET returns the
// rows + computed aggregates, optionally filtered by ?locale=; it is surfaced
// ONLY in the operator dashboard UI (UI-gated, like the Network dashboard).
//
// HONESTY (Hard Rules): time_to_dispatch_ms is captured client-side as
// ready_at → dispatched_at (the `submitted` event) and never includes the
// post-submit briefing / SOPs / ETAs.

interface MetricRow {
  id: string;
  incident_id: string | null;
  locale: string | null;
  outcome: string | null;
  started_at: string | null;
  ready_at: string | null;
  dispatched_at: string | null;
  ended_at: string | null;
  time_to_dispatch_ms: number | null;
  call_duration_ms: number | null;
  caller_turns: number | null;
  agent_turns: number | null;
  total_turns: number | null;
  questions_asked: number | null;
  productive_turns: number | null;
  fields_collected: unknown;
  reconnects: number | null;
  created_at: string | null;
}

// ── aggregate helpers ─────────────────────────────────────────────────────────
function median(sorted: number[]): number | null {
  if (sorted.length === 0) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}
function percentile(sorted: number[], p: number): number | null {
  if (sorted.length === 0) return null;
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, idx)];
}
function mean(nums: number[]): number | null {
  if (nums.length === 0) return null;
  return Math.round(nums.reduce((a, b) => a + b, 0) / nums.length);
}

function summarize(rows: MetricRow[]) {
  const total = rows.length;
  const dispatched = rows.filter((r) => r.outcome === "dispatched");
  const abandoned = rows.filter((r) => r.outcome === "abandoned");
  const errored = rows.filter((r) => r.outcome === "error");

  const ttd = dispatched
    .map((r) => r.time_to_dispatch_ms)
    .filter((n): n is number => typeof n === "number" && n >= 0)
    .sort((a, b) => a - b);

  const productive = rows.reduce((a, r) => a + (r.productive_turns ?? 0), 0);
  const turns = rows.reduce((a, r) => a + (r.total_turns ?? 0), 0);

  return {
    total_calls: total,
    dispatched: dispatched.length,
    abandoned: abandoned.length,
    errored: errored.length,
    dispatch_ready_rate: total ? dispatched.length / total : null,
    abandonment_rate: total ? abandoned.length / total : null,
    // time-to-dispatch (ms), dispatched calls only
    ttd_median_ms: median(ttd),
    ttd_p90_ms: percentile(ttd, 90),
    ttd_mean_ms: mean(ttd),
    ttd_min_ms: ttd[0] ?? null,
    ttd_max_ms: ttd[ttd.length - 1] ?? null,
    // productivity
    productive_pct: turns ? productive / turns : null,
    avg_caller_turns: mean(dispatched.map((r) => r.caller_turns ?? 0)),
    avg_call_duration_ms: mean(rows.map((r) => r.call_duration_ms ?? 0).filter((n) => n > 0)),
  };
}

// Fixed-edge buckets (ms) for the time-to-dispatch distribution / closure curve.
const HIST_EDGES_MS = [0, 30000, 60000, 90000, 120000, 180000, 240000, 300000];
function histogram(rows: MetricRow[]) {
  const vals = rows
    .filter((r) => r.outcome === "dispatched")
    .map((r) => r.time_to_dispatch_ms)
    .filter((n): n is number => typeof n === "number" && n >= 0);
  const buckets = HIST_EDGES_MS.map((lo, i) => {
    const hi = HIST_EDGES_MS[i + 1] ?? Infinity;
    const count = vals.filter((v) => v >= lo && v < hi).length;
    return { lo, hi: hi === Infinity ? null : hi, count };
  });
  return buckets;
}

export async function GET(req: Request) {
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 503 });
  const { searchParams } = new URL(req.url);
  const locale = searchParams.get("locale"); // 'en-IN' | 'hi-IN' | null

  let q = supabase.from("voice_call_metrics").select("*").order("created_at", { ascending: false });
  if (locale) q = q.eq("locale", locale);
  const { data, error } = await q;
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  const rows = (data ?? []) as MetricRow[];
  const en = rows.filter((r) => r.locale === "en-IN");
  const hi = rows.filter((r) => r.locale === "hi-IN");

  return NextResponse.json({
    calls: rows,
    overall: summarize(rows),
    byLocale: { "en-IN": summarize(en), "hi-IN": summarize(hi) },
    histogram: { overall: histogram(rows), "en-IN": histogram(en), "hi-IN": histogram(hi) },
    hist_edges_ms: HIST_EDGES_MS,
  });
}

export async function POST(req: Request) {
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 503 });
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  // Whitelist columns (the client sends exactly these). All optional/nullable.
  const insert = {
    incident_id: (body.incident_id as string) ?? null,
    locale: (body.locale as string) ?? null,
    outcome: (body.outcome as string) ?? null,
    started_at: (body.started_at as string) ?? null,
    ready_at: (body.ready_at as string) ?? null,
    dispatched_at: (body.dispatched_at as string) ?? null,
    ended_at: (body.ended_at as string) ?? null,
    time_to_dispatch_ms: (body.time_to_dispatch_ms as number) ?? null,
    call_duration_ms: (body.call_duration_ms as number) ?? null,
    caller_turns: (body.caller_turns as number) ?? null,
    agent_turns: (body.agent_turns as number) ?? null,
    total_turns: (body.total_turns as number) ?? null,
    questions_asked: (body.questions_asked as number) ?? null,
    productive_turns: (body.productive_turns as number) ?? null,
    fields_collected: body.fields_collected ?? null,
    reconnects: (body.reconnects as number) ?? 0,
    transcript: body.transcript ?? null,
  };

  const { data, error } = await supabase.from("voice_call_metrics").insert(insert).select("id").single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json(data, { status: 201 });
}
