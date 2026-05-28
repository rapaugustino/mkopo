# Glossary

Plain-English definitions for every acronym and domain term used in
the codebase or UI. Organised by topic. The "**Where**" line points
at one representative call site so you can grep from here.

This file is the source of truth — the in-app `/help` page renders
the same content via a stable schema. If you add a new metric or
domain term to the UI, add it here too.

---

## Lending domain

### UW — Underwriting
The step where a credit risk decision is built from the loan
package. In this codebase, it's the `underwriting` LangGraph agent
that consumes extracted facts, runs the rules engine, computes
KPIs (LTV, DTI, DSCR), and emits an `UnderwritingSummary`.
- **Where**: `api/mkopo/agents/underwriting.py`, the *Underwriting*
  tab on a loan detail page.

### AAL — Adverse Action Letter (a.k.a. Adverse Action Notice / AAN)
A written explanation a lender must send when an application is
declined, citing the *principal reasons*. Required by ECOA
(Regulation B, 12 CFR §1002.9) and FCRA. In this codebase the
AAL is drafted by the `decision` agent and gated by the
`aal_fidelity` eval.
- **Where**: `api/mkopo/agents/decision.py`, AAL fidelity card on
  `/eval`.

### ECOA — Equal Credit Opportunity Act (Regulation B)
US federal law prohibiting credit discrimination on protected
bases (race, sex, age, etc.) and requiring an adverse-action
notice with specific reasons. The AAL fidelity eval checks the
generated letter satisfies §1002.9(b) requirements.
- **Where**: cited in `api/evals/tasks/aal_fidelity.py`.

### CFPB — Consumer Financial Protection Bureau
US regulator for consumer financial products. CFPB Circular
2022-03 (specific reasons in AALs) and 2023-09 (AI-specific
guidance) drive the AAL fidelity rubric.
- **Where**: cited in `api/evals/tasks/aal_fidelity.py`,
  `api/mkopo/services/fairness.py`.

### FCRA — Fair Credit Reporting Act
US law governing how credit-report-based adverse actions must be
disclosed. Not deeply implemented here — flagged in `SCOPE.md`.

### HMDA — Home Mortgage Disclosure Act
US law requiring lenders to report mortgage origination data. The
fairness card surfaces approval-rate parity (AIR) which is the
metric HMDA examiners look at.

### SR 11-7 — Federal Reserve supervisory letter on model risk
The de facto bank-MRM standard in the US. Requires effective
challenge, ongoing monitoring, documentation. We cite it in
`decision_verdict.py` (challenger eval gating) and
`aal_fidelity.py` (documentation discipline).

### LTV — Loan-to-Value ratio
`loan_amount / appraised_value`. A core mortgage risk metric.

### DTI — Debt-to-Income ratio
`monthly_debts / monthly_income`. Consumer credit risk metric.

### DSCR — Debt Service Coverage Ratio
`net_operating_income / debt_service`. Commercial real-estate
metric the UW agent computes for income-property loans.

### NOI — Net Operating Income
Rental income minus operating expenses for an investment
property. Input to DSCR.

### KYC / AML — Know Your Customer / Anti-Money Laundering
Identity-verification and transaction-monitoring obligations. The
demo does **not** implement these — see `SCOPE.md`.

---

## Statistical and ML metrics

### PSI — Population Stability Index
Distribution-shift metric. Compares the share of a population in
each binned region between a *reference* window and a *current*
window:
$$\text{PSI} = \sum_i (p_i - q_i) \ln(p_i / q_i)$$
where $p_i$ is the current share, $q_i$ the reference share.
Bands (Siddiqi 2017): <0.10 stable, 0.10–0.25 minor shift,
≥0.25 major shift.
- **Where**: `api/mkopo/services/psi.py`, PSI card on `/eval`.

### AIR — Adverse Impact Ratio
The *four-fifths rule* from the EEOC Uniform Guidelines (1978):
$$\text{AIR} = \frac{\min_g(\text{approval rate}_g)}{\max_g(\text{approval rate}_g)}$$
A ratio below 0.80 historically triggers regulator scrutiny. In
this demo the protected-class signal is **synthetic** (SHA-256
based bucketing) — see the card's own disclosure.
- **Where**: `api/mkopo/services/fairness.py`, Fairness card on
  `/eval`.

