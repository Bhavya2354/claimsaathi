"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { LetterKind, LetterView } from "@/lib/types";
import { Citations, useRememberClauses } from "./Citations";
import { RunInfo, Working } from "./RunInfo";
import styles from "./workspace.module.css";

const KIND_TEXT: Record<LetterKind, string> = {
  grievance: "Grievance to insurer",
  ombudsman: "Ombudsman complaint",
};

export function LettersPanel({
  caseId,
  initial,
  ready,
}: {
  caseId: string;
  initial: LetterView[];
  ready: boolean;
}) {
  const remember = useRememberClauses();
  const [letters, setLetters] = useState<LetterView[]>(initial);
  const [selectedId, setSelectedId] = useState<string | null>(initial.at(-1)?.id ?? null);
  const [subject, setSubject] = useState(initial.at(-1)?.subject ?? "");
  const [body, setBody] = useState(initial.at(-1)?.body ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = letters.find((l) => l.id === selectedId) ?? null;

  function select(letter: LetterView) {
    setSelectedId(letter.id);
    setSubject(letter.subject);
    setBody(letter.body);
    remember(letter.clauses);
  }

  function upsert(letter: LetterView) {
    const previous = letters.find((l) => l.id === letter.id);
    const merged =
      previous && !letter.clauses.length ? { ...letter, clauses: previous.clauses } : letter;
    setLetters((all) =>
      previous ? all.map((l) => (l.id === letter.id ? merged : l)) : [...all, merged],
    );
    select(merged);
  }

  async function act(label: string, task: () => Promise<LetterView>) {
    setBusy(label);
    setError(null);
    try {
      upsert(await task());
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work. Please retry.");
    } finally {
      setBusy(null);
    }
  }

  const dirty = selected && (subject !== selected.subject || body !== selected.body);
  const approved = selected?.status === "approved";

  return (
    <div className={styles.panelBody}>
      <div className={styles.actions}>
        {(Object.keys(KIND_TEXT) as LetterKind[]).map((kind) => (
          <button
            key={kind}
            type="button"
            className={kind === "grievance" ? "button" : "button button-quiet"}
            disabled={!ready || busy !== null}
            onClick={() => act("draft", () => api.draftLetter(caseId, kind))}
          >
            Draft {KIND_TEXT[kind].toLowerCase()}
          </button>
        ))}
      </div>
      {!ready && (
        <p className={styles.caption}>
          Upload your policy and the bill or the insurer&apos;s letter to draft an appeal.
        </p>
      )}
      <p className={styles.caption}>
        Write to the insurer first. If they reject your grievance or do not reply within 30 days,
        you can complain to the Insurance Ombudsman for free.
      </p>
      {busy === "draft" && <Working>Writing the letter from your policy and the audit.</Working>}
      {error && <p className="error-text">{error}</p>}

      {letters.length > 1 && (
        <div className={styles.letterList}>
          {letters.map((letter) => (
            <button
              key={letter.id}
              type="button"
              className={letter.id === selectedId ? "button" : "button button-quiet"}
              onClick={() => select(letter)}
            >
              {KIND_TEXT[letter.kind]} · {formatDate(letter.created_at)}
            </button>
          ))}
        </div>
      )}

      {selected && (
        <section className={styles.section}>
          {approved ? (
            <article className={`panel ${styles.card} ${styles.printable}`}>
              <h3>{selected.subject}</h3>
              {"\n"}
              {selected.body}
            </article>
          ) : (
            <>
              <div className="field">
                <label htmlFor="letter-subject">Subject</label>
                <input
                  id="letter-subject"
                  className="input"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="letter-body">Letter</label>
                <textarea
                  id="letter-body"
                  className={`input ${styles.editor}`}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
              </div>
              <p className={styles.caption}>
                Fill in anything in [square brackets], then approve. Approved letters are final.
              </p>
            </>
          )}
          <Citations ids={selected.clauses.map((c) => c.clause_id)} />
          <div className={`${styles.actions} no-print`}>
            {!approved && (
              <>
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={!dirty || busy !== null}
                  onClick={() =>
                    act("save", () => api.editLetter(caseId, selected.id, subject, body))
                  }
                >
                  {busy === "save" ? "Saving…" : "Save changes"}
                </button>
                <button
                  type="button"
                  className="button"
                  disabled={Boolean(dirty) || busy !== null}
                  onClick={() => act("approve", () => api.approveLetter(caseId, selected.id))}
                >
                  Approve letter
                </button>
              </>
            )}
            <button
              type="button"
              className="button button-quiet"
              onClick={() => navigator.clipboard.writeText(`${subject}\n\n${body}`)}
            >
              Copy
            </button>
            {approved && (
              <button type="button" className="button button-quiet" onClick={() => window.print()}>
                Print or save as PDF
              </button>
            )}
          </div>
          <RunInfo run={selected} />
        </section>
      )}
    </div>
  );
}
