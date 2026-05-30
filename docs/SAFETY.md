# Safety + hallucination mitigation

A regulated lender cannot ship "the model said so" as a justification.
This document inventories — honestly — which hallucination-mitigation
techniques are in the codebase, where they live, and which are *not*
present but would matter for production.

The audit follows the standard taxonomy: **symbolic guardrails**
(deterministic checks the model can't talk its way past), **runtime
guardrails** (steering the model during execution), **retrieval-
augmented generation** (grounding outputs in real source documents),
**semantic tool selection** (constraining what the model can do),
and **multi-agent validation** (independent re-evaluation).

---

## 1. Symbolic guardrails (deterministic, model-can't-bypass)

### Pydantic structured outputs on every LLM call

``llm_gateway.py``'s ``call_structured`` is the only path to the
Anthropic API in the codebase. It requires a Pydantic schema; the
model is forced into tool-use mode where the response must validate
against that schema. Free-form text generation is not exposed.

Implication: the model cannot return a verdict that omits a required
field, supply a confidence outside [0, 1], or invent a non-enum
``path`` value. Schema mismatches retry once; persistent failures
land in ``llm_calls`` with ``status="error"`` and a structured
``error_detail``.

### Server-side path override on the decision agent

``agents/decision.py:236``:

```python
# Belt + suspenders: enforce engine verdict server-side too.
if has_block and drafted.path != "decline":
    logger.warning("decision_override_to_decline", ...)
    drafted.path = "decline"
```

The Python rules engine evaluates outcomes independently of the LLM.
If any BLOCK-severity rule failed (e.g. LTV over cap, doc completeness
failed) AND the LLM still picked ``approve`` or ``conditional``, the
server overrides to ``decline`` and writes a
``decision_override_to_decline`` audit event. This is enforced before
the verdict is persisted; the LLM has no opportunity to argue.

### Adverse-action-letter principal-reason enforcement

ECOA Reg B requires adverse-action letters to cite specific reasons.
The ``AdverseActionLetter.principal_reasons`` schema field is
constrained: the prompt is instructed that ``principal_reasons`` MUST
contain at least one ``rule_id`` from the BLOCKING failures the rules
engine produced. The model "may not invent a reason that isn't in the
supplied outcomes list." Combined with the structured-output gate,
this means the letter cannot cite a fabricated rule.

### Materials hash (cryptographic decision integrity)

Every decision is stamped with a sha256 hash of its input set
(documents, accepted extractions, borrower meta, guarantor list).
The hash is recomputed on every materials read; mismatch blocks
forward stage transitions until the decision agent is re-run. This is
protection against silent input drift, not against the model
hallucinating — but it converts "stale decision" from invisible into
fatal.

### Stage locks

``services/loan_locks.py`` rejects mutating operations server-side
once the loan is past ``decision``. The decision agent cannot be re-run
on a loan in ``conditions`` / ``approved`` / ``servicing``; documents
cannot be uploaded past ``approved``. The agents themselves are
defensive too, but the lock is at the router; the model never gets
a chance to mutate post-decision state.

### "Real identifiers" prompt block (placeholder defense)

A common failure mode: prompts asking for letters produce
``[LENDER NAME]`` / ``[DATE]`` placeholders because the model doesn't
know the actual values. ``services/institution.py:materials_block``
threads authoritative values into the user message as a
``Real identifiers`` block, and the system prompt has a hard rule
forbidding bracketed placeholders. When a field is unconfigured, the
block contains an explicit *omit this clause* instruction the model
follows. Failure mode goes from silent (placeholders in production
letters) to visible (an "omit clause" instruction the operator sees
when they read settings).

### Stage-transition prerequisites

``services/loans.py:check_prerequisites`` is the gate on every
``transition_stage`` call. Going to ``underwriting`` requires
documents AND at least one accepted extraction; going to ``decision``
requires an ``underwriting_complete`` audit event; going to
``approved``/``conditions``/``declined`` requires a
``decision_complete`` event whose path matches the destination. The
state machine cannot be advanced past a missing dependency.

---

## 2. Runtime guardrails (steering during execution)

### Pre-flight gates on each agent

Each agent's ``fetch_and_evaluate`` node checks its preconditions
before any LLM call:

- **Intake**: short-circuits to ``status=needs_documents`` if the loan
  has zero documents.
- **Underwriting**: short-circuits if no accepted extractions exist.
- **Decision**: short-circuits to ``status=needs_underwriting`` if no
  ``underwriting_complete`` audit event has been written.

This is mostly cost defense (don't burn tokens drafting a summary on
a loan with no extractions) but it's also a hallucination defense:
the LLM never sees an empty input set, so it can't invent values to
fill the void.

### Confidence gates on extractions

``tools/extractor.py`` defines per-field confidence thresholds. Each
LLM-extracted field comes back with a ``confidence`` (0..1) and a
``source_span`` (the exact quote it was read from). Below-threshold
extractions don't auto-feed the rules engine — they're set to
``ExtractionStatus.PROPOSED`` and a row lands in ``review_tasks`` for
human triage. Only ``ACCEPTED`` / ``OVERRIDDEN`` extractions feed
downstream agents.

### HITL interrupts at irreversible boundaries

The intake agent uses LangGraph's ``interrupt()`` to pause before
sending the borrower the doc-request email. The graph state is
checkpointed in Postgres; the UI surfaces the drafted email for the
underwriter to review, edit, or cancel. Same pattern at decision
delivery (no auto-send of the decision letter). Autonomous mode does
NOT bypass these — autonomy chains agent invocations but stops at
real-world commitments.

### Schema-validation retry with hint

``llm_gateway.py`` catches Pydantic validation errors on a
structured-output call, retries once with the error message folded
into the user prompt. Single retry, not infinite — runaway loops are
bounded.

### Failure attribution on streaming runs

The SSE streaming layer tracks the last completed node so that when
an exception surfaces from inside an agent graph, the streamer
attributes it to the next (in-flight) node and writes that
``agent_steps`` row as ``status="failed"`` with the truncated
exception text. The UI's AgentProgress stops spinning on the
failed node rather than appearing to hang.

---

## 3. Retrieval-augmented generation (RAG)

### Hybrid retrieval — dense + sparse + RRF

``services/qa.py`` does hybrid retrieval over ``document_chunks``:

- **Dense**: cosine similarity over pgvector ``Vector(1024)``
  embeddings (OpenAI ``text-embedding-3-small``).
- **Sparse**: PostgreSQL ``tsvector`` full-text rank on the same
  chunks (auto-generated tsvector column, migration 0004).
- **Fusion**: Reciprocal Rank Fusion (RRF, k=60) per Cormack et al.
  No learned weights; combines the two rankings into one top-K list
  for the prompt context.

Used by the "Ask the file" feature on the underwriting workspace and
by the staff chat tool ``ask_document``. Both surface the actual
chunks consulted alongside the answer so the underwriter can verify
the source.

### Document-chunk anchoring (every claim has a citation)

The underwriting agent's output schema includes ``citations[]`` per
section — each is a field name (e.g. ``"property_address"``,
``"loan_amount"``). The frontend resolves each citation back to the
underlying extraction + source quote via
``GET /loans/{id}/citations/{field_name}``. Click any citation chip
in the rendered summary, see the exact document quote the value
came from. This is the strongest "the AI didn't hallucinate" signal
the demo can produce — and the source-of-truth chain is
extraction → source_span → document chunk.

### Comparable-loans kNN

``services/comparables.py`` computes a per-loan embedding (a
canonical text representation of the loan) at underwriting time and
indexes it in pgvector. The case-file workspace shows the 5 nearest
prior loans by cosine similarity. The LLM is not in this loop —
comparables are a deterministic kNN lookup, not a generation step.

---

## 4. Semantic tool selection

### Curated tool catalog per audience

The staff and borrower chat surfaces (``staff_chat.py``,
``borrower_chat.py``) use Anthropic's tool-use protocol with a hand-
written tool catalog. Each tool has:

- A typed ``args`` Pydantic schema (the model can't malform inputs).
- A natural-language ``description`` (the model picks tools by reading
  these — this is the semantic part).
- A handler function that runs the actual operation.

Borrower tools (``agents/tools/borrower.py``) are read-only by
default; destructive operations (withdraw, erasure) require an
explicit confirmation tool call before the action tool fires.

Staff tools (``agents/tools/staff.py``) cover a wider catalog
(search loans, list activity, send borrower message) and are filtered
by the user's role.

### Tool-use is the only way agents take action

The borrower and staff chat loops do NOT have arbitrary code
execution, file write, or shell access. Every action the model can
take is in the tool catalog. The set of catalogable actions is
intentionally narrow — adding a new mutation requires a code change
in two places (the tool catalog AND the corresponding endpoint).

---

## 5. Multi-agent validation

### Decision agent independently re-runs the rules engine

The decision agent's ``fetch_and_evaluate`` node runs the rules
engine from scratch rather than trusting the underwriting agent's
result. If the engine + the LLM disagree on a blocking outcome, the
server-side override fires.

This is genuinely a validator pattern — two independent computations,
the deterministic one wins on conflict. The underwriting agent's
summary is used for *prose*, not for *decision logic*; the rule
outcomes that drive the path choice are computed fresh.

### LLM-as-Judge with Constitutional AI + Self-Refine loop

``agents/guardrails.py`` implements three combined patterns:

- **Constitutional AI** (Bai et al. 2022) — every drafted artifact is
  evaluated against an explicit ``Constitution`` (a list of
  ``principles`` + ``red_lines`` + ``forbidden_substrings``). The
  constitution is the public contract for "valid output"; the
  drafter prompt can drift, but the constitution is what the judge
  checks.
- **LLM-as-Judge** (Zheng et al. 2023) — a separate LLM call (default
  model, distinct from the drafter's heavy model) judges adherence
  with structured output (``severity``, ``failed_principles``,
  ``failed_red_lines``, ``critique``).
- **Self-Refine** (Madaan et al. 2023) — on a ``block``-severity
  failure, the LangGraph conditional edge routes back to the drafter
  with the critique appended to the next prompt. Bounded retries
  (``MAX_VALIDATION_ATTEMPTS = 3``).

Wired into **all three** agents via the shared
``make_validator_node()`` + ``make_validator_router()`` builders in
``guardrails.py`` — no per-agent copy-paste. Each agent passes the
right constitution(s) + retry/persist edge names and the rest is
mechanical:

| Agent | Drafted artifact | Constitution(s) | Self-Refine cycle |
|---|---|---|---|
| Intake | Borrower doc-request email | ``INTAKE_DOC_REQUEST_CONSTITUTION`` | ``draft_request → validate_email → (draft_request \| approve)`` |
| Underwriting | Cited credit summary | ``UNDERWRITING_SUMMARY_CONSTITUTION`` | ``draft_summary → validate_summary → (draft_summary \| persist)`` |
| Decision | Verdict text + AAL (decline path) | ``DECISION_VERDICT_CONSTITUTION``, ``ADVERSE_ACTION_LETTER_CONSTITUTION`` | ``draft_decision → validate_decision → (draft_decision \| persist)`` |

``forbidden_substrings`` is a cheap pre-judge fast-fail that catches
the common drift patterns (``[LENDER NAME]``, ``[DSCR]``, ``[BORROWER NAME]``, etc.)
before paying for the judge LLM call. The judgment lands on
``agent_runs.payload.guardrail_judgment`` so the Safety dashboard and
the observability page both render it alongside the run.

### Input-layer prompt-injection detector (hybrid pattern + Haiku)

``agents/injection.py`` is the input-side counterpart to the output-
side constitutional judge. It runs at every boundary where an
untrusted text input becomes part of an LLM prompt:

- **Documents** — staff + borrower uploads, after PDF text extraction.
  ``routers/documents.py``, ``routers/borrower_portal.py``.
- **Borrower chat messages** — before they're appended to the chat
  loop's message history. ``agents/tool_chat_loop.py``.
- **(Future) Inbound borrower email** — slot reserved in the
  ``InjectionSourceKind`` enum.

The detector is **hybrid**:

1. A compile-time regex catalog flags known injection signatures.
   Free, sub-millisecond. Each pattern carries a ``severity_floor``
   (``high`` / ``medium`` / ``low``).
2. ``high``-floor hits **block immediately** (no Haiku call).
   ``BlockedByInjectionError`` is the canonical exception; the
   document upload returns 422, the chat loop emits an SSE
   ``error`` event and skips the LLM turn.
3. ``medium``-floor hits **escalate to a Haiku second-pass** that
   verifies whether the text is a genuine injection attempt or a
   benign false positive. Haiku's verdict drives the final decision
   (``high`` → blocked, ``medium`` → flagged + allowed, ``low`` →
   allowed silently).
4. ``low``-only hits are **silent counts** — recorded for the trend
   graph, no LLM call.

Every scan persists a row in ``injection_detections``. The Safety
dashboard (``/safety``) renders the headline KPIs, severity
histogram, source-kind breakdown, top-N patterns, and the recent
detections list with a drill-in drawer (matched patterns + Haiku
critique + raw excerpt). The same panel is embedded as a tab inside
``/observability``.

**Cost envelope**: pattern catalog is free; Haiku call only on the
medium band. At Haiku list pricing (~$0.001 per ~300-input/~100-
output call) with a typical 5% medium-band escalation rate, 10k
inputs/day ≈ $0.50/day. The dashboard shows live cost.

References: Greshake et al. 2023 *"Not What You've Signed Up For"*
on indirect injection via tool-fetched content.

### Eval harness as offline validator

``evals/runner.py`` runs labeled tasks against golden sets. Today
this is in CI / on-demand, not on the runtime path. The
**eval-gated prompt promotion** path is wired (the "promote with
eval" endpoint exists) but advisory rather than blocking.

### Adversarial prompt-injection test fixtures

``evals/golden_sets/adversarial_injection/`` carries documented
injection cases:

- ``inj-001-ignore-instructions`` — direct injection in document
  body attempting to override the system prompt. Asserts the
  extractor returns the legitimate borrower entity, not the
  injection target.
- ``inj-002-rule-bypass`` — indirect injection in appraisal report
  trying to waive the LTV rule. Asserts the rules engine
  independently computes the failing LTV AND that the
  ``decision_override_to_decline`` server-side check fires AND
  that the constitutional judge would flag an approve verdict as a
  red-line violation. References Greshake et al. 2023 "Not What
  You've Signed Up For" on indirect prompt injection via tool-
  fetched content.

Add new injection patterns here when threat-modelling produces
new categories. The fixtures are documentation as much as tests —
each one names the defense layer that catches it.

### Safety scenarios catalog (the audit-ready surface)

``api/mkopo/safety/scenarios.py`` is the structured manifest of
every robustness property the system pins. Each entry describes
one specific way an attacker (or a buggy LLM) might try to bend
the system, the defense that catches it, and the test that
verifies the defense still works. The frontend renders this as
the **Scenarios catalog** tab on ``/safety`` — one card per
scenario, grouped by category and severity.

The catalog is the single source of truth for "how does the app
stay safe?" — a reviewer, auditor, or prospective adopter can
browse it without reading any code. CI failure on any of the
referenced tests flips the corresponding card to a regression
banner on the dashboard.

Current coverage (24 protected scenarios + 2 known gaps across
ten categories):

- **Pre-flight gates** — decision can't run before underwriting,
  underwriting can't run before extractions, intake can't run
  with no documents.
- **Rule engine override** — LLM-picked verdict is rewritten by
  the server when it conflicts with a BLOCKING rule failure.
- **Constitutional judge** — drafted artifacts must satisfy the
  written constitutions; placeholder leakage fast-fails without
  paying for the judge LLM.
- **Scope & role boundary** — borrower-side tool catalog has no
  staff-only actions and vice versa; destructive tools carry a
  confirmation gate.
- **Input-layer injection** — hybrid pattern + Haiku detector
  blocks documents and chat messages with override / role-swap /
  rule-waiver / data-exfil signatures.
- **Storage authz** — loan_id cross-check refuses to return bytes
  when the URI's loan path doesn't match the caller's claim.
- **Stage machine** — adjacency table blocks jumps like
  intake→approved; terminal stages have no outgoing edges.
- **Stage locks** — past the decision gate, agents are server-
  side-locked from re-running.
- **Orchestrator** — autonomous chain doesn't auto-commit any
  borrower-visible action.
- **Loop bounds** — Self-Refine cycle bounded at
  ``MAX_VALIDATION_ATTEMPTS``; chat loop bounded at
  ``_MAX_ITERATIONS``.

Adding a new scenario:
1. Write the test in ``tests/test_safety_scenarios.py``.
2. Add an entry to ``SCENARIOS`` in ``safety/scenarios.py``
   referencing the test id.
3. CI catches any drift; the new card appears on the dashboard
   automatically.

---

## What is NOT in the codebase (and matters)

Being explicit so the gaps are easy to find:

| Technique | Status | Why it matters |
|---|---|---|
| LLM-as-judge at runtime | ✅ | ``agents/guardrails.py`` runs a constitutional judge on every drafted artifact across all three agents. See § 5 |
| Constitutional AI prompts | ✅ | Four constitutions — intake email, underwriting summary, decision verdict, adverse-action letter. All in ``guardrails.py`` |
| Self-correction loops | ✅ | Self-Refine cycle wired into all three agents via shared ``make_validator_node`` / ``make_validator_router`` builders; bounded by ``MAX_VALIDATION_ATTEMPTS`` |
| Adversarial prompt-injection test fixtures | ✅ | ``evals/golden_sets/adversarial_injection/`` |
| Adversarial prompt-injection defense at runtime | ✅ | Hybrid pattern + Haiku input-layer detector in ``agents/injection.py`` blocks documents, chat messages, and (soon) inbound email before they reach the LLM. Output-side constitutional judge is the second layer |
| Constitutional judge on intake + underwriting outputs | ✅ | ``INTAKE_DOC_REQUEST_CONSTITUTION`` + ``UNDERWRITING_SUMMARY_CONSTITUTION``, wired via the shared builders |
| Output classifier ahead of borrower-facing send | ✅ | Drafts go to underwriter for approval (the underwriter is the classifier) AND the constitutional judge runs on the draft pre-persist |
| Safety dashboard for visibility | ✅ | Dedicated ``/safety`` page + Safety tab inside ``/observability`` + per-loan ``SafetyChip`` on the case-file header |
| Auth secrets handled outside source control | ✅ | The JWT signing key (``JWT_SECRET``) is `.env`-only, never committed; startup banner reports ``degraded`` until you set it. Generate + setup + rotation procedure in [README § Auth + security](../README.md#auth--security). For production deploys, put it in a secrets manager (Vault / AWS Secrets Manager / Doppler), not `.env`. The legacy dev-bearer shortcut was removed in May 2026 (task #186) |
| Active-learning feedback | Partial | Review-queue overrides feed drift_monitor; nothing yet adjusts prompts automatically |
| Bias / fair-lending output testing | ❌ | The eval harness has no fair-lending test suite. Real lender deployment needs disparate-impact monitoring. Out of scope for the demo |
| Differential privacy on the embedding corpus | ❌ | Comparable-loans corpus is anonymised at seed time but no formal DP |

---

## The defense, in two paragraphs

The system survives the question *"why should I trust an LLM
decision?"* with four orthogonal grounding mechanisms applied to
every output:

1. A deterministic **rules engine** that overrides the LLM on
   conflict (``decision.py`` server-side path override).
2. A **citation chain** back to specific document spans, navigable
   from the UI.
3. A **cryptographic hash** of the inputs that produced the
   decision (``materials_hash.py``), so input drift is detectable.
4. A **constitutional LLM-as-judge** that evaluates the output
   against a written constitution and, on block-severity failure,
   loops the drafter via LangGraph's conditional-edge cycle until
   the output passes or retries are exhausted (``guardrails.py``).

The system survives the question *"how do you know the prompt didn't
drift?"* by versioning every prompt, stamping every LLM call with the
version id that produced it, running an eval harness against a golden
set in CI, AND maintaining adversarial-injection fixtures so a
regression in either the prompt or the constitution gets caught
before it ships.

What it does NOT survive: a regulator who wants formal
disparate-impact analysis or input-layer prompt-injection
detection. Those are explicit next bites for production deployment
at a regulated lender, documented in the gap table above.
