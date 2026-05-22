# Deploying Mkopo

This document is the bridge between "the dev server runs locally" and
"someone unrelated to the original author can stand this up on real
infrastructure and have it work." It's intentionally a checklist with
explanations, not a click-by-click tutorial — the wizards in each
provider's console move every quarter, but the underlying steps don't.

If you only want to run Mkopo locally, see [`../README.md`](../README.md).

---

## Anatomy of a deployed Mkopo

```
┌──────────────────────────────┐      ┌──────────────────────────────┐
│  Next.js 16 web (Vercel /    │ ───► │  FastAPI backend (Fly /      │
│   App Runner / Render)       │      │   App Runner / Render)       │
└──────────────────────────────┘      └────────────┬─────────────────┘
                                                    │
                       ┌────────────────────────────┼─────────────────────────────┐
                       ▼                            ▼                             ▼
              ┌───────────────────┐       ┌───────────────────┐         ┌───────────────────┐
              │ Postgres 16       │       │ S3 bucket         │         │ Redis (Arq queue) │
              │ + pgvector 0.8    │       │ (documents)       │         │                   │
              │ Aurora / RDS / *  │       │                   │         │                   │
              └───────────────────┘       └───────────────────┘         └───────────────────┘
                       ▲
                       │
            ┌──────────┴──────────┐
            │ Resend (email I/O)  │
            │  outbound + inbound │
            └─────────────────────┘
```

Pick a Postgres provider, an object-storage backend, a Redis, and where
to host the two app processes. The defaults below are the cheapest paths
that still feel production-shaped; swap in whatever you already use.

---

## Pre-flight checklist

Before deploying anything, confirm the following — each one is a
half-day of confusion if it's missing later.

- [ ] **Resend domain verified.** Visit `https://resend.com/domains`,
      add your domain (Mkopo defaults to `ubunifutech.com`), publish the
      DNS records, wait for the status to flip to *Verified*. Your
      from-address has to be on a verified domain or every outbound
      send returns 403.
- [ ] **Anthropic API key with budget.** Production traffic against
      Sonnet + Opus runs ~$0.05–0.30 per loan run; budget at least
      $100/mo headroom before launch.
- [ ] **OpenAI API key with embeddings access.** `text-embedding-3-small`
      is what the app uses; very cheap (~$0.00002/1k tokens).
- [ ] **AWS account or alternative S3-compatible store** ready
      (LocalStack / MinIO / Cloudflare R2 all work — point `S3_ENDPOINT_URL`
      at the right host).
- [ ] **Postgres 16 server reachable from your app host** with the
      `vector` extension available (Aurora's PG 16 has it pre-installed
      as of 2024).
- [ ] **A Redis** for the Arq queue (any managed Redis: Upstash, Render
      Key-Value, ElastiCache).

---

## 1. Database

### Aurora PostgreSQL Serverless v2 (recommended)

Aurora PG 16 Serverless v2 is the simplest match for Mkopo's workload —
spiky during agent runs, quiet otherwise.

1. Create an Aurora PG 16 Serverless v2 cluster. Minimum ACU: 0.5
   (cheapest while quiet).
2. Connect with a superuser and run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE ROLE mkopo WITH LOGIN PASSWORD '<a-real-password>';
   CREATE DATABASE mkopo OWNER mkopo;
   \c mkopo
   GRANT ALL ON SCHEMA public TO mkopo;
   ```
3. Grab the cluster endpoint and set:
   ```env
   DATABASE_URL=postgresql+asyncpg://mkopo:<password>@<endpoint>:5432/mkopo
   DATABASE_URL_SYNC=postgresql+psycopg://mkopo:<password>@<endpoint>:5432/mkopo
   ```
4. From a shell with the backend code mounted (or via a one-off App
   Runner task), run `uv run alembic upgrade head`.

> **Why two URLs?** LangGraph's `PostgresSaver` uses raw `psycopg`
> (sync), while SQLAlchemy uses `asyncpg`. They share a Postgres but
> need different driver suffixes in the DSN.

### Alternative: any managed PG 16 with pgvector

Neon, Supabase, RDS, Render Postgres — all fine. Just confirm the
`vector` extension is on the catalog before running migrations.

---

## 2. Document storage (S3)

### Bucket setup

1. Create an S3 bucket — e.g. `mkopo-documents-prod`. Block all public
   access; the app generates presigned URLs for any UI download.
2. Enable bucket versioning if you want auditable history. Optional but
   recommended for regulated lender deployments.
3. Create an IAM user (or role for ECS / App Runner) with the policy
   in `infrastructure/iam/mkopo-s3-policy.json`. The policy grants the
   four operations the app actually uses: `PutObject`, `GetObject`,
   `DeleteObject`, `ListBucket`. Nothing else.
4. Set in the backend's env:
   ```env
   STORAGE_BACKEND=s3
   AWS_REGION=us-west-2
   AWS_ACCESS_KEY_ID=<key>
   AWS_SECRET_ACCESS_KEY=<secret>
   S3_BUCKET=mkopo-documents-prod
   ```

### Cloudflare R2 / MinIO / LocalStack

Set `S3_ENDPOINT_URL` to the alternate host. The same `STORAGE_BACKEND=s3`
codepath uses it; nothing else changes.

---

## 3. Email (Resend)

### Outbound

1. Verify your domain at `https://resend.com/domains`. Add the SPF,
   DKIM, and Return-Path records Resend gives you. Wait for *Verified*.
