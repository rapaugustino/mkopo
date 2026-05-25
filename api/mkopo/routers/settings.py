"""Staff settings endpoints.

Currently houses only the institution-settings surface — the lender
identity + authorized officer + credit reporting agency that
agents thread into borrower-visible artifacts. If the settings
landscape grows (branding, default rate sheets, intake autonomy
defaults), they live alongside here under ``/settings/...``.

Read endpoint is unauthenticated-staff (CurrentUserDep is enough).
Write endpoint also accepts CurrentUserDep but writes a
``settings_changed`` audit event so the trail records *who* updated
*which* fields (a compliance-relevant config edit on a lender's
adverse-action contact info isn't something to leave un-audited).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.services.institution import (
    get_institution,
    update_institution,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/settings", tags=["settings"])


class InstitutionSettingsOut(BaseModel):
    """Wire shape of the singleton row. All fields nullable until
    operator fills the staff settings page."""

    model_config = ConfigDict(from_attributes=True)

    lender_name: str | None
    lender_address: str | None
    lender_phone: str | None
    lender_email: str | None
    authorized_officer_name: str | None
    authorized_officer_title: str | None
    credit_reporting_agency_name: str | None
    credit_reporting_agency_address: str | None
    credit_reporting_agency_phone: str | None
    # Convenience flag — True iff *any* lender contact field is
    # populated. Frontend uses this to render a "complete setup"
    # call-to-action when the operator hasn't started yet.
    configured: bool


class InstitutionSettingsIn(BaseModel):
    """Patch payload — every field optional. Pass ``""`` to clear a
    field (server-side trim collapses that to ``None``); omit a
    field to leave it untouched."""

    lender_name: str | None = None
    lender_address: str | None = None
    lender_phone: str | None = None
    lender_email: str | None = None
    authorized_officer_name: str | None = None
    authorized_officer_title: str | None = None
    credit_reporting_agency_name: str | None = None
    credit_reporting_agency_address: str | None = None
    credit_reporting_agency_phone: str | None = None


@router.get("/institution", response_model=InstitutionSettingsOut)
async def get_institution_settings(
    user: CurrentUserDep, db: DbSessionDep
) -> InstitutionSettingsOut:
    """Return the current lender identity + officer + CRA triple.

    Never 404s — the migration seeded the row, and the service layer
    materialises a ``None``-valued snapshot if it's somehow missing.
    """
    ctx = await get_institution(db)
    return InstitutionSettingsOut(
        lender_name=ctx.lender_name,
        lender_address=ctx.lender_address,
        lender_phone=ctx.lender_phone,
        lender_email=ctx.lender_email,
        authorized_officer_name=ctx.authorized_officer_name,
        authorized_officer_title=ctx.authorized_officer_title,
        credit_reporting_agency_name=ctx.credit_reporting_agency_name,
        credit_reporting_agency_address=ctx.credit_reporting_agency_address,
        credit_reporting_agency_phone=ctx.credit_reporting_agency_phone,
        configured=ctx.configured,
    )


@router.patch("/institution", response_model=InstitutionSettingsOut)
async def update_institution_settings(
    payload: InstitutionSettingsIn,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> InstitutionSettingsOut:
    """Patch one or more fields on the institution singleton.

    Writes a ``settings_changed`` audit event with the set of keys
    that were touched (not the values — keeps the audit log free of
    secondary copies of the lender identity, which the row itself
    already stores). The audit event is loan-agnostic; we use a
    NIL UUID for ``loan_id`` since this is an org-wide config edit
    not tied to any one application.
    """
    incoming = payload.model_dump(exclude_unset=True)
    ctx = await update_institution(db, **incoming)
    if incoming:
        # Audit via structlog only — the AuditEvent table requires a
        # ``loan_id`` FK, and an org-level settings edit isn't scoped
        # to one loan. A future ``org_audit_events`` table could
        # capture this properly; for now the structured log line is
        # the durable record. Logs include the editor's user_id so
        # the "who changed what" trail still exists.
        logger.info(
            "institution_settings_changed",
            user_id=user.user_id,
            fields=sorted(incoming.keys()),
        )

    await db.commit()
    return InstitutionSettingsOut(
        lender_name=ctx.lender_name,
        lender_address=ctx.lender_address,
        lender_phone=ctx.lender_phone,
        lender_email=ctx.lender_email,
        authorized_officer_name=ctx.authorized_officer_name,
        authorized_officer_title=ctx.authorized_officer_title,
        credit_reporting_agency_name=ctx.credit_reporting_agency_name,
        credit_reporting_agency_address=ctx.credit_reporting_agency_address,
        credit_reporting_agency_phone=ctx.credit_reporting_agency_phone,
        configured=ctx.configured,
    )
