"use client";
import React, { useEffect, useState } from "react";
import { C } from "@/lib/design";
import { useAuthStore } from "@/store/authStore";
import { supabaseBrowser } from "@/lib/supabaseBrowser";
import type { ProfileRow } from "@/lib/database.types";
import {
  Sheet, Field, TextInput, TextArea, Select, CheckboxRow, PrimaryButton,
  ErrorBanner, InfoBanner, BLOOD_GROUPS,
} from "@/components/auth/ui";

// Section heading inside the form.
function Section({ children, hi, showHindi }: { children: React.ReactNode; hi?: string; showHindi: boolean }) {
  return (
    <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted, marginTop: 6 }}>
      {children}
      {showHindi && hi && <span style={{ fontWeight: 500 }}> · {hi}</span>}
    </div>
  );
}

type Form = Omit<ProfileRow, "id" | "created_at" | "updated_at">;

const EMPTY: Form = {
  full_name: "", phone: "", blood_group: "", date_of_birth: "", home_city: "",
  allergies: "", medical_conditions: "", medications: "",
  emergency_contact_1_name: "", emergency_contact_1_phone: "", emergency_contact_1_relation: "",
  emergency_contact_2_name: "", emergency_contact_2_phone: "", emergency_contact_2_relation: "",
  vehicle_registration: "", consent_share_medical: false,
};

export default function SafetyProfileSheet({ showHindi, onClose }: { showHindi: boolean; onClose: () => void }) {
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
  // Text fields normalise "" so a cleared input is stored, and undefined/null loads as "".
  const s = (v: string | null | undefined) => v ?? "";

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!supabaseBrowser || !user) { setLoading(false); return; }
      const { data, error } = await supabaseBrowser
        .from("profiles")
        .select("*")
        .eq("id", user.id)
        .maybeSingle();
      if (cancelled) return;
      if (error) setError("Could not load your profile — you can still fill it in and save.");
      if (data) {
        setForm({
          full_name: s(data.full_name), phone: s(data.phone), blood_group: s(data.blood_group),
          date_of_birth: s(data.date_of_birth), home_city: s(data.home_city),
          allergies: s(data.allergies), medical_conditions: s(data.medical_conditions), medications: s(data.medications),
          emergency_contact_1_name: s(data.emergency_contact_1_name),
          emergency_contact_1_phone: s(data.emergency_contact_1_phone),
          emergency_contact_1_relation: s(data.emergency_contact_1_relation),
          emergency_contact_2_name: s(data.emergency_contact_2_name),
          emergency_contact_2_phone: s(data.emergency_contact_2_phone),
          emergency_contact_2_relation: s(data.emergency_contact_2_relation),
          vehicle_registration: s(data.vehicle_registration),
          consent_share_medical: !!data.consent_share_medical,
        });
      }
      setLoading(false);
    }
    load();
    return () => { cancelled = true; };
  }, [user]);

  async function save() {
    if (!supabaseBrowser || !user) return;
    setSaving(true);
    setError(null);
    // Empty date must be sent as null, not "" (date column rejects "").
    const dob = form.date_of_birth ? form.date_of_birth : null;
    const { error } = await supabaseBrowser
      .from("profiles")
      .upsert({ id: user.id, ...form, date_of_birth: dob, updated_at: new Date().toISOString() }, { onConflict: "id" });
    setSaving(false);
    if (error) { setError(error.message); return; }
    setSaved(true);
  }

  return (
    <Sheet title="My safety profile" hiTitle="मेरी सुरक्षा प्रोफ़ाइल" showHindi={showHindi} onClose={onClose} busy={saving} maxWidth={620}>
      {loading ? (
        <div className="flex items-center gap-2" style={{ padding: 24 }}>
          <span className="inline-block w-4 h-4 rounded-full animate-spin" style={{ border: "2px solid #d1d5db", borderTopColor: C.secondary }} />
          <span style={{ fontSize: 13, color: C.secondary }}>Loading your profile…</span>
        </div>
      ) : (
        <div className="flex flex-col" style={{ gap: 13, padding: "16px 20px 22px" }}>
          <InfoBanner>
            This is private, behind your account, and never shown on the public map. It helps responders in the
            golden hour if you&apos;re in an accident.
          </InfoBanner>

          <Section showHindi={showHindi} hi="व्यक्तिगत">Personal</Section>
          <Field label="Full name" suffix={showHindi ? "· पूरा नाम" : undefined}>
            <TextInput value={s(form.full_name)} onChange={(e) => set("full_name", e.target.value)} placeholder="Your full name" autoComplete="name" />
          </Field>
          <div className="flex" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Field label="Phone" suffix={showHindi ? "· फ़ोन" : undefined}>
                <TextInput value={s(form.phone)} onChange={(e) => set("phone", e.target.value)} placeholder="+91…" inputMode="tel" autoComplete="tel" />
              </Field>
            </div>
            <div style={{ width: 130 }}>
              <Field label="Blood group" suffix={showHindi ? "· रक्त" : undefined}>
                <Select value={s(form.blood_group)} onChange={(v) => set("blood_group", v)} options={BLOOD_GROUPS} placeholder="—" />
              </Field>
            </div>
          </div>
          <div className="flex" style={{ gap: 12 }}>
            <div style={{ flex: 1 }}>
              <Field label="Date of birth" suffix={showHindi ? "· जन्म तिथि" : undefined}>
                <TextInput type="date" value={s(form.date_of_birth)} onChange={(e) => set("date_of_birth", e.target.value)} />
              </Field>
            </div>
            <div style={{ flex: 1 }}>
              <Field label="Home city" suffix={showHindi ? "· शहर" : undefined}>
                <TextInput value={s(form.home_city)} onChange={(e) => set("home_city", e.target.value)} placeholder="e.g. Guwahati" />
              </Field>
            </div>
          </div>

          <Section showHindi={showHindi} hi="चिकित्सा">Medical</Section>
          <Field label="Allergies" suffix={showHindi ? "· एलर्जी" : undefined}>
            <TextArea value={s(form.allergies)} onChange={(e) => set("allergies", e.target.value)} placeholder="Penicillin, latex… (or leave blank)" style={{ minHeight: 52 }} />
          </Field>
          <Field label="Chronic medical conditions" suffix={showHindi ? "· स्थितियाँ" : undefined}>
            <TextArea value={s(form.medical_conditions)} onChange={(e) => set("medical_conditions", e.target.value)} placeholder="Diabetes, asthma, heart condition…" style={{ minHeight: 52 }} />
          </Field>
          <Field label="Current medications" suffix={showHindi ? "· दवाइयाँ" : undefined}>
            <TextArea value={s(form.medications)} onChange={(e) => set("medications", e.target.value)} placeholder="Insulin, blood thinners…" style={{ minHeight: 52 }} />
          </Field>

          <Section showHindi={showHindi} hi="आपातकालीन संपर्क 1">Emergency contact 1</Section>
          <ContactRow prefix="emergency_contact_1" form={form} set={set} showHindi={showHindi} />
          <Section showHindi={showHindi} hi="आपातकालीन संपर्क 2">Emergency contact 2</Section>
          <ContactRow prefix="emergency_contact_2" form={form} set={set} showHindi={showHindi} />

          <Section showHindi={showHindi} hi="वाहन">Vehicle</Section>
          <Field label="Vehicle registration" suffix={showHindi ? "· वाहन नंबर" : undefined}>
            <TextInput value={s(form.vehicle_registration)} onChange={(e) => set("vehicle_registration", e.target.value)} placeholder="e.g. UK07 AB 1234" />
          </Field>

          <CheckboxRow checked={!!form.consent_share_medical} onChange={(v) => set("consent_share_medical", v)}>
            Allow responders to see my medical info during an emergency
            {showHindi && <span style={{ display: "block", opacity: 0.85, marginTop: 1 }}>आपात स्थिति में प्रतिक्रियाकर्ता मेरी चिकित्सा जानकारी देख सकते हैं</span>}
          </CheckboxRow>

          {error && <ErrorBanner>{error}</ErrorBanner>}
          {saved && <InfoBanner tone="green">Saved. Your safety profile is up to date.</InfoBanner>}

          <PrimaryButton onClick={save} busy={saving}>Save profile · सहेजें</PrimaryButton>
        </div>
      )}
    </Sheet>
  );
}

