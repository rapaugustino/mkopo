"""Prompt management endpoints.

Five read+write surfaces over the ``prompts`` table, backing the
``/prompts`` management UI:

  - ``GET  /prompts``                       — list every identifier
    with its active version + last-changed timestamp.
  - ``GET  /prompts/{identifier}``          — detail (description,
    current body, full history).
  - ``POST /prompts/{identifier}/versions`` — create a new version
    (body + change_note + optional immediate activation).
  - ``POST /prompts/{identifier}/activate/{version}`` — switch the
    active flag to an older version (rollback).
  - ``GET  /prompts/{identifier}/default``  — return the registry's
    code default. The UI uses this for the "Restore default" button
    so the operator doesn't have to type the canonical body back in.

All five sit under ``CurrentUserDep`` — these are staff-only
operations; the prompts table never receives writes from anonymous
or borrower traffic.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models.prompt import Prompt
from mkopo.services import prompts as prompts_service

router = APIRouter(prefix="/prompts", tags=["prompts"])


# ----- response shapes -----------------------------------------------------


class PromptVersionOut(BaseModel):
    """One row in the version history table."""

    id: str
    version: int
    body: str
    change_note: str | None
    is_active: bool
    created_at: datetime
    created_by_user_id: str | None


class PromptSummary(BaseModel):
    """List-view row. One per registered identifier.

    Reports the registry metadata (label / description) alongside the
    DB state (active version, last changed). Identifiers that exist
    in the registry but have no DB row at all show ``active_version=None``
    and ``active_at=None`` — the UI treats that as "still on code default".
    """

    identifier: str
    label: str
    description: str
    active_version: int | None
    active_at: datetime | None
    n_versions: int


class PromptDetail(BaseModel):
    """Detail-view shape. Includes the full version history."""

    identifier: str
    label: str
    description: str
    default_body: str
    versions: list[PromptVersionOut]


class CreateVersionIn(BaseModel):
    body: str = Field(min_length=1)
    change_note: str = Field(min_length=1, max_length=512)
    activate: bool = True


class RewriteIn(BaseModel):
    """Inputs for the rewrite-with-AI endpoint."""

    # Current body the user is staring at — sent from the editor
    # rather than re-read from the active row, so the user can apply
    # the instruction against their in-progress unsaved changes.
    current_body: str = Field(min_length=1, max_length=10000)
    instruction: str = Field(min_length=4, max_length=1000)


class RewriteOut(BaseModel):
    """Rewritten body + a short rationale.

    The rationale is intended for the human reviewer, not for the
    downstream LLM. It explains what changed and why so the user can
    accept / reject without re-reading the whole body.
    """

    body: str
    rationale: str


# ----- helpers -------------------------------------------------------------


def _row_to_version_out(row: Prompt) -> PromptVersionOut:
    """Map a Prompt ORM row to the wire shape.

    UUIDs are stringified so the frontend doesn't have to deserialise
    a custom type. ``created_by_user_id`` stays nullable — the
    bootstrap-seed v1 rows are authorless.
    """
    return PromptVersionOut(
        id=str(row.id),
        version=row.version,
        body=row.body,
        change_note=row.change_note,
        is_active=row.is_active,
        created_at=row.created_at,
        created_by_user_id=(str(row.created_by_user_id) if row.created_by_user_id else None),
    )


# ----- endpoints -----------------------------------------------------------


@router.get("", response_model=list[PromptSummary])
async def list_prompts(
    user: CurrentUserDep,
    db: DbSessionDep,
) -> list[PromptSummary]:
    """List every registered prompt with its active-version snapshot.

    Iterates the static registry — not the DB — as the source of
    identifiers, then joins the active rows in. This way an
    identifier that has no DB rows yet (a brand-new prompt added in
    code) still appears in the list with ``active_version=None``.
    """
    actives = await prompts_service.latest_active_per_identifier(db)

    # Per-identifier total version count — one query, grouped, joined
    # in Python. The number of identifiers is small (low tens), so
    # this is cheap.
    from sqlalchemy import func

    counts_rows = (
        await db.execute(
            select(Prompt.identifier, func.count(Prompt.id)).group_by(Prompt.identifier)
        )
    ).all()
    counts = {ident: int(n) for ident, n in counts_rows}

    out: list[PromptSummary] = []
    for d in prompts_service.list_definitions():
        active = actives.get(d.identifier)
        out.append(
            PromptSummary(
                identifier=d.identifier,
                label=d.label,
                description=d.description,
                active_version=active.version if active else None,
                active_at=active.updated_at if active else None,
                n_versions=counts.get(d.identifier, 0),
            )
        )
    return out


@router.get("/{identifier}", response_model=PromptDetail)
async def get_prompt_detail(
    identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> PromptDetail:
    """Full history + default body for one identifier.

    404 if the identifier isn't in the registry — the set of valid
    identifiers is closed by code, not by anything the UI can post.
    """
    if identifier not in prompts_service.DEFAULTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown prompt identifier {identifier!r}.",
        )
    d = prompts_service.DEFAULTS[identifier]
    rows = await prompts_service.history(db, identifier)
    return PromptDetail(
        identifier=identifier,
        label=d.label,
        description=d.description,
        default_body=d.default_body,
        versions=[_row_to_version_out(r) for r in rows],
    )


@router.post(
    "/{identifier}/versions",
    response_model=PromptVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    identifier: str,
    payload: CreateVersionIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> PromptVersionOut:
    """Append a new version. ``activate=True`` makes it the runtime body.

    The service computes the next version number atomically (latest +
    1) and the partial unique index in migration 0015 guards against
    racing activations. If two clients post simultaneously and both
    set ``activate=true``, the second will get a 409 — surface that
    plainly so the UI can show "someone else just changed this".
    """
    import uuid as _uuid

    try:
        # ``user.user_id`` is a string in CurrentUserDep; convert to
        # UUID when it parses, otherwise leave the FK null (e.g.
        # dev-mode bearer with a non-UUID identity).
        try:
            created_by = _uuid.UUID(user.user_id) if user.user_id else None
        except (ValueError, TypeError):
            created_by = None

        row = await prompts_service.create_version(
            db,
            identifier=identifier,
            body=payload.body,
            change_note=payload.change_note,
            activate=payload.activate,
            created_by_user_id=created_by,
        )
        await db.commit()
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # Most likely the partial unique index — concurrent activate.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Couldn't save: a concurrent edit may have happened. Reload and try again."),
        ) from exc

    return _row_to_version_out(row)


@router.post(
    "/{identifier}/activate/{version}",
    response_model=PromptVersionOut,
)
async def activate_version(
    identifier: str,
    version: int,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> PromptVersionOut:
    """Switch the active flag to an existing version (rollback path).

    404 if the version doesn't exist. Hitting this on the already-
    active row is a noop and returns the row unchanged.
    """
    try:
        row = await prompts_service.activate_version(db, identifier=identifier, version=version)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return _row_to_version_out(row)


@router.post("/{identifier}/rewrite", response_model=RewriteOut)
async def rewrite_prompt_endpoint(
    identifier: str,
    payload: RewriteIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> RewriteOut:
    """Ask the LLM gateway to rewrite the prompt body per an instruction.

    Read-only — does NOT save the new body. The UI loads the result
    into its editor and the user reviews + saves through the normal
    create-version path, which keeps the audit trail intact (change
    note + author land on the new row exactly as they would for a
    hand-typed edit).
    """
    if identifier not in prompts_service.DEFAULTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown prompt identifier {identifier!r}.",
        )
    meta = prompts_service.DEFAULTS[identifier]
    try:
        new_body, rationale = await prompts_service.rewrite_prompt(
            current_body=payload.current_body,
            instruction=payload.instruction,
            identifier=identifier,
            label=meta.label,
            description=meta.description,
        )
    except Exception as exc:
        # Rewrite is a best-effort assist; failure shouldn't break
        # the user's editing session. 502 keeps the failure visible
        # as "upstream LLM problem" rather than 5xx-ing as our bug.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Rewrite failed: {exc}",
        ) from exc
    return RewriteOut(body=new_body, rationale=rationale)


@router.get("/{identifier}/default", response_model=dict)
async def get_default_body(
    identifier: str,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> dict[str, str]:
    """Return the code-default body for ``identifier``.

    Powers the UI's "Restore default" button — the user clicks
    Restore, gets the canonical text loaded into the editor, then
    optionally edits before saving as a new version.
    """
    if identifier not in prompts_service.DEFAULTS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown prompt identifier {identifier!r}.",
        )
    return {
        "identifier": identifier,
        "body": prompts_service.DEFAULTS[identifier].default_body,
    }
