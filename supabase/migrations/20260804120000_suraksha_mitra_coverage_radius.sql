-- Suraksha Mitra volunteers now define their coverage as a radius (km) around
-- their base location instead of free-text highway / patrol-stretch fields.
-- Stores the chosen radius; the base point lives in base_lat/base_lng.
alter table public.suraksha_mitra
  add column if not exists coverage_radius_km integer;
