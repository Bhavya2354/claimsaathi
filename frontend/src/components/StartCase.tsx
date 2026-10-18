"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, caseTokens } from "@/lib/api";

export function StartCase() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createCase();
      caseTokens.save(created.id, created.token);
      router.push(`/cases/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start a case.");
      setBusy(false);
    }
  }

  return (
    <div>
      <button type="button" className="button" onClick={start} disabled={busy}>
        {busy ? "Starting…" : "Start a claim check"}
      </button>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
