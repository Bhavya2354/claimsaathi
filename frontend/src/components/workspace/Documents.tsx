"use client";

import { useRef, useState } from "react";

import { api } from "@/lib/api";
import { DOCUMENT_LABELS } from "@/lib/format";
import type { DocumentKind, DocumentView } from "@/lib/types";
import styles from "./workspace.module.css";

const KINDS: (DocumentKind | "auto")[] = [
  "auto",
  "policy",
  "bill",
  "estimate",
  "discharge_summary",
  "rejection_letter",
];

const STATUS_CLASS = {
  processing: styles.statusProcessing,
  ready: styles.statusReady,
  failed: styles.statusFailed,
};

const STATUS_TEXT = { processing: "Reading", ready: "Ready", failed: "Failed" };

function describe(doc: DocumentView): string | null {
  const s = doc.summary ?? {};
  if (doc.kind === "policy" && doc.clause_count) return `${doc.clause_count} clauses indexed`;
  if ((doc.kind === "bill" || doc.kind === "estimate") && typeof s.lines === "number")
    return `${s.lines} charge lines`;
  if (doc.kind === "discharge_summary" && typeof s.diagnosis === "string") return s.diagnosis;
  if (doc.kind === "rejection_letter" && typeof s.decision === "string")
    return `Decision: ${s.decision.replace("_", " ")}`;
  return null;
}

export function Documents({
  caseId,
  documents,
  onChange,
}: {
  caseId: string;
  documents: DocumentView[];
  onChange: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<DocumentKind | "auto">("auto");
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0);

  async function send(files: FileList | null) {
    if (!files?.length) return;
    setError(null);
    setUploading((n) => n + files.length);
    for (const file of Array.from(files)) {
      try {
        await api.upload(caseId, file, kind === "auto" ? null : kind);
      } catch (err) {
        setError(err instanceof Error ? `${file.name}: ${err.message}` : "Upload failed.");
      } finally {
        setUploading((n) => n - 1);
      }
    }
    onChange();
  }

  async function remove(documentId: string) {
    try {
      await api.removeDocument(caseId, documentId);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the document.");
    }
  }

  return (
    <section className={styles.section} aria-labelledby="documents-heading">
      <div className={styles.sectionHead}>
        <h2 id="documents-heading">Documents</h2>
        <span className={styles.caption}>PDF or photo, up to 15 MB</span>
      </div>

      <div className="field">
        <label htmlFor="document-kind">This upload is</label>
        <select
          id="document-kind"
          className="input"
          value={kind}
          onChange={(e) => setKind(e.target.value as DocumentKind | "auto")}
        >
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k === "auto" ? "Detect automatically" : DOCUMENT_LABELS[k]}
            </option>
          ))}
        </select>
      </div>

      <div
        role="button"
        tabIndex={0}
        className={`${styles.drop} ${dragging ? styles.dropActive : ""}`}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void send(e.dataTransfer.files);
        }}
      >
        <strong>{uploading > 0 ? "Uploading…" : "Drop files or choose"}</strong>
        <span className={styles.caption}>
          Policy wording, bill, estimate, discharge summary, insurer letter
        </span>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          accept="application/pdf,image/png,image/jpeg,image/webp"
          onChange={(e) => {
            void send(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {error && <p className="error-text">{error}</p>}

      {documents.length > 0 && (
        <ul className={styles.docList}>
          {documents.map((doc) => (
            <li key={doc.id} className={styles.doc}>
              <span className={styles.docName} title={doc.filename}>
                {doc.kind ? DOCUMENT_LABELS[doc.kind] : doc.filename}
              </span>
              <span className={`${styles.status} ${STATUS_CLASS[doc.status]}`}>
                {STATUS_TEXT[doc.status]}
              </span>
              <span className={styles.docMeta}>
                {doc.status === "failed" ? doc.error : (describe(doc) ?? doc.filename)}
                {doc.text_sources?.includes("local_ocr") && " · read with offline OCR"}
              </span>
              <button type="button" className={styles.linkButton} onClick={() => remove(doc.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
