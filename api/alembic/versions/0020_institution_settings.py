"""institution_settings — single-row table of lender identity.

The decision agent's adverse-action letter prompt and the intake
agent's borrower-email prompt both need real values for things like
*lender name*, *authorized officer*, *credit reporting agency*. Today
they emit ``[LENDER NAME]`` style placeholders because there's nowhere
in the system to source them. This migration creates a singleton
config row that the staff settings page edits and the prompts read.

Why a typed table rather than a free-form key/value:

- The set of fields is small + known up front. A typed schema
  catches typos in identifier strings.
- ECOA Reg B specifies the *exact* contact-disclosure clauses an
  adverse-action letter must carry. We want a column called
  ``credit_reporting_agency_name`` so it's obvious which clause
  it backs, not a key string lookup.
- A future "branding" surface (lender logo, brand colors) would
  fit here cleanly.

Singleton row: we keep ``id`` as a UUID PK (inherited from Base) but
the service-layer accessor always reads/writes the most recently
updated row, and the seed inserts one default at migration time so
fresh databases have something for the agents to read.

Revision ID: 0020_institution_settings
Revises: 0019_llm_call_prompt_version
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_institution_settings"
down_revision = "0019_llm_call_prompt_version"
branch_labels = None
depends_on = None


# Fixed UUID for the singleton row. Using a deterministic UUID rather
# than uuid4() so the row is locate-able without a SELECT — the
# service layer can ``INSERT ... ON CONFLICT`` against it.
SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    op.create_table(
        "institution_settings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True
        ),
        # Lender identity — surfaced on every borrower-visible artifact
        # (decision letters, term sheets, doc-request emails).
        sa.Column("lender_name", sa.String(256), nullable=True),
        sa.Column("lender_address", sa.Text, nullable=True),
        sa.Column("lender_phone", sa.String(64), nullable=True),
        sa.Column("lender_email", sa.String(256), nullable=True),
        # The human whose name signs decision letters. Distinct from
        # the loan owner — the owner is the day-to-day reviewer; the
        # authorized officer is the credit authority whose sign-off
        # is legally meaningful. In a small lender they may be the
        # same person; we store separately so they don't have to be.
        sa.Column("authorized_officer_name", sa.String(256), nullable=True),
        sa.Column("authorized_officer_title", sa.String(128), nullable=True),
        # ECOA Reg B requires naming the consumer reporting agency
        # that produced the report driving an adverse action, when
        # one was consulted. Three fields are exactly what 1002.9(b)
        # asks the letter to disclose.
        sa.Column("credit_reporting_agency_name", sa.String(256), nullable=True),
        sa.Column("credit_reporting_agency_address", sa.Text, nullable=True),
        sa.Column("credit_reporting_agency_phone", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    # Seed the singleton row so a brand-new DB has something for the
    # agents to read on day one. Values intentionally left ``NULL`` —
    # the staff settings page is where they get filled in. The
    # prompt layer falls back to "(not configured)" markers when a
    # field is null, which is more honest than fabricating a name.
    op.execute(
        f"INSERT INTO institution_settings (id) VALUES ('{SINGLETON_ID}')"
    )


def downgrade() -> None:
    op.drop_table("institution_settings")
