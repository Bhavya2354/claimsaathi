"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import type { CaseView, Facts, FactsInput } from "@/lib/types";
import styles from "./workspace.module.css";

type Draft = Record<keyof FactsInput, string>;

const TEXT_FIELDS: { key: keyof FactsInput; label: string; type: string; full?: boolean }[] = [
  { key: "sum_insured", label: "Sum insured (₹)", type: "number" },
  { key: "balance_sum_insured", label: "Cover left this year (₹)", type: "number" },
  { key: "policy_start_date", label: "Policy first started", type: "date" },
  { key: "patient_age", label: "Patient age", type: "number" },
  { key: "admission_date", label: "Admitted on", type: "date" },
  { key: "discharge_date", label: "Discharged on", type: "date" },
  { key: "room_rent_per_day", label: "Room rent charged per day (₹)", type: "number", full: true },
  { key: "diagnosis", label: "Illness or treatment", type: "text", full: true },
];

function initialDraft(facts: FactsInput): Draft {
  const draft = {} as Draft;
  for (const { key } of TEXT_FIELDS) {
    const value = facts[key];
    draft[key] = value === undefined || value === null ? "" : String(value);
  }
  draft.claim_type = facts.claim_type ?? "";
  draft.network_hospital =
    facts.network_hospital === undefined ? "" : facts.network_hospital ? "yes" : "no";
  draft.is_accident = facts.is_accident ? "yes" : "";
  return draft;
}

function placeholder(effective: Facts, key: keyof FactsInput): string | undefined {
  const value = effective[key as keyof Facts];
  if (value === null || value === undefined || value === "" || Array.isArray(value)) return;
  return `${value} (from documents)`;
}

function toInput(draft: Draft): FactsInput {
  const out: Record<string, unknown> = {};
  for (const { key } of TEXT_FIELDS) {
    const value = draft[key].trim();
    if (!value) continue;
    out[key] = key === "patient_age" ? Number(value) : value;
  }
  if (draft.claim_type) out.claim_type = draft.claim_type;
  if (draft.network_hospital) out.network_hospital = draft.network_hospital === "yes";
  out.is_accident = draft.is_accident === "yes";
  return out as FactsInput;
}

export function CaseDetails({
  view,
  onSaved,
}: {
  view: CaseView;
  onSaved: (next: CaseView) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(view.facts));
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  function set(key: keyof FactsInput, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    setState("idle");
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setState("saving");
    setError(null);
    try {
      onSaved(await api.updateFacts(view.id, toInput(draft)));
      setState("saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
      setState("idle");
    }
  }

  return (
    <section className={styles.section} aria-labelledby="details-heading">
      <div className={styles.sectionHead}>
        <h2 id="details-heading">Case details</h2>
        <span className={styles.caption}>Optional, improves accuracy</span>
      </div>
      <form className={styles.form} onSubmit={save}>
        <div className={`field ${styles.full}`}>
          <label htmlFor="claim_type">Claim type</label>
          <select
            id="claim_type"
            className="input"
            value={draft.claim_type}
            onChange={(e) => set("claim_type", e.target.value)}
          >
            <option value="">Not sure</option>
            <option value="cashless">Cashless</option>
            <option value="reimbursement">Reimbursement</option>
          </select>
        </div>
        {TEXT_FIELDS.map(({ key, label, type, full }) => (
          <div key={key} className={`field ${full ? styles.full : ""}`}>
            <label htmlFor={key}>{label}</label>
            <input
              id={key}
              className="input"
              type={type}
              min={type === "number" ? 0 : undefined}
              value={draft[key]}
              placeholder={placeholder(view.effective_facts, key)}
              onChange={(e) => set(key, e.target.value)}
            />
          </div>
        ))}
        <div className={`field ${styles.full}`}>
          <label htmlFor="network_hospital">Network hospital of the insurer</label>
          <select
            id="network_hospital"
            className="input"
            value={draft.network_hospital}
            onChange={(e) => set("network_hospital", e.target.value)}
          >
            <option value="">Not sure</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </div>
        <label className={`${styles.check} ${styles.full}`}>
          <input
            type="checkbox"
            checked={draft.is_accident === "yes"}
            onChange={(e) => set("is_accident", e.target.checked ? "yes" : "")}
          />
          Admission followed an accident
        </label>
        <div className={`${styles.actions} ${styles.full}`}>
          <button type="submit" className="button button-quiet" disabled={state === "saving"}>
            {state === "saving" ? "Saving…" : state === "saved" ? "Saved" : "Save details"}
          </button>
        </div>
        {error && <p className={`error-text ${styles.full}`}>{error}</p>}
      </form>
    </section>
  );
}
