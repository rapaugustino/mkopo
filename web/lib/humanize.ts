/**
 * Shared label maps. Every backend enum / snake_case value that touches
 * the UI flows through one of these so we never render
 * `borrower_entity` to a user.
 *
 * The rules:
 * - Each map is exhaustive for its enum where the backend has a closed
 *   set (LoanStage, RiskBand). Unknown values fall back to a tidy
 *   title-case + underscore-strip via {@link titleCase}.
 * - Acronyms and finance jargon (NOI, LTV, DSCR, AAL, NNN) are
 *   preserved in their canonical capitalisation. The naïve "Title Case"
 *   approach would give us "Annual Noi" — don't ship that.
 * - These are UI labels, not i18n keys. If we ever need i18n the
 *   call sites stay the same — only this file changes.
 */

import type { LoanStage, RiskBand } from "./api";

// ---- core helpers --------------------------------------------------------

const ACRONYMS = new Set([
  "noi",
  "ltv",
  "dscr",
  "aal",
  "nnn",
  "reit",
  "llc",
  "lp",
  "id",
  "ai",
  "pip",
  "pfs",
  "iso",
]);

/** Title-case a single word, preserving known acronyms in uppercase. */
function titleWord(word: string): string {
  if (!word) return word;
  if (ACRONYMS.has(word.toLowerCase())) return word.toUpperCase();
  return word[0]!.toUpperCase() + word.slice(1).toLowerCase();
}

/**
 * Title-case a snake_case or kebab-case identifier into a UI string.
 *
 *     titleCase("annual_noi")        → "Annual NOI"
 *     titleCase("review_task_done")  → "Review Task Done"
 *     titleCase("seed_loan_created") → "Seed Loan Created"
 *
 * For the more nuanced "Annual NOI" vs "Annual noi" decision, the
 * acronym set above is the source of truth.
 */
export function titleCase(s: string): string {
  if (!s) return "—";
  return s
    .replace(/[_-]+/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .map(titleWord)
    .join(" ");
}

/** First-letter capital, rest lowercase. Use when the string is a
 *  single word that we don't want to over-format. */
export function sentenceCase(s: string): string {
  if (!s) return "—";
  return s[0]!.toUpperCase() + s.slice(1).toLowerCase();
}

// ---- loan + risk ---------------------------------------------------------

const STAGE_LABEL: Record<LoanStage, string> = {
  intake: "Intake",
  underwriting: "Underwriting",
  decision: "Decision",
  conditions: "Conditions",
  closing: "Closing",
  servicing: "Servicing",
  declined: "Declined",
  approved: "Approved",
  withdrawn: "Withdrawn",
};

export const humanizeStage = (s: LoanStage | string | null | undefined): string =>
  s ? (STAGE_LABEL[s as LoanStage] ?? titleCase(s)) : "—";

const RISK_LABEL: Record<RiskBand, string> = {
  low: "Low",
  med: "Med",
  high: "High",
};

export const humanizeRisk = (r: RiskBand | string | null | undefined): string =>
  r ? (RISK_LABEL[r as RiskBand] ?? titleCase(r)) : "—";

const LOAN_TYPE_LABEL: Record<string, string> = {
  bridge: "Bridge",
  permanent: "Permanent",
  construction: "Construction",
  refinance: "Refinance",
};

export const humanizeLoanType = (t: string | null | undefined): string =>
  t ? (LOAN_TYPE_LABEL[t] ?? titleCase(t)) : "—";

const PROPERTY_TYPE_LABEL: Record<string, string> = {
  multifamily: "Multifamily",
  office: "Office",
  retail: "Retail",
  industrial: "Industrial",
  hotel: "Hotel",
  mixed_use: "Mixed-use",
  self_storage: "Self-storage",
  other: "Other",
};

export const humanizePropertyType = (t: string | null | undefined): string =>
  t ? (PROPERTY_TYPE_LABEL[t] ?? titleCase(t)) : "—";

// ---- parties + roles -----------------------------------------------------

const PARTY_TYPE_LABEL: Record<string, string> = {
  entity: "Entity",
  person: "Individual",
};

export const humanizePartyType = (t: string | null | undefined): string =>
  t ? (PARTY_TYPE_LABEL[t] ?? titleCase(t)) : "—";

const ROLE_LABEL: Record<string, string> = {
  borrower: "Borrower",
  guarantor: "Guarantor",
  co_borrower: "Co-borrower",
  sponsor: "Sponsor",
  signer: "Signer",
};

export const humanizeRole = (r: string | null | undefined): string =>
  r ? (ROLE_LABEL[r] ?? titleCase(r)) : "—";

// ---- documents + extractions --------------------------------------------

const DOC_TYPE_LABEL: Record<string, string> = {
  loan_application: "Loan application",
  appraisal: "Appraisal",
  rent_roll: "Rent roll",
  personal_financial_statement: "Personal financial statement",
  tax_return: "Tax return",
  bank_statement: "Bank statement",
  insurance: "Insurance",
  title_report: "Title report",
  other: "Other",
  unknown: "Unknown",
};

export const humanizeDocType = (t: string | null | undefined): string =>
  t ? (DOC_TYPE_LABEL[t] ?? titleCase(t)) : "—";

const EXTRACTION_STATUS_LABEL: Record<string, string> = {
  proposed: "Proposed",
  accepted: "Accepted",
  queued_for_review: "Pending review",
  overridden: "Overridden",
};

export const humanizeExtractionStatus = (s: string | null | undefined): string =>
  s ? (EXTRACTION_STATUS_LABEL[s] ?? titleCase(s)) : "—";

// ---- fields (the dashboard + extractions table) -------------------------

const FIELD_LABEL: Record<string, string> = {
  borrower_entity: "Borrower entity",
  property_address: "Property address",
  property_type: "Property type",
  guarantor_list: "Guarantor list",
  annual_noi: "Annual NOI",
  appraised_value: "Appraised value",
  appraisal_date: "Appraisal date",
  loan_amount: "Loan amount",
  ltv: "LTV",
  dscr: "DSCR",
  debt_yield: "Debt yield",
  doc_confidence: "Doc confidence",
};

/** Humanize a backend field name. Strips an optional ``extraction.``
 *  prefix so the same map serves the eval dashboard and the
 *  extractions panel. */
export function humanizeField(name: string | null | undefined): string {
  if (!name) return "—";
  const bare = name.replace(/^extraction\./, "");
  return FIELD_LABEL[bare] ?? titleCase(bare);
}

// ---- audit actions -------------------------------------------------------

const AUDIT_ACTION_LABEL: Record<string, string> = {
  seed_loan_created: "Loan created",
  stage_changed: "Stage changed",
  stage_transition: "Stage changed",
  intake_started: "Intake started",
  intake_complete: "Intake complete",
  extraction_complete: "Extraction complete",
  underwriting_started: "Underwriting started",
  underwriting_complete: "Underwriting complete",
  decision_started: "Decision started",
  decision_complete: "Decision made",
  send_email: "Email sent",
  internal_note: "Internal note",
  borrower_reply: "Borrower reply",
  document_uploaded: "Document uploaded",
  document_accessed: "Document accessed",
  borrower_field_updated: "Borrower updated a field",
  borrower_erasure_requested: "Borrower requested erasure",
  condition_added: "Condition added",
  condition_satisfied: "Condition satisfied",
  condition_waived: "Condition waived",
  review_task_created: "Review queued",
  review_task_resolved: "Review resolved",
  loan_created: "Loan created",
  borrower_applied: "Borrower applied via portal",
  borrower_document_uploaded: "Borrower uploaded a document",
  autonomy_changed: "Autonomy mode changed",
  orchestrator_advanced: "Orchestrator advanced stage",
  owner_reassigned: "Owner reassigned",
};

export const humanizeAuditAction = (a: string | null | undefined): string =>
  a ? (AUDIT_ACTION_LABEL[a] ?? titleCase(a)) : "—";

// ---- generic status (review-task, condition, etc.) -----------------------

const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  resolved: "Resolved",
  satisfied: "Satisfied",
  waived: "Waived",
  pending: "Pending",
  accepted: "Accepted",
  rejected: "Rejected",
  draft: "Draft",
};

