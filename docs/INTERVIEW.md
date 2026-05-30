# Interview talking points

A reference for the project owner when discussing Mkopo in interviews.
Not a script — pick whichever angle matches the conversation. Every
claim here is verifiable in the codebase; file paths are linked so
you can pull up source live.

If the conversation is short, prioritise §1 (one-paragraph elevator
pitch) and §3 (the four "interesting" stories). The rest is
preparation for follow-on questions.

---

## 1. One-paragraph elevator pitch

> Mkopo is an auditable multi-agent loan-origination system for
> private lenders. Three LangGraph agents — intake, underwriting,
> decision — chain end-to-end with explicit human-in-the-loop gates
> at the irreversible boundaries. The interesting parts are the
> guardrails: a deterministic Python rules engine has final say on
> credit decisions and overrides the LLM if they conflict; every
> input that produced a decision is cryptographically hashed so
> post-hoc tampering is detectable; the eval dashboard exposes
> seven production-side monitors (drift, calibration, fairness AIR,
> PSI, refusal-rate, agent economics, MMD on borrower-message
> embeddings) all backed by real backend functions with paired
> unit tests. The codebase has 286 tests, no `while True` anywhere
> in `mkopo/agents`, and every metric on every dashboard tile
> traces to a `file:function` documented in [`METRICS.md`](METRICS.md).

---

## 2. What this project is honestly NOT

State this upfront. It will come up; getting ahead of it
buys credibility.

- Not a regulatory product. The AAL fidelity eval checks the
  *structure* CFPB Circular 2022-03 demands; it does not certify
  the letters are legally compliant.
- Not multi-tenant.
- Not connected to real credit bureaus, real bank data, real MLS,
  or real e-signature. The agents treat borrower-supplied numbers
  as authoritative.
- Fairness AIR uses a SYNTHETIC protected-class signal (SHA-256
  bucketing of `loan_id`). Real deployment would attribute on
  HMDA-verified characteristics.

See [`SCOPE.md`](SCOPE.md) for the full list. Every "this looks
production-ready" item there is called out so a reviewer doesn't
have to guess.

---

## 3. The four "interesting" stories

Pick whichever maps to the role you're interviewing for.

### a) Decision integrity — *for ML engineering / model risk roles*

Every credit decision is stamped with a sha256 of the inputs that
produced it: documents, accepted extractions, parties, borrower
meta. The hash is computed by
[`services/materials_hash.py`](../api/mkopo/services/materials_hash.py)
and stored on `agent_runs.payload.materials_hash` when the decision
agent's `persist` node fires.

If any input changes after the decision lands — borrower edits an
income field, a doc gets replaced, a guarantor is added — the
current hash diverges from the stamped one and forward stage
transitions are blocked until the decision agent is re-run. Visible
to operators as the `MaterialsFlow` graph turning red.

**Why it matters**: a regulator can verify exactly what inputs
produced a verdict, and the system detects post-hoc tampering with
no schema changes. This is the cryptographic basis for SR 11-7-style
challenger evaluation.

**Where**: `services/materials_hash.py`, `agents/decision.py`'s
persist node, the stage-transition guard in `services/loans.py`.

### b) Rules engine overrides the LLM — *for AI safety roles*

The decision agent's LLM picks `approve` / `conditional` / `decline`,
but the deterministic rules engine has computed the same `risk_flags`
independently. In
[`agents/decision.py`](../api/mkopo/agents/decision.py) (search for
`decision_override_to_decline`), if the LLM chose `approve` but
`has_blocking_failure(flags)` is true, the server rewrites the path
to `decline` before persistence and writes an audit event recording
the override.

The LLM cannot ship a verdict the rules don't support. The audit
record makes the override forensically visible.

