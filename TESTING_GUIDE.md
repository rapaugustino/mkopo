# Mkopo Lens — testing guide

A playbook for exercising every feature in the system end-to-end. Each
flow is a numbered script with concrete steps and the **expected**
outcome for each. Run them in order on a freshly seeded database and
the eval / observability surfaces will fill in along the way.

## Quick reference

| # of flow | Tests | Sample to use | Time |
|---|---|---|---|
| B1 | Borrower commercial wizard + clean approve | 01-riverbend | 5 min |
| B2 | Borrower personal wizard | 02-maria | 5 min |
| B5 | Borrower withdraw via chat (HITL) | any | 2 min |
| B6 | Data export + erasure | any | 3 min |
| S1 | Manual loan creation + magic-link invite | 05-partial | 4 min |
| S2 | Intake agent + email approval (edit before send) | 05-partial | 4 min |
| S5 | Decision approve + edit-before-send | 01-riverbend | 5 min |
| S6 | Decision conditional + conditions tracking | 04-fern | 8 min |
| S7 | Decision decline + Reg B letter lint | 03-summit | 6 min |
| S8 | Personal decline + Reg B (personal) | 06-stratus | 6 min |
| S10 | Staff chat — send_borrower_message edit | any | 3 min |
| S11 | Per-class prerequisites gate | any in intake | 2 min |
| P2 | Prompt edit + version save | — | 2 min |
| P3 | Prompt rollback | — | 1 min |
| P4 | Rewrite-with-AI | — | 3 min |
| O2 | Trace tree (nested LLM calls under steps) | post any agent run | 2 min |
| O3-4 | Annotation good / incorrect → review task | post any agent run | 3 min |
| O5 | Replay agent run | post any agent run | 3 min |
| O6 | Regression diff (two LLM calls) | post 2+ runs | 3 min |
| O7 | Cost + p95 rollups | — | 2 min |
| E1-4 | Eval dashboard | post any agent run | 5 min |
| Z1 | End-to-end Maria capstone | 02-maria | 15 min |

Total: ~95 minutes for the full pass. Sample tree at
[`samples/`](./samples/README.md).

Six sample loan packets live in [`samples/`](./samples/README.md). Each
flow tells you which one to use.

> **Conventions in this document**
>
> - "the app" = the Next.js frontend at `http://localhost:3000`.
> - "the API" = the FastAPI backend at `http://localhost:8000`.
> - "Open X" = navigate by URL or click the link in the global nav.
> - "Expected" prefixes the observable outcome you should verify.
> - "Touchpoint" prefixes the underlying file / record / endpoint
>   you can inspect if something looks off.

---

## Part 0 — Setup

### 0.1 Fresh start

```bash
cd api
uv run alembic upgrade head
uv run python -m scripts.seed --reset
```

**Expected.** Console prints 11 loans seeded (10 commercial + 1
personal: KAYA), reference numbers `LN-2026-1001` through
`LN-2026-1011`, with stages distributed across `intake`,
`underwriting`, `decision`, `conditions`, `closing`, `approved`,
`declined`.

**Important — what the seed actually creates.** The seeded loans
exist for the **staff side** to demo against: they have `Loan`,
`Document`, `Party`, and `LoanParty` rows, and they're all owned
by Jordan Davis (`owner_user_id`). They do **NOT** create borrower
`User` accounts, so the borrower emails on those loans (e.g.
`matthew@atlasholdings.example`, `ops@cedarridge.example`,
`kaya.morales@example.com`) cannot sign in — they are display-only
contact strings on `Party` rows.

The **only** seeded `User` is `j.davis@mkopo.dev` (the underwriter),
and even that account has no password — staff auth uses the dev
bearer token (see Part 0.3).

So when you're working through the borrower flows below, you're
creating **new** users + loans via `/signup` + `/apply`. The
seeded loans are what the staff side has to chew on; they're
not the same loans the borrower flow exercises.

### 0.2 Start the servers

```bash
# Terminal A
cd api && uv run uvicorn mkopo.main:app --reload

# Terminal B
cd web && npm run dev
```

**Expected on startup.**
- API logs `prompts_seeded count=11` on first boot, then
  `prompts_cache_warmed` on subsequent boots.
- Banner shows your Anthropic + Resend + AWS keys' status.

### 0.3 Accounts you'll use

There are **two separate auth systems** in this codebase today —
read this carefully so you know what to expect:

**Staff (the underwriter / loan-officer side)** uses a bearer
token (`NEXT_PUBLIC_DEV_TOKEN` env var, default
`dev-token-replace-me`) that the frontend ships automatically on
every staff API call. **There is no staff login screen.** Open
`http://localhost:3000` and you're already operating as the seeded
underwriter Jordan Davis (`j.davis@mkopo.dev`, `role=admin`). This
is a dev-mode shortcut — production staff auth should land on the
same JWT system the borrower side uses (tracked separately; see the
"Auth dual-system" note at the bottom of Part 0).

