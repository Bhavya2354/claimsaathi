"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { CaseView } from "@/lib/types";

const POLL_MS = 2500;

/** Loads the case and keeps polling while any upload is still being read. */
export function useCase(caseId: string) {
  const [view, setView] = useState<CaseView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      try {
        const next = await api.getCase(caseId);
        if (cancelled) return;
        setView(next);
        setError(null);
        if (next.documents.some((d) => d.status === "processing")) {
          timer = setTimeout(tick, POLL_MS);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load the case.");
      }
    }

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [caseId, pollKey]);

  const refresh = useCallback(() => setPollKey((k) => k + 1), []);
  return { view, error, refresh, replace: setView };
}