### ECE — Expected Calibration Error
Calibration metric (Naeini et al. 2015, Guo et al. 2017): weighted
gap between predicted confidence and empirical accuracy per bin.
$$\text{ECE} = \sum_{m=1}^M \frac{|B_m|}{n}\left|\text{acc}(B_m) - \text{conf}(B_m)\right|$$
Lower is better. Bands (Guo et al.): <0.05 well calibrated,
0.05–0.10 moderate, ≥0.10 poor.
- **Where**: `api/mkopo/services/calibration.py`, Calibration card
  on `/eval`.

### Brier score
A strictly proper scoring rule (Brier 1950) — mean squared error
between predicted probability and outcome. Used alongside ECE
because ECE is bin-dependent and can hide miscalibration.
$$\text{Brier} = \frac{1}{n} \sum_i (c_i - y_i)^2$$

### MMD — Maximum Mean Discrepancy
Distribution-shift test for high-dimensional data (Gretton et al.
2012). We compute MMD² between current and reference embeddings
of system prompts to detect prompt-template drift.
- **Where**: `api/mkopo/services/prompt_drift.py`. Computation
  only — no UI card yet (flagged in `SCOPE.md`).

### F1 / precision / recall
- Precision = TP / (TP + FP) — of what we labelled positive, how
  much was right
- Recall = TP / (TP + FN) — of all real positives, how much we
  caught
- F1 = 2·P·R / (P + R) — harmonic mean
- *Macro-F1* = unweighted mean of per-class F1 (treats classes
  equally regardless of support)
- **Where**: Decision verdict card on `/eval`.

### Confusion matrix
Per-class TP/FP/TN/FN table. We render it for the decision agent
(approve / decline / refer-to-human) to make per-class failure
modes visible.

### Refusal-rate spike test
Binomial proportion test: compute current-vs-baseline refusal
rate as a z-score:
$$z = \frac{p_{\text{cur}} - p_{\text{base}}}{\sqrt{p_{\text{base}}(1-p_{\text{base}})/n_{\text{cur}}}}$$
$|z| \geq 2$ flagged as spike.
- **Where**: `api/mkopo/services/refusal.py`.

### RAG — Retrieval-Augmented Generation
Generation pattern that retrieves text chunks from a corpus and
includes them in the LLM context. We use it for "Ask the file"
inside the UW workspace.

### RAGAS
"RAG Assessment" framework (Es et al. 2024, arXiv:2309.15217).
We borrow its *faithfulness* metric (fraction of claims grounded
in retrieved context) for the UW groundedness eval — adapted to
work without a vector store (we judge against the source docs
directly).
- **Where**: `api/evals/tasks/uw_groundedness.py`.

### LLM-as-judge
Pattern of using an LLM to score outputs of another LLM. We use
it for: the UW groundedness eval, the input-prompt-injection
detector, the constitutional judge on decisions, and the eval
"rewrite with AI" affordance. Concerns: judge bias, prompt
sensitivity. Mitigation: judge model is *pinned* (won't drift on
upgrades), and the judge prompt is versioned.

### LLM-as-judge accuracy
For evals where the LLM is the scorer, we report *judge accuracy*
— how often the judge classified into the expected band. This
is honesty: we're not claiming the underlying probability is
perfectly calibrated, only that the classification is reliable.

### Embedding
A dense numeric vector representing the meaning of a piece of
text. We use OpenAI's `text-embedding-3-small` for document
chunks and `text-embedding-3-large` for the prompt-drift monitor.

### p50 / p95 / p99 latency
The 50th / 95th / 99th percentile of a latency distribution. p95
is the standard "tail" metric for user-facing latency. **Note**:
this codebase has two p95 implementations — nearest-rank in
`api/mkopo/routers/evals.py` and linear-interpolation in
`api/mkopo/services/agent_economics.py`. Numbers can differ on
small n. Both conventions are valid; standardising is a
follow-up.

---

## AI safety and governance

### NIST AI RMF — AI Risk Management Framework
Voluntary US framework (NIST AI 100-1, 2023) for AI risk
governance. Organised around *Govern, Map, Measure, Manage*.

