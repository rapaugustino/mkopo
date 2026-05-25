"""Prompt registry + loader.

Every LLM call site in the codebase consults this module instead of
embedding a string literal. The contract:

  1. **Code default** — every prompt has a ``DEFAULT`` entry in
     :data:`DEFAULTS`. This is the source-controlled body the app falls
     back to when no DB row exists. Removing one is a breaking change.

  2. **DB override** — :func:`load` checks ``prompts`` for an active
     row matching the identifier. If one exists, its body wins. Edits
     made through the management UI live here.

  3. **Bootstrap** — :func:`ensure_defaults_seeded` inserts v1 rows
     for any identifier that has no version on disk yet. Called at
     startup so a fresh DB lights up the management UI with the
     current code defaults, and so the runtime can stop relying on
     the in-process fallback for production reads.

Why a registry rather than a free-form table:

  - It's explicit which call sites exist — listing the management
    page becomes a dict-keys scan, not a "scrape llm_calls for unique
    prompt hashes" query.
  - The fallback is real code, so a misconfigured / wiped DB doesn't
    leave an agent silently sending empty prompts.
  - Adding a new prompt is a single registry entry + a single call
    site change, both reviewable in one PR.

The body strings here are the *canonical* prompts the underwriting
team should treat as v1; the runtime cares only that *some* version
is active.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.models.prompt import Prompt

# ----- registry -------------------------------------------------------------


@dataclass(frozen=True)
class PromptDef:
    """Static metadata about one prompt identifier.

    ``label`` and ``description`` are surfaced in the management UI so
    a non-engineer can tell "intake.draft_doc_request.personal" from
    "intake.draft_doc_request.business" without diffing bodies.
    """

    identifier: str
    label: str
    description: str
    default_body: str


# Document extraction — the workhorse. Runs on every uploaded
# document; the request schema (ExtractionResult) is enforced by the
# gateway, so this prompt is about *recall* + grounding rather than
# format compliance.
_EXTRACTOR_DEFAULT = (
    "You are an expert loan document analyst. Extract the requested "
    "fields from this document.\n\n"
    "Rules:\n"
    "- Use only information that appears verbatim in the document.\n"
    "- If a field is not present, omit it from the output.\n"
    "- For each extracted field, return a confidence score between 0 and 1 "
    "and the exact source span (a short quoted excerpt the value came from).\n"
    "- Never speculate, infer, or fabricate values."
)

# Intake doc-request drafting. Two variants because personal and
# business loans need to ask for completely different document sets.
_INTAKE_DRAFT_DOC_PERSONAL = (
    "You are a professional loan underwriter writing to an individual "
    "loan applicant. Be concise, polite, and specific.\n\n"
    "Context: this is an UNSECURED PERSONAL LOAN application. The borrower "
    "is an individual, not a business. We do NOT need an appraisal, a rent "
    "roll, business tax returns, or property documents.\n\n"
    "Compose a single email asking the borrower to upload the specific "
    "missing items. Reference each item by its human-friendly name "
    "(\"your most recent pay stubs\", \"last 2 years of tax returns\", "
    "\"a recent bank statement\"). Do not invent items that aren't in "
    "the supplied list of missing materials. Sign off as the loan officer."
)
_INTAKE_DRAFT_DOC_BUSINESS = (
    "You are a professional loan underwriter writing to a borrower. Be "
    "concise, polite, and specific.\n\n"
    "Context: this is a COMMERCIAL / BUSINESS loan application against "
    "income-producing real estate. We need a loan application, an "
    "appraisal report, a rent roll, and a personal financial statement "
    "from any guarantors.\n\n"
    "Compose a single email asking the borrower to upload the specific "
    "missing items by name. Do not invent items that aren't in the "
    "supplied list of missing materials. Sign off as the loan officer."
)

# Underwriting summary. Two variants — commercial real estate vs
# personal / consumer credit — because the metrics, defensibility
# framework, and reviewer audience all differ.
_UW_COMMERCIAL = (
    "You are an experienced commercial loan underwriter writing for a "
    "credit committee. You produce factual, concise, citation-backed "
    "summaries.\n\n"
    "Rules:\n"
    "- Every claim must be supported by at least one citation pointing "
    "to a specific extracted field or document.\n"
    "- Do not assert pass/fail on any rule — the rules engine handles "
    "that deterministically. Surface the rule outcomes; do not re-derive "
    "them.\n"
    "- Identify the asset type (multifamily / office / retail / hotel / "
    "industrial / mixed-use) and key ratios (LTV, DSCR, debt yield).\n"
    "- Note any risk factors (occupancy gaps, near-term rollovers, "
    "appraisal age, guarantor concentration) the rules engine flagged.\n"
    "- Keep the summary scannable. A committee member should grasp the "
    "deal in under 60 seconds."
)
_UW_PERSONAL = (
    "You are an experienced consumer-credit underwriter writing for a "
    "personal-loan adjudication queue. You produce factual, concise, "
    "citation-backed summaries.\n\n"
    "Rules:\n"
    "- Every claim must be supported by at least one citation pointing "
    "to a specific extracted field or document.\n"
    "- Do not assert pass/fail on any rule — the rules engine handles "
    "that deterministically. Surface the rule outcomes; do not re-derive "
    "them.\n"
    "- Identify the borrower's income basis (W-2 / self-employed), "
    "employment tenure, and key ratios (DTI, LTI, FICO band).\n"
    "- Note any risk factors (employment gap, recent credit inquiries, "
    "thin file) the rules engine flagged.\n"
    "- Keep the summary scannable. A reviewer should grasp the deal in "
    "under 60 seconds."
)

# Decision agent — three separate prompts because the path each takes
# (approve / conditional / decline) has different defensibility and
# format requirements. The decline letter in particular has ECOA Reg B
# constraints baked into the rules.
_DECISION_PATH = (
    "You are a senior credit officer making a decision recommendation.\n\n"
    "Hard rules:\n"
    "1. You may NOT contradict the rules engine's BLOCKING failures. If "
    "any blocking rule failed, the path is \"decline\".\n"
    "2. If only warnings fired and the underwriting summary supports it, "
    "you may recommend \"approve\" or \"conditional\".\n"
    "3. Output the path and a short rationale (under 200 words). The "
    "drafting prompts run after this one and will assemble the term sheet "
    "or adverse-action letter."
)
_DECISION_APPROVE_CONDITIONAL = (
    "You draft a commercial loan term sheet and (if conditional) a list "
    "of conditions to close.\n\n"
    "Hard rules:\n"
    "1. Principal = the loan amount provided. Do not propose a different "
    "amount.\n"
    "2. Pricing should reflect the asset type and risk band stated in the "
    "underwriting summary.\n"
    "3. Conditions, when present, must be specific and actionable — each "
    "should be something the borrower can satisfy by delivering a "
    "document or signing a doc.\n"
    "4. Cite every concrete claim against the underwriting summary or "
    "rules engine outcomes."
)
_DECISION_DECLINE = (
    "You draft an ADVERSE ACTION LETTER under ECOA Regulation B.\n\n"
    "Hard rules:\n"
    "1. `principal_reasons` MUST contain at least one rule_id from the "
    "BLOCKING failures the rules engine produced. You may not invent a "
    "reason that isn't in the supplied outcomes list.\n"
    "2. Letter copy must reference each principal reason in plain "
    "language the borrower can understand.\n"
    "3. Include the ECOA notice (right to a copy of any appraisal, "
    "right to know the specific reasons, contact for credit-reporting "
    "agency).\n"
    "4. Do not soften or hedge. The letter is a legal notice."
)

# Borrower-side chat assistant. Bounded scope: read-only by default,
# destructive ops gated behind a confirmation tool.
_BORROWER_CHAT = (
    "You are Mkopo's borrower-side assistant. You help the signed-in "
    "borrower understand and act on their loan application.\n\n"
    "What you can do:\n"
    "- Answer questions about their loan status, required documents, "
    "and timeline.\n"
    "- Update their contact info or basic application fields via the "
    "available tools.\n"
    "- Initiate a withdrawal or data export — these require an explicit "
    "borrower confirmation through the confirm-action tool.\n\n"
    "Hard rules:\n"
    "- Never disclose information about other borrowers' loans.\n"
    "- Do not speculate on approval likelihood — that's underwriting's "
    "job, not yours.\n"
    "- For anything you can't answer with a tool, point the borrower to "
    "their loan officer.\n"
    "- Keep responses short. The borrower is on a phone screen as often "
    "as not."
)

# Staff-side chat copilot. Wider tool catalog (rerun agents, transition "
# "stages, etc.) and an audience of operators who tolerate denser "
# replies.
_STAFF_CHAT = (
    "You are Mkopo's internal underwriting copilot. You help staff "
    "(underwriters, loan officers, admins) operate on a loan in the "
    "pipeline.\n\n"
    "What you can do:\n"
    "- Surface the loan's current state, extractions, rule outcomes, "
    "and recent activity.\n"
    "- Run intake / underwriting / decision agents on demand.\n"
    "- Transition stages, assign owners, manage conditions.\n"
    "- Draft borrower-facing messages (the user reviews and sends).\n\n"
    "Hard rules:\n"
    "- Be precise about which loan you're acting on. If the user's "
    "question is ambiguous, ask before invoking a tool that mutates state.\n"
    "- Never invent a rule outcome or extraction — read them with a tool.\n"
    "- For stage transitions, mention the prerequisite check the "
    "transition_stage tool will run."
)

# RAG answer over loan documents. Single inline prompt; the retrieval
# is hybrid (dense + sparse) and surfaces a fixed chunk set the model
# must ground in.
_QA_ANSWER = (
    "You answer questions about a single commercial loan, on behalf "
    "of an underwriter.\n\n"
    "Hard rules:\n"
    "1. Use ONLY the provided document chunks. If the chunks don't "
    "contain the answer, say so plainly.\n"
    "2. Cite chunk numbers for every concrete claim. The citation "
    "format is [chunk-N].\n"
    "3. Be concise. The reader is scanning a workspace, not reading "
    "an essay."
)


DEFAULTS: dict[str, PromptDef] = {
    p.identifier: p
    for p in [
        PromptDef(
            identifier="tools.extractor.system",
            label="Document extractor",
            description=(
                "Runs on every uploaded document. Pulls structured fields "
                "with citations + confidence scores."
            ),
            default_body=_EXTRACTOR_DEFAULT,
        ),
        PromptDef(
            identifier="intake.draft_doc_request.personal",
            label="Intake — personal-loan doc request email",
            description=(
                "Drafted by the intake agent when a personal-loan "
                "applicant is missing required documents (pay stubs, "
                "tax returns, etc.)."
            ),
            default_body=_INTAKE_DRAFT_DOC_PERSONAL,
        ),
        PromptDef(
            identifier="intake.draft_doc_request.business",
            label="Intake — commercial-loan doc request email",
            description=(
                "Drafted by the intake agent when a commercial-loan "
                "applicant is missing required documents (appraisal, "
                "rent roll, PFS, etc.)."
            ),
            default_body=_INTAKE_DRAFT_DOC_BUSINESS,
        ),
        PromptDef(
            identifier="underwriting.summary.commercial",
            label="Underwriting — commercial summary",
            description=(
                "Drafts the credit-committee-facing underwriting "
                "summary for commercial real-estate loans."
            ),
            default_body=_UW_COMMERCIAL,
        ),
        PromptDef(
            identifier="underwriting.summary.personal",
            label="Underwriting — personal-loan summary",
            description=(
                "Drafts the adjudication-queue summary for personal / "
                "consumer loans."
            ),
            default_body=_UW_PERSONAL,
        ),
        PromptDef(
            identifier="decision.path_selection",
            label="Decision — path selection",
            description=(
                "First decision-agent step. Picks approve / conditional "
                "/ decline given the rules outcomes."
            ),
            default_body=_DECISION_PATH,
        ),
        PromptDef(
            identifier="decision.approve_conditional",
            label="Decision — term sheet (approve / conditional)",
            description=(
                "Drafts the term sheet + conditions list when the "
                "decision-agent picked approve or conditional."
            ),
            default_body=_DECISION_APPROVE_CONDITIONAL,
        ),
        PromptDef(
            identifier="decision.decline_letter",
            label="Decision — adverse-action letter (decline)",
            description=(
                "Drafts the ECOA Reg B adverse-action letter when the "
                "decision-agent picked decline."
            ),
            default_body=_DECISION_DECLINE,
        ),
        PromptDef(
            identifier="chat.borrower.system",
            label="Borrower chat — system prompt",
            description=(
                "Drives the signed-in borrower's chat assistant. "
                "Read-only by default; mutations gated by a confirm tool."
            ),
            default_body=_BORROWER_CHAT,
        ),
        PromptDef(
            identifier="chat.staff.system",
            label="Staff chat — copilot system prompt",
            description=(
                "Drives the internal underwriting copilot. Wider tool "
                "catalog than the borrower chat."
            ),
            default_body=_STAFF_CHAT,
        ),
        PromptDef(
            identifier="qa.answer_question.system",
            label="Ask-the-file — RAG answer",
            description=(
                "Answers underwriter questions over a single loan's "
                "documents using retrieved chunks."
            ),
            default_body=_QA_ANSWER,
        ),
    ]
}


def list_definitions() -> list[PromptDef]:
    """All known prompt identifiers, sorted by identifier.

    Used by the management UI's list page and by the bootstrap-seed
    helper. Order is deterministic so the management page reads the
    same way across reloads.
    """
    return sorted(DEFAULTS.values(), key=lambda p: p.identifier)


# ----- runtime loader -------------------------------------------------------


# Process-level cache keyed by identifier. Warmed at app startup from
# the DB (see :func:`refresh_cache`), refreshed after each edit via
# :func:`invalidate_cache` + the router's commit hook.
#
# Reason for a cache rather than a DB read per call: agents call into
# this on every node. The active prompt set is small (low tens) and
# fits in memory comfortably. A read per call would mean a network
# round-trip per LLM-issuing node, which adds up across a full intake
# → underwriting → decision run.
_CACHE: dict[str, str] = {}


def get(identifier: str) -> str:
    """Return the active prompt body for ``identifier``. Synchronous.

    Resolution order:

    1. **Process cache** — populated at startup + after every edit.
       The expected hot path.
    2. **Code default** — the registry entry above. Used when the
       cache hasn't been warmed yet (the very first call after boot)
       or when the DB is unavailable / wiped. This is correct
       fallback behaviour: better to use a stale-but-known prompt
       than to crash an agent.
    3. **Hard error** — unknown identifier with no default. This
       should never happen in production because adding a call site
       requires adding a registry entry. Raising prevents a silently
       empty system prompt from corrupting every LLM call.

    Synchronous on purpose. Agent nodes call this freely without
    having to thread a session through every helper, and the
    no-cache path falls through to the in-process default which is
    correct without any I/O.
    """
    cached = _CACHE.get(identifier)
    if cached is not None:
        return cached

    default = DEFAULTS.get(identifier)
    if default is None:
        raise KeyError(
            f"Unknown prompt identifier {identifier!r}. Add it to "
            "mkopo.services.prompts.DEFAULTS."
        )
    return default.default_body


def invalidate_cache(identifier: str | None = None) -> None:
    """Drop one or all cached entries.

    Tests call this with no argument to reset state between cases.
    The router calls :func:`refresh_cache` after a write instead;
    invalidation is only the fallback path.
    """
    if identifier is None:
        _CACHE.clear()
    else:
        _CACHE.pop(identifier, None)


async def refresh_cache(session: AsyncSession) -> None:
    """Bulk-read every active prompt row into the process cache.

    Called once at startup (after :func:`ensure_defaults_seeded`) and
    after each mutation through the router. One query, one pass over
    the active set — no per-identifier latency on cold start.

    If the DB read fails or the table is empty, the cache stays as
    it was; :func:`get` will fall through to the code default. We
    prefer "stale but boot-able" over "crash on a DB hiccup".
    """
    try:
        rows = (
            await session.execute(
                select(Prompt.identifier, Prompt.body).where(
                    Prompt.is_active.is_(True)
                )
            )
        ).all()
    except Exception:
        # Don't take down the app on a transient DB error during
        # refresh. The next mutation or restart will retry.
        return
    new_cache = {ident: body for ident, body in rows}
    if new_cache:
        # Replace wholesale rather than merging — if an identifier
        # has been deleted from the DB (manual cleanup) we want the
        # next get() to fall back to the code default, not return
        # the stale cached value.
        _CACHE.clear()
        _CACHE.update(new_cache)


async def load(session: AsyncSession, identifier: str) -> str:
    """Async fallback for callers that need to bypass the cache.

    Most call sites should use :func:`get` (sync, cache-only). This
    variant exists for the bootstrap helpers and any future code
    that needs guaranteed-fresh reads against the DB.
    """
    stmt = select(Prompt.body).where(
        Prompt.identifier == identifier, Prompt.is_active.is_(True)
    )
    body = (await session.execute(stmt)).scalar_one_or_none()
    if body is not None:
        return body
    default = DEFAULTS.get(identifier)
    if default is None:
        raise KeyError(
            f"Unknown prompt identifier {identifier!r}. Add it to "
            "mkopo.services.prompts.DEFAULTS."
        )
    return default.default_body


# ----- bootstrap ------------------------------------------------------------


async def ensure_defaults_seeded(session: AsyncSession) -> int:
    """Insert v1 rows for any registry entry that has no DB row.

    Idempotent — re-running does nothing if the identifier has any
    version on disk already. Returns the number of rows written so
    the startup hook can log a one-line summary.

    Called on app startup so a freshly migrated database lights up
    the management UI immediately. Tests can also call it before a
    case that needs the seeded state.
    """
    existing = (
        await session.execute(select(Prompt.identifier).distinct())
    ).scalars().all()
    have = set(existing)
    written = 0
    for d in list_definitions():
        if d.identifier in have:
            continue
        session.add(
            Prompt(
                identifier=d.identifier,
                version=1,
                body=d.default_body,
                change_note="Seeded from code default at install time.",
                is_active=True,
            )
        )
        written += 1
    if written:
        await session.flush()
    return written


# ----- management mutations -------------------------------------------------


async def create_version(
    session: AsyncSession,
    *,
    identifier: str,
    body: str,
    change_note: str,
    activate: bool,
    created_by_user_id: uuid.UUID | None,
) -> Prompt:
    """Append a new version. Optionally make it active.

    Activation is a two-step transaction: deactivate the previous
    active row, then insert the new one with is_active=True. The
    partial unique index in 0015_prompts catches the race-condition
    case where two concurrent writers both try to activate — the
    second writer's flush fails cleanly and the request returns 409.
    """
    if identifier not in DEFAULTS:
        # The set of valid identifiers is closed by the registry. New
        # ones come from PRs adding both a registry entry and a call
        # site; the UI never invents them. Refuse rather than insert
        # an orphan row.
        raise KeyError(
            f"Unknown prompt identifier {identifier!r}. The set of "
            "managed prompts is fixed by the code registry — add a "
            "PromptDef entry first."
        )

    latest_version = (
        await session.execute(
            select(Prompt.version)
            .where(Prompt.identifier == identifier)
            .order_by(desc(Prompt.version))
            .limit(1)
        )
    ).scalar_one_or_none()
    next_version = (latest_version or 0) + 1

    if activate:
        # Clear is_active on whichever row currently holds it (if any).
        # Done as an UPDATE so we don't need to re-fetch the row.
        from sqlalchemy import update

        await session.execute(
            update(Prompt)
            .where(Prompt.identifier == identifier, Prompt.is_active.is_(True))
            .values(is_active=False)
        )

    row = Prompt(
        identifier=identifier,
        version=next_version,
        body=body,
        change_note=change_note,
        is_active=activate,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    await session.flush()

    if activate:
        # Refresh the cache so the next get() returns the new body.
        # refresh_cache pulls every active row in one query; cheap
        # and keeps the cache internally consistent across multi-
        # identifier edits.
        await refresh_cache(session)

    return row


async def activate_version(
    session: AsyncSession,
    *,
    identifier: str,
    version: int,
) -> Prompt:
    """Switch the active flag to ``version``. Used for rollbacks.

    Returns the now-active row. Raises if the version doesn't exist.
    """
    target = (
        await session.execute(
            select(Prompt).where(
                Prompt.identifier == identifier, Prompt.version == version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise ValueError(
            f"No version {version} for prompt {identifier!r}."
        )
    if target.is_active:
        return target  # already active — noop

    from sqlalchemy import update

    await session.execute(
        update(Prompt)
        .where(Prompt.identifier == identifier, Prompt.is_active.is_(True))
        .values(is_active=False)
    )
    target.is_active = True
    await session.flush()
    await refresh_cache(session)
    return target


# ----- read helpers (used by the router) ------------------------------------


async def history(
    session: AsyncSession, identifier: str
) -> list[Prompt]:
    """All versions for ``identifier``, newest first.

    Includes the active row. Does not include the code default
    explicitly — if the table is empty for this identifier, the list
    is empty and the caller is expected to surface "still on code
    default" in the UI.
    """
    rows = (
        await session.execute(
            select(Prompt)
            .where(Prompt.identifier == identifier)
            .order_by(desc(Prompt.version))
        )
    ).scalars().all()
    return list(rows)


# ----- rewrite-with-AI ------------------------------------------------------


# The meta-prompt the rewrite endpoint uses. Deliberately *not* in the
# DEFAULTS registry — making this user-editable would create a
# "rewrite the rewriter" loop with no source of truth. If this needs
# changing it's a code change in a reviewed PR.
_REWRITE_META_SYSTEM = (
    "You are an expert prompt engineer helping refine system prompts "
    "for production LLM agents. The user will give you a current "
    "system prompt and an instruction describing how they'd like it "
    "changed.\n\n"
    "Hard rules:\n"
    "1. Return a complete, drop-in replacement body. Do not return a "
    "diff or commentary disguised as the body.\n"
    "2. Preserve hard-rule semantics. If the original prompt has "
    "numbered rules, keep them numbered. If it forbids inventing "
    "values, the rewrite must still forbid inventing values unless "
    "the instruction explicitly says otherwise.\n"
    "3. Match the original prompt's tone and length envelope. A "
    "concise prompt stays concise; a structured rule list stays a "
    "structured rule list.\n"
    "4. Do not introduce new behavioural directives the instruction "
    "did not ask for.\n"
    "5. The `rationale` field is a 1–3 sentence explanation of what "
    "you changed and why — written for the engineer reviewing the "
    "diff, not for the LLM consuming the prompt."
)


async def rewrite_prompt(
    *,
    current_body: str,
    instruction: str,
    identifier: str,
    label: str,
    description: str,
) -> tuple[str, str]:
    """Ask the gateway to rewrite a prompt under a natural-language
    instruction. Returns ``(new_body, rationale)``.

    Caller is responsible for actually persisting the result — this
    function is read-only. The /prompts UI calls this, drops the
    rewritten body into the editor, and the user reviews + saves as
    a new version through the normal create-version flow.

    The model used is the default (Sonnet); rewriting is light enough
    that the heavy model isn't justified. If a rewrite returns garbage
    the human will catch it in the review step.
    """
    # Lazy import to dodge the circular: services.prompts is imported
    # by the gateway path at runtime, and gateway pulls in agents code.
    from pydantic import BaseModel, Field

    from mkopo.config import get_settings
    from mkopo.llm_gateway import get_gateway

    class _RewriteResult(BaseModel):
        body: str = Field(min_length=10, max_length=8000)
        rationale: str = Field(min_length=5, max_length=600)

    gateway = get_gateway()
    settings = get_settings()
    user_block = (
        f"Prompt identifier: {identifier}\n"
        f"Prompt label: {label}\n"
        f"Prompt description: {description}\n\n"
        f"--- CURRENT BODY ---\n{current_body}\n--- END CURRENT BODY ---\n\n"
        f"Instruction from the underwriting team:\n{instruction.strip()}\n\n"
        f"Produce the rewritten body and a short rationale."
    )
    result: _RewriteResult = await gateway.call_structured(
        model=settings.llm_default_model,
        system=_REWRITE_META_SYSTEM,
        user=user_block,
        schema=_RewriteResult,
    )
    return result.body, result.rationale


async def latest_active_per_identifier(
    session: AsyncSession,
) -> dict[str, Prompt]:
    """``{identifier: active_row}`` for every identifier with an active row.

    Powers the list view's "current version, last changed at" column
    without N+1 — the management page queries this once and joins
    against the static registry in Python.
    """
    rows = (
        await session.execute(
            select(Prompt).where(Prompt.is_active.is_(True))
        )
    ).scalars().all()
    return {r.identifier: r for r in rows}
