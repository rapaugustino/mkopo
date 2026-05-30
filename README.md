# Mkopo

> Auditable, agentic loan origination for private lenders.

Mkopo is an LLM-augmented credit-origination system built around one
non-negotiable: every claim is traceable to source, and the
deterministic rules engine has final say. The frontier-LLM does the
interpretive work — reading documents, drafting borrower
communications, composing cited summaries — while a Python policy
engine, structured outputs, and a cryptographic decision-integrity
hash keep the system honest.

This is a portfolio project. Synthetic data, scoped feature set,
designed for clarity. **Everything that looks production-ready but
isn't** is called out explicitly in [SCOPE.md](docs/SCOPE.md);
production gaps are documented in
[ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[SAFETY.md](docs/SAFETY.md); every dashboard metric's formula +
source file is in [METRICS.md](docs/METRICS.md). The in-app `/help`
page surfaces the same glossary + scope content so a reader can
resolve any acronym they see on the dashboard with one click.

---

## The interesting bits

- **Three agents that chain autonomously**: intake → underwriting →
  decision, all LangGraph with durable Postgres checkpoints. One
  ``POST /agents/intake/run`` on an autonomous loan cascades through
  all three, stopping only at human-required commitment gates
  (borrower email send, decision transmission).
- **The rules engine overrides the model on conflict**: if the
  decision agent's LLM picks ``approve`` but the engine detected a
  blocking failure, the server rewrites to ``decline`` and audits the
  override. The LLM cannot ship a verdict the rules don't support.
- **Cryptographic decision integrity**: every decision is stamped
  with a sha256 of the inputs that produced it (documents,
  extractions, parties, meta). If any input changes, the system
  detects drift and blocks forward stage transitions until the
  decision is re-run.
- **Source-grounded citations**: the underwriting summary cites
  extracted-field keys. Each chip is clickable — opens a side drawer
  showing the exact document quote the value came from. The "did the
  AI hallucinate?" question has a one-click answer.
- **Hybrid RAG** (dense pgvector + sparse tsvector + RRF fusion) for
  the staff "Ask the file" feature; a separate kNN over loan
  embeddings for the comparable-loans inspector.
- **Prompt registry with version stamping**: every LLM call records
  the ``prompts.id`` of the active version that produced it. Promote
  / rollback / diff are first-class.
- **Materials hash + stage locks**: once past the decision stage,
  agents are server-side-locked from re-running. The audit trail
  can't be retroactively edited.

For the full design + reasoning, see
[**ARCHITECTURE.md**](docs/ARCHITECTURE.md).
For what the demo does and does NOT cover, see
[**SCOPE.md**](docs/SCOPE.md).
For every metric on every dashboard with its formula + source,
see [**METRICS.md**](docs/METRICS.md).
For the hallucination-mitigation story, see
[**SAFETY.md**](docs/SAFETY.md).
For sample workflows with sequence diagrams, see
[**WORKFLOWS.md**](docs/WORKFLOWS.md).
For acronym definitions, see [**GLOSSARY.md**](docs/GLOSSARY.md)
(or the in-app `/help` page).
For interview talking points (project owner reference), see
[**INTERVIEW.md**](docs/INTERVIEW.md).

---

## Quick start (local)

You need:

- **Python 3.12+** with [`uv`](https://docs.astral.sh/uv/) installed
- **Node 20+** with **npm**
- **PostgreSQL 16** on ``localhost:5432`` with the
  [`pgvector`](https://github.com/pgvector/pgvector) extension
  installed — see [§ Installing pgvector](#installing-pgvector)
- **Redis** on ``localhost:6379`` (only required for the background
  worker; auth degrades open without it)

### 1. Create the database (one-time, as a superuser)

Extension creation requires superuser privileges, so this is split
from the migration on purpose — the app's runtime role (``mkopo``)
doesn't get superuser. Connect as your Postgres superuser (e.g.
``psql postgres`` or pgAdmin) and run:

```sql
CREATE ROLE mkopo WITH LOGIN PASSWORD 'mkopo';
CREATE DATABASE mkopo OWNER mkopo;
\c mkopo
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO mkopo;
```

> **About `CREATE EXTENSION vector`:**
> 1. It must run *inside the ``mkopo`` database*. In ``psql``, that's
>    what the ``\c mkopo`` line above does. In pgAdmin/DBeaver,
>    reconnect to ``mkopo`` first. Otherwise the extension lands in
>    ``postgres`` and the migration fails with
>    ``type "vector" does not exist``.
> 2. Run it as a **superuser** (typically your OS user on Postgres.app),
>    not as the ``mkopo`` app user. Extensions are DBA territory.
>
> If you get ``extension "vector" is not available``, pgvector itself
> isn't installed yet — see [§ Installing pgvector](#installing-pgvector).

### 2. Backend

```bash
cd api
cp .env.example .env
uv sync --extra dev
uv run alembic upgrade head
uv run python scripts/seed.py
uv run python scripts/seed_eval_baseline.py    # optional, populates the eval dashboard
uv run uvicorn mkopo.main:app --reload
```

**Six env vars matter.** The startup banner reports which ones are
wired vs degraded:

| Var | What it unlocks | If unset |
|---|---|---|
| ``ANTHROPIC_API_KEY`` | Every agent + the LLM gateway | Agents fail to run |
| ``OPENAI_API_KEY`` | RAG + comparable-loans kNN | Those features disabled |
| ``RESEND_API_KEY`` | Outbound email from the intake agent | Email send fails |
| ``RESEND_FROM_ADDRESS`` | Mailbox on a Resend-verified domain | Resend rejects send |
| ``RESEND_WEBHOOK_SECRET`` | Authenticates inbound borrower replies | Inbound accepts unauth (dev only) |
| ``STORAGE_BACKEND`` | ``local`` (default) or ``s3`` | Defaults to local under ``./var/storage`` |

For S3 also set ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
``AWS_REGION``, and ``S3_BUCKET``.

### 3. Worker (optional — only needed for background intake jobs)

```bash
cd api
uv run arq mkopo.workers.tasks.WorkerSettings
```

### 4. Frontend

```bash
cd web
cp .env.local.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>.

### 5. Sign in

Mkopo is JWT-authed end-to-end — the staff console and the borrower
portal each have their own login flow with separate cookies +
audiences. The seed creates two staff accounts:

| Email | Password | Role |
|---|---|---|
| ``j.davis@mkopo.dev`` | ``password123`` | underwriter |
| ``admin@mkopo.dev`` | ``password123`` | admin |

Go to <http://localhost:3000/staff/login> and sign in with either.
The login page surfaces these credentials in dev mode; production
deployments don't.

Borrower self-signup lives at <http://localhost:3000/apply> (no
seeded account needed — anyone can create one).

### 6. Trying the autonomous chain

After seeding, find a personal-loan fixture (``LN-2026-1011`` ships
with full required docs). On the loan detail page:

1. Flip the autonomy toggle to ``autonomous``.
2. Click **Extract documents** (triggers the intake agent).
3. Watch the chain: intake completes → orchestrator advances stage
   to ``underwriting`` → underwriting agent runs → orchestrator
   advances to ``decision`` → decision agent drafts the verdict.

Stage ends at ``decision`` with ``risk_band=low`` and three completed
agent runs. The chain stops there because the next step (sending the
term sheet) is a real-world commitment with no easy undo.

---

## Auth + security

Two surfaces, two cookie-based JWT sessions, designed not to leak
into each other.

### Staff console (underwriters, admins)

- **Login**: ``POST /api/v1/staff/auth/login`` (email + password) →
  sets a ``mkopo_staff_session`` httpOnly cookie + returns the JWT
  in the body for CLI / script callers. Frontend uses the cookie
  exclusively.
- **Token shape**: HS256, audience ``mkopo-staff``, issuer
  ``mkopo-staff-api``, 12h TTL by default.
- **Per-token revocation**: every JWT carries a UUID ``jti``;
  logout adds it to a Redis blacklist for the token's remaining
  lifetime so a stolen token stops working immediately.
- **Rate limits**: 10 login attempts / 5 min per email. After 6
  failures the account locks for 30 minutes (Redis flag).
- **Dev shortcut**: the legacy ``dev_api_token`` bearer is honoured
  only in ``environment="development"`` (the default in local
  ``.env``). In production it's rejected with 401 — there is no
  backdoor in any other environment.

### Borrower portal

- **Login**: ``POST /api/v1/borrower-auth/login`` (password) OR
  ``POST /api/v1/borrower-auth/magic-link/request`` → sets a
  separate ``mkopo_session`` cookie. Audience ``mkopo-borrower``.
- **Sensitive ops** (withdraw, erasure) require a fresh-auth
  challenge token from ``POST /me/challenge`` — even a valid
  session cookie isn't enough to trigger an irreversible action
  without re-entering the password.
- **Soft-delete + retention windows**: erasure marks the account +
  loans soft-deleted with HMDA (5y) / Reg B (25mo) retention timers
  before the row is permanently dropped.

### Cookie + JWT isolation

- Distinct cookie names (``mkopo_session`` vs ``mkopo_staff_session``).
- Distinct JWT audiences — a borrower token presented at a staff
  endpoint fails to decode (and vice versa) even if both cookies
  coexist on the same domain. Tested in ``test_staff_auth_jwt.py``.

### Other security work

- **Server-side AAL override**: if the LLM picks ``approve`` but
  the engine flagged a BLOCKING failure, the server rewrites to
  ``decline`` before persistence and audits the override.
- **Cryptographic decision integrity**: every decision is stamped
  with a sha256 of its inputs; mismatch blocks forward stage
  transitions until the agent re-runs.
- **Storage authz**: every ``get_object`` / ``presigned_url``
  enforces the loan_id cross-check, so a leaked URI can't pivot to
  another loan's documents.
- **Input-layer prompt-injection detector** (``agents/injection.py``):
  hybrid regex catalog + Haiku second-pass scans every document
  upload, chat message, and inbound text input. Fail-closed on
  high-severity matches; logged on medium/low.
- **Constitutional judge on every LLM-drafted artifact**
  (``agents/guardrails.py``): bounded Self-Refine loop, all three
  agents wired in via shared ``make_validator_node`` helpers.

See [`docs/SAFETY.md`](docs/SAFETY.md) for the full hallucination-
mitigation audit.

---

## Installing pgvector

The Alembic migration runs ``CREATE EXTENSION vector``, which
requires the pgvector shared library to be present on the Postgres
server. Pick whichever path matches your setup:

**From source (any Postgres on macOS/Linux, including Postgres.app):**

```bash
export PATH="/Applications/Postgres.app/Contents/Versions/latest/bin:$PATH"  # macOS only
cd /tmp
git clone --branch v0.8.2 https://github.com/pgvector/pgvector.git
cd pgvector
make
make install
```

**Homebrew (system Postgres only — not Postgres.app):**

```bash
brew install pgvector
```

**Debian / Ubuntu:**

```bash
sudo apt install postgresql-16-pgvector
```

Verify with ``SELECT * FROM pg_available_extensions WHERE name = 'vector';``
— you should see a row.

---

## Pre-commit hooks

```bash
uv tool install pre-commit
pre-commit install
```

Every ``git commit`` then runs ruff format + check on Python and
Prettier on TypeScript. Bypass with ``--no-verify`` only when fixing
the hook itself.

---

## Eval suite

Runs labeled golden-set tasks against the live LLM gateway, prints
per-task accuracy, and exits non-zero on any task below its
threshold (so it's CI-gateable).

```bash
cd api
uv run python -m evals.runner
```

Needs ``ANTHROPIC_API_KEY`` set in ``api/.env``. Each run:

1. Writes per-task accuracy to ``api/evals/results/results.json``
   (CI artifact, exit-code-gated).
2. **Inserts one ``task_runs`` row per task with ``source='golden'``**
   so the ``/eval`` dashboard shows the run alongside the production
   drift numbers. Interactive runs and the scheduled sweep both
   update the dashboard.

### Scheduled golden sweep

The arq worker runs the same golden suite nightly at 4 AM UTC
(``mkopo/workers/tasks.py::golden_eval_sweep``). Combined with the
3 AM UTC drift monitor, the dashboard's ``Production accuracy`` vs
``Golden baseline`` tiles always have near-aligned data points.
Start the worker with:

```bash
cd api
uv run arq mkopo.workers.tasks.WorkerSettings
```

### What runs today

| Task | Threshold | Golden-set size | What it pins |
|---|---|---|---|
| ``extract_borrower_entity`` | 95% | 7 | Extractor returns the canonical borrower entity name across legal forms (LLC / Inc / Corp / Trust / individual) and multi-entity disambiguation |
| ``extract_noi`` | 90% | 6 | Extractor returns the right annual NOI across multifamily / retail / industrial / office (incl. millions notation + tabular formats) |
| ``summarize_underwriting`` | 80% | 1 | Summary cites the right rule outcomes + uses the correct vocabulary for the loan class |
| ``extract_appraised_value`` | 90% | 5 | LTV denominator: reconciled appraised value across multifamily / warehouse / retail / office (as-is vs as-stabilized) + loan-amount distractors |
| ``extract_credit_score`` | 95% | 5 | FICO floor input: single-bureau, tri-merge middle score, FICO vs Vantage disambiguation, and narrative-form ("seven hundred forty-five") parsing |
| ``extract_loan_amount`` | 90% | 5 | LTV numerator / DTI input: commercial + personal, millions notation, refinance with existing-balance distractors |
| ``adversarial_injection`` | 100% | 2 | Input-layer detector blocks every documented attack pattern at HIGH severity. CI fails on any miss |
| ``decision_verdict`` | 85% | 10 | Decision LLM picks the right path (approve / conditional / decline). Aggregate emits per-class precision/recall/F1 + macro-F1 + a confusion matrix (SR 11-7 outcome analysis) |
| ``aal_fidelity`` | 75% | 6 | AAL drafter cites every blocking rule, uses friendly labels not rule_id tokens, includes the ECOA right-to-know disclosure (CFPB Circular 2022-03). Per-criterion pass rates surface on the dashboard |
| ``intake_email`` | 80% | 8 | Borrower doc-request email drafter: addressed by name, no markdown, doc asks match loan class (personal vs business), ≤ 130 words. Per-class breakdown on the dashboard |
| ``uw_groundedness`` | 80% | 4 | RAGAS-style faithfulness (Es et al. 2024) — pinned Opus judge decomposes the UW summary into atomic claims and verifies each against source. Gate measures judge accuracy on clean vs planted-hallucination fixtures |
| ``tool_call_accuracy`` | 75% | 5 | Borrower-chat tool selection: every expected tool called, no forbidden mutating tool called for read-only intents, required arg keys present. Aggregate exposes per-criterion + per-tool selection rate |

### Production monitors

Five non-CLI monitors run on the arq scheduler alongside the golden
sweep. Each writes ``task_runs`` rows so the ``/eval`` dashboard's
trend chart picks them up; each has a manual ``POST /eval/<name>/refresh``
for on-demand recomputation from the dashboard.

| Monitor | Cron (UTC) | What it pins | Card |
|---|---|---|---|
| ``services/drift.py`` | 3:00 | Per-extraction-field accuracy from staff overrides in the review queue. ``source='production'`` rows that pair against the golden baselines | Per-field bars + the trend chart |
| ``services/calibration.py`` | 3:30 | Expected Calibration Error + Brier score on the last 30 days of resolved extractions. Guo et al. 2017 | ``CalibrationCard`` — reliability diagram + ECE/Brier pills |
| ``services/fairness.py`` | 3:45 | Adverse Impact Ratio (EEOC four-fifths rule) on decisioned loans, bucketed by synthetic protected class (production replaces with HMDA) | ``FairnessCard`` |
| ``services/psi.py`` | 3:50 | Population Stability Index on loan_amount + loan_class + loan_type vs prior-90d reference (Siddiqi 2017 / FDIC SR 11-7) | ``PSICard`` |
| ``services/refusal.py`` | 3:52 | Injection-detector block rate this week vs prior 28d baseline; binomial z-score for spike detection | ``RefusalCard`` |
| ``services/agent_economics.py`` | 3:55 | Per-agent $/run + p95 latency over the last 30 days. Joins ``llm_calls`` ↔ ``agent_runs.thread_id`` | ``AgentEconomicsCard`` |
| ``services/prompt_drift.py`` | 3:58 | MMD² (Gretton et al. 2012) on borrower-inbound message embeddings, last 7d vs prior 30d. Catches semantic shifts PSI can't see | (trend chart row) |

### Eval dashboard

The runner's results feed the staff-facing ``/eval`` page. Layout:

- **Sticky in-page TOC** at the top — five sections (Overview /
  Extraction / Golden gate / Production drift / Operations). Click
  a pill, smooth-scroll, URL hash updates so deep-links work.
- **Overview**: headline accuracy + trend chart (last 30 days, two
  series per task: production solid, golden dashed).
- **Extraction**: per-field accuracy bars with "Investigate" links
  to the filtered review queue when a field drifts ≥ 3pp below
  baseline.
- **Golden gate**: confusion matrix (decision verdict), per-
  criterion bars (AAL fidelity), per-pattern coverage (adversarial
  injection), per-class email compliance, RAGAS faithfulness.
- **Production drift**: calibration reliability diagram, fairness
  AIR with per-group counts, PSI per-feature bars, refusal-rate
  spike detection.
- **Operations**: per-agent $/run + p95 latency, confidence
  calibration accept-rate, review-queue throughput, agent
  reliability, recent failures with drill-through to the LLM-call
  / agent-run detail drawers.

Most cards carry a **NIST AI 600-1 risk-category badge** in the
footer (Confabulation, Harmful Bias, Information Security,
Information Integrity, Value Chain) — hover the badge for the
section citation.

``scripts/seed_eval_baseline.py`` populates the dashboard with
synthetic baseline data so a fresh clone isn't empty.

### Adding a new task

1. Drop a YAML example in ``evals/golden_sets/<task_name>/``.
2. Add a task class in ``evals/tasks/<task_name>.py`` with
   ``name``, ``threshold``, ``predict(example)``, ``score(pred, expected)``.
3. Register it in ``_main`` inside ``evals/runner.py``.
4. CI gate picks it up automatically.

---

## Project structure

```
mkopo/
├── api/                            FastAPI backend
│   ├── mkopo/
│   │   ├── main.py                 app entrypoint, router mounting, startup checks
│   │   ├── llm_gateway.py          single Anthropic SDK choke point
│   │   ├── models/                 SQLAlchemy ORM (one file per domain)
│   │   ├── schemas/                Pydantic request/response models
│   │   ├── routers/                REST endpoints (loans, agents, settings, evals/ package, ...)
│   │   ├── services/               business logic (loans, locks, materials_hash, ...)
│   │   ├── agents/
│   │   │   ├── intake.py           extract → identify_missing → draft_request → HITL → send
│   │   │   ├── underwriting.py     fetch_and_evaluate → draft_summary → persist
│   │   │   ├── decision.py         fetch_and_evaluate → draft_decision → persist
│   │   │   ├── orchestrator.py     autonomous-mode hooks that chain agents
│   │   │   ├── streaming.py        SSE wrapper + AgentRun lifecycle
│   │   │   └── tools/              borrower + staff chat tool catalogs
│   │   ├── rules/policy.py         deterministic credit policy
│   │   └── workers/tasks.py        arq background jobs
│   ├── evals/                      golden-set eval harness
│   ├── alembic/versions/           migrations (0001 — 0020)
│   ├── scripts/seed.py             seed runner (orchestration)
│   ├── scripts/seed_fixtures/      per-domain SeedLoan fixtures
│   ├── tests/                      286 tests — math, trajectories, gates
│   └── pyproject.toml
├── web/                            Next.js 16 frontend (App Router, React 19)
│   ├── app/
│   │   ├── loans/[id]/             case-file workspace
│   │   ├── apply/                  5-step borrower wizard (per-step files under steps/)
│   │   ├── account/                borrower portal
│   │   ├── help/                   in-app glossary + scope + how-to-read
│   │   ├── settings/               institution settings (lender contact + ECOA disclosures)
│   │   ├── observability/          LLM calls + agent runs + errors + safety tab
│   │   ├── safety/                 input-side injection detections + constitutional judge rollup
│   │   ├── eval/                   drift + calibration + reliability dashboard
│   │   ├── prompts/                versioned prompt registry editor
│   │   ├── review-queue/           low-confidence extractions for human review
│   │   └── components/             CommandPalette (⌘K), CitedSource drawer, MaterialsFlow, ...
│   ├── lib/api.ts                  typed API client (runtime + helpers)
│   ├── lib/api-types.ts            type definitions (re-exported via lib/api.ts)
│   └── lib/formatting.ts, humanize.ts
├── docs/
│   ├── ARCHITECTURE.md             system design + mermaid diagrams
│   ├── SCOPE.md                    in / out of scope (read this first)
│   ├── METRICS.md                  every metric's formula + source file
│   ├── GLOSSARY.md                 acronym definitions (mirrors /help)
│   ├── SAFETY.md                   hallucination-mitigation audit
│   ├── WORKFLOWS.md                sample workflows + sequence diagrams
│   ├── EVAL_PLAN.md                eval surface state + roadmap
│   └── INTERVIEW.md                interview talking points
├── samples/                        labeled sample loan packets
├── TESTING_GUIDE.md                end-to-end click-through scripts
└── README.md
```

---

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI 0.136, Uvicorn |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Migrations | Alembic |
| Database | PostgreSQL 16 + pgvector |
| Cache / rate-limit | Redis 7 |
| Agents | LangGraph 1.1 + langgraph-checkpoint-postgres |
| LLM | Anthropic Claude (Opus + Sonnet) via gateway abstraction |
| Embeddings | OpenAI text-embedding-3-small (1024-dim) |
| Background jobs | arq |
| Email | Resend (outbound + inbound webhook) |
| Storage | Local FS or S3 (toggled by ``STORAGE_BACKEND``) |
| Frontend | Next.js 16 (App Router), React 19, Tailwind v4 |
| Frontend data | TanStack Query, motion/react |
| Borrower auth | JWT + bcrypt + magic links (Redis-backed revocation) |

---

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture, agent design, state machine, materials hash, scalability + production gaps
- [docs/SCOPE.md](docs/SCOPE.md) — what the demo does and explicitly does NOT cover
- [docs/METRICS.md](docs/METRICS.md) — every dashboard metric with its formula, source file, data table, window, and academic / regulatory reference
- [docs/GLOSSARY.md](docs/GLOSSARY.md) — acronym definitions (same content as the in-app `/help` page)
- [docs/SAFETY.md](docs/SAFETY.md) — hallucination-mitigation audit
- [docs/WORKFLOWS.md](docs/WORKFLOWS.md) — sample workflows with sequence diagrams
- [docs/EVAL_PLAN.md](docs/EVAL_PLAN.md) — current eval surface + phased plan to lender-grade
- [docs/INTERVIEW.md](docs/INTERVIEW.md) — interview talking points (project owner reference)
- [TESTING_GUIDE.md](TESTING_GUIDE.md) — clickable end-to-end scripts for every flow

---

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

Inspired by [Applied Business Software / The Mortgage Office](https://themortgageoffice.com),
which has served the private lending industry since 1978. This project
is independent and not affiliated with ABS.
