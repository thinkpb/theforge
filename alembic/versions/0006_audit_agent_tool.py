"""add agent and tool attribution to audit_log

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("agent", sa.String(), nullable=True))
    op.add_column("audit_log", sa.Column("tool", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "tool")
    op.drop_column("audit_log", "agent")
