import type { Verification } from "@/lib/types";
import styles from "./workspace.module.css";

export function RunInfo({ run }: { run: Verification }) {
  return (
    <details className={styles.verified}>
      <summary>
        <span className={`${styles.badge} ${run.verified ? styles.badgeOk : styles.badgeWarn}`}>
          {run.verified ? "Checked against your policy" : "Could not fully verify"}
        </span>
      </summary>
      {!run.verified && run.issues.length > 0 && (
        <ul className={styles.steps}>
          {run.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
      {run.steps.length > 0 && (
        <>
          <p className={styles.caption}>How this was worked out</p>
          <ol className={styles.steps}>
            {run.steps.map((step, i) => (
              <li key={`${i}-${step}`}>{step}</li>
            ))}
          </ol>
        </>
      )}
    </details>
  );
}

export function Working({ children }: { children: React.ReactNode }) {
  return (
    <p className={styles.working} role="status">
      {children}
    </p>
  );
}
