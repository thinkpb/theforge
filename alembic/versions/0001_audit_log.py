"""create append-only audit_log table

Revision ID: 0001
Revises:
Create Date: 2026-06-11
"""

import sqlalchemy as sa
from alembic import op

from forge.audit import APPEND_ONLY_DDL

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("api_key_hash", sa.String(), nullable=False),
        sa.Column("model_alias", sa.String(), nullable=False),
        sa.Column("upstream_model", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])
    op.create_index("ix_audit_log_api_key_hash", "audit_log", ["api_key_hash"])
    for statement in APPEND_ONLY_DDL:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only")
    op.drop_table("audit_log")
