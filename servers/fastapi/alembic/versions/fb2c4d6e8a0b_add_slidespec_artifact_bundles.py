"""Add append-only SlideSpec artifact bundles.

Revision ID: fb2c4d6e8a0b
Revises: fa1b2c3d4e5f
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "fb2c4d6e8a0b"
down_revision: str | None = "fa1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("slidespec_artifact_bundles"):
        expected_columns = {
            "id",
            "presentation_id",
            "slide_id",
            "slide_index",
            "spec_revision",
            "route",
            "status",
            "base_spec_hash",
            "candidate_spec_hash",
            "render_input_hash",
            "html_hash",
            "render_hash",
            "image_hash",
            "spec",
            "repair_plan",
            "acceptance",
            "evidence",
            "manifest",
            "publication_receipt",
            "html",
            "created_at",
        }
        actual_columns = {
            column["name"]
            for column in inspector.get_columns("slidespec_artifact_bundles")
        }
        if actual_columns != expected_columns:
            missing = sorted(expected_columns - actual_columns)
            unexpected = sorted(actual_columns - expected_columns)
            raise RuntimeError(
                "Existing slidespec_artifact_bundles schema is incompatible; "
                f"missing={missing}, unexpected={unexpected}"
            )

        index_names = {
            index["name"]
            for index in inspector.get_indexes("slidespec_artifact_bundles")
        }
        if "ix_slidespec_artifact_bundles_presentation_slide_created" not in index_names:
            op.create_index(
                "ix_slidespec_artifact_bundles_presentation_slide_created",
                "slidespec_artifact_bundles",
                ["presentation_id", "slide_index", "created_at"],
                unique=False,
            )
        return

    op.create_table(
        "slidespec_artifact_bundles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("slide_id", sa.Uuid(), nullable=False),
        sa.Column("slide_index", sa.Integer(), nullable=False),
        sa.Column("spec_revision", sa.Integer(), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("base_spec_hash", sa.String(length=71), nullable=True),
        sa.Column("candidate_spec_hash", sa.String(length=71), nullable=False),
        sa.Column("render_input_hash", sa.String(length=71), nullable=False),
        sa.Column("html_hash", sa.String(length=71), nullable=False),
        sa.Column("render_hash", sa.String(length=71), nullable=False),
        sa.Column("image_hash", sa.String(length=71), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("repair_plan", sa.JSON(), nullable=True),
        sa.Column("acceptance", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("publication_receipt", sa.JSON(), nullable=True),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "slide_index >= 0",
            name="ck_slidespec_artifact_bundles_slide_index_nonnegative",
        ),
        sa.CheckConstraint(
            "spec_revision >= 1",
            name="ck_slidespec_artifact_bundles_spec_revision_positive",
        ),
        sa.CheckConstraint(
            "status IN ('imported', 'shadow', 'rejected', 'published')",
            name="ck_slidespec_artifact_bundles_status",
        ),
        sa.CheckConstraint(
            "route IN ('import_shadow', 'repair_shadow', 'slidespec_active')",
            name="ck_slidespec_artifact_bundles_route",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"], ["presentations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["slide_id"], ["slides.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "slide_id",
            "candidate_spec_hash",
            "status",
            "route",
            name="uq_slidespec_artifact_bundles_slide_candidate_outcome",
        ),
    )
    op.create_index(
        "ix_slidespec_artifact_bundles_presentation_slide_created",
        "slidespec_artifact_bundles",
        ["presentation_id", "slide_index", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_slidespec_artifact_bundles_presentation_slide_created",
        table_name="slidespec_artifact_bundles",
    )
    op.drop_table("slidespec_artifact_bundles")
