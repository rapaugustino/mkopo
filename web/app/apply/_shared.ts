/**
 * Shared types + constants for the multi-step /apply wizard.
 *
 * Lives next to the page (rather than under @/lib) because none of
 * these have callers outside ``web/app/apply/``. The wizard's step
 * components import from here; the page-level orchestrator imports
 * from here too.
 */

export type LoanType = "bridge" | "permanent" | "construction" | "refinance";
export type LoanClass = "business" | "personal";

export interface GuarantorEntry {
  name: string;
  email: string;
}

export interface FormState {
  loan_class: LoanClass;
  loan_type: LoanType;
  amount: string;
  purpose: string;
  borrower_name: string;
  borrower_email: string;
  // Password for new applicants — backend creates the borrower
  // account atomically with the loan. Empty string for already-
  // signed-in users (we pre-fill name+email and hide the password
  // field) and for "passwordless / magic-link only" intent.
  borrower_password: string;
  borrower_type: "entity" | "person";
  // Multiple guarantors supported. Each row is fully optional —
  // the user adds rows as needed; the validator requires name +
  // email when ANY row is non-empty (mixing empty + populated
  // rows would otherwise allow a partially-typed entry to slip
  // through with just a name and no contact).
  guarantors: GuarantorEntry[];
  property_address: string;
  property_type: string;
  // Personal-loan-only inputs. Optional even when class is personal
  // — the intake agent will email-chase any missing items.
  annual_income: string;
  employer: string;
  credit_score: string;
  monthly_debt_payments: string;
  years_employment: string;
}

export const EMPTY: FormState = {
  loan_class: "business",
  loan_type: "bridge",
  amount: "",
  purpose: "",
  borrower_name: "",
  borrower_email: "",
  borrower_password: "",
  borrower_type: "entity",
  guarantors: [],
  property_address: "",
  property_type: "",
  annual_income: "",
  employer: "",
  credit_score: "",
  monthly_debt_payments: "",
  years_employment: "",
};

export const PROPERTY_TYPES = [
  "Multifamily",
  "Office",
  "Retail",
  "Industrial",
  "Hotel",
  "Mixed-use",
  "Self-storage",
  "Other",
];

// Per-class loan-type pickers. The wire enum stays the same four
// values (avoids a schema migration), but the labels and hints
// reframe each option for the audience: a small-business owner
// reading "Bridge — interest-only, 12-36 months" does not need the
// same copy as a homeowner picking "Short-term" for a kitchen
// renovation. Personal loans don't have a construction equivalent so
// that option is omitted.
export const LOAN_TYPE_OPTIONS_BUSINESS: {
  value: LoanType;
  label: string;
  hint: string;
}[] = [
  { value: "bridge", label: "Bridge", hint: "Short-term, interest-only, 12-36 months." },
  { value: "permanent", label: "Permanent", hint: "Long-term financing for a stabilized asset." },
  { value: "construction", label: "Construction", hint: "Draw facility for ground-up build or major capex." },
  { value: "refinance", label: "Refinance", hint: "Replace existing debt." },
];

export const LOAN_TYPE_OPTIONS_PERSONAL: {
  value: LoanType;
  label: string;
  hint: string;
}[] = [
  { value: "bridge", label: "Short-term", hint: "Pay off within 12-24 months. Interest-only options available." },
  { value: "permanent", label: "Long-term", hint: "3-7 year term with fixed monthly payments." },
  { value: "refinance", label: "Refinance", hint: "Roll an existing personal loan into better terms." },
];

/**
 * Per-form updater closure. Mirrors what the old monolithic
 * ``page.tsx`` declared inline: ``setForm((f) => ({...f, [k]: v}))``.
 * Re-export as a type so step components can take it as a prop and
 * type-check field names against ``FormState``.
 */
export type FormUpdater = <K extends keyof FormState>(
  key: K,
  value: FormState[K],
) => void;
