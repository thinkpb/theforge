"""add event type to audit_log

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("event", sa.String(), nullable=False, server_default="completion"),
    )


def downgrade() -> None:
    op.drop_column("audit_log", "event")