function ContactRow({
  prefix, form, set, showHindi,
}: {
  prefix: "emergency_contact_1" | "emergency_contact_2";
  form: Form;
  set: <K extends keyof Form>(k: K, v: Form[K]) => void;
  showHindi: boolean;
}) {
  const nameKey = `${prefix}_name` as keyof Form;
  const phoneKey = `${prefix}_phone` as keyof Form;
  const relKey = `${prefix}_relation` as keyof Form;
  const val = (k: keyof Form) => (form[k] as string) ?? "";
  return (
    <div className="flex flex-col" style={{ gap: 10 }}>
      <div className="flex" style={{ gap: 12 }}>
        <div style={{ flex: 1 }}>
          <Field label="Name" suffix={showHindi ? "· नाम" : undefined}>
            <TextInput value={val(nameKey)} onChange={(e) => set(nameKey, e.target.value as Form[typeof nameKey])} placeholder="Contact name" />
          </Field>
        </div>
        <div style={{ width: 150 }}>
          <Field label="Relation" suffix={showHindi ? "· संबंध" : undefined}>
            <TextInput value={val(relKey)} onChange={(e) => set(relKey, e.target.value as Form[typeof relKey])} placeholder="e.g. Spouse" />
          </Field>
        </div>
      </div>
      <Field label="Phone" suffix={showHindi ? "· फ़ोन" : undefined}>
        <TextInput value={val(phoneKey)} onChange={(e) => set(phoneKey, e.target.value as Form[typeof phoneKey])} placeholder="+91…" inputMode="tel" />
      </Field>
    </div>
  );
}
