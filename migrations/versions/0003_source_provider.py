"""文档快照来源类型

Revision ID: 0003_source_provider
Revises: 0002_ingestion_watch
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_source_provider"
down_revision: str | None = "0002_ingestion_watch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_snapshots",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default="fetch",
        ),
    )


def downgrade() -> None:
    op.drop_column("document_snapshots", "provider")
