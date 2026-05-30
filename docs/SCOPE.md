# Scope and Limits

This is a **demo of agentic-workflow patterns** for loan
origination — an interview-defensibility project, not a
production lending system. This file is the honest map of what
that means: what we built well, what we left out, and where a
reader of the code should be skeptical.

The structure is deliberate. Read top-to-bottom before evaluating
any single metric in the UI — context first, then numbers.

---

## What this project demonstrates

The demo is built around three competencies a lending platform
needs and that an agentic system makes interesting:

### 1. Agentic workflow

- Multi-agent LangGraph pipelines (intake → underwriting →
  decision) with explicit stage transitions and a human-in-the-
  loop interrupt for extraction approval
- Tool-using chat agents for borrower self-service and staff
  triage, with role-based tool filtering
- Pre-flight gates so agents don't burn tokens on impossible
  runs (e.g. underwriting without an approved extraction)
- Stage machine + stage locks: the only legal transitions and
  document mutations per stage are encoded as data
- Borrower portal with multi-step application wizard
- Re-authentication for sensitive operations (withdraw,
  erasure)

### 2. Evaluation harness

The eval dashboard is the centrepiece. It mixes three classes of
signal:

**Golden gate (offline, labelled fixtures)**
- Extraction field accuracy (per field, n=10–40 per field)
- Decision verdict — macro-F1 + per-class confusion matrix
- AAL fidelity — 4 binary CFPB / ECOA criteria
- Intake email — 4 binary plain-language criteria
- Adversarial injection coverage — every red-team pattern
  blocked
- UW summary groundedness — RAGAS-style faithfulness via a
  pinned judge LLM
- Tool-call accuracy — borrower chat tool selection

**Production drift (online, computed from live data)**
- Per-field extraction accuracy, computed from staff overrides
  in the review queue
- Confidence calibration — ECE + Brier
- Fairness — Adverse Impact Ratio (four-fifths rule)
- PSI — Population Stability Index on input features
- Refusal-rate spike — binomial z-test
- Per-agent economics — $/run + p95 latency joined by
  `thread_id`

**Operations**
- Agent reliability (success / interrupted / failed counts)
- Recent failures
- Review queue throughput

Every number on the dashboard is computed by a real backend
function — none are hardcoded for demo purposes. See
`docs/METRICS.md` for the formula and source file behind each.

### 3. Observability

- Every LLM call is persisted to `llm_calls` with tokens, cost,
  latency, schema, model ID, system-prompt hash
- Every LangGraph node execution is persisted to `agent_steps`
  with parent / child links
- `thread_id` is propagated through a Python `ContextVar` so
  every LLM call inside one run is joinable
- Trace tree on the loan detail page reconstructs the parent /
  child graph
- LLM call detail drawer shows tokens, cost, retries, schema
  validation outcome

---

## What this project does NOT cover

If you're considering this code as a starting point for anything
real, be aware of all of the following.

### Regulatory and compliance

- **No regulatory certification.** The AAL fidelity eval checks
  the *structure* CFPB Circular 2022-03 demands — principal
  reasons surfaced, friendly labels, no rule-IDs leaked into
  the body. It does NOT certify the letters are
  legally compliant. A regulatory attorney must review before
  any AAL is sent to a real applicant.
- **No fair-lending certification.** The AIR / four-fifths
  calculation is real, but the protected-class signal here is
  **synthetic** — a SHA-256 bucketing of `loan_id`. In a real
  deployment you'd attribute approvals to a verified protected
  characteristic, which raises its own data-collection and
  storage compliance questions (HMDA, ECOA Reg B §1002.5(b)).
- **No FCRA-grade adverse-action workflow.** Credit-bureau
  adverse actions have additional requirements (credit-report
  copies on request, score disclosure) that we don't implement.
- **No specific jurisdictional compliance.** The scaffolding is
  US-style — CFPB / ECOA / FCRA conventions. The codename
  "Mkopo" is Swahili (East African) — that's branding, not a
  jurisdiction claim. Any actual deployment needs a
  jurisdiction-specific regulatory review.
- **No KYC / AML.** No identity verification, sanctions
  screening, BSA monitoring. Real lenders bring a CIP pipeline.

### Production hardening

- **No infrastructure-as-code.** The codebase runs on Postgres +
  Redis via docker-compose locally; production runtime would
  need infra automation (Terraform / Pulumi), secrets
  management (Vault / AWS Secrets Manager), CD pipelines.
- **No multi-tenancy.** One institution per deployment. Real
  lenders need tenant isolation at the data layer.
- **No data-residency controls.** Postgres rows are not tagged
  by region; nothing routes by user locale.
- **No production-grade RBAC matrix.** We have staff vs
  borrower; production needs underwriter / processor / closer /
  loan-officer / compliance / admin roles with audit logs of
  permission grants.
- **No proper PII handling.** Seed data is fabricated. There is
  no field-level encryption, no GDPR-style "right to
  erasure" workflow beyond the borrower's account-deletion
  endpoint, no consent ledger.
- **No SOC2 / ISO 27001 / NIST 800-53 controls.** A real lender
  needs documented evidence trails; ours stop at the audit
  table and the LLM call log.

### Model risk

