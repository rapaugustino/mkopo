# Eval surface — current state + plan to industry standard

A snapshot of what the `/eval` page actually does today, what numbers
update (and when), what's missing for a credible lender-grade eval,
and a phased plan to close the gap. Industry references are inline
so anyone picking this up later can verify the claims.

Status: **proposal — not yet executed.** Owner: TBD.

---

## 1. What's on the eval page today (and how it updates)

### Frontend (`web/app/eval/page.tsx`)

Four queries power the page:

| Query | Endpoint | Refresh cadence |
|---|---|---|
| `getEvalSummary()` | `GET /eval/summary` | Mount + window focus only (no `refetchInterval`) |
| `getEvalFields()` | `GET /eval/fields` | Mount + window focus only |
| `getEvalTrend(days)` | `GET /eval/trend?days=30` | Mount + window focus only |
| `getEvalDiagnostics()` | `GET /eval/diagnostics` | **20 s polling** |

### Backend (`api/mkopo/routers/evals.py`)

All four read from the `task_runs` table. `task_runs` rows come from
two writers:

- `services/drift.py:run_drift_monitor()` — production accuracy
  computed by comparing the extractor's accepted values against staff
  overrides in the review queue. Writes one row per field per run.
- `scripts/seed_eval_baseline.py` — synthetic baseline rows so the
  page isn't empty on a fresh clone.

### What this means in practice

| You did this | Does the page update? |
|---|---|
| Ran `uv run python -m evals.runner` | **No.** CLI writes only to `evals/results/results.json` — not to `task_runs`. The page is unaware. |
| Clicked **Refresh** on the page (calls `POST /eval/refresh`) | **Yes** — re-runs `drift_monitor` and inserts new `task_runs` rows. |
| A staff member overrode a low-confidence extraction in the review queue | **Yes**, but only after the next `drift_monitor` run. There is no automatic trigger today. |
| New review-queue activity in the last 20s | The **diagnostics tile** updates (calibration + recent failures). Summary, fields, trend do NOT auto-refetch. |
| Background scheduled drift sweep | **Not wired.** `drift_monitor` only runs on the `/refresh` button (or test code). No cron, no arq job. |

### The big disconnect to flag

The CLI runner (`evals/runner.py`) and the page (`drift_monitor`) are
**two separate systems** that both call themselves "evals":

- **CLI runner** = golden-set regression test for the extractor /
  summarizer prompts. Compares prediction to a YAML reference. Writes
  to a JSON file. Used as a CI gate.
- **Drift monitor** = production accuracy computed from review-queue
  overrides. Writes to `task_runs`. Powers the dashboard.

There's no unified "eval run" that drives both. That's a chunk of
the planning work below.

---

## 2. What's missing for a credible lender-grade eval

