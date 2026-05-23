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
  | "approved"
  | "withdrawn";

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

export type AutonomyLevel = "assisted" | "autonomous";

/** Materials-hash status for a loan. ``drifted`` is true when the
 *  inputs that fed the last decision (extractions, document content,
 *  borrower-supplied meta, guarantor list) have changed since the
 *  decision agent ran. The UI surfaces this as a prominent banner —
 *  the system refuses forward transitions until the decision agent
 *  is re-run against the current materials. */
export interface MaterialsStatus {
  drifted: boolean;
  current_hash: string;
  decision_hash: string | null;
}
export type LoanClass = "business" | "personal";

export interface Loan {
  id: string;
  reference: string;
  stage: LoanStage;
  loan_type: string;
  loan_class: LoanClass;
  amount: string;
  status_detail: string | null;
  risk_band: RiskBand | null;
  stage_entered_at: string;
  autonomy_level: AutonomyLevel;
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

  // Commercial tile set — populated when loan_class === "business".
  // Fields are optional (``?:``) because either side can be absent —
  // a personal-loan KPI block omits commercial fields entirely; a
  // cached response written before the schema split won't carry the
  // personal fields. Callers should always test with ``!= null``
  // (catches both ``null`` and ``undefined``).
  ltv?: number | null;
  dscr?: number | null;
  debt_yield?: number | null;
  property_type?: string;

  // Personal-loan tile set — populated when loan_class === "personal".
  dti?: number | null;
  lti?: number | null;
  credit_score?: number | null;
  credit_band?: string | null;
  years_employment?: number | null;

  // Shared across both classes.
  doc_confidence?: number | null;
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

/** Deterministic-only subset of UnderwritingResult — what the rules
 *  engine and KPI computation produce without the LLM summary node.
 *  Returned by `GET /loans/{id}/rules`. */
export interface RulesPreview {
  kpis: UnderwritingKPIs;
  risk_flags: RiskFlag[];
  extractions: Record<string, string>;
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

// ---- Documents (Phase H) ----

export interface LoanDocument {
  id: string;
  filename: string;
  doc_type: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
  extract: {
    method?: "decode" | "pypdf" | "skipped" | string;
    page_count?: number;
    pages_with_text?: number;
    pages_needing_ocr?: number;
    char_count?: number;
  };
}

export interface UploadResult {
  document_id: string;
  storage_uri: string;
  chunks_embedded: number;
  extract: LoanDocument["extract"];
}

// ---- Observability (Phase J) ----

export interface LLMCallRow {
  id: string;
  created_at: string;
  model: string;
  schema_name: string | null;
  status: string;
  attempt: number;
  elapsed_seconds: number;
  input_tokens: number | null;
  output_tokens: number | null;
  system_prompt_hash: string;
  /** Short one-line failure summary. Null for successful calls. */
  error_reason?: string | null;
}

/** Full detail for one LLM call, with same-prompt neighbours.
 *  Returned by ``GET /observability/llm/{id}``; the drawer renders
 *  the long-form ``error_detail`` and the related-calls list. */
export interface LLMCallDetail extends LLMCallRow {
  error_detail: string | null;
  related: LLMCallRow[];
}

export interface ModelStats {
  model: string;
  calls: number;
  error_rate: number | null;
  retry_rate: number | null;
  p50_seconds: number | null;
  p95_seconds: number | null;
}

export interface LLMSummary {
  window_hours: number;
  total_calls: number;
  error_rate: number | null;
  schema_fail_rate: number | null;
  p50_seconds: number | null;
  p95_seconds: number | null;
  by_model: ModelStats[];
  recent: LLMCallRow[];
}

export interface AgentRunRow {
  id: string;
  created_at: string;
  agent_name: string;
  thread_id: string;
  status: string;
  loan_id: string;
}

/** One LangGraph node execution inside an agent run.
 *  Status: "ok" (ran), "skipped" (pre-flight gate), "interrupt"
 *  (paused for human approval), "failed" (raised). */
export interface AgentStepRow {
  id: string;
  created_at: string;
  node: string;
  status: "ok" | "skipped" | "interrupt" | "failed" | string;
  summary: string | null;
  elapsed_ms: number | null;
  payload: Record<string, unknown>;
}

/** Full trace for one agent run: row + step list + same-thread LLM calls.
 *  Returned by ``GET /observability/agents/{id}``. */
export interface AgentRunDetail {
  id: string;
  created_at: string;
  agent_name: string;
  thread_id: string;
  status: string;
  loan_id: string;
  payload: Record<string, unknown>;
  steps: AgentStepRow[];
  llm_calls: LLMCallRow[];
}

export interface AgentSummary {
  window_hours: number;
  total_runs: number;
  by_agent: Record<string, number>;
  by_status: Record<string, number>;
  recent: AgentRunRow[];
}

export const api = {
  listLoans: () => request<Loan[]>("/loans"),
  getLoan: (id: string) => request<Loan>(`/loans/${id}`),
  getAuditEvents: (id: string) => request<AuditEvent[]>(`/loans/${id}/audit`),
  getExtractions: (id: string) => request<Extraction[]>(`/loans/${id}/extractions`),
  getRulesPreview: (id: string) => request<RulesPreview>(`/loans/${id}/rules`),
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
  // ---- Documents ----
  listDocuments: (loanId: string) =>
    request<LoanDocument[]>(`/loans/${loanId}/documents`),
  transitionStage: (loanId: string, to_stage: LoanStage, reason: string) =>
    request<Loan>(`/loans/${loanId}/transition`, {
      method: "POST",
      body: JSON.stringify({ to_stage, reason }),
    }),
  getAllowedTransitions: (loanId: string) =>
    request<Record<string, string | null>>(`/loans/${loanId}/transitions`),
  getMaterialsStatus: (loanId: string) =>
    request<MaterialsStatus>(`/loans/${loanId}/materials/status`),
  setAutonomy: (loanId: string, level: AutonomyLevel, reason: string) =>
    request<Loan>(`/loans/${loanId}/autonomy`, {
      method: "PATCH",
      body: JSON.stringify({ level, reason }),
    }),
  uploadDocument: async (loanId: string, file: File): Promise<UploadResult> => {
    // FormData with file payload — different from JSON requests, so we
    // hit fetch directly rather than the `request<T>()` helper (which
    // forces Content-Type: application/json).
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_URL}/api/v1/loans/${loanId}/documents`, {
      method: "POST",
      headers: { Authorization: `Bearer ${DEV_TOKEN}` },
      body: form,
    });
    if (!res.ok) throw new Error(`Upload ${res.status}: ${await res.text()}`);
    return (await res.json()) as UploadResult;
  },
  // ---- Eval ----
  getEvalSummary: () => request<EvalSummary>(`/eval/summary`),
  getEvalFields: () => request<EvalFieldRow[]>(`/eval/fields`),
  getEvalTrend: (days = 30) => request<EvalTrend>(`/eval/trend?days=${days}`),
  refreshDrift: () => request<EvalRefreshResult>(`/eval/refresh`, { method: "POST" }),
  // ---- Observability ----
  getLLMObservability: (hours = 24) =>
    request<LLMSummary>(`/observability/llm?hours=${hours}`),
  getLLMCallDetail: (callId: string) =>
    request<LLMCallDetail>(`/observability/llm/${callId}`),
  getAgentsObservability: (hours = 24) =>
    request<AgentSummary>(`/observability/agents?hours=${hours}`),
  getAgentRunDetail: (runId: string) =>
    request<AgentRunDetail>(`/observability/agents/${runId}`),
};
