-- Phase 1: citizen + volunteer (Suraksha Mitra) accounts.
--
-- Two user-owned tables, each 1:1 with an auth.users row, protected by RLS so a
-- signed-in user can only read/write THEIR OWN row. These hold REAL
-- user-entered records (NOT synthetic seed data) — medical + emergency-contact
-- PII that stays behind auth and is never exposed on the public map.
--
-- NOTE: the synthetic Suraksha Mitra patrol-post layer in
-- data/suraksha-mitras.json is a SEPARATE, unrelated dataset that happens to
-- share the name; these user-registered volunteers are distinct.

create extension if not exists pgcrypto;

-- ── Citizen safety profile (1:1 with the auth user) ──────────────────────────
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  phone text,
  blood_group text,           -- A+ A- B+ B- AB+ AB- O+ O-
  date_of_birth date,
  home_city text,
  allergies text,
  medical_conditions text,
  medications text,
  emergency_contact_1_name text,
  emergency_contact_1_phone text,
  emergency_contact_1_relation text,
  emergency_contact_2_name text,
  emergency_contact_2_phone text,
  emergency_contact_2_relation text,
  vehicle_registration text,
  consent_share_medical boolean default false,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
alter table public.profiles enable row level security;
create policy "own profile read"   on public.profiles for select using (auth.uid() = id);
create policy "own profile insert" on public.profiles for insert with check (auth.uid() = id);
create policy "own profile update" on public.profiles for update using (auth.uid() = id) with check (auth.uid() = id);

-- ── Suraksha Mitra volunteer registration (one per account) ──────────────────
create table public.suraksha_mitra (
  user_id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null,
  phone text not null,
  email text,
  occupation text,            -- local resident / dhaba or restaurant / petrol pump / doctor-nurse-paramedic / driver / student / other
  first_aid_trained boolean default false,
  first_aid_level text,       -- none / basic / cpr / professional
  highway text,               -- e.g. NH-27, AT Road, Guwahati (Assam)
  patrol_stretch text,        -- coverage area / stretch
  district text,
  city text,
  base_lat double precision,
  base_lng double precision,
  availability text,          -- daytime / night / 24x7 / weekends
  vehicle_available boolean default false,
  blood_group text,
  languages text,
  good_samaritan_consent boolean default false,
  status text default 'registered',
  created_at timestamptz default now()
);
alter table public.suraksha_mitra enable row level security;
create policy "own mitra read"   on public.suraksha_mitra for select using (auth.uid() = user_id);
create policy "own mitra insert" on public.suraksha_mitra for insert with check (auth.uid() = user_id);
create policy "own mitra update" on public.suraksha_mitra for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
