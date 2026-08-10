"use client";
import React, { useEffect, useState } from "react";
import { C, RADIUS } from "@/lib/design";
import { useAuthStore } from "@/store/authStore";
import { supabaseBrowser } from "@/lib/supabaseBrowser";
import { reverseGeocode } from "@/lib/geocode";
import { publishVolunteer } from "@/lib/signalsPublisher";
import { MapPinIcon, CrosshairIcon, MapIcon } from "@/components/ui/icons";
import LocationPicker from "@/components/auth/LocationPicker";
import type { SurakshaMitraRow } from "@/lib/database.types";
import {
  Sheet, Field, TextInput, Select, Toggle, CheckboxRow, PrimaryButton,
  ErrorBanner, InfoBanner, BLOOD_GROUPS,
} from "@/components/auth/ui";

interface VolunteerLocation { lat: number; lng: number; label: string }

function Section({ children, hi, showHindi }: { children: React.ReactNode; hi?: string; showHindi: boolean }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted, marginTop: 6 }}>
      {children}
      {showHindi && hi && <span style={{ fontWeight: 500 }}> · {hi}</span>}
    </div>
  );
}

const OCCUPATIONS = [
  { value: "local resident", label: "Local resident" },
  { value: "dhaba or restaurant staff", label: "Dhaba / restaurant staff" },
  { value: "petrol-pump staff", label: "Petrol-pump staff" },
  { value: "doctor-nurse-paramedic", label: "Doctor / nurse / paramedic" },
  { value: "driver", label: "Driver" },
  { value: "student", label: "Student" },
  { value: "other", label: "Other" },
];
const FIRST_AID_LEVELS = [
  { value: "none", label: "None" },
  { value: "basic", label: "Basic first aid" },
  { value: "cpr", label: "CPR trained" },
  { value: "professional", label: "Professional (medic)" },
];
const AVAILABILITY = [
  { value: "daytime", label: "Daytime" },
  { value: "night", label: "Night" },
  { value: "24x7", label: "24×7" },
  { value: "weekends", label: "Weekends" },
];
// Coverage is defined as a radius (km) around the volunteer's base location —
// their notification zone — rather than free-text highway / patrol-stretch.
const RADIUS_OPTIONS = [5, 8, 10] as const;
const DEFAULT_RADIUS_KM = 8;

// Free-text coverage fields (highway/patrol_stretch/district/city) are no longer
// collected — coverage is the base point + radius. They stay in the DB schema
// but are omitted from the form.
type Form = Omit<
  SurakshaMitraRow,
  "user_id" | "created_at" | "status" | "base_lat" | "base_lng" | "location_label"
  | "coverage_radius_km" | "highway" | "patrol_stretch" | "district" | "city"
>;

const EMPTY: Form = {
  full_name: "", phone: "", email: "", occupation: "", first_aid_trained: false,
  first_aid_level: "none",
  availability: "", vehicle_available: false, blood_group: "", languages: "",
  good_samaritan_consent: false,
};

