import type {
  AuditView,
  CaseView,
  ChatReply,
  Checklist,
  ClauseView,
  CreatedCase,
  DocumentKind,
  DocumentView,
  FactsInput,
  LetterKind,
  LetterView,
  LineCategory,
  ReviewView,
} from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;
const TOKEN_PREFIX = "claimsaathi.case.";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function baseUrl(): string {
  if (!BASE_URL) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL is not set");
  }
  return BASE_URL.replace(/\/$/, "");
}

// The case token is the only credential; it stays in this browser.
export const caseTokens = {
  save(caseId: string, token: string) {
    window.localStorage.setItem(TOKEN_PREFIX + caseId, token);
  },
  get(caseId: string): string | null {
    return window.localStorage.getItem(TOKEN_PREFIX + caseId);
  },
  forget(caseId: string) {
    window.localStorage.removeItem(TOKEN_PREFIX + caseId);
  },
};

async function request<T>(path: string, init: RequestInit & { caseId?: string } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.caseId) {
    const token = caseTokens.get(init.caseId);
    if (!token) {
      throw new ApiError("This case was opened in another browser.", 401);
    }
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${baseUrl()}${path}`, { ...init, headers });
  if (response.status === 204) {
    return undefined as T;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return payload as T;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  createCase: () => request<CreatedCase>("/api/cases", { method: "POST" }),

  getCase: (caseId: string) => request<CaseView>(`/api/cases/${caseId}`, { caseId }),

  updateFacts: (caseId: string, facts: FactsInput) =>
    request<CaseView>(`/api/cases/${caseId}/facts`, {
      caseId,
      method: "PATCH",
      body: json(facts),
    }),

  deleteCase: (caseId: string) =>
    request<void>(`/api/cases/${caseId}`, { caseId, method: "DELETE" }),

  upload: (caseId: string, file: File, kind: DocumentKind | null) => {
    const form = new FormData();
    form.append("file", file);
    if (kind) form.append("kind", kind);
    return request<DocumentView>(`/api/cases/${caseId}/documents`, {
      caseId,
      method: "POST",
      body: form,
    });
  },

  removeDocument: (caseId: string, documentId: string) =>
    request<void>(`/api/cases/${caseId}/documents/${documentId}`, { caseId, method: "DELETE" }),

  audit: (caseId: string) =>
    request<AuditView>(`/api/cases/${caseId}/audit`, { caseId, method: "POST" }),

  estimate: (
    caseId: string,
    lines: { description: string; amount: string; category: LineCategory; quantity?: string }[],
  ) =>
    request<AuditView>(`/api/cases/${caseId}/estimate`, {
      caseId,
      method: "POST",
      body: json({ lines }),
    }),

  review: (caseId: string) =>
    request<ReviewView>(`/api/cases/${caseId}/review`, { caseId, method: "POST" }),

  analysis: (caseId: string, analysisId: string) =>
    request<AuditView | ReviewView>(`/api/cases/${caseId}/analyses/${analysisId}`, { caseId }),

  draftLetter: (caseId: string, kind: LetterKind) =>
    request<LetterView>(`/api/cases/${caseId}/letters`, {
      caseId,
      method: "POST",
      body: json({ kind }),
    }),

  editLetter: (caseId: string, letterId: string, subject: string, body: string) =>
    request<LetterView>(`/api/cases/${caseId}/letters/${letterId}`, {
      caseId,
      method: "PUT",
      body: json({ subject, body }),
    }),

  approveLetter: (caseId: string, letterId: string) =>
    request<LetterView>(`/api/cases/${caseId}/letters/${letterId}/approve`, {
      caseId,
      method: "POST",
    }),

  chat: (caseId: string, message: string) =>
    request<ChatReply>(`/api/cases/${caseId}/chat`, {
      caseId,
      method: "POST",
      body: json({ message }),
    }),

  checklist: (caseId: string) => request<Checklist>(`/api/cases/${caseId}/checklist`, { caseId }),

  clause: (caseId: string, clauseId: string) =>
    request<ClauseView>(`/api/cases/${caseId}/clauses/${encodeURIComponent(clauseId)}`, {
      caseId,
    }),

  feedback: (
    caseId: string,
    targetType: "analysis" | "letter" | "message",
    targetId: string,
    helpful: boolean,
  ) =>
    request<void>(`/api/cases/${caseId}/feedback`, {
      caseId,
      method: "POST",
      body: json({ target_type: targetType, target_id: targetId, helpful }),
    }),
};
