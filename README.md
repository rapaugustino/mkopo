# mkopo

> AI-first loan origination for private lenders.

Mkopo is an auditable, agentic loan origination system. LLMs handle the messy interpretive work — reading documents, drafting communications, surfacing risks. A deterministic rules engine enforces hard policy. Every action is traceable.

This repo is a portfolio project — synthetic data, scoped feature set, designed for clarity over coverage.

## Quick start (local)

You need:

- **Python 3.12+** with [`uv`](https://docs.astral.sh/uv/) installed
- **Node 20+** with **npm**
- A **PostgreSQL 16** server running on `localhost:5432` with the [`pgvector`](https://github.com/pgvector/pgvector) extension installed (see [§ Installing pgvector](#installing-pgvector) below)
- A **Redis** server running on `localhost:6379` (only needed if you want to run the background worker)

### 1. Create the database (one-time, as a superuser)

Extension creation requires superuser privileges, so this is split from the migration on purpose — the app's runtime role (`mkopo`) doesn't get superuser. Connect as your Postgres superuser (e.g. via `psql postgres` or pgAdmin) and run:

```sql
CREATE ROLE mkopo WITH LOGIN PASSWORD 'mkopo';
CREATE DATABASE mkopo OWNER mkopo;

\c mkopo
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO mkopo;
```

> **Two important details about the `CREATE EXTENSION vector` line:**
>
> 1. It must run *inside the `mkopo` database*. In `psql`, that's what the `\c mkopo` line above does. In a GUI (pgAdmin/DBeaver), disconnect and reconnect to the `mkopo` database before running it. Otherwise the extension lands in `postgres` (or wherever you were connected), the migration fails with `type "vector" does not exist`, and you'll wonder why.
> 2. You must run it as a **superuser** (typically the OS user, e.g. `richardpallangyo` on macOS Postgres.app), *not* as the `mkopo` app user. The app user lacks `CREATE EXTENSION` privilege by design — extensions are DBA territory.
>
> If `CREATE EXTENSION vector` errors with `extension "vector" is not available`, you haven't installed pgvector at the server level yet — see [§ Installing pgvector](#installing-pgvector).

If you'd rather use your own credentials, edit `DATABASE_URL` and `DATABASE_URL_SYNC` in `api/.env`.

### 2. Backend

```bash
cd api
cp .env.example .env
uv sync --extra dev
uv run alembic upgrade head
uv run python scripts/seed.py
uv run python scripts/seed_eval_baseline.py    # optional: golden baseline for the eval dashboard
uv run uvicorn mkopo.main:app --reload
```

**Six env vars matter** in `api/.env`. The app boots either way and the
startup banner reports which ones are wired vs degraded:

| Var | What it unlocks | If unset |
|---|---|---|
| `ANTHROPIC_API_KEY` | Every agent + the LLM gateway | Agents will fail to run |
| `OPENAI_API_KEY` | RAG ("Ask the file") + comparable-loans kNN | Those features are disabled |
| `RESEND_API_KEY` | Outbound email from the intake agent | Email send fails at the final node |
| `RESEND_FROM_ADDRESS` | Mailbox on a Resend-verified domain (default: `mkopo@ubunifutech.com`) | Resend rejects the send |
| `RESEND_WEBHOOK_SECRET` | Authenticates inbound borrower replies | Inbound webhook accepts without auth (dev only) |
| `STORAGE_BACKEND` | `local` (default) or `s3` for document storage | Local filesystem under `./var/storage` |

For the S3 backend also set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_REGION`, and `S3_BUCKET`. The boot-time check prints exactly which
ones are missing.

### 3. Worker (optional — only needed for the background intake job)

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

Open <http://localhost:3000> and click into the seeded Atlas Holdings loan to see the case file timeline. Click **Run intake agent** to kick off the LangGraph workflow.

## Installing pgvector

The Alembic migration runs `CREATE EXTENSION vector`, which requires the pgvector shared library to be present on the Postgres server. Pick whichever path matches your setup:

**From source (works for any Postgres on macOS/Linux, including Postgres.app):**

Make sure `pg_config` for your target Postgres is first on `PATH` (Postgres.app: `export PATH="/Applications/Postgres.app/Contents/Versions/latest/bin:$PATH"`), then:

```bash
cd /tmp
git clone --branch v0.8.2 https://github.com/pgvector/pgvector.git
cd pgvector
make
make install
```

The `CREATE EXTENSION vector` SQL itself is run for you by `alembic upgrade head` — you don't need to run it by hand.

**Homebrew (macOS, system Postgres only — not Postgres.app):**

```bash
brew install pgvector
```

**Debian / Ubuntu:**

```bash
sudo apt install postgresql-16-pgvector
```

Verify with `SELECT * FROM pg_available_extensions WHERE name = 'vector';` — you should see a row.

## Pre-commit hooks (optional but recommended)

Once after cloning:

```bash
uv tool install pre-commit
pre-commit install
```

After that every `git commit` runs ruff format + check on the Python side and Prettier on the TS side. Bypass with `git commit --no-verify` if you ever need to.

## Run the eval suite

```bash
cd api
uv run python -m evals.runner
```

You'll see per-task accuracy against the golden set, with the gate failing if any task falls below threshold.

## Project structure

```
mkopo/
├── api/                            # FastAPI backend
│   ├── mkopo/
│   │   ├── main.py                 # FastAPI app
│   │   ├── config.py               # pydantic-settings
│   │   ├── db.py                   # async SQLAlchemy
│   │   ├── llm_gateway.py          # schema-gated LLM client
│   │   ├── models/                 # ORM models
│   │   ├── schemas/                # Pydantic request/response models
│   │   ├── routers/                # REST endpoints
│   │   │   ├── loans.py
│   │   │   ├── documents.py
│   │   │   ├── agents.py
│   │   │   ├── webhooks.py         # Resend inbound
│   │   │   └── auth.py             # dependency only (no router)
│   │   ├── services/               # business logic
│   │   │   ├── loans.py            # state-machine transitions
│   │   │   ├── audit.py            # append-only event log
│   │   │   └── storage.py          # local-filesystem document storage
│   │   ├── agents/
│   │   │   └── intake.py           # LangGraph intake agent
│   │   ├── tools/                  # shared agent tools
│   │   │   ├── extractor.py
│   │   │   └── comms.py            # Resend wrapper
│   │   ├── rules/
│   │   │   └── policy.py           # deterministic policy rules
│   │   └── workers/
│   │       └── tasks.py            # Arq background jobs
│   ├── evals/
│   │   ├── types.py
│   │   ├── runner.py
│   │   ├── tasks/                  # one file per eval task
│   │   └── golden_sets/            # YAML labeled examples
│   ├── alembic/                    # migrations
│   ├── tests/
│   ├── scripts/seed.py
│   └── pyproject.toml
├── web/                            # Next.js 16 frontend
│   ├── app/                        # App Router pages
│   ├── lib/api.ts                  # typed API client
│   └── package.json
├── docs/
│   └── DESIGN.md                   # technical design doc
└── README.md
```

## How the intake agent works

Triggered when a new loan packet arrives:

1. **Extract** — every document is passed to the LLM via the schema-gated gateway with a list of required fields. Each extraction comes back with a confidence score and a source span.
2. **Route** — extractions above per-field confidence thresholds are accepted; below-threshold ones are queued for human review.
3. **Identify missing items** — the agent compares extracted fields to the required set.
4. **Draft email** — LLM drafts a doc-request email scoped to the missing items.
5. **Interrupt for approval** — `interrupt()` pauses the graph; the UI surfaces the draft for the underwriter to review, edit, or cancel.
6. **Send** — on approval, the email goes out via Resend; the message is logged with the agent that drafted it and the user who approved it.

The LangGraph `PostgresSaver` checkpointer means the agent's state survives crashes, deploys, and human delays. Resuming a 3-day-old pause is the same as resuming a 3-second-old one.

## Reliability story

| Mechanism | Where it lives | What it prevents |
|---|---|---|
| Schema-gated LLM calls | `mkopo/llm_gateway.py` | Free-form parsing of model output |
| Per-field confidence thresholds | `mkopo/tools/extractor.py` `FIELD_THRESHOLDS` | Silent acceptance of bad extractions |
| Stage transition validator | `mkopo/services/loans.py` `transition_stage` | Illegal loan state transitions |
| Append-only audit log | `mkopo/services/audit.py` + `audit_events` table | Untraceable agent actions |
| Eval harness in CI | `evals/runner.py` | Regressions on golden-set tasks |
| Pinned judge model | `evals/tasks/summarize_underwriting.py` `JUDGE_MODEL` | Drift in LLM-as-judge trend lines |
| HITL `interrupt()` | `mkopo/agents/intake.py` | Irreversible actions without human approval |

## Tech stack

| Layer | Choice |
|---|---|
| Language / runtime | Python 3.12 |
| Web | FastAPI 0.136, Uvicorn |
| DB | PostgreSQL 16 + pgvector |
| ORM | SQLAlchemy 2.0 (async, asyncpg) |
| Migrations | Alembic |
| Agents | LangGraph 1.1 with langgraph-checkpoint-postgres |
| LLM | Anthropic Claude (via gateway abstraction) |
| Queue | Arq + Redis |
| Email | Resend |
| Storage | Local filesystem **or** S3 (selected by `STORAGE_BACKEND`; S3 path is compatible with MinIO/LocalStack) |
| Frontend | Next.js 16 (App Router), React 19, Tailwind v4 |
| Auth | Dev bearer token (replace with real auth for any real deployment) |

## Documentation

- [DESIGN.md](docs/DESIGN.md) — full technical design with diagrams

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgments

Inspired by [Applied Business Software / The Mortgage Office](https://themortgageoffice.com), which has served the private lending industry since 1978. This project is independent and not affiliated with ABS.