export default function SurakshaMitraSheet({ showHindi, onClose }: { showHindi: boolean; onClose: () => void }) {
  const user = useAuthStore((s) => s.user);
  const [form, setForm] = useState<Form>(EMPTY);
  const [loc, setLoc] = useState<VolunteerLocation | null>(null);
  const [radiusKm, setRadiusKm] = useState<number>(DEFAULT_RADIUS_KM);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setSaved(false);
  }
  function setLocation(next: VolunteerLocation | null) {
    setLoc(next);
    setSaved(false);
  }
  const s = (v: string | null | undefined) => v ?? "";

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!supabaseBrowser || !user) { setLoading(false); return; }
      const { data, error } = await supabaseBrowser
        .from("suraksha_mitra")
        .select("*")
        .eq("user_id", user.id)
        .maybeSingle();
      if (cancelled) return;
      if (error) setError("Could not load your registration — you can still fill it in and save.");
      if (data) {
        setForm({
          full_name: s(data.full_name), phone: s(data.phone), email: s(data.email),
          occupation: s(data.occupation), first_aid_trained: !!data.first_aid_trained,
          first_aid_level: s(data.first_aid_level) || "none",
          availability: s(data.availability), vehicle_available: !!data.vehicle_available,
          blood_group: s(data.blood_group), languages: s(data.languages),
          good_samaritan_consent: !!data.good_samaritan_consent,
        });
        if (typeof data.base_lat === "number" && typeof data.base_lng === "number") {
          setLoc({ lat: data.base_lat, lng: data.base_lng, label: s(data.location_label) });
        }
        if (typeof data.coverage_radius_km === "number") setRadiusKm(data.coverage_radius_km);
      } else {
        // New registration: prefill name/email from the auth account.
        setForm((f) => ({
          ...f,
          full_name: (user.user_metadata?.full_name as string) ?? f.full_name,
          email: user.email ?? "",
        }));
      }
      setLoading(false);
    }
    load();
    return () => { cancelled = true; };
  }, [user]);

  const canSave = !!s(form.full_name).trim() && !!s(form.phone).trim() && !!loc;

  // GPS auto-detect → reverse-geocode a label. Degrades to an explicit,
  // actionable message (permission denied / unavailable → use the map picker).
  function detect() {
    setGeoError(null);
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setGeoError("Location isn't available on this device — set it on the map instead.");
      return;
    }
    setDetecting(true);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lat = pos.coords.latitude;
        const lng = pos.coords.longitude;
        let label = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
        try { label = await reverseGeocode(lat, lng); } catch { /* keep coords */ }
        setLocation({ lat, lng, label });
        setDetecting(false);
      },
      (err) => {
        setGeoError(
          err.code === err.PERMISSION_DENIED
            ? "Location permission denied — set it on the map instead."
            : "Couldn't detect your location — set it on the map instead."
        );
        setDetecting(false);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
  }

  async function save() {
    if (!supabaseBrowser || !user) return;
    if (!canSave || !loc) {
      setError(!loc ? "Please set your location." : "Full name and phone are required.");
      return;
    }
    setSaving(true);
    setError(null);
    const { error } = await supabaseBrowser
      .from("suraksha_mitra")
      .upsert(
        {
          user_id: user.id,
          ...form,
          full_name: s(form.full_name).trim(),
          phone: s(form.phone).trim(),
          base_lat: loc.lat,
          base_lng: loc.lng,
          location_label: loc.label || null,
          coverage_radius_km: radiusKm,
          status: "registered",
        },
        { onConflict: "user_id" }
      );
    setSaving(false);
    if (error) { setError(error.message); return; }
    setSaved(true);

    // Fire-and-forget mirror into the Signals/Aggregator responder registry.
    // Never awaited; a Signals outage silently no-ops — the Supabase record
    // above is the source of truth. Registration record only.
    publishVolunteer({
      userId: user.id,
      name: s(form.full_name).trim(),
      phone: s(form.phone).trim(),
      lat: loc.lat,
      lng: loc.lng,
      locationLabel: loc.label,
      coverageRadiusKm: radiusKm,
      occupation: s(form.occupation),
      firstAidTrained: !!form.first_aid_trained,
      firstAidLevel: s(form.first_aid_level),
    });
  }

  return (
    <Sheet title="Suraksha Mitra" hiTitle="सुरक्षा मित्र" showHindi={showHindi} onClose={onClose} busy={saving} maxWidth={620}>
      {loading ? (
        <div className="flex items-center gap-2" style={{ padding: 24 }}>
          <span className="inline-block w-4 h-4 rounded-full animate-spin" style={{ border: "2px solid #d1d5db", borderTopColor: C.secondary }} />
          <span style={{ fontSize: 13, color: C.secondary }}>Loading…</span>
        </div>
      ) : (
        <div className="flex flex-col" style={{ gap: 13, padding: "16px 20px 22px" }}>
          <Section showHindi={showHindi} hi="आपके बारे में">About you</Section>
          <Field label="Full name" suffix={showHindi ? "· पूरा नाम" : undefined} required>
            <TextInput value={s(form.full_name)} onChange={(e) => set("full_name", e.target.value)} placeholder="Your full name" autoComplete="name" />
          </Field>
          <div className="flex" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Field label="Phone" suffix={showHindi ? "· फ़ोन" : undefined} required>
                <TextInput value={s(form.phone)} onChange={(e) => set("phone", e.target.value)} placeholder="+91…" inputMode="tel" autoComplete="tel" />
              </Field>
            </div>
            <div style={{ flex: 1 }}>
              <Field label="Email" suffix={showHindi ? "· ईमेल" : undefined}>
                <TextInput type="email" value={s(form.email)} onChange={(e) => set("email", e.target.value)} placeholder="you@example.com" inputMode="email" />
              </Field>
            </div>
          </div>
          <Field label="Occupation / background" suffix={showHindi ? "· पृष्ठभूमि" : undefined}>
            <Select value={s(form.occupation)} onChange={(v) => set("occupation", v)} options={OCCUPATIONS} placeholder="Select…" />
          </Field>

          <Section showHindi={showHindi} hi="प्राथमिक उपचार">First aid</Section>
          <div className="flex" style={{ gap: 16, alignItems: "flex-end" }}>
            <Field label="First-aid trained?" suffix={showHindi ? "· प्रशिक्षित?" : undefined}>
              <Toggle value={!!form.first_aid_trained} onChange={(v) => set("first_aid_trained", v)} />
            </Field>
            <div style={{ flex: 1 }}>
              <Field label="Training level" suffix={showHindi ? "· स्तर" : undefined}>
                <Select value={s(form.first_aid_level)} onChange={(v) => set("first_aid_level", v)} options={FIRST_AID_LEVELS} />
              </Field>
            </div>
          </div>

          <Section showHindi={showHindi} hi="आपका स्थान">Your location</Section>
          {loc ? (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              style={{
                width: "100%", boxSizing: "border-box", padding: "12px 13px", borderRadius: RADIUS.input,
                fontSize: 13, cursor: "pointer", textAlign: "left", fontWeight: 600,
                border: `1px solid ${C.greenSoftBorder}`, background: C.greenSoftBg, color: C.greenSoftText,
              }}
            >
              <span className="flex items-center gap-2">
                <MapPinIcon size={15} style={{ flex: "none" }} />
                {loc.label || `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)}`}
              </span>
            </button>
          ) : (
            <div style={{
              width: "100%", boxSizing: "border-box", padding: "12px 13px", borderRadius: RADIUS.input,
              fontSize: 13, border: "1.5px dashed #C9B98F", background: "#FDFBF6", color: C.muted,
            }}>
              <span className="flex items-center gap-2">
                <MapPinIcon size={15} style={{ flex: "none" }} />
                Set your base location below
              </span>
            </div>
          )}
          <div className="flex" style={{ gap: 10 }}>
            <button
              type="button"
              onClick={detect}
              disabled={detecting}
              style={{
                flex: 1, padding: "10px 12px", border: `1px solid ${C.border}`, borderRadius: RADIUS.input,
                background: "#fff", color: detecting ? C.muted : C.body, fontSize: 12.5, fontWeight: 600,
                cursor: detecting ? "default" : "pointer",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
              }}
            >
              <CrosshairIcon size={15} style={{ flex: "none" }} />
              {detecting ? "Detecting…" : "Detect automatically"}
            </button>
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              style={{
                flex: 1, padding: "10px 12px", border: `1px solid ${C.border}`, borderRadius: RADIUS.input,
                background: "#fff", color: C.body, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
              }}
            >
              <MapIcon size={15} style={{ flex: "none" }} />
              Set on map
            </button>
          </div>
          {geoError && <ErrorBanner>{geoError}</ErrorBanner>}

          {/* Coverage radius — the notification zone around the base location. */}
          <Field label="Coverage radius" suffix={showHindi ? "· कवरेज दायरा" : undefined}>
            <div className="flex" style={{ gap: 6, background: C.page, borderRadius: RADIUS.input, padding: 4 }}>
              {RADIUS_OPTIONS.map((r) => {
                const active = r === radiusKm;
                return (
                  <button
                    key={r}
                    type="button"
                    onClick={() => { setRadiusKm(r); setSaved(false); }}
                    style={{
                      flex: 1, padding: "8px 0", border: "none", borderRadius: 7, cursor: "pointer",
                      fontSize: 13, fontWeight: active ? 700 : 500,
                      background: active ? "#fff" : "transparent",
                      color: active ? C.saffronSoftText : C.secondary,
                      boxShadow: active ? "0 1px 2px rgba(0,0,0,.08)" : "none",
                    }}
                  >
                    {r} km
                  </button>
                );
              })}
            </div>
          </Field>
          <p style={{ fontSize: 11, color: C.muted, marginTop: -4 }}>
            Accidents within this radius of your location are in your area. Only this zone is shown publicly —
            your phone number and precise address stay private.
            {showHindi && <span style={{ display: "block", marginTop: 1 }}>इस दायरे में होने वाली दुर्घटनाएँ आपके क्षेत्र में हैं। सार्वजनिक रूप से केवल यह क्षेत्र दिखता है; फ़ोन नंबर व सटीक पता निजी रहता है।</span>}
          </p>

          <Section showHindi={showHindi} hi="उपलब्धता">Availability</Section>
          <div className="flex" style={{ gap: 16, alignItems: "flex-end" }}>
            <div style={{ flex: 1 }}>
              <Field label="Availability / shift" suffix={showHindi ? "· पाली" : undefined}>
                <Select value={s(form.availability)} onChange={(v) => set("availability", v)} options={AVAILABILITY} placeholder="Select…" />
              </Field>
            </div>
            <Field label="Own vehicle?" suffix={showHindi ? "· वाहन?" : undefined}>
              <Toggle value={!!form.vehicle_available} onChange={(v) => set("vehicle_available", v)} />
            </Field>
          </div>

          <Section showHindi={showHindi} hi="वैकल्पिक">Optional</Section>
          <div className="flex" style={{ gap: 12 }}>
            <div style={{ width: 150 }}>
              <Field label="Blood group" suffix={showHindi ? "· रक्त" : undefined}>
                <Select value={s(form.blood_group)} onChange={(v) => set("blood_group", v)} options={BLOOD_GROUPS} placeholder="—" />
              </Field>
            </div>
            <div style={{ flex: 1 }}>
              <Field label="Languages spoken" suffix={showHindi ? "· भाषाएँ" : undefined}>
                <TextInput value={s(form.languages)} onChange={(e) => set("languages", e.target.value)} placeholder="Hindi, English, Garhwali…" />
              </Field>
            </div>
          </div>

          <CheckboxRow checked={!!form.good_samaritan_consent} onChange={(v) => set("good_samaritan_consent", v)}>
            I&apos;m willing to help as a Good Samaritan first-responder and consent to being registered
            {showHindi && <span style={{ display: "block", opacity: 0.85, marginTop: 1 }}>मैं नेक इंसान (Good Samaritan) के रूप में मदद के लिए तैयार हूँ</span>}
          </CheckboxRow>

          {error && <ErrorBanner>{error}</ErrorBanner>}
          {saved && <InfoBanner tone="green">Registered. Thank you for volunteering as a Suraksha Mitra.</InfoBanner>}

          <PrimaryButton onClick={save} busy={saving} disabled={!canSave}>Register</PrimaryButton>
          {!loc && (
            <p style={{ fontSize: 11, color: C.muted, textAlign: "center", marginTop: -4 }}>
              Set your location to register.
            </p>
          )}
        </div>
      )}

      {pickerOpen && (
        <LocationPicker
          initial={loc ? { lat: loc.lat, lng: loc.lng } : null}
          radiusKm={radiusKm}
          showHindi={showHindi}
          onCancel={() => setPickerOpen(false)}
          onConfirm={(p, label) => { setLocation({ lat: p.lat, lng: p.lng, label }); setPickerOpen(false); }}
        />
      )}
    </Sheet>
  );
}
