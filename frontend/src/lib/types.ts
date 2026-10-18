// Mirrors the API response models in backend/app/services/views.py.
// Money arrives as decimal strings ("15000.00") so no precision is lost in transit.

export type Money = string;

export type DocumentKind =
  "policy" | "bill" | "estimate" | "discharge_summary" | "rejection_letter" | "other";

export type LineCategory =
  | "room"
  | "icu"
  | "nursing"
  | "doctor_fees"
  | "surgery"
  | "investigations"
  | "pharmacy"
  | "consumables"
  | "implants"
  | "non_payable"
  | "other_medical";

export interface Facts {
  claim_type: "cashless" | "reimbursement" | null;
  patient_age: number | null;
  network_hospital: boolean | null;
  diagnosis: string | null;
  procedures: string[];
  policy_start_date: string | null;
  admission_date: string | null;
  discharge_date: string | null;
  sum_insured: Money | null;
  balance_sum_insured: Money | null;
  room_rent_per_day: Money | null;
  is_accident: boolean;
  pre_existing_conditions: string[];
}

export type FactsInput = Partial<{
  claim_type: "cashless" | "reimbursement";
  patient_age: number;
  network_hospital: boolean;
  diagnosis: string;
  policy_start_date: string;
  admission_date: string;
  discharge_date: string;
  sum_insured: string;
  balance_sum_insured: string;
  room_rent_per_day: string;
  is_accident: boolean;
}>;

export interface DocumentView {
  id: string;
  filename: string;
  kind: DocumentKind | null;
  status: "processing" | "ready" | "failed";
  error: string | null;
  page_count: number | null;
  text_sources: string[] | null;
  clause_count: number | null;
  summary: Record<string, unknown> | null;
  created_at: string;
}

export interface ClauseView {
  clause_id: string;
  section: string;
  citation: string;
  text: string;
}

export interface Verification {
  verified: boolean;
  issues: string[];
  steps: string[];
}

export interface Deduction {
  reason: string;
  amount: Money;
  explanation: string;
  clause_ids: string[];
}

export interface LineDecision {
  index: number;
  description: string;
  category: LineCategory;
  page: number | null;
  billed: Money;
  payable: Money;
  deductions: Deduction[];
  catalogue_item: string | null;
  note: string | null;
  status: "covered" | "partly_covered" | "not_covered";
}

export interface Coverage {
  decision: "payable" | "not_payable";
  lines: LineDecision[];
  claim_deductions: Deduction[];
  billed_total: Money;
  line_payable_total: Money;
  payable_total: Money;
  patient_pays: Money;
  eligible_room_rent: Money | null;
  charged_room_rent: Money | null;
  proportion_ratio: string;
  warnings: string[];
}

export interface AuditView extends Verification {
  analysis_id: string;
  source: "bill" | "estimate" | "form";
  coverage: Coverage;
  facts: Facts;
  clauses: ClauseView[];
  warnings: string[];
  reasoning: string | null;
}

export type Verdict = "supported" | "not_supported" | "partly_supported" | "unclear";

export interface ReviewView extends Verification {
  analysis_id: string;
  review: {
    reasons: {
      insurer_reason: string;
      verdict: Verdict;
      explanation: string;
      clause_ids: string[];
    }[];
    recommendation: "appeal" | "accept" | "send_documents";
    missing_documents: string[];
    summary: string;
  };
  rejection: {
    insurer_name: string | null;
    claim_number: string | null;
    decision: "rejected" | "partially_approved" | "query";
    amount_claimed: number | null;
    amount_approved: number | null;
  };
  coverage: Coverage | null;
  clauses: ClauseView[];
}

export type LetterKind = "grievance" | "ombudsman";

export interface LetterView extends Verification {
  id: string;
  kind: LetterKind;
  subject: string;
  body: string;
  status: "draft" | "approved";
  clauses: ClauseView[];
  created_at: string;
  approved_at: string | null;
}

export interface Checklist {
  items: { document: string; why: string; group: string }[];
  source: string;
}

export interface MessageView {
  id: string;
  role: "user" | "assistant";
  content: string;
  verified: boolean | null;
  created_at: string;
}

export interface ChatReply extends Verification {
  intent: "ask" | "audit" | "review" | "letter" | "checklist" | "out_of_scope";
  text: string;
  clauses: ClauseView[];
  audit: AuditView | null;
  review: ReviewView | null;
  letter: LetterView | null;
  checklist: Checklist | null;
}

export interface CaseView {
  id: string;
  expires_at: string;
  facts: FactsInput;
  effective_facts: Facts;
  documents: DocumentView[];
  letters: LetterView[];
  messages: MessageView[];
  latest_audit_id: string | null;
  latest_review_id: string | null;
}

export interface CreatedCase {
  id: string;
  token: string;
  expires_at: string;
}