2. Set the env vars:
   ```env
   RESEND_API_KEY=re_<your-key>
   RESEND_FROM_ADDRESS=mkopo@<your-verified-domain>
   RESEND_FROM_NAME=Mkopo
   ```
3. The first time the intake agent reaches its `send` node, the email
   goes via Resend. Confirm in the Resend dashboard that it landed.

### Inbound (borrower replies)

1. In Resend, create an inbound endpoint pointing at
   `https://<your-backend-host>/api/v1/webhooks/resend/inbound`.
2. Copy the webhook secret Resend gives you and set:
   ```env
   RESEND_WEBHOOK_SECRET=whsec_<your-secret>
   ```
3. Set the inbound forwarding address on the domain (e.g.
   `mkopo+inbound@<your-domain>`) and use that as the `reply_to` on
   outbound. Borrower replies hit the webhook, the gateway threads
   them by `In-Reply-To`, and the case-file timeline picks them up as
   `inbound_email` events.

---

## 4. Backend (FastAPI)

### AWS App Runner (recommended for AWS shops)

1. Push the API container to ECR. `Dockerfile` is at `api/Dockerfile`.
2. Create an App Runner service from the image. Min/max instances 1/2.
3. Attach a VPC connector to reach Aurora.
4. Attach the IAM role that grants S3 access (same policy as §2).
5. Set all six required env vars (see README). Use AWS Secrets Manager
   for the secrets.
6. Health check: `/health/ready` — returns 503 if Postgres is down so
   App Runner restarts the task automatically.

### Fly / Render alternative

The image is portable. The same env vars apply. Render's auto-PR
preview environments are useful when iterating on the agents.

---

## 5. Frontend (Next.js)

Vercel is the obvious host: it's where the rest of the Next.js
ecosystem lives. Render and Cloudflare Pages work just as well.

1. Connect the GitHub repo, set the project root to `web/`, build
   command `npm run build`, output `.next`.
2. Set env vars:
   ```env
   NEXT_PUBLIC_API_URL=https://<your-backend-host>
   NEXT_PUBLIC_DEV_TOKEN=<rotate-the-default>
   ```
3. Verify CORS: the backend's `FRONTEND_URL` env var must match the
   deployed web origin or the browser will block the API calls.

---

## 6. Worker (Arq)

The background worker handles long intake runs and the nightly drift
monitor. Two options:

1. **Same image, different command.** Most managed hosts let you
   define multiple services from one image. Command:
   `uv run arq mkopo.workers.tasks.WorkerSettings`.
2. **Skip it.** All the agent runs work through the synchronous SSE
   path; only the nightly drift monitor and any backgrounded intake
   need the worker. For a low-traffic deployment you can run the drift
   monitor as an App Runner / Fly Scheduled Task instead.

---

## 7. First-boot sanity check

The backend prints a startup banner that reports every integration's
status. After deploy, hit `/health/ready` and tail the app logs — you
should see something like:

```
  ────────────────────────────────────────────────────────────────────────
   Startup check · environment=production
  ────────────────────────────────────────────────────────────────────────
   ✓  Anthropic (LLM)           using claude-sonnet-4-6 (default) / claude-opus-4-6 (heavy)
   ✓  OpenAI (embeddings)       text-embedding-3-small @ 1024 dims
   ✓  Resend (email)            from mkopo@ubunifutech.com, inbound webhook authenticated
   ✓  Document storage          S3 bucket mkopo-documents-prod in us-west-2
   ✓  Auth                      dev bearer token configured
  ────────────────────────────────────────────────────────────────────────
```

Any `⚠` or `✗` is a config knob you haven't set — the hint underneath
tells you what to fix. Five `✓`s and the app is healthy.

---

## 8. Smoke test the deploy

Click through this list once after first deploy:

1. Open the web app. Pipeline view loads with the seeded loans.
2. Click into any loan; the case-file timeline renders.
3. Drag a small PDF onto the *Documents* panel. The upload toast
   reports extracted page count.
4. Click **Run intake agent**. The progress card ticks through the
   five LangGraph nodes via SSE.
5. When intake pauses for approval, the modal opens with the
   AI-drafted email. Click *Send* and confirm in Resend that the
   borrower email actually went out.
6. Move the loan to underwriting via the header's *Move to
   underwriting* button. Provide a reason. Audit event written.
7. Open `/eval`. The dashboard shows the production vs. golden
   accuracy table.

Any failure here usually points back at one of the env vars from §1–§3.
The startup banner is your friend.

---

## Teardown

The cheapest path to "stop paying":

```bash
# AWS App Runner service: pause from the console (keeps config, stops billing)
# Aurora cluster: stop the cluster (paused state, ~$0.05/h vs ~$0.16/h running)
# S3 bucket: keep — storage is pennies; deleting destroys audit history
# Redis: delete; ephemeral state, no audit value
```

If you want to fully tear down, delete the App Runner service, the
Aurora cluster, the Redis instance, and the S3 bucket (after exporting
audit logs if you need them for compliance).
