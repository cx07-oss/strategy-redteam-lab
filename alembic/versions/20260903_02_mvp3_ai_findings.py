"""Persist deterministic verification of AI hypotheses.

Revision ID: 20260903_02
Revises: 20260903_01
Create Date: 2026-09-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260903_02"
down_revision = "20260903_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column(
        "experiment_results",
        sa.Column("ai_findings", json_type, nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("experiment_results", "ai_findings")
