"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { api, caseTokens } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { CaseView, DocumentKind } from "@/lib/types";
import { AskPanel } from "./AskPanel";
import { AuditPanel } from "./AuditPanel";
import { CaseDetails } from "./CaseDetails";
import { ChecklistPanel } from "./ChecklistPanel";
import { CitationProvider } from "./Citations";
import { Documents } from "./Documents";
import { LettersPanel } from "./LettersPanel";
import { ReviewPanel } from "./ReviewPanel";
import { useCase } from "./useCase";
import styles from "./workspace.module.css";

const TABS = [
  { id: "audit", label: "Bill check" },
  { id: "review", label: "Insurer decision" },
  { id: "ask", label: "Ask your policy" },
  { id: "letters", label: "Letters" },
  { id: "checklist", label: "Documents needed" },
] as const;

type Tab = (typeof TABS)[number]["id"];

function has(view: CaseView, ...kinds: DocumentKind[]) {
  return view.documents.some((d) => d.status === "ready" && d.kind && kinds.includes(d.kind));
}

export function Workspace() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { view, error, refresh, replace } = useCase(id);
  const [tab, setTab] = useState<Tab>("audit");

  if (error && !view) {
    return (
      <div className={styles.empty}>
        <h2>This case cannot be opened</h2>
        <p className="muted">{error}</p>
        <p className="muted">
          Cases open only in the browser that started them and are deleted after 24 hours.
        </p>
        <Link className="button" href="/">
          Start a new case
        </Link>
      </div>
    );
  }
  if (!view) return <WorkspaceSkeleton />;

  const hasPolicy = has(view, "policy");
  const hasBill = has(view, "bill", "estimate");
  const hasLetter = has(view, "rejection_letter");

  async function remove() {
    if (!window.confirm("Delete this case and everything in it now?")) return;
    await api.deleteCase(id);
    caseTokens.forget(id);
    router.push("/");
  }

  return (
    <CitationProvider caseId={id}>
      <div className={styles.layout}>
        <aside className={styles.rail}>
          <Documents caseId={id} documents={view.documents} onChange={refresh} />
          <CaseDetails key={view.id} view={view} onSaved={replace} />
          <p className={styles.caption}>
            Deleted automatically on {formatDate(view.expires_at)}.{" "}
            <button type="button" className={styles.linkButton} onClick={remove}>
              Delete now
            </button>
          </p>
        </aside>

        <section>
          <div className={styles.tabs} role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                className={styles.tab}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div role="tabpanel">
            {tab === "audit" && (
              <AuditPanel
                caseId={id}
                latestId={view.latest_audit_id}
                hasPolicy={hasPolicy}
                hasBill={hasBill}
              />
            )}
            {tab === "review" && (
              <ReviewPanel
                caseId={id}
                latestId={view.latest_review_id}
                ready={hasPolicy && hasLetter}
              />
            )}
            {tab === "ask" && <AskPanel caseId={id} history={view.messages} ready={hasPolicy} />}
            {tab === "letters" && (
              <LettersPanel
                caseId={id}
                initial={view.letters}
                ready={hasPolicy && (hasBill || hasLetter)}
              />
            )}
            {tab === "checklist" && (
              <ChecklistPanel caseId={id} refreshKey={JSON.stringify(view.facts)} />
            )}
          </div>
        </section>
      </div>
    </CitationProvider>
  );
}

export function WorkspaceSkeleton() {
  return (
    <p className={styles.working} role="status">
      Opening your case.
    </p>
  );
}