**Borrowers (anyone using `/apply`, `/account`, the chat panel)**
use real password + JWT auth. They have to sign up first.

| Persona | Email | How to sign in |
|---|---|---|
| Underwriter (seeded) | `j.davis@mkopo.dev` | **No sign-in.** Open `localhost:3000` — you're already staff. |
| Borrower — Riverbend | `elena@riverbendholdings.example` | `/signup` first (pick any 8+ char password), then `/login` |
| Borrower — Maria | `maria.aguilar@example.com` | `/signup` then `/login` |
| Borrower — Summit | `damian@summithospitality.example` | `/signup` then `/login` |
| Borrower — Indigo (partial) | `nia@indigocapital.example` | created by staff invite (Flow S1) — uses magic link, no password |
| Borrower — Trevor | `trevor.stratus@example.com` | `/signup` then `/login` |
| Borrower — Aspen (real email) | your real email | created by staff invite — uses magic link, no password |

**There are no seeded borrower passwords.** Whatever password you
type at `/signup` is what you'll use at `/login`. The borrower
portal sends magic-link emails for password reset + initial
invites; the dev environment skips real email send unless
`RESEND_API_KEY` is configured (you'll see the link in the API
logs).

> **Why the split?** The borrower-portal auth (signup / login /
> JWT cookies / magic links / revocation / re-auth gates) is
> production-ready. The staff bearer token is a deferred
> placeholder from early development — see the "Auth dual-system"
> note below before you ship anything based on this code.

### 0.4 Auth dual-system — known gap

The current state of staff vs borrower auth is, honestly, a mess:

| Surface | Mechanism | Production-ready? |
|---|---|---|
| Staff (`/`, `/loans/*`, `/eval`, `/observability`, `/prompts`, `/review-queue`) | Shared bearer token `Authorization: Bearer ${DEV_TOKEN}` resolved by `routers/auth.py:resolve_current_user`. Same identity (`dev-user`, `role=admin`) for every staff call. | **No.** It's a dev-mode shortcut. No per-user identity, no revocation, no expiry. |
| Borrower (`/apply/*`, `/account/*`, `/api/v1/borrower-auth/*`, `/api/v1/borrower-portal/*`) | Real JWT auth in an HttpOnly cookie. Sign-up, password hashing (argon2), JWT-with-jti revocation via Redis blacklist, per-purpose magic links, rate limiting, sensitive-op re-auth. | **Yes.** |

