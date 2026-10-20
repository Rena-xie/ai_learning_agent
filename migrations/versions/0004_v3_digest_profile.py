"""V3 每日资讯与学习画像基础表

Revision ID: 0004_v3_digest_profile
Revises: 0003_source_provider
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from learning_agent.persistence.models import UTCDateTime

revision: str = "0004_v3_digest_profile"
down_revision: str | None = "0003_source_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("feed_url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column(
            "category", sa.String(length=64), nullable=False, server_default="AI"
        ),
        sa.Column(
            "trust_level",
            sa.String(length=16),
            nullable=False,
            server_default="trusted",
        ),
        sa.Column("auto_push", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "auto_ingest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_checked_at", UTCDateTime(), nullable=True),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("last_modified", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_url", name="uq_push_sources_feed_url"),
    )
    op.create_index("ix_push_sources_enabled", "push_sources", ["enabled"])

    op.create_table(
        "push_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("topic", sa.String(length=64), nullable=False, server_default="AI"),
        sa.Column("published_at", UTCDateTime(), nullable=True),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.ForeignKeyConstraint(["source_id"], ["push_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_url", name="uq_push_items_canonical_url"),
    )
    op.create_index("ix_push_items_published", "push_items", ["published_at"])
    op.create_index("ix_push_items_source", "push_items", ["source_id"])

    op.create_table(
        "push_digests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("digest_date", sa.String(length=10), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="queued"
        ),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest_date", name="uq_push_digests_date"),
    )
    op.create_index("ix_push_digests_status", "push_digests", ["status"])

    op.create_table(
        "push_digest_items",
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["digest_id"], ["push_digests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["push_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("digest_id", "item_id"),
    )

    op.create_table(
        "push_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("digest_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["digest_id"], ["push_digests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest_id", "channel", name="uq_push_delivery_channel"),
    )
    op.create_index("ix_push_deliveries_status", "push_deliveries", ["status"])

    op.create_table(
        "learning_topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="learning"
        ),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_learning_topics_name"),
    )

    op.create_table(
        "learning_concepts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "mastery", sa.String(length=16), nullable=False, server_default="learning"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["learning_topics.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "name", name="uq_learning_concept_topic_name"),
    )
    op.create_index("ix_learning_concepts_topic", "learning_concepts", ["topic_id"])

    op.create_table(
        "learning_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["learning_topics.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"], ["learning_concepts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_learning_events_created", "learning_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_learning_events_created", table_name="learning_events")
    op.drop_table("learning_events")
    op.drop_index("ix_learning_concepts_topic", table_name="learning_concepts")
    op.drop_table("learning_concepts")
    op.drop_table("learning_topics")
    op.drop_index("ix_push_deliveries_status", table_name="push_deliveries")
    op.drop_table("push_deliveries")
    op.drop_table("push_digest_items")
    op.drop_index("ix_push_digests_status", table_name="push_digests")
    op.drop_table("push_digests")
    op.drop_index("ix_push_items_source", table_name="push_items")
    op.drop_index("ix_push_items_published", table_name="push_items")
    op.drop_table("push_items")
    op.drop_index("ix_push_sources_enabled", table_name="push_sources")
    op.drop_table("push_sources")
