# Metrics Reference

Every numeric value, percentage, badge, or trend shown in the
UI is computed by a real backend function. This file is the
catalog: for each metric, you get the label as shown, the
formula, the source file, the data table behind it, the window
used, the thresholds, the academic / regulatory reference (if
any), and the known limitations.

Cross-reference:
- Acronyms are defined in [GLOSSARY.md](./GLOSSARY.md).
- What the demo does and does NOT cover is in [SCOPE.md](./SCOPE.md).
- The in-app `/help` route renders a condensed version of this
  file with deep links into the UI.

---

## Overview tiles (top of `/eval`)

### Production accuracy (paired)

- **What**: Mean accuracy across tasks that have BOTH a
  production row and a golden row — i.e. the apples-to-apples
  intersection. Production data comes from staff overrides in
  the review queue.
- **Formula**: `mean(latest_prod[k].accuracy for k in paired_keys)`
  where `paired_keys = latest_prod.keys() ∩ latest_gold.keys()`.
  Per-field accuracy = `accepted / (accepted + overridden)`.
- **Source**:
  - Aggregation: `api/mkopo/routers/evals.py::get_eval_summary`
  - Per-field write: `api/mkopo/services/drift.py::run_drift_monitor`
  - Denylist of non-accuracy prefixes:
    `_DERIVED_METRIC_PREFIXES` in `routers/evals.py`.
- **Data**: `task_runs` rows with `source='production'`.
- **Window**: Latest run per field.
- **Why paired-only**: Pre-cleanup, this averaged the full
  production set against the full golden set. Because production
  only covers the extraction tasks the drift monitor writes,
  while golden covers the full eval suite, the delta was
  apples-to-oranges. Paired-only makes the comparison
  meaningful: same task names on both sides.

### Golden baseline (paired)

- **What**: Mean accuracy on the labelled YAML golden sets —
  restricted to the same task set as Production accuracy.
- **Formula**: `mean(latest_gold[k].accuracy for k in paired_keys)`.
- **Source**: same `get_eval_summary` function.
- **Data**: `task_runs` rows with `source='golden'`. Fixtures
  in `api/evals/golden_sets/`.
- **Window**: Latest run per task.
- **Note**: For the broader view (all golden tasks, not just
  the paired ones) see *Full eval suite* below.

### Full eval suite

- **What**: Mean accuracy across every latest golden eval
  task — not restricted to the production-paired subset.
  Surfaced in the Golden baseline tile's sub-line as
  "suite X% across N tasks".
- **Formula**: `mean(latest_gold[k].accuracy for k in latest_gold)`.
- **API fields**: `golden_suite_accuracy`,
  `golden_suite_n_tasks` on the `EvalSummary` response.
- **Why separate**: Answers a different question than the
  paired headline. The paired headline answers "how does
  production match the golden baseline for what we monitor
  live"; the suite answers "how is the eval suite doing
  overall".

### LLM p95 latency

- **What**: 95th percentile of LLM call elapsed seconds.
- **Formula**: Nearest-rank — sort latencies ascending,
  return value at index `ceil(0.95 · n) − 1`.
- **Source**: `api/mkopo/routers/evals.py::_percentile`
  (line ~168) and `_llm_health_window` (line ~257).
- **Data**: `llm_calls.elapsed_seconds` where `status='ok'`.
- **Window**: Cascade — last 24h, falls back to last 7d, then
  all-time, whichever has data. The tile shows the actual
  window used.
- **Limitation**: A separate p95 implementation (linear-
  interpolation) lives in `agent_economics.py`. Numbers can
  differ on small samples.

### LLM error rate

- **What**: Fraction of LLM calls that ended in a non-ok
  status.
- **Formula**: `count(status != 'ok') / count(*)`.
- **Source**: `api/mkopo/routers/evals.py::_llm_health_window`.
- **Data**: `llm_calls.status`.
- **Window**: Same 24h → 7d → all-time cascade as p95.

---

## Per-field extraction accuracy

### Per-field bar chart (`/eval > Extraction`)

