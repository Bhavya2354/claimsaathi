"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { Checklist } from "@/lib/types";
import styles from "./workspace.module.css";

const GROUP_TEXT: Record<string, string> = {
  common: "Every claim",
  reimbursement: "Reimbursement claims",
  cashless: "Cashless claims",
  accident: "Because of the accident",
  surgery: "Because of the surgery",
};

export function ChecklistView({ checklist }: { checklist: Checklist }) {
  const groups = [...new Set(checklist.items.map((item) => item.group))];
  return (
    <div className={styles.panelBody}>
      {groups.map((group) => (
        <section key={group} className={styles.section}>
          <h3>{GROUP_TEXT[group] ?? group}</h3>
          <ul className={styles.reasonList}>
            {checklist.items
              .filter((item) => item.group === group)
              .map((item) => (
                <li key={item.document} className={styles.reasonRow}>
                  <strong>{item.document}</strong>
                  <span />
                  <p>{item.why}</p>
                </li>
              ))}
          </ul>
        </section>
      ))}
      <p className={styles.caption}>{checklist.source}</p>
    </div>
  );
}

export function ChecklistPanel({ caseId, refreshKey }: { caseId: string; refreshKey: string }) {
  const [checklist, setChecklist] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .checklist(caseId)
      .then((value) => !cancelled && setChecklist(value))
      .catch(
        (err: unknown) =>
          !cancelled && setError(err instanceof Error ? err.message : "Unavailable."),
      );
    return () => {
      cancelled = true;
    };
  }, [caseId, refreshKey]);

  if (error) return <p className="error-text">{error}</p>;
  if (!checklist) return null;
  return <ChecklistView checklist={checklist} />;
}
