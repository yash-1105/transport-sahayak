"use client";
import React, { useEffect, useState } from "react";
import { C } from "@/lib/design";
import { useAuthStore } from "@/store/authStore";
import { supabaseBrowser } from "@/lib/supabaseBrowser";
import type { SurakshaMitraRow } from "@/lib/database.types";
import {
  Sheet, Field, TextInput, Select, Toggle, CheckboxRow, PrimaryButton,
  ErrorBanner, InfoBanner, BLOOD_GROUPS,
} from "@/components/auth/ui";

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

type Form = Omit<SurakshaMitraRow, "user_id" | "created_at" | "status" | "base_lat" | "base_lng">;

const EMPTY: Form = {
  full_name: "", phone: "", email: "", occupation: "", first_aid_trained: false,
  first_aid_level: "none", highway: "", patrol_stretch: "", district: "", city: "",
  availability: "", vehicle_available: false, blood_group: "", languages: "",
  good_samaritan_consent: false,
};

export default function SurakshaMitraSheet({ showHindi, onClose }: { showHindi: boolean; onClose: () => void }) {
  const user = useAuthStore((s) => s.user);
  const [form, setForm] = useState<Form>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((f) => ({ ...f, [key]: value }));
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
          first_aid_level: s(data.first_aid_level) || "none", highway: s(data.highway),
          patrol_stretch: s(data.patrol_stretch), district: s(data.district), city: s(data.city),
          availability: s(data.availability), vehicle_available: !!data.vehicle_available,
          blood_group: s(data.blood_group), languages: s(data.languages),
          good_samaritan_consent: !!data.good_samaritan_consent,
        });
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

  const canSave = !!s(form.full_name).trim() && !!s(form.phone).trim();

  async function save() {
    if (!supabaseBrowser || !user) return;
    if (!canSave) { setError("Full name and phone are required."); return; }
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
          status: "registered",
        },
        { onConflict: "user_id" }
      );
    setSaving(false);
    if (error) { setError(error.message); return; }
    setSaved(true);
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
          <InfoBanner tone="saffron">
            <b>Community volunteer first-responder.</b> In the golden hour after a crash, a trained neighbour who
            reaches the scene first can save lives. This is a <b>registration only</b> — it records your details;
            nothing here dispatches or activates you. India&apos;s Good Samaritan law protects those who help.
            {showHindi && (
              <span style={{ display: "block", marginTop: 3, opacity: 0.9 }}>
                यह केवल पंजीकरण है — आपका विवरण दर्ज करता है; कोई सक्रियण नहीं।
              </span>
            )}
          </InfoBanner>

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

          <Section showHindi={showHindi} hi="कवरेज क्षेत्र">Coverage</Section>
          <div className="flex" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Field label="Highway" suffix={showHindi ? "· राजमार्ग" : undefined}>
                <TextInput value={s(form.highway)} onChange={(e) => set("highway", e.target.value)} placeholder="e.g. NH-27 / Delhi–Dehradun Expy" />
              </Field>
            </div>
            <div style={{ flex: 1 }}>
              <Field label="Patrol stretch / area" suffix={showHindi ? "· क्षेत्र" : undefined}>
                <TextInput value={s(form.patrol_stretch)} onChange={(e) => set("patrol_stretch", e.target.value)} placeholder="Coverage stretch" />
              </Field>
            </div>
          </div>
          <div className="flex" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Field label="District" suffix={showHindi ? "· ज़िला" : undefined}>
                <TextInput value={s(form.district)} onChange={(e) => set("district", e.target.value)} placeholder="District" />
              </Field>
            </div>
            <div style={{ flex: 1 }}>
              <Field label="City / town" suffix={showHindi ? "· शहर" : undefined}>
                <TextInput value={s(form.city)} onChange={(e) => set("city", e.target.value)} placeholder="City or town" />
              </Field>
            </div>
          </div>
          {/* base_lat/base_lng intentionally left unset in Phase 1 — map-pin base
              location wiring is a later phase (kept out per scope). */}

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

          <PrimaryButton onClick={save} busy={saving} disabled={!canSave}>Register · पंजीकरण करें</PrimaryButton>
        </div>
      )}
    </Sheet>
  );
}
