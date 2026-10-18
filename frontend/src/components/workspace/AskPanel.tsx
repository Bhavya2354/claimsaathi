"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { ChatReply, ClauseView, MessageView } from "@/lib/types";
import { AuditResult } from "./AuditPanel";
import { ChecklistView } from "./ChecklistPanel";
import { Citations, useRememberClauses } from "./Citations";
import { ReviewResult } from "./ReviewPanel";
import { RunInfo, Working } from "./RunInfo";
import styles from "./workspace.module.css";

const SUGGESTIONS = [
  "What is my room rent limit?",
  "Is there a co-payment on my policy?",
  "How long is the waiting period for pre-existing diseases?",
  "Which documents do I need for a reimbursement claim?",
];

interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
  reply?: ChatReply;
}

function fromHistory(messages: MessageView[]): Turn[] {
  return messages.map((m) => ({ id: m.id, role: m.role, text: m.content }));
}

export function AskPanel({
  caseId,
  history,
  ready,
}: {
  caseId: string;
  history: MessageView[];
  ready: boolean;
}) {
  const remember = useRememberClauses();
  const [turns, setTurns] = useState<Turn[]>(() => fromHistory(history));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [turns]);

  async function ask(message: string) {
    const question = message.trim();
    if (!question || busy) return;
    setText("");
    setBusy(true);
    setError(null);
    setTurns((all) => [...all, { id: crypto.randomUUID(), role: "user", text: question }]);
    try {
      const reply = await api.chat(caseId, question);
      const cited: ClauseView[] = [
        ...reply.clauses,
        ...(reply.audit?.clauses ?? []),
        ...(reply.review?.clauses ?? []),
      ];
      remember(cited);
      setTurns((all) => [
        ...all,
        { id: crypto.randomUUID(), role: "assistant", text: reply.text, reply },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No answer this time. Please retry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.panelBody}>
      {!ready && <p className={styles.caption}>Upload your policy wording to ask about it.</p>}
      <div className={styles.thread}>
        {turns.map((turn) => (
          <div
            key={turn.id}
            className={`${styles.bubble} ${turn.role === "user" ? styles.bubbleUser : ""}`}
          >
            <span>{turn.text}</span>
            {turn.reply && <ReplyDetails reply={turn.reply} />}
          </div>
        ))}
        {busy && <Working>Looking through your policy.</Working>}
        <div ref={end} />
      </div>

      {turns.length === 0 && (
        <div className={styles.suggestions}>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              className="button button-quiet"
              disabled={!ready || busy}
              onClick={() => ask(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <form
        className={styles.composer}
        onSubmit={(e) => {
          e.preventDefault();
          void ask(text);
        }}
      >
        <input
          className="input"
          aria-label="Your question"
          placeholder="Ask about your cover, a deduction, or what to do next"
          value={text}
          maxLength={2000}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="submit" className="button" disabled={!ready || busy || !text.trim()}>
          Ask
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

function ReplyDetails({ reply }: { reply: ChatReply }) {
  return (
    <>
      <Citations ids={reply.clauses.map((c) => c.clause_id)} />
      {reply.audit && <AuditResult audit={reply.audit} />}
      {reply.review && <ReviewResult review={reply.review} />}
      {reply.checklist && <ChecklistView checklist={reply.checklist} />}
      {reply.letter && (
        <p className={styles.caption}>Open the Letters tab to edit and approve it.</p>
      )}
      {reply.intent !== "out_of_scope" && reply.steps.length > 0 && <RunInfo run={reply} />}
    </>
  );
}