- **What**: Each tracked extraction field with its production
  accuracy, sample size, and delta vs golden baseline.
- **Formula** (per field):
  - `production_accuracy = accepted / (accepted + overridden)`
  - `delta = production_accuracy − golden_accuracy`
- **Source**:
  - Endpoint: `api/mkopo/routers/evals.py::GET /eval/fields`
  - Computation: `_latest_per_field` (line ~289)
- **Data**: `task_runs` filtered by `task_name LIKE
  'extraction.%'`.
- **Drift threshold**: `delta ≤ −0.03` flips the row to
  drifting (banner + investigate button).
- **Limitation**: Sample size `n` is the count of resolved
  review-queue items the drift monitor saw in its last sweep.
  Small `n` (n<10) means the number is high-variance.

---

## Golden gate cards

### Decision verdict (confusion matrix + macro-F1)

- **What**: Per-class precision / recall / F1 plus a
  confusion matrix for the credit decision agent
  (`approve` / `decline` / `refer-to-human`).
- **Formula** (per class, one-vs-rest):
  - Precision = TP / (TP + FP)
  - Recall = TP / (TP + FN)
  - F1 = 2·P·R / (P + R)
  - Macro-F1 = unweighted mean of per-class F1
- **Source**: `api/evals/tasks/decision_verdict.py`
  (computation at lines 115–170).
- **Data**: Golden fixtures in
  `api/evals/golden_sets/decision_verdict/`.
- **Threshold**: Card flips to warning at macro-F1 < 0.85.
- **Reference**: SR 11-7 §VI on challenger models; the
  agent is challenged against the rule-engine decision in
  the same eval.

### AAL fidelity (CFPB / ECOA)

- **What**: Are generated Adverse Action Letters compliant
  with ECOA §1002.9(b)? Four binary checks per fixture, all
  must pass.
- **Criteria**:
  1. `principal_reasons_complete` — every expected
     `rule_id` substring appears in the LLM's reasons list
  2. `friendly_label_in_body` — the human-readable reason
     label appears in the body text
  3. `no_rule_id_in_body` — no internal rule identifiers
     leak into the body the borrower sees
  4. `right_to_know_disclosure` — required disclosure
     language is present
- **Formula**: Overall pass = strict AND of the four
  criteria. Card accuracy = `passed_fixtures / total`.
- **Source**: `api/evals/tasks/aal_fidelity.py` (lines
  119–187).
- **Reference**: CFPB Circular 2022-03, Circular 2023-09,
  12 CFR §1002.9(b).

### Adversarial injection coverage

- **What**: For each adversarial prompt-injection pattern in
  the red-team set, does the detector block it at HIGH
  severity?
- **Formula**: Per fixture, `passed = (decision == BLOCKED
  AND severity == HIGH)`. Per-pattern rate aggregated by
  `metadata.pattern`.
- **Source**: `api/evals/tasks/adversarial_injection.py`
  (lines 39–104).
- **Threshold**: 100%. Any pattern under 100% is a real
  miss — these are red-team patterns the system promises
  to block.

### Intake email compliance

- **What**: Four plain-language criteria the borrower-facing
  intake email must satisfy.
- **Criteria**:
  1. `addressed_by_name` — borrower name appears in body
  2. `no_markdown` — body has no markdown syntax that
     would render as raw `**` etc. in an email client
  3. `doc_asks_match_class` — document asks match the
     loan class (business vs personal vocabulary)
  4. `within_word_limit` — body ≤ 130 words (prompt asks
     for 120 with +10 tolerance)
- **Source**: `api/evals/tasks/intake_email.py` (criteria
  at lines 229–291).

### UW summary groundedness (RAGAS-style)

- **What**: Is every claim in the underwriting summary
  supported by something in the source documents?
- **Formula** (per fixture):
  `raw_score = supported_claims / total_claims`
  where a pinned judge LLM (`claude-opus-4-5`) extracts
  claims and labels each `SUPPORTED | UNSUPPORTED |
  INFERRED`.
