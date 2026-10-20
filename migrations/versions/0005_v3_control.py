"""V3.1 偏好、反馈与画像控制表

Revision ID: 0005_v3_control
Revises: 0004_v3_digest_profile
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from learning_agent.persistence.models import UTCDateTime

revision: str = "0005_v3_control"
down_revision: str | None = "0004_v3_digest_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topics_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("top_n", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("catch_up_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "push_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["push_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "action", name="uq_push_feedback_item_action"),
    )
    op.create_index("ix_push_feedback_item", "push_feedback", ["item_id"])
    op.create_table(
        "push_item_ingestions",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["push_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_table(
        "learning_profile_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("learning_concepts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_corrected",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("learning_concepts") as batch_op:
        batch_op.drop_column("user_corrected")
    op.drop_table("learning_profile_settings")
    op.drop_table("push_item_ingestions")
    op.drop_index("ix_push_feedback_item", table_name="push_feedback")
    op.drop_table("push_feedback")
    op.drop_table("push_preferences")