export const humanizeStatus = (s: string | null | undefined): string =>
  s ? (STATUS_LABEL[s] ?? titleCase(s)) : "—";

/** Rule-engine identifier → friendly label.
 *
 *  Rule ids are deliberately code-shaped (``ltv_under_cap``,
 *  ``dscr_above_floor``, ``doc_completeness``) so they're stable
 *  across versions and easy to reference in audit logs. They are
 *  NOT a user-facing string. Surfaces that show rule outcomes —
 *  the underwriting risk-flag chips, the decision verdict
 *  cascade, the materials-drift banner — should run rule ids
 *  through this. Eval / Observability / Prompts are exempt; those
 *  audiences want the raw key.
 *
 *  Adding a new rule? Land its friendly label here at the same
 *  time you register the rule. The fallback below is correct for
 *  one-off rules but reads as "Some Snake Cased Thing" which is
 *  obviously a temporary stand-in.
 */
const RULE_LABELS: Record<string, string> = {
  ltv_under_cap: "Loan-to-value within cap",
  dscr_above_floor: "Debt-service coverage",
  debt_yield_above_floor: "Debt yield",
  appraisal_age: "Appraisal age",
  doc_completeness: "Document completeness",
  guarantor_concentration: "Guarantor concentration",
  credit_score_floor: "Credit score floor",
  dti_under_cap: "Debt-to-income within cap",
  lti_under_cap: "Loan-to-income within cap",
  employment_tenure_minimum: "Employment tenure",
  income_minimum: "Minimum income",
};

export const humanizeRuleId = (id: string | null | undefined): string =>
  id ? (RULE_LABELS[id] ?? titleCase(id)) : "—";

/** Loan class — ``business`` vs ``personal``. Borrower-facing
 *  surfaces (apply form review step, /account dashboard) and
 *  staff loan headers both render this. The values would look
 *  awkward lowercased ("business · bridge"). */
export const humanizeLoanClass = (c: string | null | undefined): string => {
  if (!c) return "—";
  const lower = c.toLowerCase();
  if (lower === "personal") return "Personal";
  if (lower === "business") return "Business";
  return titleCase(c);
};
