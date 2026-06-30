"use client";

import { useState, useEffect, useCallback } from "react";
import type { DbAccident } from "@/lib/types";

export function useAccidents() {
  const [accidents, setAccidents] = useState<DbAccident[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetch_ = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await fetch("/api/accidents", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: DbAccident[] = await res.json();
      setAccidents(Array.isArray(data) ? data : []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetch_(); }, [fetch_]);

  return { accidents, loading, error, refetch: fetch_ };
}
