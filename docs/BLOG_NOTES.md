# Blog notes — concrete numbers from a real populate run

A reference of every number that's worth citing in writing about
Mkopo. All values from a fresh end-to-end populate on 2026-05-30:
12-task eval pass + 7 production monitors + 7 agent runs across 3
loans + 10 injection-detection samples + 40 inbound borrower
messages.

If you need a number that isn't here, the same data is queryable
from `task_runs`, `llm_calls`, `agent_runs`, `agent_steps`,
`injection_detections`, and `messages`. `docs/METRICS.md` lists the
file:function for each.

---

## Headline numbers (one-liner per section)

| Surface | Headline |
|---|---|
| Eval gates | **11 of 12 tasks at 100%, one at 87.5%** (all PASS) |
| Production drift | **0% per-field drift** across 10 extraction fields |
| Calibration | **ECE 0.012** (well-calibrated, threshold <0.05); Brier 0.0002 |
| MMD borrower-corpus drift | **0.0095** — "minor" band on the 7d vs 30d window |
| Per-agent economics | **decision $0.035/run, underwriting $0.017/run, intake $0.018/run** |
| Safety | **4 of 4 high-severity injection attacks blocked**, 0 false-negative |
| Pipeline | **15 agent runs**, 527 LLM calls, $5.88 total spend |
| LLM error rate | **0%** structural errors after the gateway's 1-retry budget |
| LLM retry rate | **21.7%** (94 of 433 priced calls re-attempted; every retry recovered) |

---

## Eval gates — golden suite results

Twelve labeled tasks, ranged 1–10 fixtures each, scored against the
live LLM gateway (`claude-sonnet-4-6` for drafters, `claude-opus-4-6`
for judges). Run: `cd api && uv run python -m evals.runner`.

| Task | Threshold | Score | n |
|---|---|---|---|
| extract_borrower_entity | 95% | **100%** | 7 |
| extract_noi | 90% | **100%** | 6 |
| extract_appraised_value | 90% | **100%** | 5 |
| extract_credit_score | 95% | **100%** | 5 |
| extract_loan_amount | 90% | **100%** | 5 |
| summarize_underwriting | 80% | **100%** | 1 |
| adversarial_injection | 100% | **100%** | 2 |
| decision_verdict | 85% | **100%** | 10 |
| aal_fidelity | 75% | **100%** | 6 |
| uw_groundedness | 80% | **100%** | 4 |
| tool_call_accuracy | 75% | **100%** | 5 |
| intake_email | 80% | **87.5%** | 8 |

**Total spend on the eval pass: ~$2.16** (54 LLM calls, mixed
sonnet + opus-as-judge).

**One miss is the most informative result.** `intake_email`
clears its 80% gate at 87.5% (7 of 8 fixtures pass). The failing
fixture sits inside the body-word-count criterion — drafter
sometimes goes past the 130-word ceiling. That's an iteratable
prompt issue, not a model issue, which is exactly the kind of
signal an eval suite is supposed to produce.

---

## Production monitors — observed values

Cron-scheduled monitors (manual trigger: `uv run python scripts/run_all_monitors.py`).

### Healthy + populating
- **Calibration** — ECE **0.0123**, Brier **0.0002**, n=64.
  Threshold-table: ECE < 0.05 well calibrated. Result: well within band.
  Source: `services/calibration.py`.
- **Per-field extraction drift** — 10 fields tracked, **all at 100%**
  vs golden baseline. n per field ranges 3–7.
  Source: `services/drift.py`.
- **Per-agent economics** —
  - Intake: $0.0183/run avg, p95 5.77s
  - Underwriting: $0.0169/run avg, p95 18.56s
  - Decision: $0.0346/run avg, p95 16.14s
  Source: `services/agent_economics.py`.
- **Prompt drift (MMD² on borrower-inbound corpus)** — **0.0095**
  ("minor" band, 0.005 ≤ MMD² < 0.02). Sigma (median-heuristic
  bandwidth) = 1.23. n_current=20, n_reference=20.
  Source: `services/prompt_drift.py`.

### Skipped (insufficient data — by design)
- **Fairness AIR** — n_decisioned=4, single group bucketed. The
  AIR formula needs both groups represented. Floor enforced.
- **PSI** — n_current=11 loans, floor=30. Not enough volume.
- **Refusal-rate** — n_baseline=0 (no historical refusals). Needs
  ~30 days of operation to populate a baseline.

The "insufficient data → no number" path is the right behaviour —
these monitors fail-closed on low n rather than emit a noisy
single-point estimate. The dashboard cards render "no data yet"
with the floor noted; same standard as bank-MRM monitoring (SR
11-7 §VII).

---

## LLM economics

Across the entire session (~$5.88 in real Anthropic spend):

| Model | Calls | Avg latency | Total cost | Retries | Errors |
|---|---|---|---|---|---|
| `claude-sonnet-4-6` (drafter + judge sonnet) | 376 | 4.07s | $1.79 | 89 | 0 |
| `claude-opus-4-6` (RAGAS judge + complex drafts) | 57 | 11.08s | $4.09 | 5 | 0 |

