"use client";

import { useState, useEffect, useCallback } from "react";
import type { DbPothole } from "@/lib/types";

export function usePotholes() {
  const [potholes, setPotholes] = useState<DbPothole[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetch_ = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await fetch("/api/potholes", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: DbPothole[] = await res.json();
      setPotholes(Array.isArray(data) ? data : []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetch_(); }, [fetch_]);

  return { potholes, loading, error, refetch: fetch_ };
}
