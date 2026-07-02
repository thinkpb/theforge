"""create agent_runs table

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("api_key_hash", sa.String(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_type", sa.String(), nullable=True),
        sa.Column("num_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_runs_team", "agent_runs", ["team"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_team", table_name="agent_runs")
    op.drop_table("agent_runs")