This pattern shows up twice more: the constitutional judge with
Self-Refine loop (bounded by `MAX_VALIDATION_ATTEMPTS = 3`) inside
`agents/guardrails.py`, and the pre-judge `forbidden_substrings`
fast-fail that catches `[LENDER NAME]` / `[DATE]` drift before
paying for the judge LLM round-trip.

**Why it matters**: model-risk frameworks (SR 11-7, NIST AI 600-1)
demand effective challenge. This is what "effective challenge" looks
like in code rather than in slides.

### c) Eval dashboard — *for platform / observability roles*

Twelve golden-set tasks + seven production monitors all write to
one `task_runs` table that powers `/eval`. Every dashboard card
traces to a real backend function listed in
[`METRICS.md`](METRICS.md) — no hardcoded demo numbers.

The production monitors run on the arq scheduler:

| Time (UTC) | Monitor | What it pins |
|---|---|---|
| 3:00 | drift | Per-field accuracy from staff overrides |
| 3:30 | calibration | ECE + Brier (Guo et al. 2017) |
| 3:45 | fairness | AIR (EEOC four-fifths) |
| 3:50 | PSI | Population Stability Index (Siddiqi 2017) |
| 3:52 | refusal | Binomial z-test for refusal-rate spike |
| 3:55 | agent economics | $/run + p95 latency per agent |
| 3:58 | prompt drift | MMD² on inbound borrower-message embeddings |
| 4:00 | golden eval | The full eval suite re-run |

Each math-heavy monitor has paired unit tests:
`tests/test_psi_math.py`, `tests/test_calibration_math.py`,
`tests/test_fairness_math.py`, `tests/test_refusal_math.py`,
`tests/test_prompt_drift_math.py`. A sign-flip in any of those
formulas would fail a test before it could corrupt persisted
numbers silently.

**Why it matters**: if you can't verify a metric you can't ship it.
This is what "trust the dashboard" looks like in practice.

### d) Re-auth on the chat tool path — *for application security roles*

This was a finding from the project's own readiness audit, and
fixing it is documented in commit history. The REST endpoints for
withdraw + erasure protect themselves with a fresh-auth challenge
(`POST /borrower-auth/me/challenge` → single-use token consumed by
the destructive call). The chat agent exposes the SAME actions as
tools — and without an equivalent gate, a stolen session cookie
could walk an account off the platform via the chat path,
bypassing the REST gate entirely.

The fix added a `requires_reauth: bool` flag to the `Tool`
dataclass, marked `withdraw_application` and `request_erasure`
with `requires_reauth=True`, taught the chat loop to demand a
`challenge_token` on the `tool_resume` payload for those tools,
and wired the inline password prompt into the existing
`ConfirmModal`. A test invariant in
`tests/test_tool_trajectory.py::test_borrower_irreversible_tools_require_reauth`
guards against a silent regression.

**Why it matters**: It's an example of threat-model reasoning
applied to the agent boundary — the chat path is a parallel
attack surface to REST, and protecting one without the other is
strictly worse than no auth at all because the asymmetry creates
plausible-deniability for the gap.

---

## 4. Likely follow-on questions

### "How would you scale this?"

Two answers, both honest:

1. **The shape that scales**: stateless API workers; LangGraph
   checkpoints in Postgres (so an agent run can resume on a
   different worker); single LLM gateway (one place to swap
   providers, account for tokens, gate structured outputs); the
   materials hash recomputes on demand. Horizontal scale on the
   API is `replicas=N`.
2. **The single biggest open architectural debt**: the
   orchestrator runs inline. `maybe_chain_after_underwriting`
   calls `_run_decision_agent` inside the request's async stack,
   which pins a worker for the full decision-agent duration
   (10–30s). At scale this needs an `arq` enqueue (the dependency
   is already there); the hook would enqueue rather than `await`.

