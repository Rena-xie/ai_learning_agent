"""入库任务通知订阅与消息类型

Revision ID: 0002_ingestion_watch
Revises: 0001_initial_schema
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from learning_agent.persistence.models import UTCDateTime

revision: str = "0002_ingestion_watch"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="chat",
        ),
    )
    op.create_table(
        "ingestion_watches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="watching",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", UTCDateTime(), nullable=True),
        sa.Column("next_check_at", UTCDateTime(), nullable=False),
        sa.Column("notified_at", UTCDateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index(
        "ix_ingestion_watches_status_next",
        "ingestion_watches",
        ["status", "next_check_at"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_watches_conversation",
        "ingestion_watches",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_watches_conversation", table_name="ingestion_watches")
    op.drop_index("ix_ingestion_watches_status_next", table_name="ingestion_watches")
    op.drop_table("ingestion_watches")
    op.drop_column("messages", "kind")