**Why both still exist.** Borrower auth was built out properly
(tasks #158, #167, #168, #169). Staff auth was deferred (task
#155 — RBAC roles for internal users). The two systems were
never reconciled, so today staff calls bypass everything the
borrower side enforces.

**What "fixed" looks like.** Staff should authenticate through
the same JWT system the borrower side uses, with the user's
`role` (admin / underwriter / loan_officer) driving the
tool-registry filter (already wired in `agents/tools/staff.py`)
and any other RBAC check. Staff login UI would mirror the
borrower's, posting to a `/api/v1/auth/login` endpoint that
issues a JWT cookie. The current dev-token shortcut would
either be removed or kept only behind an `environment=dev` flag.

This is the next thing to do if you intend to deploy. Until
then, treat anything you're testing on the staff side as
running in "trust everyone with the token" mode.

The same `/login` route exists for borrowers, but **don't try to
use it for staff** — it's not wired to anything that grants
`role=admin`.

---

## Part 1 — Borrower flows

> **The wizard is five steps, not six. Documents are uploaded
> AFTER submit on the status page.** That's by design — the apply
> wizard collects loan structure (class, type, amount, parties),
> the submit creates the loan row, and document upload happens on
> `/apply/[loan_id]` where the `DocsUploader` lives. Don't go
> hunting for a docs step inside the wizard.

### Flow B1 · Self-service commercial application (clean approve)

**Scenario.** Riverbend Holdings — `samples/01-riverbend-commercial-approve/`.

1. Open `/signup`. Sign up as `elena@riverbendholdings.example` with
   any 8-char+ password (e.g. `riverbend123`).
2. Open `/apply` (or click the CTA from `/account`).
3. **Step 1 — Loan type.** Pick "Business / commercial real estate"
   and `Bridge` as the type.
4. **Step 2 — Business.** Enter the entity name
   `Riverbend Holdings, LLC`. Borrower email + name should already be
   pre-filled from your signup — if not, you'll see a "set state during
   render" issue (already fixed in the latest code).
5. **Step 3 — The loan.** Amount `$2,000,000`, property address
   `1622 East Republican Street, Seattle, WA 98112`, property type
   `Multifamily`, purpose "Acquisition financing".
6. **Step 4 — Guarantors.** Click **+ Add guarantor**, enter
   `Elena Park` + `elena@riverbendholdings.example`. Click **+ Add
   guarantor** again, enter `Joel Park` + `joel@riverbendholdings.example`.
7. **Step 5 — Review.** Confirm the summary reads correctly,
   then click **Submit application**.

**Expected after submit.**
- Redirected to `/apply/[loan_id]` (the status page — NOT the
  wizard).
- Pipeline (staff side) shows a new loan in `intake`.
- "Required documents" checklist on the status page shows four
  unticked items (loan application, appraisal, rent roll, PFS).

**Now upload the documents.**

8. On `/apply/[loan_id]`, scroll to the dashed upload box labelled
   **"Drop files here or click to upload"**. (There's no "Add a
   document" button — the dashed box *is* the uploader.)
9. Drag all four `.txt` files from
   `samples/01-riverbend-commercial-approve/` onto it.

**Expected after upload.**
- Each upload increments the checklist; eventually all four boxes
  tick.
- The tick is a **filename-heuristic match**, not a content match —
  `loan_application.txt` ticks the "Loan application" box regardless
  of what's actually inside. If you drop Maria's
  `loan_application.txt` onto a business loan, it'll still tick
  "Loan application" even though the content is for a personal
  product. Real content classification happens during the intake
  agent's run.
- Pipeline / case-file shows four document rows.
- Backend writes a `Loan` (loan_class=`business`), four `Document`
  rows, and `audit_events.action=borrower_applied` +
  `document_uploaded` rows.

### Flow B2 · Self-service personal application

**Scenario.** Maria Aguilar — `samples/02-maria-personal-approve/`.

1. Open a **new private browser window**. Sign up as
   `maria.aguilar@example.com` with any 8-char+ password.
2. Open `/apply`. **Step 1**, pick **"Personal / individual"**, loan
   type `Permanent` (renders as "Long-term"), amount `$25,000`.
3. **Step 2 — About you.** Name + email pre-filled from signup. Add
   purpose "Home improvement (kitchen renovation)".
4. **Step 3 — The loan.** (Personal flow doesn't ask for property.)
5. **Step 4 — Finances.** Annual income `108400`, employer
   `Pacific Northwest Health System`, credit score `745`, monthly
   debt payments `3406`, years at employer `6.4`.
6. **Step 5 — Review.** Submit.

**Expected after submit.**
- Status page renders the **personal-loan** required-doc checklist
  (loan application, tax return, bank statement, PFS) — NOT the
  commercial set.
- Status copy refers to pay stubs / tax returns / bank statement
  rather than "appraisal".
- `loan_class=personal` in the DB.

**Upload the documents on the status page.**

7. Drag all four files from `samples/02-maria-personal-approve/`
   onto the uploader on `/apply/[loan_id]`.
8. The checklist should tick all four boxes.

**Touchpoint.** Verify `loan.meta` carries `annual_income`,
`monthly_debt_payments`, `credit_score`, `years_employment` exactly
as entered.

### Flow B3 · Borrower status page + missing-docs prompt

**Scenario.** Reuse Riverbend from B1.

1. Stay on `/apply/[loan_id]`.
2. Verify the page shows: reference number, current stage chip,
   "What's next" copy, the required-docs checklist, the timeline.
3. Drag an unrelated file onto the "Drop files here or click to
   upload" box (e.g. a `loan_application.txt` from a different
   scenario).

**Expected.** The file uploads but the doc-type doesn't tick the
matching checklist box (heuristic filename match — different content).

### Flow B4 · Borrower chat — ask "what's missing"

1. Still on `/apply/[loan_id]` as the Maria borrower.
2. Scroll to the chat panel at the bottom.
3. Type "What documents am I missing?" and send.

**Expected.**
- SSE streams the assistant's response.
- The assistant calls a read-only tool (e.g. `get_loan_status`) and
  produces a short answer referencing exactly the missing doc names
  from your loan's required set.

**Touchpoint.** Check `tool_uses` table — there should be a new row
with `tool_name="get_loan_status"`, `status="ok"`, `loan_id` matching.

### Flow B5 · Borrower withdraw (destructive HITL)

1. Open Maria's `/account` page.
2. Open her loan, click **Withdraw application** in the chat (ask
   the assistant to withdraw on her behalf).

**Expected.**
- Chat fires a `confirm_required` SSE event.
- Confirmation modal opens showing the action details.
- Clicking Confirm transitions the loan to `withdrawn`.
- Clicking Cancel logs the cancellation and the agent acknowledges.

### Flow B6 · Data export + erasure (privacy)

1. Open `/account/privacy` as the Maria borrower.
2. Click **Download** under "Export my data".

**Expected.** A `mkopo-data-export-YYYY-MM-DD.json` file downloads.
Open it: contains your user record, every loan you've submitted,
the document filenames + SHA256 hashes (not the document bytes),
and the audit log.

3. Click **Start erasure** → fill in reason + check the
   acknowledgement → click **Erase my account**.
4. Re-auth modal opens. Enter your password.

**Expected.**
- Toast confirming erasure requested.
- You're signed out and bounced to `/`.
- Server-side: `users.deleted_at` is set; `loans.deleted_at` is set
  on all your loans; `retention_until` is computed (25mo for
  declined/withdrawn, 5y for approved).

---

## Part 2 — Staff flows

### Flow S1 · Manual loan creation + invite email

**Scenario.** Indigo Capital partial intake —
`samples/05-partial-intake-only/`.

> **Variant for testing real email delivery.** Use
> [`samples/07-aspen-real-email-test/`](./samples/07-aspen-real-email-test/)
> with `rapaugustino@gmail.com` as the borrower email. If Resend is
> configured (`RESEND_API_KEY`, `RESEND_FROM_ADDRESS`,
> `RESEND_FROM_NAME`), a real email lands in that inbox. See the
> scenario's README for the full step-by-step. Falls back to a
> console-logged magic-link URL when Resend isn't configured.

1. Log in as the seeded underwriter.
2. From `/` (pipeline), click **+ New loan**.
3. Fill in: borrower email `nia@indigocapital.example`, borrower name
   `Nia Foster`, entity `Indigo Capital Partners, LLC`, loan type
   `Bridge`, amount `$1,800,000`, loan class `business`.
4. Submit.

**Expected.**
- New loan appears in the pipeline at `intake`.
- Toast: "Loan created. Invite emailed to nia@indigocapital.example."
- API log shows the magic-link URL (until you wire Resend).
- Audit event `loan_created` with `invite_sent_to` in payload.

5. Copy the magic-link URL from the API log. Open it in a private
   window.

**Expected.**
- The link auto-signs you in as the borrower (no password needed —
  this is the `loan_invite` purpose).
- Redirects to `/apply/[loan_id]` for that specific loan.
- Status copy says something like "Your loan officer started this
  application. Add the documents we'll need below."
- You can now upload the `loan_application.txt` from
  `samples/05-partial-intake-only/` and any other docs you want.

### Flow S2 · Run intake agent + HITL email approval

**Scenario.** Continue from S1 with only the one document uploaded.

1. As staff, open the case file for Indigo's loan.
2. Click **Run intake** in the agent panel.

**Expected.**
- SSE progress trail: `started` → `extract_all_documents` →
  `identify_missing` → `draft_doc_request` → `request_human_approval`
  → **interrupt**.
- `IntakeApprovalModal` opens with the LLM's drafted email loaded.
  Subject + body are editable. Body mentions the specific missing
  docs (appraisal, rent roll, PFS) by friendly name.

3. Edit the body — change a sentence or add your signature.
4. Click **Send**.

**Expected.**
- `audit_events` shows `action=send_email` with the **edited** body
  in the payload (verify with `SELECT payload FROM audit_events
  WHERE action = 'send_email' ORDER BY created_at DESC LIMIT 1`).
- The graph reaches `complete`.

### Flow S3 · Review queue — accept / override

**Scenario.** Continue with any loan that's had intake run (e.g.
Indigo from S2 or any seeded loan in `underwriting`).

1. Open `/review-queue`.

**Expected.** A list of extractions queued because of low
confidence. Each row shows field name, AI-extracted value,
confidence pill, and the loan reference.

2. Click an item to open the source viewer with the highlighted
   snippet from the document.
3. **Accept** if the AI got it right; **Override** with a corrected
   value otherwise.

**Expected.**
- `extractions.status` flips to `accepted` or `overridden`.
- The review-queue throughput tile on `/eval` increments next
  refresh.

### Flow S4 · Run underwriting agent

**Scenario.** Riverbend (B1) once intake has finished and at least
one extraction is accepted.

1. Open Riverbend's case file → **Workspace** tab.
2. Verify the "Transition to underwriting" button is enabled (the
   prerequisite check passes).
3. Click it. Provide a transition reason like "intake complete,
   moving to underwriting".
4. Switch to **Decision** tab. Click **Run underwriting**.

**Expected.**
- SSE trail: `started` → `evaluate_rules` → `draft_summary` →
  `persist` → `done`.
- Workspace populates: KPIs panel shows the loan amount, property
  type, LTV, DSCR, debt yield, all rule outcomes green.
- Risk band pill renders `low`.
- Underwriting summary with citations is visible.

**Touchpoint.** Verify `agent_runs` has one row for `underwriting`
with `status=complete`; `agent_steps` has 3 rows; `llm_calls` has
≥ 1 row whose `parent_step_id` matches the `draft_summary` step.

### Flow S5 · Decision agent — approve path

**Scenario.** Continue with Riverbend.

1. Stay on the **Decision** tab.
2. Click **Run decision**.

**Expected.**
- SSE trail: `evaluate_rules` → `draft_decision` → `persist` → `done`.
- Path defaults to **approve**.
- Term sheet renders: principal $2M, rate ~SOFR+350, 24 months IO,
  origination fee, prepay terms.
- No conditions list.

3. Click **Review & send to borrower**.
4. Preview-and-edit modal opens with the auto-composed message body.
5. Edit the message (e.g. add a personal note).
6. Click **Send to borrower**.

**Expected.**
- Loan transitions to `approved`.
- The edited text lands as a `borrower_reply` audit event verbatim.
- Open `/apply/[loan_id]` as Elena (the borrower) — the message
  appears on her status page.

### Flow S6 · Decision agent — conditional path

**Scenario.** Fern Industrial — upload from
`samples/04-fern-conditional/` (NOT the rent roll). Run intake →
underwriting → decision.

**Expected.**
- Underwriting summary mentions the missing rent roll;
  `doc_completeness` fires `passed=false severity=warn`.
- Decision path defaults to **conditional**.
- Conditions list renders 1–4 specific, verifiable conditions
  including one about delivering the rent roll.

1. Click **Review & send to borrower**.
2. Preview modal shows the full composed message (terms + conditions
   numbered).
3. Edit one of the conditions to be more specific (e.g. "within 30
   days of closing" instead of "promptly").
4. Send.

**Expected.**
- Loan transitions to `conditions`.
- The conditions show up on `/loans/[id]` with status `open`.
- Mark them satisfied (or waive) one by one. When all are closed,
  the "Transition to closing" button enables.

### Flow S7 · Decision agent — decline + ECOA Reg B letter

**Scenario.** Summit Hospitality —
`samples/03-summit-hotel-decline/`.

1. Upload all four docs as the Summit borrower (or staff-create + invite).
2. Run intake → underwriting → decision.

**Expected after underwriting.**
- Risk band `high`.
- `ltv_under_cap` rule outcome `passed=false severity=block`.
- Underwriting summary recommends `decline`.

**Expected after decision.**
- Path defaults to **decline**.
- Adverse-action letter renders with principal reasons including
  `ltv_under_cap`.

3. Click **Review & send adverse action letter**.
4. The preview modal opens in **danger** chrome. Principal-reason
   pills render below the body.
5. Edit the body to **remove** the rule reference (e.g. delete the
   sentence that names "loan-to-value").

**Expected.** The `ltv_under_cap` pill flips to red with "missing",
and a yellow warning appears in the footer: "1 reason is no longer
cited in the body."

6. Restore the citation. Pills go back to success.
7. Click **Send adverse action letter**.

**Expected.**
- Loan transitions to `declined`.
- `audit_events.action=borrower_reply` carries the edited body.

### Flow S8 · Personal-loan decline + adverse-action letter

**Scenario.** Trevor Stratus — `samples/06-stratus-personal-decline/`.

Same shape as S7 but exercising the **personal rule pack**:
- Multiple blocking rules fire (`credit_score_floor`, `dti_under_cap`).
- The adverse-action letter principal-reasons list contains both.

Verify the body references both rules in plain language ("FICO of
612 is below…", "monthly debt-to-income ratio of 80%…").

### Flow S9 · Staff chat copilot — query loan

1. From any loan's case file, open the **Copilot** panel.
2. Ask "Why did this loan get declined?" (on Summit).

**Expected.**
- The copilot calls a read tool (e.g. `get_rule_outcomes` /
  `get_decision_result`), grounds in real data, and answers with
  specific rule ids.

3. Ask "Re-run the decision agent."

**Expected.**
- The copilot calls `run_agent` (destructive) and the confirmation
  modal opens — read-only `ConfirmModal` because `run_agent`
  doesn't have an editable body.
- Confirm. Stream resumes; the agent runs.

### Flow S10 · Staff chat — send borrower message (edit before send)

1. On any loan's chat, type "Send the borrower a note that we need a
   newer appraisal."
2. The copilot calls `send_borrower_message` (destructive). The
   **preview-and-edit modal** opens (not the read-only one).
3. Edit the body. Click **Send**.

**Expected.**
- The edited body lands in `audit_events.action=borrower_reply`.
- The original drafted text is NOT what got saved — the edits win.

### Flow S11 · Stage transition with prereqs + reasoning

1. Pick a fresh loan in `intake`. Try to transition it to
   `underwriting` BEFORE running intake.

**Expected.** The button shows the prerequisite message on hover —
something like "No documents uploaded yet — add the loan packet
first." The button is disabled.

2. Upload docs but don't run intake. Hover again.

**Expected.** "Intake hasn't produced any accepted extractions yet…"

3. For a personal loan, omit one required doc (e.g. don't upload
   the bank statement).

**Expected.** "Missing required personal-loan document(s): bank
statement. Upload these before moving to underwriting." (This is
the per-class check we added in task #179.)

### Flow S12 · Owner reassignment

1. Open any loan's case file.
2. Use the owner picker to reassign to another underwriter (create
   a second user if needed).

**Expected.** Audit event `owner_reassigned` with from/to user ids.

---

## Part 3 — Prompt management flows

### Flow P1 · View prompts list

1. Open `/prompts`.

**Expected.** 11 prompts listed: extractor, intake personal/business,
underwriting commercial/personal, decision (3), borrower chat, staff
chat, QA. Each row shows the current version number + "Last changed"
timestamp.

### Flow P2 · Edit a prompt + save new version

1. Click `tools.extractor.system`.
2. Edit a sentence in the body.
3. Type a change note like "Tightened source-span rule".
4. Click **Save & activate**.

**Expected.**
- New row in the history list (v2, then v3, etc.).
- The runtime now uses v2 — verify by triggering a new extraction
  and checking `llm_calls.system_prompt_hash` matches the SHA256 of
  the new body.

### Flow P3 · Rollback to a previous version

1. From the history list, click **Activate** on v1.

**Expected.**
- v1 becomes the active row.
- Toast confirms `Activated v1`.
- Next extraction uses v1's body again.

### Flow P4 · Rewrite-with-AI

1. Open any prompt's detail page.
2. Click **Rewrite with AI**.
3. Type instruction: "Make it more concise and require at most two
   sentences per output field."
4. Click **Rewrite**.

**Expected.**
- Body in the editor updates with the LLM-produced version.
- "AI rationale" card renders below explaining what changed.
- Change note auto-seeds with `AI rewrite: <instruction>`.
- Click **Save & activate** — new version persists with the rewrite
  visible in the history list.

**Touchpoint.** A new `llm_calls` row is written for the rewrite
itself (system_prompt_hash matches the `_REWRITE_META_SYSTEM` in
`services/prompts.py`).

### Flow P5 · Restore code default

1. Click **Restore default**.

**Expected.** The editor reloads with the canonical default body
from `services/prompts.DEFAULTS`. Save & activate to commit.

---

## Part 4 — Observability flows

### Flow O1 · Loan trace tab

1. Open any loan that's been run through agents.
2. Click the **Trace** tab on the case file.

**Expected.**
- A timeline of every agent run on this loan, newest first.
- Each row shows agent name, status, started_at, elapsed.
- Click into a run → opens `AgentRunDrawer`.

### Flow O2 · AgentRunDrawer — nested trace tree

1. From the trace tab, click any agent run that produced LLM calls.

**Expected.**
- **Annotations panel** at top (verdict buttons).
- **Step trace** showing each LangGraph node (intake's three nodes,
  underwriting's three, decision's three).
- Each step shows an `n calls` chip when it has child LLM calls.
- Steps with calls auto-expand to show the nested calls beneath them.
- **Unattributed LLM calls** section appears only if some calls
  couldn't be matched to a step (rare).

**Touchpoint.** Verify in DB that the calls have
`parent_step_id` filled in: `SELECT id, parent_step_id FROM
llm_calls WHERE thread_id = '<your-thread-id>'`.

### Flow O3 · Annotate a run as good

1. In an `AgentRunDrawer`, click the **Good** verdict button.
2. Add an optional note like "Clean extraction, all fields high
   confidence."
3. Click **Save annotation**.

**Expected.**
- The annotation appears in the list below.
- Toast: "Annotation saved." (NO review-queue spawn — `good` is the
  benign verdict.)
- Eval dashboard's "Recent failures" section is unaffected.

### Flow O4 · Annotate as incorrect → auto review-task

1. Find a run on a loan that has at least one queued / proposed
   extraction.
2. Click **Incorrect**. Add a note "Extracted NOI wrong — missed
   the operating expense block."
3. Save.

**Expected.**
- Toast: "Annotation saved. Auto-added a follow-up to the review
  queue."
- The annotation row shows a "→ review queue" deep link.
- Open `/review-queue` — a new task appears at the top with
  `reason` carrying the annotation note.

### Flow O5 · Replay an agent run

1. In `AgentRunDrawer`, in the **ReplayBar** below the annotations,
   click **Replay**.

**Expected.**
- Toast: "Replay started — running underwriting again — new run
  will appear in observability shortly."
- After ~1 second the recent-runs list invalidates and a new
  `underwriting` run appears.
- Open that new run.

**Expected on the new run's drawer.**
- The **Replays** field in the header shows `run xxxxxxxx…` linking
  back to the original.

**Touchpoint.** `agent_runs.payload->>'replays_run_id'` on the new
row equals the original run's id.

### Flow O6 · Compare two LLM calls (regression diff)

1. From any `LLMCallDrawer`, scroll to the **Compare to…** section.
2. Pick one of the same-prompt neighbours from the dropdown.

**Expected.**
- "Computing diff…" placeholder briefly.
- A side-by-side table renders: Model · System prompt · Status ·
  Latency · Input tokens · Output tokens · Cost · Attempts.
- Rows flag-coloured: green for improvements, red for regressions,
  grey for matches.
- A summary line at the top: "B vs A: 2 regressions · 1 improvement."

### Flow O7 · Observability page — full

1. Open `/observability`.

**Expected.**
- Top stat tiles: LLM calls, p95 latency, error rate, agent runs.
- Window picker (1h / 24h / 7d / 30d).
- "By model" table showing per-model breakdown (calls, p50, p95,
  error rate, retry rate).
- Recent LLM calls table.
- Recent agent runs table.

2. Switch the window picker between values; tiles update.

### Flow O7b · Cost rollups (per-model + per-run)

After running a few agents (intake + underwriting + decision on at
least 2 loans):

1. Open `/observability`, switch the window to **24h**.
2. Look at the "By model" table.

**Expected.**
- A row each for `claude-sonnet-4-5-…` and `claude-opus-4-5-…`.
- Columns: calls, p50, p95, error rate, retry rate.
- Hovering over the row tooltip surfaces aggregate input/output
  tokens and **total cost in dollars** (NEW from task #174).

3. Open any `LLMCallDrawer`. Look at the metadata grid.

**Expected.** Two new cells: **Input cost** and **Output cost**,
each formatted as `$0.001234` to 6 decimal places. Sum matches what
the compare-diff endpoint reports for total cost.

**Touchpoint.** Run this query:
```sql
SELECT model,
       COUNT(*)                                  AS calls,
       SUM(cost_input_usd + cost_output_usd)::numeric(10, 4) AS total_usd
FROM llm_calls
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY model
ORDER BY total_usd DESC;
```
The dollar totals should sum to ~$0.10–$1.00 for a small demo run.

### Flow O7c · Server-error tracking

1. Force a 500 by pointing the API at a broken Anthropic key:
   ```bash
   ANTHROPIC_API_KEY=invalid uv run uvicorn mkopo.main:app --reload
   ```
2. Try running an agent. The SSE stream should surface an `error`
   event with the auth failure.
3. Open `/observability`.

**Expected.**
- Recent LLM calls table has rows with `status="error"` and a
  short `error_reason` chip.
- Eval `Recent failures` section on `/eval` lists them.
- Clicking opens the drawer with the long-form `error_detail`.

**Touchpoint.** `infrastructure_errors` table (task #174) gets a row
per uncaught exception with the request path, status code, and
stack-trace digest.

### Flow O8 · Drill into a failure

1. Find a row in the LLM calls table with status `error` or
   `schema_failed` (run a few intake passes — schema retries are
   common in dev).
2. Click the row to open `LLMCallDrawer`.

**Expected.**
- The `error_reason` chip renders at the top.
- Long-form `error_detail` is expanded inside the drawer.
- The same-prompt neighbours table lets you check whether the same
  prompt failed for other calls (signal vs blip).

---

## Part 5 — Eval flows

### Flow E1 · Visit eval dashboard

1. Open `/eval`.

**Expected.**
- 4 top stat tiles: production accuracy, golden baseline, LLM p95
  latency, error rate. Window label shows "last 24h" / "last 7d" /
  "all-time" depending on traffic.
- Accuracy trend chart (last 30 days).
- Per-field accuracy table — colored bars and `n=` sample sizes.
- Below the existing surface, four new cards:
  - **Confidence calibration** — accept-rate per band.
  - **Review queue throughput** — Open / Resolved 7d / Median age.
  - **Agent reliability (last 7 days)** — stacked bars per agent.
  - **Recent failures** — clickable LLM/agent failure list.

### Flow E2 · Run drift monitor

1. Click **Refresh drift**.

**Expected.**
- Toast: either "Drift monitor ran. Wrote N production fields." or
  "Drift monitor ran — no fields written" with the friendly hint
  about needing 5+ resolved extractions per field.
- The trend chart + per-field table refresh.

### Flow E3 · Trace a failure from eval → drawer

1. Click any row in the **Recent failures** card.

**Expected.**
- If it's `kind=llm`: `LLMCallDrawer` opens with the call.
- If it's `kind=agent_step`: `AgentRunDrawer` opens at the run that
  contained the failed step.
- From either drawer, you can annotate, replay (agent), or diff
  (llm) — closing the loop from "saw a failure" to "took an action."

### Flow E4 · Investigate drifting field

1. Find a row in the per-field accuracy table with a red drift
   delta (production > 3pp below golden).
2. Click **Investigate**.

**Expected.** Lands on `/review-queue?field=<the-field>` filtered to
that one field, showing the specific extractions that contributed
to the drop.

---

## Part 6 — Borrower self-service (full path)

### Flow Z1 · End-to-end Maria

The capstone flow — exercises everything personal-loan-specific.

1. Sign up as Maria.
2. Apply through the wizard (all 4 docs).
3. From `/account`, watch the stage chip cycle as staff (in another
   window) advances the loan: `intake` → `underwriting` →
   `decision` → `approved`.
4. When staff sends the decision, refresh `/apply/[id]` — the message
   appears on the timeline (NOT the raw auto-composed text, but the
   underwriter-edited version).
5. Maria asks the chat "What were the conditions of approval?".
6. Verify the chat answers correctly (or, on a clean approve, says
   there were no conditions).
7. Maria exports her data, then withdraws — should fail because the
   loan is `approved` and not withdrawable.
8. Maria erases her account. Verify she's signed out and `users.deleted_at`
   is set.

---

## Part 7 — Edge cases & negative tests

| Case | How to test | Expected |
|---|---|---|
| Wrong-class docs on a loan | Upload `tax_return_2024.txt` to a business loan | Intake will skip / low-confidence on the wrong fields. Review queue rows appear. |
| Empty upload | Submit the apply wizard with zero docs | Backend accepts the loan but `Required documents` checklist shows all unchecked. Running intake hits the pre-flight gate and short-circuits with `status=needs_documents`. |
| Materials drift | Approve a loan, then override one extraction | The next stage transition (approved → closing) is gated with "Materials have changed since the decision was made — re-run the decision agent." |
| Guarantor concentration | Add a guarantor who's already on `>$8M` of seeded loans (Matthew Chen in seed) | Underwriting summary surfaces `guarantor_concentration` warn. |
| Stale appraisal | Edit a loan's appraisal date in DB to > 180 days ago and re-run underwriting | `appraisal_age` rule fires `passed=false severity=block`. |
| Idempotent magic link | Click the same `loan_invite` magic link twice | First click consumes; second click shows "this link has been used." |

---

## Coverage matrix

| Feature ↓ / Flow → | B1 | B2 | B5 | B6 | S1 | S2 | S5 | S6 | S7 | S8 | S10 | P4 | O3 | O5 | O6 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| Multi-step apply wizard | ● | ● | | | | | | | | | | | | | |
| Class-aware intake | ● | ● | | | | ● | | | | | | | | | |
| Per-class required docs check | ● | ● | | | | | | ● | | | | | | | |
| Manual loan invite | | | | | ● | | | | | | | | | | |
| HITL email approval + edit | | | | | | ● | | | | | | | | | |
| Editable decision send | | | | | | | ● | ● | ● | ● | | | | | |
| Reg B reason-lint | | | | | | | | | ● | ● | | | | | |
| Staff chat tool catalog | | | | | | | | | | | ● | | | | |
| Send-borrower-message edit | | | | | | | | | | | ● | | | | |
| Withdraw flow (HITL) | | | ● | | | | | | | | | | | | |
| Data export + erasure | | | | ● | | | | | | | | | | | |
| Prompt registry edit | | | | | | | | | | | | ● | | | |
| Rewrite-with-AI | | | | | | | | | | | | ● | | | |
| Annotations → review task | | | | | | | | | | | | | ● | | |
| Trace tree (nested calls) | | | | | | | | | | | | | ● | | |
| Replay agent run | | | | | | | | | | | | | | ● | |
| Regression diff | | | | | | | | | | | | | | | ● |

Top-line: 22 flows cover every feature shipped to date. Run them in
order on a freshly seeded DB and the eval surfaces (drift trend,
confidence calibration, agent reliability, recent failures) will
populate naturally.

---

## When something doesn't match

- **SSE streaming feels stuck.** Check API logs for the agent's
  structured `node_complete` events. If they're firing on the
  backend but not appearing on the frontend, your CORS / cookie
  config is wrong — check `frontend_url` in `config.py`.
- **Prompts page is empty.** Run `uv run alembic upgrade head` to
  apply migration 0015, then restart the API — the startup hook
  seeds the 11 defaults on the first boot.
- **Eval cards show nothing.** Run intake + underwriting + decision
  on at least one loan, then accept/override 5+ extractions per
  field in the review queue, then click "Refresh drift" on `/eval`.
- **Replays look identical to the original.** That's expected when
  nothing about the code or prompts has changed between runs — the
  point of the diff is to surface the case where something *has*
  changed. To deliberately produce a diff, edit a prompt via P2
  between the original run and the replay.
