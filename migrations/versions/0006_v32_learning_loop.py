"""V3.2 学习笔记、自测、闪卡与复习记录

Revision ID: 0006_v32_learning_loop
Revises: 0005_v3_control
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from learning_agent.persistence.models import UTCDateTime

revision: str = "0006_v32_learning_loop"
down_revision: str | None = "0005_v3_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "learning_notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("push_item_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("digest_date", sa.String(length=10), nullable=False),
        sa.Column("topic_name", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["push_item_id"], ["push_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["learning_topics.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("push_item_id", name="uq_learning_notes_push_item"),
    )
    op.create_index(
        "ix_learning_notes_topic_created",
        "learning_notes",
        ["topic_name", "created_at"],
    )

    op.create_table(
        "learning_note_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("note_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("publisher", sa.String(length=160), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column(
            "provider", sa.String(length=32), nullable=False, server_default="digest"
        ),
        sa.Column("excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["learning_notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", "label", name="uq_learning_note_source_label"),
        sa.UniqueConstraint("note_id", "url", name="uq_learning_note_source_url"),
    )

    op.create_table(
        "learning_quizzes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("note_id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["learning_notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id"),
    )

    op.create_table(
        "learning_quiz_questions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["quiz_id"], ["learning_quizzes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["learning_note_sources.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quiz_id", "position", name="uq_learning_quiz_position"),
    )

    op.create_table(
        "learning_flashcards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("note_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("card_key", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lapse_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reviewed_at", UTCDateTime(), nullable=True),
        sa.Column("next_review_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["learning_notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["learning_note_sources.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", "card_key", name="uq_learning_flashcard_key"),
    )
    op.create_index(
        "ix_learning_flashcards_due", "learning_flashcards", ["next_review_at"]
    )
    op.create_index("ix_learning_flashcards_note", "learning_flashcards", ["note_id"])

    op.create_table(
        "learning_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("flashcard_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("previous_interval_days", sa.Integer(), nullable=False),
        sa.Column("new_interval_days", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", UTCDateTime(), nullable=False),
        sa.Column("next_review_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["flashcard_id"], ["learning_flashcards.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_reviews_card_time",
        "learning_reviews",
        ["flashcard_id", "reviewed_at"],
    )
    op.create_index("ix_learning_reviews_time", "learning_reviews", ["reviewed_at"])


def downgrade() -> None:
    op.drop_index("ix_learning_reviews_time", table_name="learning_reviews")
    op.drop_index("ix_learning_reviews_card_time", table_name="learning_reviews")
    op.drop_table("learning_reviews")
    op.drop_index("ix_learning_flashcards_note", table_name="learning_flashcards")
    op.drop_index("ix_learning_flashcards_due", table_name="learning_flashcards")
    op.drop_table("learning_flashcards")
    op.drop_table("learning_quiz_questions")
    op.drop_table("learning_quizzes")
    op.drop_table("learning_note_sources")
    op.drop_index("ix_learning_notes_topic_created", table_name="learning_notes")
    op.drop_table("learning_notes")
