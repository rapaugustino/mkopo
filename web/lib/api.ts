const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Staff auth is cookie-based (httpOnly ``mkopo_staff_session``) issued
 * by POST /staff/auth/login. ``credentials: 'include'`` on every fetch
 * is what tells the browser to send it cross-origin (dev: localhost:3000
 * → localhost:8000).
 *
 * On a 401 the request helper redirects to /login (preserving the
 * intended destination). This is the cleanest way to wire "session
 * expired" without an auth context spilling across every component —
 * the redirect happens at the API boundary, not at every render site.
 */
function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  // Don't bounce in a redirect loop if we're already on /staff/login.
  if (window.location.pathname.startsWith("/staff/login")) return;
  // Don't intercept borrower-portal pages — those use their own
  // /login (borrower login) and /apply flows. The borrower paths
  // never call into the staff ``api.*`` client, so this is mostly
  // defensive.
  if (
    window.location.pathname.startsWith("/account") ||
    window.location.pathname.startsWith("/apply") ||
    window.location.pathname === "/login" ||
    window.location.pathname.startsWith("/signup") ||
    window.location.pathname.startsWith("/forgot-password")
  ) {
    return;
  }
  const next = encodeURIComponent(here);
  window.location.href = `/staff/login?next=${next}`;
}


// All shape types live in ./api-types and are re-exported below for
// backwards compatibility — existing `import { type Loan } from "@/lib/api"`
// keeps working. New code can import directly from ./api-types when it
// only needs a shape and not the api client.
// Re-export every shape type for backwards compat (existing
// `import { type Loan } from "@/lib/api"` keeps working).
export type * from "./api-types";

