"""Document and extraction models."""

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mkopo.models.base import Base

if TYPE_CHECKING:
    from mkopo.models.loan import Loan


class DocumentType(enum.StrEnum):
    LOAN_APPLICATION = "loan_application"
    APPRAISAL = "appraisal"
    RENT_ROLL = "rent_roll"
    PERSONAL_FINANCIAL_STATEMENT = "personal_financial_statement"
    TAX_RETURN = "tax_return"
    BANK_STATEMENT = "bank_statement"
    INSURANCE = "insurance"
    TITLE_REPORT = "title_report"
    OTHER = "other"
    UNKNOWN = "unknown"


class ExtractionStatus(enum.StrEnum):
    PROPOSED = "proposed"  # AI just wrote it
    ACCEPTED = "accepted"  # auto-confirmed above threshold
    QUEUED_FOR_REVIEW = "queued_for_review"
    OVERRIDDEN = "overridden"  # human changed the value


class Document(Base):
    __tablename__ = "documents"

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[DocumentType] = mapped_column(
        String(64), nullable=False, default=DocumentType.UNKNOWN
    )
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    # sha256(bytes) recorded at upload time. Feeds the materials hash
    # so a post-decision document swap is detectable. Nullable for
    # backwards compatibility with rows uploaded before 0011_; the
    # materials-hash service treats null as "unknown content" which
    # forces a re-underwriting if a decision was made with such a row
    # in scope (conservative: better to ask than to let a quiet
    # tamper through).
    content_hash: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    loan: Mapped["Loan"] = relationship(back_populates="documents")
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Extraction(Base):
    """A single field extracted from a document."""

    __tablename__ = "extractions"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_span: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[ExtractionStatus] = mapped_column(
        String(32), nullable=False, default=ExtractionStatus.PROPOSED
    )
    model_used: Mapped[str | None] = mapped_column(String(128))

    document: Mapped["Document"] = relationship(back_populates="extractions")


class ReviewTask(Base):
    """Items routed to human review queue."""

    __tablename__ = "review_tasks"

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    extraction: Mapped["Extraction"] = relationship(lazy="joined")
