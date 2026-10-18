"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { CATEGORY_LABELS, formatMoney, REASON_LABELS } from "@/lib/format";
import type { AuditView, Coverage, Deduction } from "@/lib/types";
import { Citations, useRememberClauses } from "./Citations";
import { EstimateForm } from "./EstimateForm";
import { RunInfo, Working } from "./RunInfo";
import styles from "./workspace.module.css";

function groupDeductions(
  coverage: Coverage,
): { reason: string; amount: number; sample: Deduction }[] {
  const groups = new Map<string, { amount: number; sample: Deduction }>();
  const all = [...coverage.lines.flatMap((line) => line.deductions), ...coverage.claim_deductions];
  for (const deduction of all) {
    const current = groups.get(deduction.reason);
    groups.set(deduction.reason, {
      amount: (current?.amount ?? 0) + Number(deduction.amount),
      sample: current?.sample ?? deduction,
    });
  }
  return [...groups.entries()]
    .map(([reason, value]) => ({ reason, ...value }))
    .sort((a, b) => b.amount - a.amount);
}

export function AuditResult({ audit }: { audit: AuditView }) {
  const remember = useRememberClauses();
  useEffect(() => remember(audit.clauses), [audit.clauses, remember]);
  const { coverage } = audit;
  const grouped = groupDeductions(coverage);

  return (
    <div className={styles.panelBody}>
      {audit.source !== "bill" && (
        <p className={styles.caption}>
          This is an estimate from{" "}
          {audit.source === "form" ? "the charges you entered" : "the hospital's cost estimate"}.
        </p>
      )}
      <div className={styles.totals}>
        <div className={styles.total}>
          <span className={styles.totalLabel}>Billed</span>
          <span className={`num ${styles.totalValue}`}>{formatMoney(coverage.billed_total)}</span>
        </div>
        <div className={styles.total}>
          <span className={styles.totalLabel}>Insurer should pay</span>
          <span className={`num ${styles.totalValue} ${styles.totalCovered}`}>
            {formatMoney(coverage.payable_total)}
          </span>
        </div>
        <div className={styles.total}>
          <span className={styles.totalLabel}>You pay</span>
          <span className={`num ${styles.totalValue} ${styles.totalPay}`}>
            {formatMoney(coverage.patient_pays)}
          </span>
        </div>
      </div>

      {audit.warnings.map((warning) => (
        <p key={warning} className={styles.notice}>
          {warning}
        </p>
      ))}

      {grouped.length > 0 && (
        <section className={styles.section}>
          <h3>Where the difference comes from</h3>
          <ul className={styles.reasonList}>
            {grouped.map(({ reason, amount, sample }) => (
              <li key={reason} className={styles.reasonRow}>
                <strong>{REASON_LABELS[reason] ?? reason}</strong>
                <span className="num">{formatMoney(amount)}</span>
                <p>{sample.explanation}</p>
                <Citations ids={sample.clause_ids} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className={styles.section}>
        <h3>Every line on the bill</h3>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Item</th>
                <th className={styles.amount}>Billed</th>
                <th className={styles.amount}>Payable</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {coverage.lines.map((line) => (
                <tr key={line.index} className={`${styles.line} ${styles[`line_${line.status}`]}`}>
                  <td>
                    {line.description}
                    <div className={styles.category}>
                      {CATEGORY_LABELS[line.category]}
                      {line.page ? ` · page ${line.page}` : ""}
                    </div>
                  </td>
                  <td className={`num ${styles.amount}`}>{formatMoney(line.billed)}</td>
                  <td className={`num ${styles.amount}`}>{formatMoney(line.payable)}</td>
                  <td>
                    <div className={styles.why}>
                      {line.deductions.length === 0 ? (
                        <span>Covered</span>
                      ) : (
                        line.deductions.map((d) => (
                          <span key={d.reason}>
                            {d.explanation} <Citations ids={d.clause_ids} />
                          </span>
                        ))
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {audit.reasoning && <p className={styles.caption}>{audit.reasoning}</p>}
      <RunInfo run={audit} />
    </div>
  );
}

export function AuditPanel({
  caseId,
  latestId,
  hasPolicy,
  hasBill,
}: {
  caseId: string;
  latestId: string | null;
  hasPolicy: boolean;
  hasBill: boolean;
}) {
  const [audit, setAudit] = useState<AuditView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);

  useEffect(() => {
    if (!latestId) return;
    let cancelled = false;
    api
      .analysis(caseId, latestId)
      .then((view) => {
        if (!cancelled && "coverage" in view && "terms" in view) setAudit(view as AuditView);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [caseId, latestId]);

  async function run(task: () => Promise<AuditView>) {
    setBusy(true);
    setError(null);
    try {
      setAudit(await task());
      setPlanning(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The check failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.panelBody}>
      <div className={styles.actions}>
        <button
          type="button"
          className="button"
          disabled={busy || !hasPolicy || !hasBill}
          onClick={() => run(() => api.audit(caseId))}
        >
          {audit ? "Check again" : "Check the bill"}
        </button>
        <button
          type="button"
          className="button button-quiet"
          disabled={busy || !hasPolicy}
          onClick={() => setPlanning((p) => !p)}
        >
          {planning ? "Close estimate" : "Estimate a planned admission"}
        </button>
      </div>
      {!hasPolicy && <p className={styles.caption}>Upload your policy wording to begin.</p>}
      {hasPolicy && !hasBill && !planning && (
        <p className={styles.caption}>
          Upload the hospital bill or cost estimate, or enter expected charges.
        </p>
      )}
      {planning && (
        <EstimateForm
          disabled={busy}
          onSubmit={(lines) => run(() => api.estimate(caseId, lines))}
        />
      )}
      {busy && (
        <Working>
          Reading your policy and checking each charge. Free AI models can take up to a minute.
        </Working>
      )}
      {error && <p className="error-text">{error}</p>}
      {audit && !busy && <AuditResult audit={audit} />}
    </div>
  );
}
