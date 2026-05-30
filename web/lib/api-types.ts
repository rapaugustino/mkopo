/**
 * Type definitions for the staff-side API client.
 *
 * Lives separately from the api client (``./api``) so a file that only
 * needs a shape (a component declaring ``props.loan: Loan``) doesn't
 * import the whole ``api`` object + its fetch helpers. Re-exported
 * from ``./api`` for backwards compatibility: existing imports of
 * ``{ type Loan }`` from ``@/lib/api`` keep working.
 *
 * No runtime code lives here — pure types. The split is "data shape"
 * vs "data fetch", which mirrors how callers use them.
 */

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

/** A staff user available as a loan owner. Superset of ``Owner`` —
 *  the dropdown needs to surface roles so the underwriter can tell
 *  who's an admin vs a regular underwriter at a glance. */
export interface StaffUser {
  id: string;
  name: string;
  email: string;
  initials: string;
  role: string;
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

// ---- Institution settings ----

/** Lender identity + ECOA Reg B disclosure triple. Edited via
 *  the staff ``/settings`` page; threaded into every agent that
 *  drafts a borrower-visible artifact so the LLM never emits
 *  bracketed placeholders. */
export interface InstitutionSettings {
  lender_name: string | null;
  lender_address: string | null;
  lender_phone: string | null;
  lender_email: string | null;
  authorized_officer_name: string | null;
  authorized_officer_title: string | null;
  credit_reporting_agency_name: string | null;
  credit_reporting_agency_address: string | null;
  credit_reporting_agency_phone: string | null;
  /** True iff *any* lender contact field is populated. The
   *  settings page uses this to render a "complete setup" CTA
   *  on first run. */
  configured: boolean;
}

export type InstitutionSettingsPatch = Partial<
  Omit<InstitutionSettings, "configured">
>;

// ---- Citations (grounded-AI hover) ----

/** Resolved underwriting citation. Returned by
 *  ``GET /loans/{id}/citations/{field}``. The frontend ``Cited``
 *  chip fetches this on click to populate the side-drawer
 *  preview with the exact document chunk the value came from.
 */
export interface Citation {
  field_name: string;
  value: string;
  confidence: number;
  document_id: string;
  document_filename: string;
  page: number | null;
  /** The actual source span ("Address: 1622 East Republican Street, ..."). */
  quote: string;
  char_start: number | null;
  char_end: number | null;
  /** "accepted" | "overridden" | "proposed" — drives the
   *  confidence chip colour in the drawer. */
  status: string;
}

// ---- Stage-based locks ----

/** Per-stage lock snapshot. Returned by ``GET /loans/{id}/locks``.
 *  Same source of truth as the 409s thrown by the agent + document
 *  endpoints — the frontend uses this to hide mutation actions and
 *  surface a banner before the user clicks.
 */
export interface LockStatus {
  stage: string;
  is_terminal: boolean;
  agents_locked: boolean;
  documents_locked: boolean;
  /** Banner copy ("Loan is finalized.") or null when no lock applies. */
  headline: string | null;
  /** Sub-copy describing what's still allowed. */
  detail: string | null;
}

// ---- Global search (command palette / Cmd+K) ----

export interface SearchHit {
  /** "loan" | "party" — drives icon + section in the palette. */
  kind: string;
  id: string;
  /** Primary text ("LN-2026-1003" or "Elena Park"). */
  label: string;
  /** Secondary text ("Riverbend Holdings · underwriting"). Nullable. */
  sublabel: string | null;
  /** Route to navigate to on Enter / click. */
  href: string;
}

export interface SearchResults {
  loans: SearchHit[];
  parties: SearchHit[];
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
  /** Mean accuracy across the INTERSECTION of (latest production
   *  rows, latest golden rows) keyed by task_name. Same denominator
   *  as ``overall_golden_accuracy`` — apples-to-apples comparison. */
  overall_production_accuracy: number | null;
  /** Mean accuracy across the intersection — same task set as
   *  production. Comparing the full golden suite against a
   *  production set that only covers extraction would produce a
   *  misleading delta. */
  overall_golden_accuracy: number | null;
  /** production - golden over the intersection. Defined iff both
   *  sides have data. */
  overall_delta: number | null;
  /** Size of the intersection — denominator behind the three values
   *  above. */
  fields_tracked: number;
  fields_drifting: number;
  drift_threshold: number;
  /** Full golden eval suite mean — every latest golden task, not
   *  just the intersection. Surfaced as a separate tile because the
   *  question "how is the eval suite doing overall" is different
   *  from "how does production match its golden baseline". */
  golden_suite_accuracy: number | null;
  /** Number of tasks in the full golden suite (denominator behind
   *  ``golden_suite_accuracy``). */
  golden_suite_n_tasks: number;
  llm_calls_24h: number;
  llm_p95_latency_seconds: number | null;
  llm_error_rate_24h: number | null;
  /** Which window the LLM stats above actually came from. The backend
   *  cascades 24h → 7d → all-time so a quiet demo install still shows
   *  meaningful numbers rather than leaving every tile at "—". */
  llm_window_label?: string;
}

// Diagnostics — everything below the drift trend on the eval page.
// One response shape so the page makes one extra fetch.

export interface EvalConfidenceBucket {
  label: string;
  n: number;
  accepted: number;
  overridden: number;
}

export interface EvalReviewQueueStats {
  open: number;
  resolved_7d: number;
  median_open_age_hours: number | null;
}

export interface EvalAgentReliabilityRow {
  agent_name: string;
  runs: number;
  ok: number;
  interrupted: number;
  failed: number;
}

export interface EvalFailureRow {
  /** `"llm"` opens the LLM call drawer; `"agent_step"` opens the
   *  agent run drawer (drill-in is by parent run, not by step). */
  kind: "llm" | "agent_step";
  id: string;
  at: string;
  summary: string;
  detail: string | null;
}

export interface EvalDiagnostics {
  confidence_buckets: EvalConfidenceBucket[];
  extractions_total: number;
  review_queue: EvalReviewQueueStats;
  agent_reliability: EvalAgentReliabilityRow[];
  recent_failures: EvalFailureRow[];
}

// Trace annotations — verdicts humans recorded on LLM calls / agent
// runs / agent steps. Surfaced inside the observability drawers and
// rolled up on the eval dashboard. "bad"/"incorrect" auto-spawn a
// review_task pointing at a routable extraction on the same loan.

export type AnnotationTargetKind = "llm_call" | "agent_run" | "agent_step";
export type AnnotationVerdict = "good" | "bad" | "incorrect";

export interface Annotation {
  id: string;
  target_kind: AnnotationTargetKind;
  target_id: string;
  verdict: AnnotationVerdict;
  note: string | null;
  created_by_user_id: string | null;
  created_at: string;
  /** Non-null when this annotation auto-created a review_tasks row
   *  (bad/incorrect verdicts with a linkable loan). The drawer
   *  surfaces this as "+ added to review queue". */
  spawned_review_task_id: string | null;
}

/** Regression-diff result for two LLM calls. Metadata-only — we
 *  don't store prompts or response text. */
export interface LLMCallDiffField {
  label: string;
  a: string;
  b: string;
  delta: string;
  /** "match" | "different" | "regression" | "improvement" — drives
   *  the row's colour in the diff table. */
  flag: string;
}

export interface LLMCallDiff {
  a_id: string;
  b_id: string;
  fields: LLMCallDiffField[];
  /** Short one-line takeaway. */
  summary: string;
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
  /** Parent agent step, when the call happened inside one. Used by
   *  AgentRunDrawer to nest calls under their step. Null for ad-hoc
   *  calls (eval CI, rewrite assists, etc.) and for pre-backfill rows. */
  parent_step_id?: string | null;
}

/** Full detail for one LLM call, with same-prompt neighbours.
 *  Returned by ``GET /observability/llm/{id}``; the drawer renders
 *  the long-form ``error_detail`` and the related-calls list. */
export interface ToolUseRow {
  id: string;
  sequence_num: number;
  tool_name: string;
  status: string; // "ok" | "error" | "cancelled"
  elapsed_ms: number | null;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
}

export interface LLMCallDetail extends LLMCallRow {
  error_detail: string | null;
  /** Per-call costs. ``null`` when the gateway didn't have pricing
   *  data for the model (third-party / unknown). The drawer renders
   *  ``—`` in that case rather than a misleading ``$0.0000``. */
  cost_input_usd: number | null;
  cost_output_usd: number | null;
  related: LLMCallRow[];
  /** Tool trajectory for this call. Empty when the call didn't use
   *  tools. Ordered by ``sequence_num`` so the drawer can render the
   *  steps in the order the model proposed them. */
  tool_uses: ToolUseRow[];
}

export interface ModelStats {
  model: string;
  calls: number;
  error_rate: number | null;
  retry_rate: number | null;
  p50_seconds: number | null;
  p95_seconds: number | null;
  /** Window total spend on this model (input + output combined).
   *  ``null`` when the model isn't in the pricing registry — UI
   *  shows "—" in that case so the operator sees the gap rather
   *  than a misleading $0. */
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
}

export interface LLMSummary {
  window_hours: number;
  total_calls: number;
  error_rate: number | null;
  schema_fail_rate: number | null;
  p50_seconds: number | null;
  p95_seconds: number | null;
  /** Window total spend. ``uncosted_calls > 0`` means some rows
   *  weren't priced and the dollar figure understates the bill. */
  total_cost_usd: number;
  uncosted_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  by_model: ModelStats[];
  recent: LLMCallRow[];
}

// ---- Infrastructure errors (Phase: cost + error tracking) ----

export interface InfrastructureErrorRow {
  id: string;
  created_at: string;
  path: string;
  method: string;
  status_code: number;
  error_class: string;
  error_message: string;
  user_id: string | null;
  request_id: string | null;
}

export interface InfrastructureErrorDetail extends InfrastructureErrorRow {
  traceback: string | null;
}

export interface ErrorClassStat {
  error_class: string;
  count: number;
  last_seen: string;
}

export interface InfrastructureErrorSummary {
  window_hours: number;
  total: number;
  by_class: ErrorClassStat[];
  recent: InfrastructureErrorRow[];
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

// ---- Staff auth --------------------------------------------------------

export interface StaffMe {
  id: string;
  email: string;
  name: string;
  role: string;
}

export interface StaffLoginResponse {
  token: string;
  expires_in_seconds: number;
  user: StaffMe;
}

// ---- Safety dashboard --------------------------------------------------
//
// Input-side injection detections (hybrid pattern + Haiku) + output-
// side constitutional judge rollups. Backed by /api/v1/safety/*.

export interface InjectionDetectionRow {
  id: string;
  created_at: string;
  loan_id: string | null;
  source_kind:
    | "document"
    | "chat_message"
    | "inbound_email"
    | "borrower_application";
  source_id: string | null;
  severity: "low" | "medium" | "high";
  decision: "allowed" | "flagged" | "blocked";
  llm_judge_called: boolean;
  llm_judge_severity: "low" | "medium" | "high" | null;
  actor_kind: string;
  actor_id: string | null;
  n_patterns: number;
}

export interface InjectionDetectionDetail extends InjectionDetectionRow {
  matched_patterns: Array<{
    pattern_id: string;
    description: string;
    severity_floor: string;
    span_start: number;
    span_end: number;
    matched_text: string;
  }>;
  llm_judge_critique: string | null;
  raw_text_excerpt: string;
}

export interface PatternHitCount {
  pattern_id: string;
  description: string;
  hits: number;
  severity_floor: string;
}

export interface SafetySummary {
  window_hours: number;
  total_scanned: number;
  total_allowed: number;
  total_flagged: number;
  total_blocked: number;
  by_severity: Record<string, number>;
  by_source_kind: Record<string, number>;
  pattern_top: PatternHitCount[];
  llm_judge_calls: number;
  cost_estimate_usd: number;
  recent: InjectionDetectionRow[];
}

export interface JudgmentRow {
  agent_run_id: string;
  agent_name: string;
  loan_id: string;
  started_at: string;
  severity: "ok" | "warn" | "block";
  attempts: number;
  failed_principles: string[];
  failed_red_lines: string[];
  critique: string | null;
  constitution_hint: string;
}

export interface JudgmentSummary {
  window_hours: number;
  total_judgments: number;
  by_severity: Record<string, number>;
  by_agent: Record<string, number>;
  retry_distribution: Record<string, number>;
  rows: JudgmentRow[];
}

// ---- Safety scenarios catalog -------------------------------------------
//
// Each scenario is a structured robustness property the system pins.
// Backed by a test in tests/test_safety_scenarios.py — CI failure on
// the test means the corresponding scenario card on the UI flips to
// a regression banner.

export type ScenarioCategory =
  | "preflight-gate"
  | "rule-engine-override"
  | "constitutional-judge"
  | "scope-and-role"
  | "input-injection"
  | "storage-authz"
  | "stage-machine"
  | "stage-lock"
  | "orchestrator"
  | "loop-bound";

export type ScenarioSeverity = "critical" | "high" | "medium" | "low";
export type ScenarioStatus = "protected" | "known-gap";

export interface ScenarioRow {
  id: string;
  category: ScenarioCategory;
  title: string;
  threat: string;
  defense: string;
  defense_layer: string;
  test_id: string | null;
  severity: ScenarioSeverity;
  status: ScenarioStatus;
}

export interface ScenariosResponse {
  protected: ScenarioRow[];
  known_gaps: ScenarioRow[];
}

// ---- Per-task detail (Phase 2 eval metrics) --------------------------------
//
// Generic shape returned by GET /eval/task-detail/{task_name}. The
// ``details`` field is task-specific — the dashboard card component
// for each task dispatches on its known structure (confusion matrix
// for decision_verdict, per-criterion for aal_fidelity, bins for
// calibration, by_pattern for adversarial_injection). Tasks that
// don't implement AggregatingEvalTask leave details empty.

export interface TaskDetail {
  task_name: string;
  found: boolean;
  accuracy: number | null;
  avg_score: number | null;
  n: number | null;
  source: string | null;
  ran_at: string | null;
  // Task-specific aggregate payload. Shape depends on task_name —
  // each card declares its own narrowed view.
  details: Record<string, unknown> | null;
}

/** Decision-verdict aggregate. Per-class metrics use the standard
 *  one-vs-rest decomposition; confusion matrix is keyed
 *  [expected][predicted]. */
export interface DecisionVerdictDetails {
  classes: string[];
  confusion_matrix: Record<string, Record<string, number>>;
  per_class: Record<
    string,
    {
      n: number;
      precision: number;
      recall: number;
      f1: number;
      tp: number;
      fp: number;
      fn: number;
    }
  >;
  macro_f1: number;
}

/** AAL fidelity per-criterion pass rates. Keys mirror the score
 *  function's flags so a regression on one criterion (e.g. the
 *  ECOA right-to-know disclosure) shows on its own bar. */
export interface AALFidelityDetails {
  per_criterion: Record<
    string,
    { n: number; passed: number; rate: number }
  >;
}

/** Calibration reliability-diagram bins + headline metrics. */
export interface CalibrationDetails {
  ece: number;
  brier: number;
  window_days: number;
  bins: {
    lower: number;
    upper: number;
    n: number;
    mean_confidence: number;
    empirical_accuracy: number;
  }[];
}

/** Adversarial-injection per-pattern coverage. */
export interface AdversarialInjectionDetails {
  by_pattern: Record<
    string,
    { n: number; passed: number; rate: number }
  >;
}

/** Intake-email compliance. Per-criterion pass rates + a per-class
 *  (personal vs business) breakdown — a regression on one loan class
 *  shouldn't be masked by the other's pass rate. */
export interface IntakeEmailDetails {
  per_criterion: Record<
    string,
    { n: number; passed: number; rate: number }
  >;
  by_class: Record<
    string,
    { n: number; passed: number; rate: number }
  >;
}

/** Refusal-rate trend. Block-rate over last 7d compared against
 *  baseline (prior 28d), with a z-score for spike detection. ``flag``
 *  trips ``spike`` at |z| ≥ 2; ``stable`` otherwise. The dashboard
 *  reads ``current_rate`` for the headline and ``z_score`` for the
 *  flag colour. */
export interface RefusalDetails {
  current_rate: number;
  baseline_rate: number;
  n_current: number;
  n_current_blocked: number;
  n_baseline: number;
  n_baseline_blocked: number;
  z_score: number | null;
  flag: "stable" | "spike" | "insufficient_data";
  window_current_days: number;
  window_baseline_days: number;
  spike_threshold_sigma: number;
}

/** Per-agent economics row. Returned by ``GET /eval/agent-economics``;
 *  the dashboard card renders one row per agent. ``cost_per_run_usd``
 *  drives the headline, p95 is the latency-tail signal. */
export interface AgentEconRow {
  agent_name: string;
  n_runs: number;
  n_calls: number;
  total_cost_usd: number;
  cost_per_run_usd: number;
  p95_latency_seconds: number | null;
  p50_latency_seconds: number | null;
}

export interface AgentEconResponse {
  rows: AgentEconRow[];
  window_days: number;
}

/** PSI per-feature drift. The details payload written by
 *  ``mkopo/services/psi.py:run_psi_monitor`` — one task_runs row per
 *  feature (task_name = ``psi.loan_amount`` / ``psi.loan_class`` /
 *  ``psi.loan_type``). Bands follow Siddiqi 2017: <0.10 stable,
 *  0.10–0.25 minor, ≥0.25 major. ``feature_kind`` is "numeric" or
 *  "categorical"; the dashboard renders bin labels differently per
 *  kind. */
export interface PSIDetails {
  feature: string;
  feature_kind: "numeric" | "categorical";
  psi: number;
  flag: "stable" | "minor" | "major";
  n_reference: number;
  n_current: number;
  window_current_days: number;
  window_reference_days: number;
  bins: {
    label: string;
    reference_pct: number;
    current_pct: number;
    psi_contribution: number;
  }[];
  thresholds: { stable: number; minor: number };
}

/** Adverse Impact Ratio (four-fifths rule).
 *
 *  AIR = min_group_approval_rate / max_group_approval_rate. EEOC
 *  flags < 0.80 as a screening threshold for disparate impact. The
 *  ``flag`` field is the dashboard's pre-computed band; ``groups``
 *  carries the per-class counts so the card can render a side-by-
 *  side comparison. ``four_fifths_threshold`` is stashed so the
 *  card doesn't have to know the 0.80 constant. */
export interface FairnessDetails {
  air: number | null;
  flag: "ok" | "watch" | "concern" | "insufficient_data";
  window_days: number;
  four_fifths_threshold: number;
  groups: {
    name: string;
    n_decisioned: number;
    n_approved: number;
    n_declined: number;
    approval_rate: number;
  }[];
}

/** Tool-call accuracy on the borrower-chat agent's tool-selection
 *  pass. Per-criterion bars (3 criteria) + per-tool selection rate
 *  (was the right tool reached for when the fixture expected it?). */
export interface ToolCallAccuracyDetails {
  per_criterion: Record<
    string,
    { n: number; passed: number; rate: number }
  >;
  per_tool: Record<
    string,
    { n: number; selected: number; rate: number }
  >;
}

/** UW-summary groundedness (RAGAS-style faithfulness).
 *
 *  - ``judge_accuracy`` — fraction of fixtures the judge classified
 *    into the right band (clean vs hallucinated). This is the
 *    gate-relevant number; also equals ``TaskDetail.accuracy``.
 *  - ``avg_grounded_clean`` — mean groundedness on clean fixtures;
 *    the "how grounded are our good summaries?" headline.
 *  - ``avg_grounded_hallucinated`` — mean groundedness on planted-
 *    hallucination fixtures; should sit well below the clean bar to
 *    prove the judge actually discriminates.
 *  - ``per_example`` — every fixture's score + expected band, so the
 *    card can render a per-fixture row. */
export interface UWGroundednessDetails {
  judge_accuracy: number;
  avg_grounded_clean: number;
  avg_grounded_hallucinated: number;
  total_claims: number;
  supported_claims: number;
  n_clean: number;
  n_hallucinated: number;
  per_example: {
    id: string;
    score: number;
    passed: boolean;
    total_claims: number;
    supported_claims: number;
    expected_band: string;
  }[];
}
