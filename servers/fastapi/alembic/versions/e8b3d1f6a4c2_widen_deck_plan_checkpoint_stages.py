"""Admit the content brief into the deck-wide planning checkpoint stages.

The Stage A content brief is the most expensive preamble a generation runs
(535 s measured on a 20-slide deck). The admission deadline is pinned per
operation, so a provider failure after the brief made attempt 2 pay for it a
second time and left authoring almost no budget. Persisting the brief needs
one more value in the checkpoint stage vocabulary.

Revision ID: e8b3d1f6a4c2
Revises: c1a3e5f7b9d0
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e8b3d1f6a4c2"
down_revision: str | None = "c1a3e5f7b9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "authored_deck_plan_checkpoints"
CONSTRAINT = "ck_authored_deck_plan_checkpoint_stage"
ADDED_STAGE = "content_brief"

OLD_STAGES = ("deck_plan", "chapter_plan", "header", "illustration")
NEW_STAGES = (*OLD_STAGES, ADDED_STAGE)


def _stage_expression(stages: Sequence[str]) -> str:
    rendered = ", ".join(f"'{stage}'" for stage in stages)
    return f"stage IN ({rendered})"


def _has_table() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def _table_definition(stages: Sequence[str]) -> sa.Table:
    """The full table as it must exist after the rewrite.

    SQLite cannot alter a CHECK constraint, so batch mode recreates the table.
    Reflection would silently drop the other two CHECK constraints (SQLite
    reflection does not surface them through the batch copy), so every column,
    constraint and index is spelled out here and handed to ``copy_from``.
    """

    metadata = sa.MetaData()
    return sa.Table(
        TABLE,
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("generation_job_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("output_digest", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("produced_attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["generation_job_id"],
            ["presentation_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "generation_job_id",
            "stage",
            name="uq_authored_deck_plan_checkpoint_stage",
        ),
        sa.CheckConstraint(_stage_expression(stages), name=CONSTRAINT),
        sa.CheckConstraint(
            "length(input_digest) = 64 AND length(output_digest) = 64",
            name="ck_authored_deck_plan_checkpoint_digests",
        ),
        sa.CheckConstraint(
            "produced_attempt_number >= 1",
            name="ck_authored_deck_plan_checkpoint_attempt",
        ),
        sa.Index(
            "ix_authored_deck_plan_checkpoint_job",
            "generation_job_id",
            "stage",
        ),
    )


def _apply_stage_constraint(stages: Sequence[str]) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # ``recreate="always"`` with an explicit ``copy_from`` rewrites the
        # table from the definition above and copies the rows across, so no
        # column, index or foreign key depends on what reflection can see.
        with op.batch_alter_table(
            TABLE, copy_from=_table_definition(stages), recreate="always"
        ):
            pass
        return
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, _stage_expression(stages))


def upgrade() -> None:
    # A database that never reached the checkpoint revision has no table to
    # widen. "No constraint" then means "nothing to do", not "alter a table
    # that is not there".
    if not _has_table():
        return
    _apply_stage_constraint(NEW_STAGES)


def downgrade() -> None:
    if not _has_table():
        return
    # Rows for the added stage cannot survive the narrower constraint.
    op.execute(
        sa.text(f"DELETE FROM {TABLE} WHERE stage = :stage").bindparams(
            stage=ADDED_STAGE
        )
    )
    _apply_stage_constraint(OLD_STAGES)
