"""Add SlideSpec candidate staging and append-only publication attempts.

Revision ID: fc3d5e7f9a1b
Revises: fb2c4d6e8a0b
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "fc3d5e7f9a1b"
down_revision: str | None = "fb2c4d6e8a0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite batch mode reflects the whole table before recreating it.  Some
    # repairable legacy databases reached this revision with the artifact
    # table present before its referenced ``slides`` table.  Following foreign
    # keys during reflection would make the migration fail with
    # ``NoSuchTableError`` before Alembic can finish repairing the lineage.
    with op.batch_alter_table(
        "slidespec_artifact_bundles",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            "ck_slidespec_artifact_bundles_status", type_="check"
        )
        batch.create_check_constraint(
            "ck_slidespec_artifact_bundles_status",
            "status IN ('imported', 'candidate', 'shadow', 'rejected', 'published')",
        )

    op.create_table(
        "slidespec_publication_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_bundle_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("slide_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('staged', 'activation_committed', 'verified', 'failed', "
            "'rolled_back', 'rollback_conflict')",
            name="ck_slidespec_publication_attempt_state",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_bundle_id"],
            ["slidespec_artifact_bundles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"], ["presentations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["slide_id"], ["slides.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            "state",
            name="uq_slidespec_publication_attempt_state",
        ),
    )
    op.create_index(
        "ix_slidespec_publication_attempts_slide_created",
        "slidespec_publication_attempts",
        ["slide_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_slidespec_publication_attempts_slide_created",
        table_name="slidespec_publication_attempts",
    )
    op.drop_table("slidespec_publication_attempts")
    with op.batch_alter_table(
        "slidespec_artifact_bundles",
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        batch.drop_constraint(
            "ck_slidespec_artifact_bundles_status", type_="check"
        )
        batch.create_check_constraint(
            "ck_slidespec_artifact_bundles_status",
            "status IN ('imported', 'shadow', 'rejected', 'published')",
        )
