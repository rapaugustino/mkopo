const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEV_TOKEN = process.env.NEXT_PUBLIC_DEV_TOKEN || "dev-token-replace-me";

export type LoanStage =
  | "intake"
  | "underwriting"
  | "decision"
  | "conditions"
  | "closing"
  | "servicing"
  | "declined"
  | "approved";

export type IntakeStatus = "running" | "awaiting_approval" | "complete" | "failed";

export type RiskBand = "low" | "med" | "high";

export interface Owner {
  id: string;
  name: string;
  email: string;
  initials: string;
}

export interface Borrower {
  id: string;
  name: string;
  party_type: string;
}

export interface Loan {
  id: string;
  reference: string;
  stage: LoanStage;
  loan_type: string;
  amount: string;
  status_detail: string | null;
  risk_band: RiskBand | null;
  stage_entered_at: string;
  owner: Owner | null;
  borrower: Borrower | null;
  guarantors: Borrower[];
  created_at: string;
  updated_at: string;
}

export interface AuditEvent {
  id: string;
  actor_type: string;
  actor_id: string;
  action: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface DraftEmail {
  subject: string;
  body_text: string;
}

/**
 * Payload returned by the intake agent when it pauses for human approval.
 * Mirrors the dict produced by `request_human_approval`'s `interrupt(...)` call
 * in api/mkopo/agents/intake.py.
 */
export interface IntakeInterrupt {
  type: "approve_email";
  loan_id: string;
  draft: DraftEmail;
  missing_fields: string[];
}

export interface RunIntakeResponse {
  thread_id: string;
  status: IntakeStatus;
  interrupt: IntakeInterrupt | null;
}

export interface ResumeIntakeResponse {
  thread_id: string;
  status: IntakeStatus;
}

// ---- Underwriting ----

export interface Extraction {
  id: string;
  field_name: string;
  value: string;
  confidence: number;
  status: string;
  source_span: { page?: number; char_start?: number; char_end?: number; quote?: string };
}

export type RiskSeverity = "block" | "warn" | "info";
export type UnderwritingRecommendation =
  | "proceed_to_decision"
  | "request_more_info"
  | "decline";

export interface RiskFlag {
  rule_id: string;
  severity: RiskSeverity;
  passed: boolean;
  message: string;
  details: Record<string, unknown>;
}

export interface UnderwritingSection {
  title: string;
  body: string;
  citations: string[];
}

export interface UnderwritingKPIs {
  loan_amount: string;
  ltv: number | null;
  dscr: number | null;
  debt_yield: number | null;
  doc_confidence: number | null;
  property_type: string;
}

export interface UnderwritingResult {
  kpis: UnderwritingKPIs;
  sections: UnderwritingSection[];
  risk_flags: RiskFlag[];
  recommendation: UnderwritingRecommendation;
  rationale: string;
  generated_at: string;
  agent_run_id: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${DEV_TOKEN}`,
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// ---- Comparables + Ask the file ----

export interface ComparableLoan {
  loan_id: string;
  reference: string;
  borrower: string | null;
  loan_type: string;
  amount: string;
  risk_band: RiskBand | null;
  similarity: number;
}

export interface CitedChunk {
  document_id: string;
  filename: string;
  ordinal: number;
  content: string;
  similarity: number;
}

export interface AskResponse {
  question: string;
  answer: string;
  citations: CitedChunk[];
  comparable_loans: ComparableLoan[];
}

// ---- Credit decision (Phase D) ----

export type DecisionPath = "approve" | "conditional" | "decline";

export interface TermSheet {
  principal: string; // Decimal as string
  rate_pct: number;
  rate_basis: string;
  term_months: number;
  amortization: string;
  origination_fee_pct: number;
  prepay_terms: string;
  notes: string;
}

export interface ConditionDraft {
  description: string;
  due_within_days: number | null;
}

export interface AdverseActionLetter {
  subject: string;
  body_text: string;
  principal_reasons: string[];
}

export interface DecisionResult {
  path: DecisionPath;
  confidence: number; // 0..1
  verdict_text: string;
  rationale: string;
  term_sheet: TermSheet | null;
  conditions: ConditionDraft[];
  adverse_action_letter: AdverseActionLetter | null;
  generated_at: string;
  agent_run_id: string;
}

export interface Condition {
  id: string;
  description: string;
  status: string; // open | satisfied | waived
  due_date: string | null;
  drafted_by_agent: boolean;
  created_at: string;
}

// ---- Review queue (Phase E) ----

export interface ReviewTaskExtraction {
  id: string;
  field_name: string;
  value: string;
  confidence: number;
  status: string;
  source_span: {
    page?: number;
    char_start?: number;
    char_end?: number;
    quote?: string;
  };
}

export interface ReviewTaskLoan {
  id: string;
  reference: string;
}

export interface ReviewTaskDocument {
  id: string;
  filename: string;
}

export interface ReviewTask {
  id: string;
  reason: string;
  status: string; // open | resolved
  created_at: string;
  extraction: ReviewTaskExtraction;
  loan: ReviewTaskLoan;
  document: ReviewTaskDocument;
}

export interface ExtractionSource {
  extraction: ReviewTaskExtraction;
  document: ReviewTaskDocument;
  loan: ReviewTaskLoan;
  document_text: string;
}

// ---- Entity inspector (Phase F) ----

export interface LoanRef {
  id: string;
  reference: string;
  stage: string;
  loan_type: string;
  amount: string;
  risk_band: RiskBand | null;
}

export interface RelatedParty {
  party_id: string;
  name: string;
  role: string;
  shared_loan_count: number;
  shared_exposure: string;
}

export interface PartyProfile {
  party_id: string;
  name: string;
  party_type: string;
  email: string | null;
  role: string;
  active_exposure: string;
  active_loans: LoanRef[];
  delinquencies: number;
  policy_limit: string;
  related_parties: RelatedParty[];
}

// ---- Eval dashboard (Phase G) ----

export interface EvalSummary {
  overall_production_accuracy: number | null;
  overall_golden_accuracy: number | null;
  overall_delta: number | null;
  fields_tracked: number;
  fields_drifting: number;
  drift_threshold: number;
  llm_calls_24h: number;
  llm_p95_latency_seconds: number | null;
  llm_error_rate_24h: number | null;
}

export interface EvalFieldRow {
  field_name: string;
  production_accuracy: number | null;
  production_n: number | null;
  production_at: string | null;
  golden_accuracy: number | null;
  golden_n: number | null;
  golden_at: string | null;
  delta: number | null;
}

export interface EvalTrendPoint {
  task_name: string;
  source: "production" | "golden" | string;
  created_at: string;
  accuracy: number;
  n: number;
}

export interface EvalTrend {
  days: number;
  points: EvalTrendPoint[];
}

export interface EvalRefreshResult {
  status: string;
  fields_written: number;
}

export const api = {
  listLoans: () => request<Loan[]>("/loans"),
  getLoan: (id: string) => request<Loan>(`/loans/${id}`),
  getAuditEvents: (id: string) => request<AuditEvent[]>(`/loans/${id}/audit`),
  getExtractions: (id: string) => request<Extraction[]>(`/loans/${id}/extractions`),
  getComparables: (id: string) =>
    request<ComparableLoan[]>(`/loans/${id}/comparables`),
  askLoan: (id: string, question: string) =>
    request<AskResponse>(`/loans/${id}/ask`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  // Agent runs no longer have JSON helpers here — they stream Server-Sent
  // Events via `useAgentRun` (see lib/useAgentRun.ts). The endpoints
  // /loans/{id}/agents/{intake,underwriting,decision}/run return
  // text/event-stream now; calling them with `request<T>()` would parse
  // the SSE body as JSON and fail. The streaming hook is the only
  // sanctioned entry point.
  getConditions: (id: string) => request<Condition[]>(`/loans/${id}/conditions`),
  addNote: (id: string, text: string, kind: "internal_note" | "borrower_reply" = "internal_note") =>
    request<AuditEvent>(`/loans/${id}/notes`, {
      method: "POST",
      body: JSON.stringify({ text, kind }),
    }),
  // ---- Review queue ----
  listReviewTasks: (status: "open" | "resolved" = "open") =>
    request<ReviewTask[]>(`/review-tasks?status_filter=${status}`),
  getReviewTaskSource: (taskId: string) =>
    request<ExtractionSource>(`/review-tasks/${taskId}/source`),
  acceptReviewTask: (taskId: string) =>
    request<ReviewTask>(`/review-tasks/${taskId}/accept`, { method: "POST" }),
  overrideReviewTask: (taskId: string, value: string, notes?: string) =>
    request<ReviewTask>(`/review-tasks/${taskId}/override`, {
      method: "POST",
      body: JSON.stringify({ value, notes }),
    }),
  getPartyProfile: (id: string) => request<PartyProfile>(`/parties/${id}/profile`),
  // ---- Eval ----
  getEvalSummary: () => request<EvalSummary>(`/eval/summary`),
  getEvalFields: () => request<EvalFieldRow[]>(`/eval/fields`),
  getEvalTrend: (days = 30) => request<EvalTrend>(`/eval/trend?days=${days}`),
  refreshDrift: () => request<EvalRefreshResult>(`/eval/refresh`, { method: "POST" }),
};