- **Pinned judges are still subject to judge bias.** The
  UW-groundedness eval uses `claude-opus-4-5` as judge; that
  judge has its own training distribution and its own
  failure modes. SR 11-7 expects ongoing challenger evaluation;
  we have only the golden fixtures.
- **No production-grade red team.** The
  `adversarial_injection` eval is a starter pattern catalog
  with ~20 patterns. A real red team — paid researchers,
  competition-style elicitation, jailbreaks — is out of scope.
- **No model card.** We pin the model identifier
  (`claude-sonnet-4-6` etc.) in the LLM gateway but don't ship
  a NIST-style model card with capabilities, limitations,
  evaluation history.
- **No counterfactual fairness analysis.** AIR / four-fifths
  catches gross approval-rate disparity but not subtler
  unfairness (e.g. equal approval rate, but with materially
  different terms — pricing fairness).

### Data integrations

- **No real credit bureau integration.** FICO / Experian /
  Equifax / TransUnion pulls do not happen. The agent treats
  credit scores from the borrower's input as authoritative.
- **No real bank-data integration.** No Plaid / Finicity feeds.
- **No real property data.** Appraisals are PDFs uploaded
  manually; no MLS, no county-recorder integration.
- **No real document OCR.** We use PyMuPDF for text extraction;
  scanned PDFs without an OCR layer produce empty text. A real
  pipeline needs Textract / Document AI.
- **No e-signature integration.** No DocuSign / Adobe Sign.

### Things in the codebase that look complete but aren't

To save someone reading the code a frustrating discovery:

- **Embedding-distribution prompt drift (MMD)** — the
  computation in `api/mkopo/services/prompt_drift.py` is real
  (Gretton et al. 2012 formulation) but currently has **no UI
  card**. It measures distribution drift on inbound borrower
  messages (NOT on system-prompt embeddings — that wording in
  earlier docs was wrong; corrected May 2026). Results are
  persisted to `task_runs` with prefix `prompt_drift.` and
  excluded from the trend chart by `_DERIVED_METRIC_PREFIXES`
  in `routers/evals/_shared.py`. You can see them via the API
  but not on the dashboard.
- **Scenarios catalog "verified by" links** — each card in
  `/safety` claims it is verified by a specific pytest, but
  the manifest is static. A test failure does NOT actually
  flip the card to a regression banner. Aspirational claim;
  honest doc-as-code would wire pytest output to the
  manifest.
- **Per-agent economics cost estimate** — the Safety page's
  Haiku-judge cost is a flat `n × $0.001` estimate, not the
  actual `LLMCall.cost_input_usd`. Conservative and called out
  in code comments. The Observability page's cost figure is
  from real cost columns.
- **AIR synthetic class** — already disclosed in the card UI
  itself.
- **Three p95 implementations** — `routers/evals/_shared.py`
  and `routers/observability.py` both use nearest-rank;
  `services/agent_economics.py` uses linear-interpolation. All
  three are valid p95 conventions; the numbers can differ on
  small n.

---

## Where to look for what

| Concern | Best place to look |
|---|---|
| Agent definitions | `api/mkopo/agents/` |
| Tool catalogs | `api/mkopo/agents/tools/` |
| Eval task implementations | `api/evals/tasks/` |
| Production monitors | `api/mkopo/services/` (drift, calibration, fairness, psi, refusal, agent_economics, prompt_drift) |
| LLM gateway | `api/mkopo/llm/gateway.py` |
| Safety guardrails | `api/mkopo/safety/` |
| UI cards explaining each metric | `web/app/eval/cards/` |
| Metric formulas in human terms | `docs/METRICS.md` |
| Acronyms | `docs/GLOSSARY.md` |
| Architecture overview | `docs/ARCHITECTURE.md` |
| Workflow overview | `docs/WORKFLOWS.md` |
| Safety surface overview | `docs/SAFETY.md` |
| Eval surface overview | `docs/EVAL_PLAN.md` |

---

## Reading the dashboards skeptically

If you're being asked to defend a number on the dashboard, the
order to walk through it is:

1. **What does the label claim?** Read the tooltip.
2. **What's the backend function?** Open `docs/METRICS.md` and
   find the entry — it points at `file:function`.
3. **What's the data source?** Almost always a Postgres table:
   `task_runs`, `llm_calls`, `agent_steps`, `agent_runs`, or
   the per-loan tables. Inspect rows directly via psql.
4. **What window?** Window is per-monitor, not a global default.
   Calibration 30d, PSI 30d current vs ~90d reference, Fairness
   90d, Refusal 7d current (z-tested against baseline),
   Prompt-drift 7d vs 30d reference (with 7d gap). LLM tiles
   cascade 24h → 7d → all-time depending on data availability.
   Constants live alongside each monitor in
   `services/<name>.py` — search for `_WINDOW_DAYS` /
   `_CURRENT_DAYS`.
5. **What threshold or band?** Documented in the corresponding
   card source and in METRICS.md.

If a number ever looks wrong, the answer is almost always one of:
- The window is empty (fresh DB, just-ran-once)
- The eval task hasn't been added to `_DERIVED_METRIC_PREFIXES`
  in `routers/evals/_shared.py` and is being averaged into
  headline accuracy when it shouldn't be
- The cron sweep hasn't run yet (drift monitor at 3 AM UTC,
  golden eval at 4 AM UTC)

None of those are "the math is wrong" — they're data-completeness
issues.