- **Aggregation**: We don't report `raw_score` directly
  (would imply more precision than the judge gives us).
  Instead, `passed = raw_score >= 0.85` for clean fixtures,
  `raw_score < 0.85` for adversarial / hallucinated fixtures.
  Card reports `judge_accuracy = passed / n`.
- **Source**: `api/evals/tasks/uw_groundedness.py` (lines
  173–235).
- **Reference**: Es et al. 2024 (arXiv:2309.15217), RAGAS
  faithfulness metric. Adapted: we judge against source
  documents directly rather than via vector store.
- **Limitation**: LLM-as-judge bias. Mitigation: judge
  model is pinned, judge prompt is versioned.

### Tool-call accuracy (borrower chat)

- **What**: For the borrower chat agent, three properties on
  every fixture:
  - `trajectory_inclusion` — expected tools were called
  - `no_forbidden` — no mutating tool was called for a
    read-only intent (e.g. status question)
  - `argument_correctness` — required argument keys
    present
- **Formula**: Per fixture, passed = AND of the three.
  Card accuracy = `passed / n`. Per-criterion bars =
  per-criterion `passed / n`. Per-tool selection = for
  each tool, `selected / n` over fixtures that expected
  it.
- **Source**: `api/evals/tasks/tool_call_accuracy.py`
  (aggregator at lines 198–268, threshold at line 174 =
  0.75).
- **Note**: The card Pill threshold (`overallAcc >= 0.85`)
  is stricter than the task's pass threshold (0.75). Both
  are defensible — 0.85 makes the headline tile loud
  before the eval gate itself fails.

---

## Production drift cards

### Confidence calibration (ECE + Brier)

- **What**: Are extraction confidence scores well-calibrated
  against staff accept / override?
- **Formula**:
  - ECE = `Σ_m (|B_m|/n) · |acc(B_m) − conf(B_m)|` over
    10 equal-width bins
  - Brier = `mean((c − y)²)` over all samples
  - Ground truth: `y=1` for ACCEPTED, `y=0` for OVERRIDDEN
- **Source**: `api/mkopo/services/calibration.py`
  (computation at lines 104–176, `_N_BINS=10`).
- **Data**: `review_tasks` joined to source extractions for
  the confidence value.
- **Bands** (Guo et al. 2017):
  - ECE < 0.05 well calibrated
  - 0.05 ≤ ECE < 0.10 moderate miscalibration
  - ECE ≥ 0.10 poor
- **Reference**: Naeini, Cooper, Hauskrecht 2015 (ECE
  definition); Guo et al. ICML 2017 arXiv:1706.04599
  (modern bands).

### Adverse Impact Ratio (fairness)

- **What**: Approval-rate parity across protected groups
  using the EEOC four-fifths rule.
- **Formula**: `AIR = min(group approval rates) / max(group approval rates)`
- **Source**: `api/mkopo/services/fairness.py` (lines
  117–153). Synthetic-class function at lines 156–181.
- **Data**: `loans` table joined to verdict outputs.
- **Bands**:
  - AIR ≥ 0.85 OK
  - 0.80 ≤ AIR < 0.85 Watch
  - AIR < 0.80 Concern (four-fifths rule trigger)
- **Reference**: EEOC Uniform Guidelines 1978 (29 CFR
  §1607.4(D)); CFPB Circular 2022-03; Watkins et al. 2024.
- **CRITICAL LIMITATION**: The protected class in this
  deployment is **synthetic** — a SHA-256 bucketing of
  `loan_id` modulo 2. The card discloses this explicitly.
  A production replacement is a one-function change in
  `_synthetic_class_for_loan`.

### PSI — input feature drift

- **What**: Population Stability Index for each input
  feature, comparing the current vs reference distribution.
- **Formula** (per feature):
  `PSI = Σ_i (p_i − q_i) · ln(p_i / q_i)`
  with Laplace `1e-6` smoothing to avoid log(0). Numeric
  features: 10 quantile bins from the reference. Categorical:
  per-category.
- **Source**: `api/mkopo/services/psi.py` (lines 147–248,
  smoothing at 134–144, bands at 125–131).