Brief summary; full research with citations in
[§ 4 Industry references](#4-industry-references).

| Gap | Why it matters |
|---|---|
| Tiny golden sets (2, 1, 1 examples) | No statistical signal; 25–50 examples per task is the minimum for meaningful accuracy bands |
| Only extraction tasks scored | Decision verdict, AAL, intake email all unscored — and they're the highest-stakes outputs |
| Single accuracy metric per task | Real eval suites surface **precision, recall, F1, calibration, faithfulness** separately |
| No confusion matrix on the decision agent | SR 11-7 outcome analysis (the canonical US bank guidance) requires direction-of-error breakdown |
| No calibration metrics on confidence | Extractor + decision both emit confidence; no Expected Calibration Error (ECE) or Brier score is computed |
| No faithfulness / groundedness score | The #1 NIST AI 600-1 generative-AI risk is Confabulation; no online metric tracks it |
| No fair-lending metrics | Adverse Impact Ratio is the entry-level ECOA test; not present |
| No PSI / drift on inputs | Population Stability Index is bank-canonical for input-distribution drift; not present |
| CLI runner ↔ dashboard disconnect | CI run doesn't update the dashboard; harder to demo "model upgrade caused regression" |
| Page doesn't auto-refresh top-line metrics | A staff user clicking around won't see a fresh number unless they reload |

---

## 3. Phased plan

Three phases. Each phase is independently shippable — the dashboard
gets better at each step, you can stop at any phase.

### Phase 1 — Plumbing (ship-blocker fixes; ~1 day)

The most painful disconnect is that the CLI doesn't feed the
dashboard. Fix that first.

- [ ] **Unify the writers.** Update `evals/runner.py` to insert one
  `task_runs` row per task with a `kind="golden"` discriminator (the
  ORM already has `kind` — just needs to be populated correctly). The
  drift monitor's `kind="production"` rows stay as-is.
- [ ] **Update `GET /eval/summary` + `/eval/fields`** to read both
  kinds and surface them side-by-side. The dashboard already has
  `production_accuracy` / `golden_accuracy` columns; just wire the
  CLI to feed the golden side.
- [ ] **Add `refetchInterval: 60_000`** to summary, fields, trend
  queries so the page stays fresh without a manual reload. Drop
  diagnostics to 60s too (20s is overkill for slow-moving data).
- [ ] **Make the "last run" timestamp visible** on the dashboard
  header so users know how stale the numbers are.
- [ ] **Add a background arq job** that runs `drift_monitor` every
  hour. Existing arq infra in `workers/tasks.py`.

**Why this first:** without this, every other phase ships into a
dashboard that doesn't reflect reality. After Phase 1, "ran the
eval" → "saw new numbers" is reliable.

### Phase 2 — Industry-standard metric coverage

The four highest-value items in this phase **shipped** — the
``AggregatingEvalTask`` protocol lets each task emit a richer
JSON-serialisable details payload that lands in ``task_runs.details``;
new ``GET /eval/task-detail/{task_name}`` endpoint serves them.

- [x] **Decision verdict task** — 10 loan contexts across all three
  paths. Per-class precision/recall/F1 + macro-F1 + a confusion
  matrix in ``details``. SR 11-7 §VI outcome analysis. See
  ``evals/tasks/decision_verdict.py``.
- [x] **AAL fidelity task** — 6 decline scenarios. Four AND-ed
  criteria: principal_reasons_complete, friendly_label_in_body,
  no_rule_id_in_body, right_to_know_disclosure. Per-criterion pass
  rates in ``details``. CFPB Circular 2022-03 + 2023-09. See
  ``evals/tasks/aal_fidelity.py``.
- [x] **Calibration metrics on extractor confidence** — ECE (K=10)
  + Brier score on the last 30 days of resolved extractions.
  Reliability-diagram bins in ``details`` for the binned-bar UI.
  Scheduled at 3:30 AM UTC via
  ``workers/tasks.py::calibration_monitor``. Guo et al. 2017 +
  Spiess et al. 2024 backing. See ``services/calibration.py``.
- [x] **Score the adversarial-injection fixtures** — wired as
  ``adversarial_injection`` runner task, threshold 100%. Per-pattern
  pass rate in ``details``. See
  ``evals/tasks/adversarial_injection.py``.

Remaining for **Phase 2.5** (deferred — each substantial enough for
a focused turn):

- [x] **Intake email task per loan class** — implemented as
  ``evals/tasks/intake_email.py`` with 4 personal + 4 commercial
  fixtures (scale-up path ready — drop more YAMLs into
  ``evals/golden_sets/intake_email/``). Scores four AND-ed
  criteria: addressed-by-name, no-markdown, doc-asks-match-class,
  within-word-limit. Aggregates per-criterion pass rates AND a
  per-class (personal vs business) breakdown so a regression on
  one prompt is visible. Threshold 0.80. Dashboard surface:
  ``web/app/eval/cards/IntakeEmailCard.tsx``. Uses the live
  ``intake.draft_doc_request.{personal|business}`` prompts from
  the registry — i.e. measures the prompt-as-deployed.
- [x] **Faithfulness / groundedness on underwriting summaries** —
  Implemented as ``evals/tasks/uw_groundedness.py``. A pinned Opus
  judge decomposes the candidate summary into atomic claims and
  verifies each against ``source_excerpts`` (RAGAS Faithfulness;
  Es et al. 2024, arXiv:2309.15217) in a single structured call.
  Fixtures declare ``expected.expected_band`` ∈ {``high``,
  ``low``} so the gate measures *judge accuracy* — whether the
  judge correctly classifies clean vs hallucinated summaries.
  The aggregate exposes ``judge_accuracy``, ``avg_grounded_clean``,
  ``avg_grounded_hallucinated`` (so the discrimination gap is
  visible), and per-fixture rows. Initial fixture set: 4 (2 clean
  + 2 hallucination-planted); scale-up path is more YAMLs. Dashboard
  card: ``web/app/eval/cards/UWGroundednessCard.tsx``. Production-
  trend pass (RAGAS on the live underwriting_results stream) is
  the natural next step — defer until we want a continuous
  groundedness time series.
- [x] **Bigger extraction goldens** — bumped ``extract_borrower
  _entity`` from n=2 to n=7 (legal-form coverage: LLC, Inc, Corp,
  Trust, individual, multi-entity disambig) and ``extract_noi``
  from n=1 to n=6 (retail, industrial, office in millions,
  multifamily, tabular). Added three new field tasks for the
  load-bearing numerics: ``extract_appraised_value`` (LTV
  denominator, n=5), ``extract_credit_score`` (FICO floor, n=5),
  ``extract_loan_amount`` (LTV numerator / DTI input, n=5). Each
  fixture intentionally includes a distractor (loan amount next
  to appraised value; FICO vs Vantage; "as-is" vs "as-stabilized";
  borrower vs seller vs lender). Suite at 11 tasks; scale-up to
  25–30 fixtures each is just adding more YAMLs.
- [x] **Frontend rendering** — dashboard cards for the Phase 2
  payloads. Implemented as four cards under
  ``web/app/eval/cards/``:
  - ``DecisionVerdictCard`` — confusion matrix heatmap (row-
    normalized colour intensity, brand-green diagonal / soft-red
    off-diagonal) + per-class precision/recall/F1 pills + macro-F1
    headline.
  - ``AALFidelityCard`` — four horizontal bars, one per ECOA-load-
    bearing criterion. Tooltips name the regulation (CFPB Circular
    2022-03/2023-09, 12 CFR §1002.9(b)) each one enforces.
  - ``CalibrationCard`` — SVG reliability diagram with y=x
    reference line + bin-level over/under-confidence colour code,
    plus ECE + Brier pills.
  - ``AdversarialInjectionCard`` — per-pattern coverage list,
    sorted by rate ascending so any regression floats to the top.
  Mounted in a 2-column grid on ``/eval`` between per-field
  accuracy and the diagnostics row. All four refetch on 60s
  cadence + the existing manual Refresh.

### Phase 3 — Operational + compliance metrics (~3–5 days)

These are the "you're running this in production" metrics.

- [x] **Adverse Impact Ratio** — implemented as
  ``mkopo/services/fairness.py``. Bucketises decisioned loans
  (APPROVED / SERVICING / CONDITIONS / CLOSING / DECLINED, 90-day
  window) by a stable hash of ``loan.id`` (synthetic protected
  class — production path replaces with HMDA demographic). AIR
  = lowest-group approval rate ÷ highest-group approval rate.
  Banded ``ok`` ≥ 0.85, ``watch`` 0.80–0.85, ``concern`` < 0.80
  (EEOC four-fifths convention). Writes ``task_runs`` row only
  when AIR is computable (≥ 2 populated groups) so the trend
  chart doesn't pollute with 0.0 points on empty windows. Cron
  3:45 UTC, between calibration (3:30) and golden sweep (4:00).
  Manual refresh via ``POST /eval/fairness/refresh``. Dashboard
  surface: ``web/app/eval/cards/FairnessCard.tsx``. Explicitly
  documented as a screening heuristic, not a per-se finding
  (Watkins et al. 2024).
- [x] **PSI on input features** — implemented as
  ``mkopo/services/psi.py``. Quantile-binned numeric PSI for
  ``loan_amount`` (10 bins, equal-count from the reference);
  categorical PSI for ``loan_class`` (personal/business) and
  ``loan_type`` (bridge/permanent/refi/construction). Reference =
  prior 90-day window ending 30 days ago; current = last 30 days
  (with a 30-day gap so the windows don't trivially overlap).
  Bands per Siddiqi 2017 / FDIC SR 11-7: < 0.10 stable, 0.10–0.25
  minor, ≥ 0.25 major. Laplace-smoothed zero counts so a new bin
  appearing doesn't blow up the divergence. Skips the row-write
  below 30 samples in either window. Writes one ``task_runs`` row
  per feature (``task_name='psi.<feature>'``) so the trend chart
  can plot each independently. Cron at 3:50 UTC, between fairness
  (3:45) and golden sweep (4:00). Manual refresh via
  ``POST /eval/psi/refresh``. Dashboard surface:
  ``web/app/eval/cards/PSICard.tsx``. Future expansion to DSCR /
  LTV / FICO is just more queries — those features live on
  ``extractions`` / ``underwriting_results``, not ``loans``.
- [x] **Embedding-distribution drift on prompts** — implemented as
  ``mkopo/services/prompt_drift.py``. Unbiased U-statistic MMD²
  (Gretton et al. 2012) with an RBF kernel + median-heuristic σ
  on borrower inbound messages. Reference = prior 30d (with 7d
  gap); current = last 7d. Bands calibrated for OpenAI
  text-embedding-3-small @ 1024d: < 0.005 stable, 0.005–0.02
  minor, ≥ 0.02 major. Embeddings cached via the existing
  ``EmbeddingService`` so each unique body costs one API call.
  Cron 3:58 UTC.
- [x] **Tool-call accuracy** task — implemented as
  ``evals/tasks/tool_call_accuracy.py`` (5 fixtures). Scores
  Trajectory Inclusion (every expected tool called) + Negative
  Trajectory (no forbidden tool called, e.g. ``withdraw_application``
  on a status question) + Argument Correctness (required arg keys
  present). Mocked tool catalog mirrors the production
  ``mkopo/agents/tools/borrower.py`` surface. Threshold 0.75;
  aggregate exposes per-criterion + per-tool selection rates.
- [x] **Refusal / abstain rate trend** — implemented as
  ``mkopo/services/refusal.py``. Block-rate over last 7d vs
  prior 28d baseline (with 7d gap) on InjectionDetection.
  Binomial-proportion z-score; |z| ≥ 2 flips ``stable`` → ``spike``.
  Cron 3:52 UTC. Manual refresh via ``POST /eval/refusal/refresh``.
  Dashboard surface: ``web/app/eval/cards/RefusalCard.tsx``.
  Not a CI gate — operator attention metric.
- [x] **$/decision + p95 latency per agent** — implemented as
  ``mkopo/services/agent_economics.py``. Joins ``llm_calls`` ↔
  ``agent_runs.thread_id`` over the last 30 days; aggregates
  total cost ÷ run count + 95th-percentile per-call latency per
  agent. Pure-Python percentile (no numpy). Cron 3:55 UTC writes
  one ``task_runs`` row per agent (``task_name='economics.<agent>'``)
  so the trend chart can plot cost regression alongside accuracy.
  Read-only endpoint at ``GET /eval/agent-economics``. Dashboard
  surface: ``web/app/eval/cards/AgentEconomicsCard.tsx``.
- [x] **NIST AI 600-1 mapping** — implemented as ``web/app/eval/
  cards/NISTBadge.tsx``. Reusable badge primitive carrying the
  NIST 600-1 risk category (Confabulation, Harmful Bias, Data
  Privacy, Information Security, Information Integrity, Value
  Chain, Dangerous Content). Stamped on Fairness, UW
  Groundedness, Adversarial Injection, PSI, Refusal, Agent
  Economics cards (the regulator-load-bearing surfaces). Each
  badge tooltip quotes the NIST 600-1 section so the link to
  the framework is direct.
  Information Integrity, Data Privacy). Reviewer-facing
  documentation tied to the regulator's framework.

---

## 4. Industry references

### Eval frameworks (pick from)

| Framework | Best for | Source |
|---|---|---|
| **RAGAS** | RAG faithfulness, context precision/recall | [Es et al., EACL 2024](https://arxiv.org/abs/2309.15217), [docs.ragas.io](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) |
| **DeepEval** | G-Eval (custom LLM-judge with CoT), hallucination, RAG metrics | [deepeval.com docs](https://deepeval.com/docs/metrics-llm-evals) |
| **TruLens** | The "RAG triad" (context relevance, groundedness, answer relevance) — reference-free | [trulens.org RAG triad](https://www.trulens.org/getting_started/core_concepts/rag_triad/) |
| **Vectara HHEM-2.x** | Fast cross-encoder hallucination scoring (~184MB model, no LLM call) | [Vectara HHEM blog](https://www.vectara.com/blog/hhem-v2-a-new-and-improved-factual-consistency-scoring-model) |
| **HELM (Stanford CRFM)** | 7-axis benchmark: accuracy, calibration, robustness, fairness, bias, toxicity, efficiency | [Liang et al. 2022](https://arxiv.org/abs/2211.09110), [crfm.stanford.edu/helm](https://crfm.stanford.edu/helm/) |
| **MLflow LLM Evaluate** | Drop-in eval for existing MLflow shops; ROUGE, exact-match, custom judges | [MLflow LLM Evaluate docs](https://www.mlflow.org/docs/2.21.3/llms/llm-evaluate/) |
| **LangSmith** | Latency P50/P99, token, cost dashboards out of the box; integrates with LangGraph (we use it) | [LangSmith eval concepts](https://docs.langchain.com/langsmith/evaluation-concepts) |

### Calibration

- Expected Calibration Error (ECE) — [Guo et al., ICML 2017](https://arxiv.org/abs/1706.04599) — the canonical paper; report at K=10 bins.
- Brier score — strictly proper scoring rule; decomposes into reliability + resolution + uncertainty. [Spiess et al. 2024](https://arxiv.org/pdf/2402.02047) on LLM application.
- Reliability diagram (visual companion) — required for model cards. Guo et al. 2017.
- Verbalized confidence is poorly calibrated by default — [Tian et al. 2023](https://arxiv.org/pdf/2412.14737); CoT prompting improves it. Spiess et al. 2024.

### Per-output-type metrics

- **Extraction** (NOI, borrower name, etc.) — SQuAD-style Exact Match + token F1. [Rajpurkar et al.](https://aclanthology.org/D16-1264.pdf). For multi-entity, SemEval-2013 strict entity F1.
- **Free-form prose** — G-Eval (LLM-as-judge with CoT) outperforms ROUGE/BLEU/BERTScore on correlation with human judgement. [Liu et al., EMNLP 2023](https://aclanthology.org/2023.emnlp-main.153/). BERTScore is the strongest n-gram-ish alternative (~0.59 human-correlation vs 0.47–0.50 for BLEU/ROUGE per [arXiv 2407.00747](https://arxiv.org/html/2407.00747v1)).
- **Classification** (approve/decline) — per-class precision/recall/F1 + confusion matrix. SR 11-7 §VI on outcome analysis requires direction-of-error.
- **Agent trajectories** — Tool-call accuracy, Trajectory Inclusion, Trajectory Exact Match per [TRAJECT-Bench (arXiv 2510.04550)](https://arxiv.org/pdf/2510.04550) and the [LLM agent eval survey, arXiv 2507.21504](https://arxiv.org/html/2507.21504v1).

### Hallucination / faithfulness

- **RAGAS Faithfulness** — atomic-claim decomposition + per-claim entailment. Practical for CI. Es et al. 2023.
- **HHEM-2.x** — cross-encoder, fast, no LLM call. Practical for per-request gating. Vectara, 2024.
- **FActScore** — gold standard for accuracy, expensive (per-fact retrieval). [Min et al., EMNLP 2023](https://aclanthology.org/2023.emnlp-main.741/). Use offline.
- **RAGTruth** — 18K token-level annotations; for training detectors, not online use. [Niu et al., ACL 2024](https://aclanthology.org/2024.acl-long.585/).

### Regulated-industry frameworks

- **NIST AI RMF 1.0** ([NIST AI 100-1 PDF](https://nvlpubs.nist.gov/nistpubs/ai/nist.ai.100-1.pdf)) — GOVERN, MAP, MEASURE, MANAGE functions. MEASURE function explicitly requires quantitative bias / robustness / security tracking.
- **NIST AI 600-1 Generative AI Profile** ([PDF, July 2024](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)) — 12 generative-AI risk categories with >200 suggested actions. Lending-relevant: Confabulation, Harmful Bias, Information Integrity, Data Privacy, Human-AI Configuration.
- **EU AI Act** — credit scoring is high-risk (Annex III). Article 15 mandates declared accuracy + robustness + cybersecurity, post-market monitoring per Article 72. Enforceable Aug 2, 2026. [Article 15 text](https://artificialintelligenceact.eu/article/15/).
- **Federal Reserve SR 11-7** ([source](https://www.federalreserve.gov/supervisionreg/srletters/SR1107.htm)) — canonical US bank model risk management guidance. Requires: conceptual soundness, outcome analysis (backtesting + sensitivity), ongoing monitoring (drift triggers), independent validation. Generative AI explicitly carved out of the April 2026 [revised guidance](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.pdf) pending follow-on rulemaking.
- **CFPB Circular 2022-03 on Adverse Action notices** ([source](https://www.consumerfinance.gov/compliance/circulars/circular-2022-03-adverse-action-notification-requirements-in-connection-with-credit-decisions-based-on-complex-algorithms/)) — reasons given to borrower must be the principal reasons the model used; "the model is too complex" is not a defense. Reinforced by [Circular 2023-09](https://files.consumerfinance.gov/f/documents/cfpb_adverse_action_notice_circular_2023-09.pdf) for AI-based scoring.
- **Adverse Impact Ratio** — protected-group approval rate / control approval rate; <0.80 = practically significant (four-fifths rule, originally EEOC). The mapping to ECOA disparate impact is contested in the legal literature — see [Watkins et al., FAccT 2024](https://facctconference.org/static/papers24/facct24-53.pdf). Treat as a screening trigger, not a safe harbor.

### Cost + latency reference numbers (2026)

From [Token Mix benchmarks](https://tokenmix.ai/blog/ai-api-latency-benchmark) + Anthropic + OpenAI pricing docs:

- **TTFT P50**: 400–600ms Haiku 4.5; 450–500ms GPT-5.4 / Sonnet 4.6.
- **TTFT P95/P50 ratio**: ~1.8× Anthropic; ~2.7× OpenAI.
- **Output speed**: 85–90 tok/s frontier closed models.
- **$/1M input tokens**: $1 Haiku 4.5, $3 Sonnet 4.6, $5 Opus 4.7, $2.50 GPT-5.4.
- **$/1M output tokens**: $5/$15/$25 Anthropic tiers; $15 GPT-5.4.
- **Cache hit discount**: up to 90% (Anthropic).
- **Batch API discount**: 50% (Anthropic).

For an origination pipeline the load-bearing SLOs are
**$/decision** (sum across all LLM steps), **end-to-end P95
latency** (extraction + summary + decision), and **cache-hit ratio**.

### Drift detection

- **PSI** — < 0.10 stable; 0.10–0.25 minor; > 0.25 major. Bank-canonical, regulator-friendly. [Fiddler AI PSI](https://www.fiddler.ai/blog/measuring-data-drift-population-stability-index).
- **KS** on continuous features; **chi-square** on categorical. p < 0.05 = shift. [AWS drift detection guide](https://docs.aws.amazon.com/prescriptive-guidance/latest/gen-ai-lifecycle-operational-excellence/prod-monitoring-drift.html).
- **Embedding drift** via MMD or clustering (KS struggles in high-dim). [Evidently AI on embedding drift](https://www.evidentlyai.com/blog/embedding-drift-detection).
- **Eval-replay on trigger** — re-run golden set when PSI > 0.10 or weekly minimum. Direct accuracy measurement, not a proxy.

---

## 5. Recommended starter dashboard

Twelve metrics, prioritised. The first 6 are compliance-load-bearing
(you can defend each to a regulator or auditor by name); the last 6
are operational hygiene that production teams need anyway.

| # | Metric | Output type | Stakeholder | Source / standard |
|---|---|---|---|---|
| 1 | Extraction EM + token F1 per field | Structured extraction | UW QA | SQuAD; SR 11-7 outcome analysis |
| 2 | Decision F1 + confusion matrix | Classification | UW lead, MRM | SR 11-7 backtesting in its most direct form |
| 3 | Faithfulness / Groundedness on memos (RAGAS or HHEM-2) | Free-form prose | UW lead, compliance | NIST AI 600-1 #1 risk (Confabulation) |
| 4 | Expected Calibration Error + Brier score | Confidence | MRM | Guo et al. 2017; Spiess et al. 2024 |
| 5 | Adverse Impact Ratio per protected class | Decision | Fair-lending, compliance | ECOA / Reg B; CFPB Circular 2023-09 |
| 6 | Principal-reason fidelity rate | AAL notices | Compliance, legal | CFPB Circular 2022-03 |
| 7 | PSI per input feature vs baseline | Input drift | MRM, SRE | Bank-canonical since early 2000s |
| 8 | Prompt-embedding drift (MMD) | Semantic drift | SRE | NIST AI RMF MEASURE |
| 9 | Tool-call accuracy + Trajectory Inclusion | Agent trajectory | ML eng | TRAJECT-Bench, T-Eval |
| 10 | End-to-end decision P95 latency + $/decision | Operational | SRE, finance | Standard SLO |
| 11 | Refusal / abstain rate trend | Behavioral | Product, compliance | Cheap leading indicator |
| 12 | Eval-replay pass rate on frozen golden set | Regression | All | SR 11-7 change management |

Practical opening cut: 1–6 + 10 + 12. Add 7–9 once the first eight
are stable.

---

## 6. Contested calls (be honest about these)

- **Adverse Impact Ratio's 0.80 threshold** comes from EEOC, not
  ECOA. Recent peer-reviewed work argues that mechanically applying
  four-fifths to lending is "epistemic trespassing" ([Watkins et al.
  FAccT 2024](https://facctconference.org/static/papers24/facct24-53.pdf)).
  Use as screening trigger, not as a legal safe harbor. The April
  2026 CFPB final rule on ECOA disparate-impact enforcement changes
  the legal landscape.
- **LLM-as-judge** correlates with human judgement at ~85% but
  shows self-preference bias ([G-Eval paper](https://aclanthology.org/2023.emnlp-main.153/)).
  Calibrate any judge to your domain before trusting it; periodically
  spot-check with humans.
- **No single calibration metric** is unambiguous. ECE is sensitive
  to bin width; pair it with Brier + reliability diagram.
- **SR 11-7 carves generative AI out** in the April 2026 revised
  guidance. Banks are extending the existing framework by analogy —
  there is no agreed standard for generative-AI MRM yet. The
  dashboard *is* the documentation.

---

## 7. What to do now vs later

**Now (deferred per user request).**

**Picking it up later, in order of value:**

1. Phase 1 plumbing (1 day) → CLI feeds dashboard, auto-refresh works.
2. Decision verdict task + confusion matrix (1 day) → ships SR 11-7 outcome analysis.
3. Calibration card (½ day) → ECE + Brier on extractor confidence.
4. Faithfulness card (1 day) → groundedness on every summary.
5. Wire the adversarial-injection fixtures as a scored task (½ day) → CI gate on detector regressions.
6. AAL fidelity task (½ day) → CFPB Circular 2022-03 covered.
7. Bigger goldens (ongoing) → 25–30 examples per task.
8. Operational metrics ($/decision, p95) on the eval page (½ day).
9. Adverse Impact Ratio (1 day) → fair-lending screen.
10. PSI + embedding drift (1–2 days) → input + semantic drift.

Total: ~7–10 days to a credibly comprehensive dashboard.