// Local-use imports — the api object body below references these
// types directly and they must be in scope as well as re-exported.
import type {
  AALFidelityDetails,
  AdversarialInjectionDetails,
  AdverseActionLetter,
  AgentEconResponse,
  AgentEconRow,
  AgentRunDetail,
  AgentRunRow,
  AgentStepRow,
  AgentSummary,
  Annotation,
  AnnotationTargetKind,
  AnnotationVerdict,
  AskResponse,
  AuditEvent,
  AutonomyLevel,
  Borrower,
  CalibrationDetails,
  Citation,
  CitedChunk,
  ComparableLoan,
  Condition,
  ConditionDraft,
  DecisionPath,
  DecisionResult,
  DecisionVerdictDetails,
  DraftEmail,
  ErrorClassStat,
  EvalAgentReliabilityRow,
  EvalConfidenceBucket,
  EvalDiagnostics,
  EvalFailureRow,
  EvalFieldRow,
  EvalRefreshResult,
  EvalReviewQueueStats,
  EvalSummary,
  EvalTrend,
  EvalTrendPoint,
  Extraction,
  ExtractionSource,
  FairnessDetails,
  InfrastructureErrorDetail,
  InfrastructureErrorRow,
  InfrastructureErrorSummary,
  InjectionDetectionDetail,
  InjectionDetectionRow,
  InstitutionSettings,
  InstitutionSettingsPatch,
  IntakeEmailDetails,
  IntakeInterrupt,
  IntakeStatus,
  JudgmentRow,
  JudgmentSummary,
  LLMCallDetail,
  LLMCallDiff,
  LLMCallDiffField,
  LLMCallRow,
  LLMSummary,
  Loan,
  LoanClass,
  LoanDocument,
  LoanRef,
  LoanStage,
  LockStatus,
  MaterialsStatus,
  ModelStats,
  Owner,
  PSIDetails,
  PartyProfile,
  PatternHitCount,
  RefusalDetails,
  RelatedParty,
  ResumeIntakeResponse,
  ReviewTask,
  ReviewTaskDocument,
  ReviewTaskExtraction,
  ReviewTaskLoan,
  RiskBand,
  RiskFlag,
  RiskSeverity,
  RulesPreview,
  RunIntakeResponse,
  SafetySummary,
  ScenarioCategory,
  ScenarioRow,
  ScenarioSeverity,
  ScenarioStatus,
  ScenariosResponse,
  SearchHit,
  SearchResults,
  StaffLoginResponse,
  StaffMe,
  StaffUser,
  TaskDetail,
  TermSheet,
  ToolCallAccuracyDetails,
  ToolUseRow,
  UWGroundednessDetails,
  UnderwritingKPIs,
  UnderwritingRecommendation,
  UnderwritingResult,
  UnderwritingSection,
  UploadResult,
} from "./api-types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (res.status === 401) {
    // Session expired or never authenticated — bounce to login.
    // Throws after redirect so callers' .then/.catch don't fire on
    // a stale response shape.
    redirectToLogin();
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json();
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
  /** Rehydrate the last completed underwriting agent result so the
   *  workspace stays populated across page reloads. Backend returns
   *  the full Pydantic dump stored under ``agent_runs.payload.result_json``
   *  by the persist node. ``null`` means the agent has never run on
   *  this loan. */
  getLatestUnderwriting: (id: string) =>
    request<UnderwritingResult | null>(`/loans/${id}/underwriting/latest`),
  /** Mirror of ``getLatestUnderwriting`` for the decision agent. */
  getLatestDecision: (id: string) =>
    request<DecisionResult | null>(`/loans/${id}/decision/latest`),
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
  /** Global search powering Cmd+K. Returns mixed loan + party hits
   *  (capped at 8 each, server-side). The palette calls this on
   *  every debounced keystroke ≥ 2 chars. */
  search: (q: string) =>
    request<SearchResults>(`/search?q=${encodeURIComponent(q)}`),
  // ---- Documents ----
  listDocuments: (loanId: string) =>
    request<LoanDocument[]>(`/loans/${loanId}/documents`),
  getDocumentDownloadUrl: (loanId: string, documentId: string) =>
    request<{
      url: string;
      filename: string;
      content_type: string;
      expires_in_seconds: number;
    }>(`/loans/${loanId}/documents/${documentId}/download-url`),
  transitionStage: (loanId: string, to_stage: LoanStage, reason: string) =>
    request<Loan>(`/loans/${loanId}/transition`, {
      method: "POST",
      body: JSON.stringify({ to_stage, reason }),
    }),
  getAllowedTransitions: (loanId: string) =>
    request<Record<string, string | null>>(`/loans/${loanId}/transitions`),
  getMaterialsStatus: (loanId: string) =>
    request<MaterialsStatus>(`/loans/${loanId}/materials/status`),
  /** Stage-based lock snapshot. Mirrors the backend's
   *  ``services.loan_locks`` policy — the UI uses it to hide
   *  agent / upload buttons before the user clicks. */
  getLockStatus: (loanId: string) =>
    request<LockStatus>(`/loans/${loanId}/locks`),
  /** Resolve a citation key (e.g. ``"property_address"``) back to
   *  the extraction it came from, with the source span the LLM
   *  read. Powers the side-panel hover preview on underwriting
   *  citation chips. */
  getCitation: (loanId: string, fieldName: string) =>
    request<Citation>(
      `/loans/${loanId}/citations/${encodeURIComponent(fieldName)}`,
    ),
  /** Read the singleton institution settings (lender contact +
   *  authorized officer + credit reporting agency). Used by the
   *  staff settings page and as the "is the lender configured"
   *  signal on the nav. */
  getInstitutionSettings: () =>
    request<InstitutionSettings>(`/settings/institution`),
  /** Patch one or more fields on the institution singleton. Empty
   *  strings clear a field; omitted fields stay unchanged. */
  updateInstitutionSettings: (body: InstitutionSettingsPatch) =>
    request<InstitutionSettings>(`/settings/institution`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  // ---- Staff + owner reassignment ----
  listStaffUsers: () =>
    request<StaffUser[]>(`/loans/staff/users`),
  setLoanOwner: (loanId: string, ownerId: string | null, reason: string) =>
    request<Loan>(`/loans/${loanId}/owner`, {
      method: "PATCH",
      body: JSON.stringify({ owner_id: ownerId, reason }),
    }),
  setAutonomy: (loanId: string, level: AutonomyLevel, reason: string) =>
    request<Loan>(`/loans/${loanId}/autonomy`, {
      method: "PATCH",
      body: JSON.stringify({ level, reason }),
    }),
  uploadDocument: async (loanId: string, file: File): Promise<UploadResult> => {
    // FormData with file payload — different from JSON requests, so we
    // hit fetch directly rather than the `request<T>()` helper (which
    // forces Content-Type: application/json). ``credentials: 'include'``
    // carries the staff session cookie so the backend can authenticate.
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_URL}/api/v1/loans/${loanId}/documents`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (res.status === 401) {
      redirectToLogin();
      throw new Error("Not authenticated");
    }
    if (!res.ok) throw new Error(`Upload ${res.status}: ${await res.text()}`);
    return (await res.json()) as UploadResult;
  },
  // ---- Eval ----
  getEvalSummary: () => request<EvalSummary>(`/eval/summary`),
  getEvalFields: () => request<EvalFieldRow[]>(`/eval/fields`),
  getEvalTrend: (days = 30) => request<EvalTrend>(`/eval/trend?days=${days}`),
  /** Confidence calibration + review queue + agent reliability + recent
   *  failures. Same backend table set as observability, sliced for the
   *  eval page's "is the AI actually working?" framing. */
  getEvalDiagnostics: () => request<EvalDiagnostics>(`/eval/diagnostics`),
  refreshDrift: () => request<EvalRefreshResult>(`/eval/refresh`, { method: "POST" }),
  refreshFairness: () =>
    request<{
      status: string;
      n_loans_decisioned: number;
      air: number | null;
      flag: "ok" | "watch" | "concern" | "insufficient_data";
      window_days: number;
    }>(`/eval/fairness/refresh`, { method: "POST" }),
  refreshPSI: () =>
    request<{
      status: string;
      features: {
        feature: string;
        psi: number;
        flag: "stable" | "minor" | "major";
        n_reference: number;
        n_current: number;
      }[];
      window_current_days: number;
      window_reference_days: number;
    }>(`/eval/psi/refresh`, { method: "POST" }),
  refreshRefusal: () =>
    request<{
      status: string;
      current_rate: number;
      baseline_rate: number;
      n_current: number;
      n_baseline: number;
      z_score: number | null;
      flag: "stable" | "spike" | "insufficient_data";
    }>(`/eval/refusal/refresh`, { method: "POST" }),
  getAgentEconomics: () =>
    request<AgentEconResponse>(`/eval/agent-economics`),
  // ---- Eval annotations ----
  /** List annotations on one trace row, newest first. Drives the
   *  "Existing annotations" section of LLMCallDrawer + AgentRunDrawer. */
  listAnnotations: (
    target_kind: AnnotationTargetKind,
    target_id: string,
  ) =>
    request<Annotation[]>(
      `/eval/annotations?target_kind=${encodeURIComponent(
        target_kind,
      )}&target_id=${encodeURIComponent(target_id)}`,
    ),
  /** Persist a verdict + optional note. bad/incorrect auto-spawn a
   *  review_task when the trace is linkable to a loan. */
  createAnnotation: (body: {
    target_kind: AnnotationTargetKind;
    target_id: string;
    verdict: AnnotationVerdict;
    note?: string | null;
  }) =>
    request<Annotation>(`/eval/annotations`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteAnnotation: (id: string) =>
    request<void>(`/eval/annotations/${id}`, { method: "DELETE" }),
  // ---- Eval regression diff ----
  /** Compare two LLM calls on stored metadata. Used by the
   *  "Compare to…" panel inside LLMCallDrawer. */
  diffLLMCalls: (a: string, b: string) =>
    request<LLMCallDiff>(
      `/eval/diff/llm-calls?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
    ),
  // ---- Observability ----
  getLLMObservability: (hours = 24) =>
    request<LLMSummary>(`/observability/llm?hours=${hours}`),
  getLLMCallDetail: (callId: string) =>
    request<LLMCallDetail>(`/observability/llm/${callId}`),
  getAgentsObservability: (hours = 24) =>
    request<AgentSummary>(`/observability/agents?hours=${hours}`),
  getAgentRunDetail: (runId: string) =>
    request<AgentRunDetail>(`/observability/agents/${runId}`),
  /** Recent 5xx errors. Default window is 7d because errors should
   *  be rare — a 24h view on a healthy install is usually empty. */
  getErrorsObservability: (hours = 168) =>
    request<InfrastructureErrorSummary>(`/observability/errors?hours=${hours}`),
  getErrorDetail: (errorId: string) =>
    request<InfrastructureErrorDetail>(`/observability/errors/${errorId}`),
  // ---- Staff auth -----------------------------------------------------
  /** Resolve the current staff user from the session cookie. 401 if
   *  not signed in — the ``request<T>`` helper handles the redirect
   *  to /login. Used by the AuthGate and the header user menu. */
  getStaffMe: () => request<StaffMe>(`/staff/auth/me`),
  /** Exchange email + password for a session cookie. Returns the
   *  user payload on success; throws on 401 with a generic message. */
  staffLogin: (email: string, password: string) =>
    request<StaffLoginResponse>(`/staff/auth/login`, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  /** Clear the cookie + revoke the JTI server-side. */
  staffLogout: () =>
    request<void>(`/staff/auth/logout`, { method: "POST" }),
  // ---- Safety dashboard -----------------------------------------------
  /** Top-of-page rollup for /safety. Drives the StatTiles, the
   *  severity histogram, the source-kind pie, and the pattern
   *  top-N. */
  getSafetySummary: (hours = 24, recentLimit = 25) =>
    request<SafetySummary>(
      `/safety/summary?hours=${hours}&recent_limit=${recentLimit}`,
    ),
  /** Filterable list of recent detections — the bottom-of-page
   *  table. ``severity`` / ``decision`` / ``source_kind`` are
   *  server-side filters. */
  listInjectionDetections: (params: {
    hours?: number;
    severity?: string;
    decision?: string;
    source_kind?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.hours) qs.set("hours", String(params.hours));
    if (params.severity) qs.set("severity", params.severity);
    if (params.decision) qs.set("decision", params.decision);
    if (params.source_kind) qs.set("source_kind", params.source_kind);
    if (params.limit) qs.set("limit", String(params.limit));
    return request<InjectionDetectionRow[]>(
      `/safety/detections?${qs.toString()}`,
    );
  },
  /** Drawer payload — full matched-patterns + Haiku critique +
   *  raw excerpt. */
  getInjectionDetectionDetail: (id: string) =>
    request<InjectionDetectionDetail>(`/safety/detections/${id}`),
  /** All-time detections for one loan — powers the loan-detail
   *  SafetyChip + the per-loan inspector. */
  getLoanInjectionDetections: (loanId: string) =>
    request<InjectionDetectionRow[]>(`/safety/loans/${loanId}/detections`),
  /** Output-side guardrail rollup — every agent run whose payload
   *  carried a guardrail_judgment from the constitutional judge. */
  getJudgmentSummary: (hours = 24, limit = 100) =>
    request<JudgmentSummary>(
      `/safety/judgments?hours=${hours}&limit=${limit}`,
    ),
  /** Static scenarios catalog — every robustness property the
   *  system pins, with the test that backs it. Doubles as
   *  audit documentation. */
  getSafetyScenarios: () =>
    request<ScenariosResponse>(`/safety/scenarios`),
  /** Per-task detail snapshot. ``details`` is task-specific —
   *  callers narrow the type at the use site. Renders the
   *  Phase 2 metric cards (confusion matrix, per-criterion bars,
   *  reliability diagram, per-pattern). */
  getTaskDetail: (taskName: string) =>
    request<TaskDetail>(`/eval/task-detail/${encodeURIComponent(taskName)}`),
  // ---- Prompts ----
  /** List every registered prompt with its current active version. */
  listPrompts: () => request<PromptSummary[]>(`/prompts`),
  /** Detail view for one prompt: full body + version history. */
  getPromptDetail: (identifier: string) =>
    request<PromptDetail>(`/prompts/${encodeURIComponent(identifier)}`),
  /** Fetch the code-default body. Powers the "Restore default" button. */
  getPromptDefault: (identifier: string) =>
    request<{ identifier: string; body: string }>(
      `/prompts/${encodeURIComponent(identifier)}/default`,
    ),
  /** Append a new version. ``activate=true`` makes it the runtime body. */
  createPromptVersion: (
    identifier: string,
    body: { body: string; change_note: string; activate: boolean },
  ) =>
    request<PromptVersion>(`/prompts/${encodeURIComponent(identifier)}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Switch the active flag to a previous version (rollback). */
  activatePromptVersion: (identifier: string, version: number) =>
    request<PromptVersion>(
      `/prompts/${encodeURIComponent(identifier)}/activate/${version}`,
      { method: "POST" },
    ),
  /** Ask the LLM gateway to rewrite the current body per a natural-
   *  language instruction. Read-only — the result is loaded into the
   *  editor; the user reviews and saves through the normal version
   *  flow, which keeps the audit trail honest. */
  rewritePrompt: (
    identifier: string,
    body: { current_body: string; instruction: string },
  ) =>
    request<PromptRewriteResult>(
      `/prompts/${encodeURIComponent(identifier)}/rewrite`,
      { method: "POST", body: JSON.stringify(body) },
    ),
};


// ---- Prompts (Phase: prompt management UI) ----

export interface PromptSummary {
  identifier: string;
  label: string;
  description: string;
  /** ``null`` when the identifier exists in the registry but has no
   *  DB row yet — UI shows "code default" in that case. */
  active_version: number | null;
  active_at: string | null;
  n_versions: number;
}

export interface PromptVersion {
  id: string;
  version: number;
  body: string;
  change_note: string | null;
  is_active: boolean;
  created_at: string;
  created_by_user_id: string | null;
}

export interface PromptDetail {
  identifier: string;
  label: string;
  description: string;
  /** The canonical code default — what "Restore default" snaps back to. */
  default_body: string;
  /** Newest first. Empty means we've never written a DB row for this
   *  identifier; ``default_body`` is what the runtime is using. */
  versions: PromptVersion[];
}

export interface PromptRewriteResult {
  /** Drop-in replacement body for the editor. */
  body: string;
  /** 1–3 sentence summary of what the rewrite changed and why.
   *  Shown next to the editor so the user can accept / reject
   *  without re-reading the whole body. */
  rationale: string;
}