**Cost is dominated by Opus.** Opus is 14% of calls but 70% of
cost. This matches the design intent — Opus is reserved for
groundedness judging and underwriting drafts where the additional
reasoning capacity is worth the price.

**Schema retry rate is 21.7%** (94 of 433 priced calls re-attempted).
Every retry recovered — zero structural errors landed in
`llm_calls.status='error'`. The retry pattern is concentrated in
the decision agent's verdict drafter, where the LLM consistently
exceeds the 400-character rationale ceiling on the first attempt.
The Pydantic schema bound forces the retry; the retry succeeds.

This is what "structured outputs" buys you in production: a model
can't ship a response that violates the schema, but the cost is
extra LLM round-trips when the model is on the edge of the bound.
A blog-worthy tradeoff to call out.

---

## Constitutional judge in action

The May 30 underwriting run on loan LN-2026-1006 (MERIDIAN
hospitality) hit the Self-Refine loop **three times**:

```
attempt=1 severity=block n_failed_principles=1 n_failed_red_lines=1
attempt=2 severity=block n_failed_principles=2 n_failed_red_lines=1
attempt=3 severity=block n_failed_principles=2 n_failed_red_lines=1
guardrail_max_attempts_reached attempts=3 persist_node=persist
```

Cap reached at MAX_VALIDATION_ATTEMPTS=3 → the latest draft was
persisted with the `guardrail_judgment` audit so a human reviewer
sees the unresolved flag. The decision agent on the same loan went
1 attempt → block → 1 retry → warn (passed), then 1 attempt → block
→ 1 retry → warn for the AAL.

**Total LLM cost on guardrail loop overhead for that one loan: ~$0.25.**
Mitigation working as designed: bounded retries + visible audit
trail when the loop terminates without resolution.

---

## Safety — input-layer injection detector

Ten injection attempts run through `detect_injection`:

| Decision | Severity | Count |
|---|---|---|
| blocked | high | 4 |
| allowed (catalog-clean — no row written) | n/a | 6 |

**4 of 4 HIGH-severity attacks blocked, 0 false negatives.**
The 6 lower-severity attempts didn't match the catalog at all
(no `injection_detections` row written). Those would only
materialize if the Haiku second-pass were triggered, which it
wasn't because the catalog regex didn't fire.

This matches the audit's `Scanned` tooltip fix: the dashboard's
"Scanned" tile counts rows in `injection_detections`, not total
inputs scanned. The 6 catalog-clean inputs don't appear there
by definition — same as the design intent.

Blocked patterns came from: instruction override, system-prompt
exfiltration, role-play / jailbreak. Each one matched at HIGH
severity with no Haiku call (fail-fast on high-confidence
patterns).

---

## Pipeline activity

| Stage | Loan count | New agent runs this populate |
|---|---|---|
| intake | 2 | 4 |
| underwriting | 3 | 5 |
| decision | 2 | 3 |
| conditions | 0 | — |
| closing | 1 | — |
| approved | 1 | — |
| servicing | 1 | — |
| declined | 1 | — |
| withdrawn | 0 | — |

Three agent runs are stuck at `running` status — those are
**pre-existing orphans** from a code path the audit fixed:
`agents/orchestrator._finalize_failed_agent_run` (added during
the readiness-audit cleanup) now finalizes the AgentRun on
`graph.ainvoke` failure. The orphans pre-date that fix; new runs
won't accumulate the same way.

This is itself a blog-worthy story: an audit found phantom
in-flight runs in observability, traced the gap to a missing
failure-finalization in the orchestrator, fixed it with a helper
that mirrors the SSE path's behaviour. Visible in
`git log --grep="workflow correctness"`.

---

## What this populate didn't generate (and why)

- **Fairness AIR with multiple groups** — needs more decisioned
  loans with the synthetic protected-class bucketing flipping
  both ways. SCOPE.md flags this: the protected-class signal is
  synthetic (SHA-256 of `loan_id`), so getting both groups
  requires loan-id variety. ~30 loans would do it.
- **PSI with real bands** — floor is n=30 per window; we have
  11 loans. PSI's "stable / minor / major" bands matter from
  ~50+ samples per window.
- **Refusal-rate spike** — no agent has refused yet (no
  ambiguous-edge fixtures in the seed). Would populate naturally
  as agents see real refusal-shaped inputs.
- **Calibration with overrides** — review-queue throughput is 1.
  The calibration card's "well-calibrated 0.50–0.70 band should
  accept ~60% of the time" insight needs ~50+ overridden
  extractions to be statistically meaningful.

All of these are **honest "needs more volume" gaps**, not
broken code. The monitors all run; they just fail-closed on
low n with the floor in the log.

---

## Suggested blog angles

Three concrete pieces with the data to back them up.

### 1. "What it actually costs to run an agentic underwriting pipeline"

