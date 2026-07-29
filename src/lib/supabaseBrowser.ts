"use client";
// Browser-side Supabase client for the Phase-1 accounts feature (auth + the
// user's own profiles / suraksha_mitra rows via RLS). This is deliberately
// SEPARATE from src/lib/supabase.ts, which stays untouched for the existing
// server route handlers (potholes / accidents) — sharing one client would risk
// changing their behaviour. The app is a client-only SPA (MapView is an
// ssr:false dynamic import), so authenticated reads/writes go straight from the
// browser and RLS scopes every row to auth.uid(). No @supabase/ssr, no
// middleware, no service-role key — the anon key + RLS are sufficient.

import { createClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const supabaseBrowser =
  url && key
    ? createClient<Database>(url, key, {
        auth: {
          // Persist the session in localStorage and silently refresh the access
          // token so a signed-in user stays signed in across reloads. No OAuth
          // redirect flow is used here, so URL session detection is off.
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: false,
        },
      })
    : null;

export const isSupabaseConfigured = !!supabaseBrowser;
