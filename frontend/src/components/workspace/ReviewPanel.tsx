"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { ReviewView, Verdict } from "@/lib/types";
import { Citations, useRememberClauses } from "./Citations";
import { RunInfo, Working } from "./RunInfo";
import styles from "./workspace.module.css";

const VERDICT_TEXT: Record<Verdict, string> = {
  supported: "Policy supports the insurer",
  not_supported: "Policy does not support this",
  partly_supported: "Partly supported",
  unclear: "Documents do not show enough",
};

const RECOMMENDATION_TEXT = {
  appeal: "You have grounds to appeal. Draft a grievance letter from the Letters tab.",
  accept: "The insurer's decision appears to follow your policy.",
  send_documents: "The insurer needs more documents. Send them before appealing.",
};

export function ReviewResult({ review }: { review: ReviewView }) {
  const remember = useRememberClauses();
  useEffect(() => remember(review.clauses), [review.clauses, remember]);
  const { rejection } = review;

  return (
    <div className={styles.panelBody}>
      <div className={styles.totals}>
        <div className={styles.total}>
          <span className={styles.totalLabel}>Claimed</span>
          <span className={`num ${styles.totalValue}`}>
            {formatMoney(rejection.amount_claimed)}
          </span>
        </div>
        <div className={styles.total}>
          <span className={styles.totalLabel}>Insurer approved</span>
          <span className={`num ${styles.totalValue}`}>
            {formatMoney(rejection.amount_approved)}
          </span>
        </div>
        <div className={styles.total}>
          <span className={styles.totalLabel}>Policy says payable</span>
          <span className={`num ${styles.totalValue} ${styles.totalCovered}`}>
            {review.coverage ? formatMoney(review.coverage.payable_total) : "Upload the bill"}
          </span>
        </div>
      </div>

      <p
        className={`${styles.notice} ${review.review.recommendation === "appeal" ? styles.noticeDanger : ""}`}
      >
        {RECOMMENDATION_TEXT[review.review.recommendation]}
      </p>
      <p>{review.review.summary}</p>

      <section className={styles.section}>
        <h3>The insurer&apos;s reasons, checked</h3>
        {review.review.reasons.map((reason) => (
          <article
            key={reason.insurer_reason}
            className={`${styles.verdictCard} ${styles[`verdict_${reason.verdict}`]}`}
          >
            <span className={styles.verdictLabel}>{VERDICT_TEXT[reason.verdict]}</span>
            <strong>{reason.insurer_reason}</strong>
            <p>{reason.explanation}</p>
            <Citations ids={reason.clause_ids} />
          </article>
        ))}
      </section>

      {review.review.missing_documents.length > 0 && (
        <section className={styles.section}>
          <h3>Documents to send</h3>
          <ul>
            {review.review.missing_documents.map((doc) => (
              <li key={doc}>{doc}</li>
            ))}
          </ul>
        </section>
      )}
      <RunInfo run={review} />
    </div>
  );
}

export function ReviewPanel({
  caseId,
  latestId,
  ready,
}: {
  caseId: string;
  latestId: string | null;
  ready: boolean;
}) {
  const [review, setReview] = useState<ReviewView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!latestId) return;
    let cancelled = false;
    api
      .analysis(caseId, latestId)
      .then((view) => {
        if (!cancelled && "review" in view) setReview(view);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [caseId, latestId]);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setReview(await api.review(caseId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The review failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.panelBody}>
      <div className={styles.actions}>
        <button type="button" className="button" disabled={busy || !ready} onClick={run}>
          {review ? "Review again" : "Check the insurer's decision"}
        </button>
      </div>
      {!ready && (
        <p className={styles.caption}>
          Upload your policy and the insurer&apos;s rejection or settlement letter.
        </p>
      )}
      {busy && <Working>Comparing each reason with your policy.</Working>}
      {error && <p className="error-text">{error}</p>}
      {review && !busy && <ReviewResult review={review} />}
    </div>
  );
}
