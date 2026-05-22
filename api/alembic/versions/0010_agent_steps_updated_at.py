"""Add the ``updated_at`` column the Base class expects.

The ``mkopo.models.base.Base`` declarative class auto-maps three
columns onto every ORM model: ``id``, ``created_at``, and
``updated_at``. Migration ``0009_agent_steps`` created the table but
only included ``id`` and ``created_at`` (plus the agent-step-specific
``started_at``/``completed_at``), so any SELECT through the ORM
failed with ``column agent_steps.updated_at does not exist``.

Adding the column with a sensible default brings the on-disk schema
back in sync with the ORM. ``DEFAULT now()`` so existing rows
populate immediately; ``onupdate=now()`` lives in the ORM layer.

Revision ID: 0010_agent_steps_updated_at
Revises: 0009_agent_steps
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_agent_steps_updated_at"
down_revision: str | None = "0009_agent_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_steps",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_steps", "updated_at")