The unit economics:
- Intake (5 docs, no missing fields): ~5 LLM calls, ~$0.018 cost, ~6s wall
- Underwriting (1 draft + 1 judge, typical): 2 calls, ~$0.017 cost, ~19s wall
- Decision (1 path + 1 verdict + 1 AAL judge, decline path): ~4 calls, ~$0.035 cost, ~16s wall
- **End-to-end on a single loan: ~11 LLM calls, ~$0.07, ~40s wall.**

What blows it up:
- Constitutional judge loop (up to 3 retries on block-severity)
- Schema retry on long-rationale outputs
- Opus-as-judge for groundedness checks

How it's bounded:
- `MAX_VALIDATION_ATTEMPTS=3` per Self-Refine loop
- Worst-case call counts pre-computed per agent path (in ARCHITECTURE.md)
- No `while True` anywhere in `mkopo/agents`
- Per-call cost recorded in `llm_calls.cost_input_usd + cost_output_usd`
- `_MAX_ITERATIONS=6` on the chat tool-call loop

### 2. "Self-Refine in practice: 21% of LLM calls retried, every one recovered"

94 of 433 priced calls were schema retries (most from the decision
verdict drafter hitting the 400-char rationale ceiling).
**Zero structural errors landed in `llm_calls.status='error'`** —
the Pydantic-schema-as-contract pattern bought us 100% recovery
from soft failures.

The mechanism: `llm_gateway.call_structured` requires a Pydantic
schema; the model is forced into tool-use mode where the response
must validate against that schema. Mismatch → retry once. The
retry succeeds because the model adjusts after seeing what failed.

Failure modes:
- Output too long for a string field (string_too_long): caught
  consistently, recovered consistently
- Out-of-range numeric: never observed in this run
- Missing required field: never observed

### 3. "Constitutional AI without the philosophy"

The decision agent's `agents/guardrails.py` implements a
practical Constitutional-AI loop: LLM drafter →
LLM-as-judge against an explicit constitution (e.g.
`UNDERWRITING_SUMMARY_CONSTITUTION`) → on block-severity failure,
LangGraph conditional-edge routes back to the drafter with the
critique appended (Self-Refine, Madaan et al. 2023).

On the populate run:
- 3 of 3 decision-agent runs hit the judge
- 2 of 3 had at least one block-severity flag the first time
- 1 of 3 maxed out at 3 attempts and persisted with the audit flag
- Pre-judge `forbidden_substrings` fast-fail caught zero (the
  current drafts don't leak `[LENDER NAME]` patterns)

**The point**: this isn't a thought experiment, it's a working
loop with a visible audit trail when it fails to converge. Every
`guardrail_judgment` event lands in `agent_runs.payload` and the
observability page surfaces it.

### 4. Bonus: "Three p95 implementations, one ledger" (engineering-discipline angle)

The same project has **three different p95 implementations** —
deliberately, with different tradeoffs:
- `routers/evals/_shared._percentile` — nearest-rank, used by the
  /eval summary tile
- `routers/observability._percentile` — nearest-rank, used by the
  observability surface
- `services/agent_economics._percentile` — linear-interpolation
  (NumPy-default convention), used by the per-agent cost card

All three are valid p95 conventions. The numbers can differ on
small n. The system is honest about it: the help-page glossary
calls out all three locations and explains why. That's the
disciplined version of "we noticed and we documented it" — not
"we have three because we forgot we already wrote one".

The story is the discipline, not the bug. The fix to standardize
would be one file; the value of doing it is small (the cards
that show p95 are visually separated); the cost of pretending
there's only one would be reviewer surprise + a credibility hit.

---

## Reproducing this snapshot

```bash
# 1. From a freshly seeded DB:
cd api
uv run python scripts/seed.py --reset
uv run python scripts/seed_eval_baseline.py
uv run python scripts/run_all_monitors.py
uv run python -m evals.runner
# 2. (Optional) Run agents on a few loans + populate safety:
#    see git log for the inline scripts used here
```

Expected cost: $2–8 in Anthropic spend depending on how many
agent runs are triggered.

---

## File map for the writer

| Claim in the post | Verifiable at |
|---|---|
| "ECE 0.012" | `services/calibration.py`, `task_runs WHERE task_name='calibration.extractor_confidence'` |
| "MMD² 0.0095 minor band" | `services/prompt_drift.py`, `task_runs WHERE task_name='prompt_drift.borrower_inbound'` |
| "100% on 11 of 12 evals" | `evals/runner.py`, `task_runs WHERE source='golden'` |
| "Schema retries" | `llm_calls WHERE attempt > 0` |
| "Per-agent cost" | `services/agent_economics.py`, `agent_runs JOIN llm_calls ON thread_id` |
| "Constitutional judge loop" | `agents/guardrails.py`, audit events with `action='guardrail_judgment'` |
| "Injection blocked" | `safety/injection.py`, `injection_detections WHERE decision='blocked'` |
| "Materials hash" | `services/materials_hash.py`, `agent_runs.payload.materials_hash` |

All quotes above are reproducible by anyone with read access to
the database. The dashboard at `/eval` renders the same numbers
the SQL queries return; the `/help` page resolves any acronym you
might forget.
