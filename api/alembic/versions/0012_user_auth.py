"""User authentication: password hashes + magic-link tokens.

Phase 1 of the borrower self-service product. The existing ``users``
table was modelled for internal staff identity only (name, email,
role) and never carried credentials — the dev-token auth layer
returned a fixed identity bypassing the DB entirely. This migration
adds the credential columns and the magic-link mechanism so real
users can sign in.

Schema changes:

- ``users.password_hash`` — nullable bcrypt-cost-12 hash. Nullable
  because magic-link-only users may never set a password.
- ``users.email_verified_at`` — nullable timestamp. Filled in when
  the user clicks a verification magic-link. Operational endpoints
  may require a verified email; the gate is application-level.
- New ``magic_links`` table — single-use tokens. We never store
  the token itself, only ``sha256(token)`` so a DB dump can't be
  used to log in as anyone. The token plain-text lives only in the
  delivered email.

The ``users.role`` column already exists from migration 0002 and
keeps its existing semantics: ``"underwriter"`` / ``"admin"`` for
staff plus the new ``"borrower"`` value for self-service users.

Revision ID: 0012_user_auth
Revises: 0011_document_content_hash
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0012_user_auth"
down_revision: str | None = "0011_document_content_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- users credential columns -------------------------------------
    op.add_column("users", sa.Column("password_hash", sa.String(256), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- magic_links --------------------------------------------------
    op.create_table(
        "magic_links",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # The user we'll authenticate when the link is consumed. We
        # bind by id (not email) so a user changing their email later
        # doesn't invalidate outstanding links — the link still works
        # for the user who minted it.
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # sha256(plain_token) — 64 hex chars. We never persist the
        # plain-text token; the email contains it and that's it. A DB
        # dump leak can't be used to log in.
        sa.Column("token_hash", sa.String(64), nullable=False),
        # ``login``           — replaces a password for one-time auth
        # ``set_password``    — sent on signup so user can set initial PW
        # ``password_reset``  — sent on "forgot password"
        # ``email_verify``    — sent to confirm a new email address
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Set to now() when consumed. Single-use semantics — a token
        # with ``consumed_at IS NOT NULL`` is rejected. Records WHO
        # consumed it (the user's id) implicitly via ``user_id``.
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Token-hash lookup must be O(log n) since every consume request
    # arrives with one. Unique because a hash collision would be a
    # security event regardless of cause.
    op.create_index(
        "ix_magic_links_token_hash",
        "magic_links",
        ["token_hash"],
        unique=True,
    )
    # Sweep stale links by expiry — also useful for admin "show me
    # outstanding links for this user".
    op.create_index(
        "ix_magic_links_user_expires",
        "magic_links",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_magic_links_user_expires", table_name="magic_links")
    op.drop_index("ix_magic_links_token_hash", table_name="magic_links")
    op.drop_table("magic_links")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "password_hash")
