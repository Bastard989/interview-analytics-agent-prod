"""init

Revision ID: e062d4e36604
Revises:
Create Date: 2026-02-02 13:41:40.898323
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e062d4e36604"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "done", "failed", name="pipelinestatus"),
            nullable=False,
        ),
        sa.Column(
            "consent", sa.Enum("unknown", "granted", "denied", name="consentstatus"), nullable=False
        ),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("raw_transcript", sa.Text(), nullable=False),
        sa.Column("enhanced_transcript", sa.Text(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=True),
    )

    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("meeting_id", sa.String(length=64), sa.ForeignKey("meetings.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=64), nullable=True),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("enhanced_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.UniqueConstraint("meeting_id", "seq", name="uq_transcript_segments_meeting_seq"),
    )
    op.create_index(
        "ix_transcript_segments_meeting_seq",
        "transcript_segments",
        ["meeting_id", "seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transcript_segments_meeting_seq", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_table("meetings")
    op.execute("DROP TYPE IF EXISTS pipelinestatus")
    op.execute("DROP TYPE IF EXISTS consentstatus")
