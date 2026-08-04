-- Suraksha Mitra volunteers now capture a base location (GPS or map-set). The
-- base_lat/base_lng columns already exist; add a human-readable location label
-- (reverse-geocoded coverage area) shown in the form's "set" state and mirrored
-- into the responder registry. Owner-only RLS on the table is unchanged.
alter table public.suraksha_mitra
  add column if not exists location_label text;
