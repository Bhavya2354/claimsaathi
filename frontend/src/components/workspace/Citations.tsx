"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { ClauseView } from "@/lib/types";
import styles from "./workspace.module.css";

interface CitationApi {
  remember: (clauses: ClauseView[]) => void;
  open: (clauseId: string) => void;
  label: (clauseId: string) => string;
}

const CitationContext = createContext<CitationApi | null>(null);

export function CitationProvider({
  caseId,
  children,
}: {
  caseId: string;
  children: React.ReactNode;
}) {
  const known = useRef(new Map<string, ClauseView>());
  const [, setVersion] = useState(0);
  const [shown, setShown] = useState<ClauseView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remember = useCallback((clauses: ClauseView[]) => {
    let added = false;
    for (const clause of clauses) {
      if (!known.current.has(clause.clause_id)) {
        known.current.set(clause.clause_id, clause);
        added = true;
      }
    }
    if (added) setVersion((v) => v + 1);
  }, []);

  const open = useCallback(
    async (clauseId: string) => {
      setError(null);
      const cached = known.current.get(clauseId);
      if (cached) {
        setShown(cached);
        return;
      }
      try {
        const clause = await api.clause(caseId, clauseId);
        known.current.set(clauseId, clause);
        setShown(clause);
      } catch {
        setError(`Clause ${clauseId} could not be loaded.`);
      }
    },
    [caseId],
  );

  const label = useCallback(
    (clauseId: string) => known.current.get(clauseId)?.citation ?? clauseId,
    [],
  );

  const value = useMemo(() => ({ remember, open, label }), [remember, open, label]);

  return (
    <CitationContext.Provider value={value}>
      {children}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {shown && (
        <>
          <div className={styles.drawerBackdrop} onClick={() => setShown(null)} />
          <aside className={styles.drawer} role="dialog" aria-label="Policy clause">
            <div className={styles.drawerHead}>
              <div>
                <p className={styles.caption}>From your policy</p>
                <h3>{shown.citation}</h3>
              </div>
              <button type="button" className="button button-quiet" onClick={() => setShown(null)}>
                Close
              </button>
            </div>
            <div className={styles.drawerText}>{shown.text}</div>
          </aside>
        </>
      )}
    </CitationContext.Provider>
  );
}

function useCitations(): CitationApi {
  const context = useContext(CitationContext);
  if (!context) throw new Error("CitationProvider is missing");
  return context;
}

export function useRememberClauses() {
  return useCitations().remember;
}

export function Citations({ ids }: { ids: string[] }) {
  const { open, label } = useCitations();
  const unique = [...new Set(ids)];
  if (unique.length === 0) return null;
  return (
    <span className={styles.chips}>
      {unique.map((id) => (
        <button key={id} type="button" className={styles.chip} onClick={() => open(id)}>
          {label(id)}
        </button>
      ))}
    </span>
  );
}