- **Data**: `loans` table input fields.
- **Bands** (Siddiqi 2017):
  - PSI < 0.10 stable
  - 0.10 ≤ PSI < 0.25 minor shift, investigate
  - PSI ≥ 0.25 major shift, action required
- **Reference**: Siddiqi 2017, *Credit Risk Scorecards*
  §6; FDIC / SR 11-7 monitoring guidance.

### Refusal-rate trend

- **What**: Has the agent's refusal / abstain rate spiked
  vs baseline?
- **Formula**:
  - Current window: 7 days
  - Baseline: 28 days ending 7 days ago (7-day gap to
    prevent overlap)
  - z = (p_cur − p_base) / sqrt(p_base · (1 − p_base) / n_cur)
  - Spike if |z| ≥ 2
- **Source**: `api/mkopo/services/refusal.py` (lines
  117–165).
- **Data**: `agent_runs` with refusal/abstain outcomes.
- **Floor**: n_cur ≥ 20, n_base ≥ 30; below the floor we
  show "insufficient data" rather than a noisy z.

### Embedding-distribution drift (MMD) — backend only

- **What**: Maximum Mean Discrepancy between current and
  reference embeddings of system prompts. Detects
  prompt-template drift.
- **Formula**: MMD² with Gaussian kernel; threshold from
  Gretton et al. 2012.
- **Source**: `api/mkopo/services/prompt_drift.py`.
- **Note**: Computation is real but currently has **no UI
  card**. Results persist to `task_runs` with prefix
  `prompt_drift.` and are excluded from the trend chart by
  `_DERIVED_METRIC_PREFIXES`. Surfacing this in the UI is a
  follow-up.

---

## Operations cards

### Per-agent economics ($/run + p95 latency)

- **What**: Total LLM cost and call latency per agent, in
  the chosen window.
- **Formula**:
  - `$/run = sum(cost_input_usd + cost_output_usd) / count(runs)`
  - `p95 latency = linear-interpolated 95th percentile of
    elapsed_seconds`
- **Source**: `api/mkopo/services/agent_economics.py`
  (lines 87–157; percentile at 68–84).
- **Data**: `llm_calls` joined to `agent_runs` by `thread_id`.
- **Cost bands**: < $0.05 success, < $0.20 info, < $0.50
  warn, ≥ $0.50 danger (workshop defaults — adjust to
  your unit economics).
- **Latency bands**: < 10s success, < 30s info, < 60s
  warn, ≥ 60s danger.
- **Note**: This card uses linear-interpolated p95. The
  overview tile uses nearest-rank. Numbers can differ on
  small n.

### Confidence calibration diagnostics

- **What**: Per-band accept rate. Distinct from the
  Calibration card — this is band-conditional accept rate,
  not ECE.
- **Formula**: For each confidence band (`≥0.95`,
  `0.85–0.95`, `0.70–0.85`, `0.50–0.70`, `<0.50`):
  `accept_rate = accepted / (accepted + overridden)`
- **Source**: `api/mkopo/routers/evals.py` (lines 777–841).
- **Interpretation**: Well-calibrated = top band accept-rate
  close to 1.0. A well-calibrated 0.50–0.70 band should
  accept ~60% of the time.

### Review queue throughput

- **What**: Three counters — Open / Resolved-7d / Median-age.
- **Source**: `api/mkopo/routers/evals.py::_review_queue_stats`.
- **Data**: `review_tasks` table.

### Agent reliability (last 7 days)

- **What**: Per-agent run outcomes: success / interrupted /
  failed.
- **Formula**: "Worst-step-wins" — `agent_steps.status`
  rolled up so a single failed step marks the whole run
  failed.
- **Source**: `api/mkopo/routers/evals.py` (lines 669–685).
- **Note**: `interrupted` counts HITL pauses (LangGraph
  interrupts), not failures.

### Recent failures

- **What**: List view of recent `llm_calls` with
  `status != 'ok'` and `agent_steps` with `status='failed'`.
