"""create ingestion_jobs table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team", sa.String(), nullable=False),
        sa.Column("api_key_hash", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=True),
        sa.Column("chunks", sa.Integer(), nullable=True),
        sa.Column("pii_redactions", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ingestion_jobs_team", "ingestion_jobs", ["team"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_team", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
