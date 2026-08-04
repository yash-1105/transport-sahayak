// Supabase database types — GENERATED. Do not edit the generated block by hand.
// Regenerate after any schema change with:
//   supabase gen types typescript --linked --schema public > src/lib/database.types.ts
// (then re-append the "Convenience aliases" block at the bottom).
// Project: transport-sahayak (ref zmlftlekudemqttflinb).

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  public: {
    Tables: {
      incident_reviews: {
        Row: {
          created_at: string | null
          duplicate_of: string | null
          incident_id: string
          review_status: string | null
          reviewed_at: string | null
        }
        Insert: {
          created_at?: string | null
          duplicate_of?: string | null
          incident_id: string
          review_status?: string | null
          reviewed_at?: string | null
        }
        Update: {
          created_at?: string | null
          duplicate_of?: string | null
          incident_id?: string
          review_status?: string | null
          reviewed_at?: string | null
        }
        Relationships: []
      }
      potholes: {
        Row: {
          created_at: string
          description: string | null
          id: string
          lat: number
          lng: number
          reported_date: string
          road: string
          severity: string
          status: string
        }
        Insert: {
          created_at?: string
          description?: string | null
          id: string
          lat: number
          lng: number
          reported_date?: string
          road: string
          severity: string
          status?: string
        }
        Update: {
          created_at?: string
          description?: string | null
          id?: string
          lat?: number
          lng?: number
          reported_date?: string
          road?: string
          severity?: string
          status?: string
        }
        Relationships: []
      }
      profiles: {
        Row: {
          allergies: string | null
          blood_group: string | null
          consent_share_medical: boolean | null
          created_at: string | null
          date_of_birth: string | null
          emergency_contact_1_name: string | null
          emergency_contact_1_phone: string | null
          emergency_contact_1_relation: string | null
          emergency_contact_2_name: string | null
          emergency_contact_2_phone: string | null
          emergency_contact_2_relation: string | null
          full_name: string | null
          home_city: string | null
          id: string
          medical_conditions: string | null
          medications: string | null
          phone: string | null
          updated_at: string | null
          vehicle_registration: string | null
        }
        Insert: {
          allergies?: string | null
          blood_group?: string | null
          consent_share_medical?: boolean | null
          created_at?: string | null
          date_of_birth?: string | null
          emergency_contact_1_name?: string | null
          emergency_contact_1_phone?: string | null
          emergency_contact_1_relation?: string | null
          emergency_contact_2_name?: string | null
          emergency_contact_2_phone?: string | null
          emergency_contact_2_relation?: string | null
          full_name?: string | null
          home_city?: string | null
          id: string
          medical_conditions?: string | null
          medications?: string | null
          phone?: string | null
          updated_at?: string | null
          vehicle_registration?: string | null
        }
        Update: {
          allergies?: string | null
          blood_group?: string | null
          consent_share_medical?: boolean | null
          created_at?: string | null
          date_of_birth?: string | null
          emergency_contact_1_name?: string | null
          emergency_contact_1_phone?: string | null
          emergency_contact_1_relation?: string | null
          emergency_contact_2_name?: string | null
          emergency_contact_2_phone?: string | null
          emergency_contact_2_relation?: string | null
          full_name?: string | null
          home_city?: string | null
          id?: string
          medical_conditions?: string | null
          medications?: string | null
          phone?: string | null
          updated_at?: string | null
          vehicle_registration?: string | null
        }
        Relationships: []
      }
      reported_accidents: {
        Row: {
          created_at: string
          description: string | null
          flags: string[]
          id: string
          lat: number
          lng: number
          location_label: string
          report_mode: string
          reported_date: string
          severity: string | null
        }
        Insert: {
          created_at?: string
          description?: string | null
          flags?: string[]
          id: string
          lat: number
          lng: number
          location_label: string
          report_mode: string
          reported_date?: string
          severity?: string | null
        }
        Update: {
          created_at?: string
          description?: string | null
          flags?: string[]
          id?: string
          lat?: number
          lng?: number
          location_label?: string
          report_mode?: string
          reported_date?: string
          severity?: string | null
        }
        Relationships: []
      }
      suraksha_mitra: {
        Row: {
          availability: string | null
          base_lat: number | null
          base_lng: number | null
          blood_group: string | null
          city: string | null
          coverage_radius_km: number | null
          created_at: string | null
          district: string | null
          email: string | null
          first_aid_level: string | null
          first_aid_trained: boolean | null
          full_name: string
          good_samaritan_consent: boolean | null
          highway: string | null
          languages: string | null
          location_label: string | null
          occupation: string | null
          patrol_stretch: string | null
          phone: string
          status: string | null
          user_id: string
          vehicle_available: boolean | null
        }
        Insert: {
          availability?: string | null
          base_lat?: number | null
          base_lng?: number | null
          blood_group?: string | null
          city?: string | null
          coverage_radius_km?: number | null
          created_at?: string | null
          district?: string | null
          email?: string | null
          first_aid_level?: string | null
          first_aid_trained?: boolean | null
          full_name: string
          good_samaritan_consent?: boolean | null
          highway?: string | null
          languages?: string | null
          location_label?: string | null
          occupation?: string | null
          patrol_stretch?: string | null
          phone: string
          status?: string | null
          user_id: string
          vehicle_available?: boolean | null
        }
        Update: {
          availability?: string | null
          base_lat?: number | null
          base_lng?: number | null
          blood_group?: string | null
          city?: string | null
          coverage_radius_km?: number | null
          created_at?: string | null
          district?: string | null
          email?: string | null
          first_aid_level?: string | null
          first_aid_trained?: boolean | null
          full_name?: string
          good_samaritan_consent?: boolean | null
          highway?: string | null
          languages?: string | null
          location_label?: string | null
          occupation?: string | null
          patrol_stretch?: string | null
          phone?: string
          status?: string | null
          user_id?: string
          vehicle_available?: boolean | null
        }
        Relationships: []
      }
      voice_call_metrics: {
        Row: {
          agent_turns: number | null
          call_duration_ms: number | null
          caller_turns: number | null
          created_at: string | null
          dispatched_at: string | null
          ended_at: string | null
          fields_collected: Json | null
          id: string
          incident_id: string | null
          locale: string | null
          outcome: string | null
          productive_turns: number | null
          questions_asked: number | null
          ready_at: string | null
          reconnects: number | null
          started_at: string | null
          time_to_dispatch_ms: number | null
          total_turns: number | null
          transcript: Json | null
        }
        Insert: {
          agent_turns?: number | null
          call_duration_ms?: number | null
          caller_turns?: number | null
          created_at?: string | null
          dispatched_at?: string | null
          ended_at?: string | null
          fields_collected?: Json | null
          id?: string
          incident_id?: string | null
          locale?: string | null
          outcome?: string | null
          productive_turns?: number | null
          questions_asked?: number | null
          ready_at?: string | null
          reconnects?: number | null
          started_at?: string | null
          time_to_dispatch_ms?: number | null
          total_turns?: number | null
          transcript?: Json | null
        }
        Update: {
          agent_turns?: number | null
          call_duration_ms?: number | null
          caller_turns?: number | null
          created_at?: string | null
          dispatched_at?: string | null
          ended_at?: string | null
          fields_collected?: Json | null
          id?: string
          incident_id?: string | null
          locale?: string | null
          outcome?: string | null
          productive_turns?: number | null
          questions_asked?: number | null
          ready_at?: string | null
          reconnects?: number | null
          started_at?: string | null
          time_to_dispatch_ms?: number | null
          total_turns?: number | null
          transcript?: Json | null
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      [_ in never]: never
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const

// ── Convenience aliases (hand-maintained) ─────────────────────────────────────
export type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];
export type ProfileInsert = Database["public"]["Tables"]["profiles"]["Insert"];
export type SurakshaMitraRow = Database["public"]["Tables"]["suraksha_mitra"]["Row"];
export type SurakshaMitraInsert = Database["public"]["Tables"]["suraksha_mitra"]["Insert"];
export type VoiceCallMetricsRow = Database["public"]["Tables"]["voice_call_metrics"]["Row"];
export type IncidentReviewRow = Database["public"]["Tables"]["incident_reviews"]["Row"];
