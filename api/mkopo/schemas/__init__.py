"""API request/response schemas. Separate from ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from mkopo.models import LoanStage, LoanType


class PartyIn(BaseModel):
    name: str
    party_type: str
    email: str | None = None
    role: str


class LoanCreate(BaseModel):
    loan_type: LoanType
    amount: Decimal = Field(gt=0)
    borrower_email: EmailStr
    parties: list[PartyIn] = []


class OwnerOut(BaseModel):
    """Minimal owner identity for display."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    initials: str


class BorrowerOut(BaseModel):
    """Borrower party for display in pipeline + case file header."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    party_type: str  # 'entity' | 'person'


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    stage: LoanStage
    loan_type: LoanType
    amount: Decimal
    status_detail: str | None
    risk_band: str | None
    stage_entered_at: datetime
    owner: OwnerOut | None
    borrower: BorrowerOut | None
    guarantors: list[BorrowerOut] = []
    created_at: datetime
    updated_at: datetime


class StageTransitionIn(BaseModel):
    to_stage: LoanStage
    reason: str


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field_name: str
    value: str
    confidence: float
    status: str
    source_span: dict


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_type: str
    actor_id: str
    action: str
    payload: dict
    created_at: datetime


class ApproveEmailIn(BaseModel):
    """Payload for approving/editing a drafted email from an interrupt()."""

    action: str = Field(pattern="^(send|cancel)$")
    subject: str | None = None
    body_text: str | None = None


class NoteIn(BaseModel):
    """Body for POST /loans/{id}/notes — an internal note the underwriter
    wants to attach to the case file."""

    text: str = Field(min_length=1, max_length=4000)
    kind: Literal["internal_note", "borrower_reply"] = "internal_note"


# --- Underwriting agent output ---


class UnderwritingSection(BaseModel):
    """One labelled section of the underwriting summary.

    `citations` references extraction field names (e.g. "annual_noi"). The UI
    looks them up against the loan's extractions so the user can see the source.
    """

    title: str = Field(max_length=80)
    body: str = Field(max_length=2000)
    citations: list[str] = Field(default_factory=list)


class RiskFlag(BaseModel):
    """A single rules-engine outcome surfaced to the underwriter."""

    rule_id: str
    severity: Literal["block", "warn", "info"]
    passed: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class UnderwritingKPIs(BaseModel):
    """Headline numbers for the workspace tile strip.

    Computed deterministically from extractions + loan amount. The LLM does
    not produce these — it cites them.
    """

    loan_amount: Decimal
    ltv: float | None  # 0–1, e.g. 0.68 = 68%
    dscr: float | None  # e.g. 1.42
    debt_yield: float | None  # 0–1
    doc_confidence: float | None  # 0–1, average accepted-extraction confidence
    property_type: str  # value of PropertyType


class UnderwritingResult(BaseModel):
    """End-to-end underwriting agent output."""

    kpis: UnderwritingKPIs
    sections: list[UnderwritingSection]
    risk_flags: list[RiskFlag]
    recommendation: Literal["proceed_to_decision", "request_more_info", "decline"]
    rationale: str = Field(max_length=800)
    generated_at: datetime
    agent_run_id: uuid.UUID


# --- Comparable loans ---


class ComparableLoanOut(BaseModel):
    """One row in the comparable-loans kNN response."""

    loan_id: uuid.UUID
    reference: str
    borrower: str | None
    loan_type: str
    amount: Decimal
    risk_band: str | None
    similarity: float  # 0..1, higher = more similar


# --- Ask the file (RAG) ---


class CitedChunk(BaseModel):
    document_id: uuid.UUID
    filename: str
    ordinal: int
    content: str
    similarity: float


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=600)


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[CitedChunk]
    comparable_loans: list[ComparableLoanOut]


# --- Credit decision (Phase D) ---


class DecisionPath(str):
    """Enum-like — kept as str so it interops with both Pydantic v2
    Literal and the SQLAlchemy column. Use the constants below."""

    APPROVE = "approve"
    CONDITIONAL = "conditional"
    DECLINE = "decline"


DecisionPathLiteral = Literal["approve", "conditional", "decline"]


class TermSheet(BaseModel):
    """Draft commercial loan terms.

    Per-asset-type defaults; the underwriter edits before sending. We do
    NOT compute live market rates — the LLM is told to use the rules
    engine's data + a per-loan-type rate proxy. A real build would index
    the rate to a live benchmark.
    """

    principal: Decimal
    rate_pct: float = Field(ge=0, le=30)  # annual, e.g. 9.5
    rate_basis: str = Field(max_length=80)  # "10Y T-bill + 525bps" etc.
    term_months: int = Field(ge=1, le=600)
    amortization: str = Field(max_length=60)  # "Interest-only", "25-yr amort", ...
    origination_fee_pct: float = Field(ge=0, le=10)
    prepay_terms: str = Field(max_length=80)  # "3-2-1 step-down", "open", ...
    notes: str = Field(default="", max_length=500)


class ConditionDraft(BaseModel):
    """One condition-to-close drafted by the decision agent."""

    description: str = Field(min_length=4, max_length=400)
    due_within_days: int | None = Field(default=None, ge=1, le=365)


class AdverseActionLetter(BaseModel):
    """ECOA-defensible decline notice.

    Regulation B requires PRINCIPAL REASONS to be specific — "internal
    policy" is not sufficient. `principal_reasons` carries the named
    rule_ids the decision relied on; `body_text` is the underwriter-ready
    letter that names them explicitly.
    """

    subject: str = Field(max_length=120)
    body_text: str = Field(min_length=20, max_length=3000)
    principal_reasons: list[str] = Field(
        min_length=1,
        description="The rule_ids (or named factors) the decline rests on.",
    )


class DecisionResult(BaseModel):
    """End-to-end decision support agent output.

    Exactly one of `term_sheet` / `adverse_action_letter` is populated
    (decline → AAL, approve/conditional → term sheet). `conditions` is
    populated only for the conditional path.
    """

    path: DecisionPathLiteral
    confidence: float = Field(ge=0, le=1)
    verdict_text: str = Field(max_length=120)
    rationale: str = Field(max_length=800)
    term_sheet: TermSheet | None = None
    conditions: list[ConditionDraft] = Field(default_factory=list)
    adverse_action_letter: AdverseActionLetter | None = None
    generated_at: datetime
    agent_run_id: uuid.UUID


class ConditionOut(BaseModel):
    """One row from the conditions table for display."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    description: str
    status: str
    due_date: datetime | None
    drafted_by_agent: bool
    created_at: datetime