- **Source**: `api/mkopo/routers/evals.py` (lines 687–699).

---

## Observability page (`/observability`)

### Top KPI strip

| Tile | Formula | Source |
|---|---|---|
| LLM calls | `count(llm_calls)` in window | `observability.py:302` |
| p95 latency | nearest-rank p95 of `elapsed_seconds` | `observability.py:306` |
| Error rate | `count(status != 'ok') / count(*)` | `observability.py:303-304` |
| LLM cost | `sum(cost_input_usd + cost_output_usd)`; `uncosted_calls` = NULL-cost count | `observability.py:285-289` |
| Agent runs | `count(agent_runs)` | `observability.py` |
| Server errors | `count(infrastructure_errors)` | `observability.py` |

### Per-model rollup table

- **Columns**: Model / Calls / p50 / p95 / Error rate /
  Retry rate / Cost.
- **Source**: `api/mkopo/routers/observability.py` (lines
  268–281).
- **Retry rate**: `count(attempt > 0) / count(*)`.

### LLM call detail drawer

Reads a single `llm_calls` row plus its `agent_steps`
parent. Surfaces tokens, cost, retries, schema validation
errors, the model used, the system-prompt hash.

---

## Safety page (`/safety`)

### KPI strip

| Tile | Formula | Source |
|---|---|---|
| Scanned | `count(injection_detections)` in window | `safety.py:240` |
| Allowed | `count(decision='allowed')` — rows that the catalog matched but the judge cleared | `safety.py:241` |
| Flagged | `count(decision='flagged')` — medium severity confirmed by judge | `safety.py:242` |
| Blocked | `count(decision='blocked')` — fail-closed events | `safety.py:243` |
| Haiku judge | calls × $0.001 estimate (NOT actual cost) | `safety.py:145` constant |

**Important**: The "Allowed" tile reflects only persisted
detections — silent allows (where the catalog matched
nothing and no row is written) are by definition NOT in this
count. The "Haiku judge" cost is a flat-rate estimate, not
billed cost. See `SCOPE.md` for both caveats.

### Severity stacked bar

`InjectionDetection.severity` counter — low / medium / high.

### Top patterns

Top-10 `pattern_id`s by hit count from the
`matched_patterns` JSONB column.

### Constitutional judge rollup

Reads `agent_runs.payload.guardrail_judgment`.

### Scenarios catalog

A static manifest at `api/mkopo/safety/SCENARIOS` served via
`GET /safety/scenarios`. Each card lists threat / defense /
defense layer / verified-by test_id. The "verified by" link
is a string field, not a live test runner; a test failure
does NOT automatically flag the card.

---

## NIST AI 600-1 badges

The small badge next to each eval card maps the metric to
one of five NIST GenAI Profile risk categories: Confabulation,
Harmful Bias, Information Security, Information Integrity,
Value Chain. The mapping is curated, not computed.

- **Source**: `web/app/eval/cards/NISTBadge.tsx`
- **Reference**: NIST AI 600-1 (2024).

---

## Methodological notes

### Why latest-per-task vs averaged-over-window

The headline accuracy uses the most recent run per task, not
an average. Reason: a run is itself an average over N
fixtures, and re-averaging across runs would over-weight
periods when the cron ran more frequently. The chart on the
same page DOES show all runs over time so you can see drift.

### Why two p95 implementations exist

The overview tile uses nearest-rank (no interpolation
ambiguity). The per-agent economics card uses linear-
interpolation (matches NumPy default and is more stable on
small samples). Both are valid p95 conventions — the
discrepancy on small `n` is real. Standardising on one is a
follow-up.

### Why golden ≠ production almost always

Golden is a labelled, frozen test set. Production is live
data flowing through the review queue. Two reasons they
disagree:
1. Real-world inputs are out-of-distribution vs fixtures
2. Staff overrides include "right answer, wrong format"
   cases the golden set doesn't model

A divergence ≥ 0.03 (3 percentage points) is the drift
trigger threshold; the field's bar in the per-field section
shows a red banner with an "investigate" link when this
happens.