### NIST AI 600-1 — Generative AI Profile
A profile of the RMF (NIST AI 600-1, 2024) tailored to GenAI.
Defines twelve risk categories. We map our eval cards onto five
of them via the `NISTBadge` widget: Confabulation, Harmful Bias,
Information Security, Information Integrity, Value Chain.
- **Where**: `web/app/eval/cards/NISTBadge.tsx`.

### TEVV — Test, Evaluation, Validation, Verification
NIST's umbrella term for what the `/eval` dashboard does.

### HITL — Human-In-The-Loop
Workflow pattern where the agent pauses for human review at a
decision boundary. In this codebase the intake agent pauses for
extraction confirmation via a LangGraph `interrupt`. HITL pauses
are visible as `interrupted` in the agent reliability rollup.

### Prompt injection
Class of attack where untrusted input (a borrower's document,
chat message, URL) contains instructions that try to override the
agent's system prompt. The input-layer detector catches the
common patterns (instruction override, role play, exfiltration).
- **Where**: `api/mkopo/safety/injection.py`, Safety page.

### Constitutional judge
Per-decision LLM-as-judge that scores whether the agent's output
satisfies a set of principles (no PII leakage, no contradicting
rule engine, etc.). If it fails, the decision is held back for
human review.
- **Where**: `api/mkopo/safety/constitutional.py`.

### Fail-closed
When in doubt, deny. The injection detector and the stage machine
are both fail-closed: an unrecognised input or invalid transition
is blocked rather than passed through.

### Red teaming
Structured adversarial testing. The
`api/evals/tasks/adversarial_injection.py` eval is a starter
red-team suite. A production red-team is out of scope — see
`SCOPE.md`.

### Stage machine
The state graph that controls which loan stage transitions are
legal. Encoded in `api/mkopo/services/stages.py` as a directed
graph; transitions outside it raise a `StageMachineError`.

### Stage lock
Per-stage rule about which agents and which document mutations
are permitted. Encoded in `api/mkopo/services/stage_locks.py`.

---

## Technical / platform

### SSE — Server-Sent Events
HTTP streaming protocol used to push agent-progress updates from
the FastAPI backend to the browser as they happen.

### RBAC — Role-Based Access Control
Tool catalog is filtered by user role (`staff` vs `borrower`) so
e.g. a borrower can't invoke `update_loan_field` on someone
else's loan.

### JWT — JSON Web Token
Stateless auth token format. We use it with server-side
revocation (`jti` denylist) so logouts and password changes can
invalidate live tokens immediately.

### Magic link
Single-use sign-in link sent to the user's email. Implemented for
borrower auth as an alternative to password.

### ORM — Object-Relational Mapping
SQLAlchemy 2.x `Mapped[...]` style classes that mirror Postgres
tables.

### Migration
A versioned schema-change script. We use Alembic; migrations
live in `api/alembic/versions/`.

### LLM gateway
Single chokepoint (`api/mkopo/llm/gateway.py`) through which every
LLM call flows. Writes to `llm_calls` and `agent_steps` for
observability + cost accounting.

### thread_id
Identifier propagated via Python `ContextVar` that ties every
LLM call inside one agent run together. Used in the trace tree
and in the per-agent economics join.

### parent_step_id
Foreign key on `llm_calls` that builds the trace tree: a call's
parent is the agent step it was made from.

### Materials hash
SHA-256 of the inputs that fed a decision (loan snapshot + rule
results + extraction). Stored alongside the verdict so we can
detect post-hoc tampering / drift in decision inputs.

### Golden set / golden fixture
A YAML file under `api/evals/golden_sets/` that defines a labelled
input-output pair for an eval task. Frozen ground truth.

### Drift monitor
A scheduled job that pulls recent extractions out of the review
queue and writes per-field `production` accuracy task_runs. See
`api/mkopo/services/drift.py`.

### task_runs / llm_calls / agent_steps
Three Postgres tables:
- `llm_calls` — every LLM call (tokens, cost, latency, schema)
- `agent_steps` — every LangGraph node execution (parent of LLM
  calls)
- `task_runs` — every eval task result (golden or production)
