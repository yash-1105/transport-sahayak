-- Operator-only features: (A) Post-Call voice-dispatcher analytics, and
-- (B) ambiguity/duplicate incident review. Both are surfaced ONLY in the
-- operator dashboard UI; metric capture, however, happens on every dispatcher
-- call regardless of caller (prototype RLS allows anon insert). Transcripts are
-- operator-only content (see CLAUDE.md / route handlers).

create extension if not exists pgcrypto;

-- ── (A) Post-Call Analytics ──────────────────────────────────────────────────
-- One row per voice-dispatcher call. time_to_dispatch_ms (ready_at →
-- dispatched_at) is the CORE metric and stops the clock at the `submitted`
-- event — it never times the post-submit briefing / SOPs / ETAs (Hard Rules).
create table public.voice_call_metrics (
  id uuid primary key default gen_random_uuid(),
  incident_id text,
  locale text,                     -- 'en-IN' | 'hi-IN'
  outcome text,                    -- 'dispatched' | 'abandoned' | 'error'
  started_at timestamptz,
  ready_at timestamptz,
  dispatched_at timestamptz,
  ended_at timestamptz,
  time_to_dispatch_ms integer,     -- ready_at -> dispatched_at (CORE metric)
  call_duration_ms integer,
  caller_turns integer,
  agent_turns integer,
  total_turns integer,
  questions_asked integer,
  productive_turns integer,
  fields_collected jsonb,          -- [{ "field": "...", "at_ms": 1234 }, ...]
  reconnects integer default 0,
  transcript jsonb,                -- [{ "role": "...", "at_ms": 1234, "text": "..." }] operator-only drill-down
  created_at timestamptz default now()
);
alter table public.voice_call_metrics enable row level security;
-- Prototype: anyone's call may insert metrics; reads are surfaced only in the
-- operator UI (UI-gated, matching how the Network dashboard is gated).
create policy "insert metrics" on public.voice_call_metrics for insert with check (true);
create policy "read metrics"   on public.voice_call_metrics for select using (true);

-- ── (B) Ambiguity / duplicate review ─────────────────────────────────────────
-- A SEPARATE table so the accidents write path is never disturbed. One row per
-- reviewed incident. review_status='ignored' (with duplicate_of set) marks a
-- likely duplicate the operator chose to ignore; 'kept' marks a cluster the
-- operator confirmed as genuinely separate (so it stops being re-flagged).
create table public.incident_reviews (
  incident_id text primary key,
  review_status text default 'open',   -- 'open' | 'ignored' | 'kept'
  duplicate_of text,                    -- the incident this one duplicates
  reviewed_at timestamptz,
  created_at timestamptz default now()
);
alter table public.incident_reviews enable row level security;
create policy "insert reviews" on public.incident_reviews for insert with check (true);
create policy "update reviews" on public.incident_reviews for update using (true) with check (true);
create policy "read reviews"   on public.incident_reviews for select using (true);
