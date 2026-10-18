import type { DocumentKind, LineCategory, Money } from "./types";

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMoney(value: Money | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = typeof value === "number" ? value : Number(value);
  return Number.isFinite(amount) ? rupees.format(amount) : "—";
}

const dates = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric" });

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : dates.format(parsed);
}

export const DOCUMENT_LABELS: Record<DocumentKind, string> = {
  policy: "Policy wording",
  bill: "Hospital bill",
  estimate: "Cost estimate",
  discharge_summary: "Discharge summary",
  rejection_letter: "Insurer letter",
  other: "Other",
};

export const CATEGORY_LABELS: Record<LineCategory, string> = {
  room: "Room",
  icu: "ICU",
  nursing: "Nursing",
  doctor_fees: "Doctor fees",
  surgery: "Surgery / OT",
  investigations: "Tests",
  pharmacy: "Medicines",
  consumables: "Consumables",
  implants: "Implants",
  non_payable: "Non-medical",
  other_medical: "Other medical",
};

export const REASON_LABELS: Record<string, string> = {
  non_payable_item: "Non-medical items",
  room_rent_excess: "Room rent above limit",
  icu_rent_excess: "ICU rent above limit",
  proportionate_deduction: "Proportionate deduction",
  sub_limit: "Sub-limit",
  deductible: "Deductible",
  co_payment: "Co-payment",
  sum_insured_exceeded: "Cover exhausted",
  initial_waiting_period: "Initial waiting period",
  pre_existing_waiting_period: "Pre-existing disease wait",
  specific_disease_waiting_period: "Specific disease wait",
};