[`ARCHITECTURE.md § Scalability`](ARCHITECTURE.md#scalability)
walks the same trade-offs.

### "How do you handle LLM hallucinations?"

[`SAFETY.md`](SAFETY.md) is the long answer. The short answer in
five layers:

1. **Symbolic guardrails** — Pydantic-typed `call_structured` is
   the ONLY path to the Anthropic API. The model literally cannot
   return free-form text from anywhere in `mkopo/agents`.
2. **Server-side path override** on the decision agent — the rules
   engine wins on conflict, with an audit event recording the
   override.
3. **RAG-grounded summaries** — the underwriting summary cites
   extracted-field keys; each chip is clickable, opening a side
   drawer with the exact document quote. "Did it hallucinate?"
   has a one-click answer.
4. **Constitutional LLM-as-judge with Self-Refine loop** — every
   LLM-drafted artifact gets a separate-LLM judge pass against an
   explicit constitution. Bounded by `MAX_VALIDATION_ATTEMPTS=3`.
   Pre-judge fast-fail via `forbidden_substrings` catches the
   common drift patterns without paying for the judge.
5. **Input-layer prompt-injection detector** — hybrid regex
   catalog + Haiku second-pass on every document upload, chat
   message, and inbound text input. Fail-closed on high-severity
   matches.

What's NOT there (and called out in `SAFETY.md`): production-grade
red team, counterfactual fairness analysis beyond AIR, model card,
ongoing challenger evaluation.

### "What about LLM cost?"

Three failure modes for LLM cost in an agentic system: unbounded
loops, retries that compound, agent chains that redo each other's
work. Each one is bounded:

- **No `while True`** anywhere in `mkopo/agents`. Every loop is
  bounded `for range()` or LangGraph conditional-edge gated by an
  attempt counter.
- Cap table in
  [`ARCHITECTURE.md § Cost + loop bounds`](ARCHITECTURE.md#cost--loop-bounds):
  chat tool-call rounds capped at 6; Self-Refine loop at 3 attempts;
  gateway schema retry capped at 1.
- Worst-case LLM call counts are precomputed per agent path —
  decision (decline path, max retries) tops out at 12 calls.
- Every `llm_calls` row records `cost_input_usd + cost_output_usd`,
  surfaced per-agent on the `/observability` page.

What's NOT there: a per-loan hard cap that aborts a run if it
exceeds budget. A one-line check inside
`LLMGateway._record_call` would be the right shape; it's a
production gap, flagged in ARCHITECTURE.md.

### "Why LangGraph and not LangChain / a custom orchestrator?"

LangGraph gives you durable checkpoints via
`AsyncPostgresSaver` (so a HITL pause survives worker recycling)
plus a typed node DAG for each agent. Both matter for the audit
story — every node execution writes one `agent_steps` row, the
trace tree is reconstructible, and the borrower-side chat agent
uses the same checkpointing for its destructive-confirmation
interrupts.

What we don't use: LangChain's broader chain abstractions, agents
framework, or memory abstractions. The LLM gateway
(`mkopo/llm_gateway.py`) is the only place the SDK is touched —
swapping providers (or evaluating a challenger model) is a
one-file change.

### "What's the dev workflow?"

`README.md § Quick start (local)` walks the six-env-var setup +
the one-command `uv run python scripts/seed.py` that puts
11 demo loans in the pipeline. The seed renders real PDFs (not
text blobs) so the intake extractor exercises its actual pypdf
round-trip, not a debug shortcut.

286 tests pass; `pre-commit` is wired with ruff + Prettier. The
arq worker can be left running in a separate terminal for the
cron-scheduled monitors.

For end-to-end click-throughs of every flow, see
[`TESTING_GUIDE.md`](../TESTING_GUIDE.md) — ~95 minutes for the
full pass, time-stamped per flow.

### "What was the hardest decision?"

The materials hash schema. Naive options were both wrong:
- Per-loan version number → doesn't catch document-content edits
- sha256 of every column → too noisy, includes
  reviewer-updated metadata that shouldn't invalidate decisions

The pattern that worked: a `canonical_json` projection of just
the inputs that the rules engine reads (documents by
`content_hash`, extractions by `id+value`, parties by `party_id`,
selected meta keys) — then sha256 that. The projection is itself
a deliberate API surface; changing it requires re-running
existing decisions but doesn't require a schema migration.

Lives in [`services/materials_hash.py`](../api/mkopo/services/materials_hash.py).

### "Show me the code you're most proud of"

Three honest candidates:

- The **decision agent's server-side override + Self-Refine loop**
  in `agents/decision.py` + `agents/guardrails.py`. Belt-and-
  suspenders safety pattern with bounded retries.
- The **prompt registry's ContextVar-based version stamping** in
  `services/prompts.py` + `llm_gateway.py`. Lets every
  `llm_calls` row trace back to the exact prompt body that
  produced it without threading the version id through every
  helper.
- The **eval-task interface** in `evals/tasks/_base.py`. Adding
  a new metric is one YAML file under `golden_sets/` + one
  `predict + score + aggregate` class. The dashboard's
  task-detail page picks it up automatically because everything
  reads from `task_runs.details`.

---

## 5. Things to NOT oversell

When the interviewer pokes, agree honestly:

- The fairness AIR is real math; the protected-class signal it
  runs on is synthetic.
- The scenarios catalog on `/safety` claims it's verified by
  pytest, but the manifest is static — a test failure does NOT
  flip the card. Aspirational claim.
- The MMD monitor is fully implemented and unit-tested, but has
  no UI card yet. Persists to `task_runs`; you can see it via
  the API.
- Two p95 implementations exist for valid reasons (nearest-rank
  for tail-latency tiles, linear-interpolation for cost analytics);
  small-n numbers can diverge.
- The orchestrator runs inline (no queue). Single biggest piece
  of architectural debt.
- The eval gate exit-codes on golden-set failure, but doesn't
  gate prompt promotion in CI yet — the promotion endpoint
  exists and is wired, the gating is advisory.

Saying these out loud builds more credibility than the alternative.

---

## 6. Document map for live walkthroughs

If the interviewer asks "show me X", these are the files to open:

| Topic | File |
|---|---|
| Headline architecture diagram | [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) (mermaid blocks) |
| What's in / out of scope | [`docs/SCOPE.md`](SCOPE.md) |
| Every metric's formula + source | [`docs/METRICS.md`](METRICS.md) |
| Acronym definitions | [`docs/GLOSSARY.md`](GLOSSARY.md) |
| End-to-end click-throughs | [`TESTING_GUIDE.md`](../TESTING_GUIDE.md) |
| Hallucination-mitigation audit | [`docs/SAFETY.md`](SAFETY.md) |
| Sample workflow sequence diagrams | [`docs/WORKFLOWS.md`](WORKFLOWS.md) |
| Eval surface status + roadmap | [`docs/EVAL_PLAN.md`](EVAL_PLAN.md) |
| The decision agent | [`api/mkopo/agents/decision.py`](../api/mkopo/agents/decision.py) |
| The LLM gateway | [`api/mkopo/llm_gateway.py`](../api/mkopo/llm_gateway.py) |
| The materials hash | [`api/mkopo/services/materials_hash.py`](../api/mkopo/services/materials_hash.py) |
| MMD math + tests | [`api/mkopo/services/prompt_drift.py`](../api/mkopo/services/prompt_drift.py) + [`api/tests/test_prompt_drift_math.py`](../api/tests/test_prompt_drift_math.py) |
| The eval dashboard cards | [`web/app/eval/cards/`](../web/app/eval/cards/) |
| The in-app glossary surface | [`web/app/help/page.tsx`](../web/app/help/page.tsx) |

The `/help` page in the running app is the same content as
GLOSSARY.md + METRICS.md, rendered with deep-anchor links so a
tooltip on the dashboard can drop you onto the right definition.
