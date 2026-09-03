"""MVP 2 experiment persistence.

Revision ID: 20260903_01
Revises:
Create Date: 2026-09-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260903_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum("PENDING", "RUNNING", "COMPLETED", "FAILED", name="experiment_status")
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("status", status, nullable=False),
        sa.Column("configuration", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("dataset_provenance_hash", sa.String(length=64), nullable=True),
        sa.Column("software_version", sa.String(length=128), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "experiment_results",
        sa.Column("experiment_id", sa.String(length=36), nullable=False),
        sa.Column("gross_return", sa.Float(), nullable=False), sa.Column("net_return", sa.Float(), nullable=False),
        sa.Column("benchmark_return", sa.Float(), nullable=False), sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True), sa.Column("turnover", sa.Float(), nullable=True),
        sa.Column("total_cost", sa.Float(), nullable=False),
        sa.Column("structured_result", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("experiment_id"),
    )


def downgrade() -> None:
    op.drop_table("experiment_results")
    op.drop_table("experiments")
    sa.Enum(name="experiment_status").drop(op.get_bind(), checkfirst=True)
