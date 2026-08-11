"""Add canonical asset and presentation profile compatibility bridges.

Revision ID: a8c1e4f7b2d9
Revises: f7c9e1a3b5d8

The legacy columns are deliberately retained. Data backfill touches private
files and validates presentation identities, so it is performed by the
restartable compatibility service rather than inside this schema transaction.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "a8c1e4f7b2d9"
down_revision: str | None = "f7c9e1a3b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _constraint_columns(constraint: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(value) for value in constraint.get("constrained_columns") or ())


def _unique_columns(constraint: dict[str, object]) -> tuple[str, ...]:
    values = constraint.get("column_names") or constraint.get("constrained_columns")
    return tuple(str(value) for value in values or ())


def _ensure_image_asset_constraints() -> None:
    inspector = sa.inspect(op.get_bind())
    unique_constraints = inspector.get_unique_constraints("imageasset")
    named_unique = next(
        (
            constraint
            for constraint in unique_constraints
            if constraint.get("name") == "uq_imageasset_canonical_asset_id"
        ),
        None,
    )
    if named_unique is not None and _unique_columns(named_unique) != (
        "canonical_asset_id",
    ):
        raise RuntimeError("imageasset canonical asset unique constraint is invalid")
    has_canonical_unique = any(
        _unique_columns(constraint) == ("canonical_asset_id",)
        for constraint in unique_constraints
    )

    foreign_keys = inspector.get_foreign_keys("imageasset")
    named_foreign_key = next(
        (
            constraint
            for constraint in foreign_keys
            if constraint.get("name") == "fk_imageasset_canonical_asset_id"
        ),
        None,
    )

    def _valid_foreign_key(constraint: dict[str, object]) -> bool:
        options = constraint.get("options") or {}
        return (
            _constraint_columns(constraint) == ("canonical_asset_id",)
            and constraint.get("referred_table") == "workspace_assets"
            and tuple(constraint.get("referred_columns") or ()) == ("id",)
            and str(options.get("ondelete") or "").upper() == "SET NULL"
        )

    if named_foreign_key is not None and not _valid_foreign_key(named_foreign_key):
        raise RuntimeError("imageasset canonical asset foreign key is invalid")
    canonical_foreign_keys = [
        constraint
        for constraint in foreign_keys
        if "canonical_asset_id" in _constraint_columns(constraint)
    ]
    if any(not _valid_foreign_key(constraint) for constraint in canonical_foreign_keys):
        raise RuntimeError("imageasset canonical asset foreign key is invalid")
    has_canonical_foreign_key = bool(canonical_foreign_keys)

    if not has_canonical_unique or not has_canonical_foreign_key:
        with op.batch_alter_table("imageasset") as batch:
            if not has_canonical_foreign_key:
                batch.create_foreign_key(
                    "fk_imageasset_canonical_asset_id",
                    "workspace_assets",
                    ["canonical_asset_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if not has_canonical_unique:
                batch.create_unique_constraint(
                    "uq_imageasset_canonical_asset_id", ["canonical_asset_id"]
                )


def _ensure_index(table: str, name: str, columns: tuple[str, ...]) -> None:
    indexes = sa.inspect(op.get_bind()).get_indexes(table)
    named = next((index for index in indexes if index.get("name") == name), None)
    if named is not None:
        if tuple(named.get("column_names") or ()) != columns or bool(
            named.get("unique")
        ):
            raise RuntimeError(f"{table} compatibility index is invalid: {name}")
        return
    op.create_index(name, table, list(columns), unique=False)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    required_tables = {"workspace_assets", "presentations"}
    missing_tables = required_tables - tables
    if missing_tables:
        raise RuntimeError(
            "asset/profile compatibility predecessor tables are missing: "
            + ",".join(sorted(missing_tables))
        )

    if "imageasset" not in tables:
        foreign_keys = [
            sa.ForeignKeyConstraint(
                ["canonical_asset_id"],
                ["workspace_assets.id"],
                name="fk_imageasset_canonical_asset_id",
                ondelete="SET NULL",
            )
        ]
        if "user" in tables:
            foreign_keys.append(
                sa.ForeignKeyConstraint(
                    ["owner_id"],
                    ["user.id"],
                    name="fk_imageasset_owner_id_user",
                    ondelete="CASCADE",
                )
            )
        op.create_table(
            "imageasset",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("owner_id", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "is_uploaded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("extras", sa.JSON(), nullable=True),
            sa.Column("canonical_asset_id", sa.Uuid(), nullable=True),
            sa.Column(
                "canonicalization_attempts",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("canonicalization_error_code", sa.String(), nullable=True),
            sa.Column(
                "canonicalized_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "canonical_asset_id",
                name="uq_imageasset_canonical_asset_id",
            ),
            *foreign_keys,
        )
        op.create_index("ix_imageasset_owner_id", "imageasset", ["owner_id"])
        op.create_index(
            "ix_imageasset_canonical_asset_id",
            "imageasset",
            ["canonical_asset_id"],
        )
        tables.add("imageasset")

    if "imageasset" in tables:
        image_columns = _columns("imageasset")
        with op.batch_alter_table("imageasset") as batch:
            if "canonical_asset_id" not in image_columns:
                batch.add_column(sa.Column("canonical_asset_id", sa.Uuid(), nullable=True))
            if "canonicalization_attempts" not in image_columns:
                batch.add_column(
                    sa.Column(
                        "canonicalization_attempts",
                        sa.Integer(),
                        nullable=False,
                        server_default=sa.text("0"),
                    )
                )
            if "canonicalization_error_code" not in image_columns:
                batch.add_column(
                    sa.Column("canonicalization_error_code", sa.String(), nullable=True)
                )
            if "canonicalized_at" not in image_columns:
                batch.add_column(
                    sa.Column("canonicalized_at", sa.DateTime(timezone=True), nullable=True)
                )
        _ensure_image_asset_constraints()
        _ensure_index(
            "imageasset",
            "ix_imageasset_canonical_asset_id",
            ("canonical_asset_id",),
        )

    if "presentations" in tables:
        presentation_columns = _columns("presentations")
        with op.batch_alter_table("presentations") as batch:
            if "profile_driver_id" not in presentation_columns:
                batch.add_column(sa.Column("profile_driver_id", sa.String(32), nullable=True))
            if "profile_schema_version" not in presentation_columns:
                batch.add_column(sa.Column("profile_schema_version", sa.Integer(), nullable=True))
            if "profile_backfilled_at" not in presentation_columns:
                batch.add_column(
                    sa.Column("profile_backfilled_at", sa.DateTime(timezone=True), nullable=True)
                )
        _ensure_index(
            "presentations",
            "ix_presentations_profile_driver_id",
            ("profile_driver_id",),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "presentations" in tables:
        columns = _columns("presentations")
        indexes = {
            index["name"]
            for index in sa.inspect(op.get_bind()).get_indexes("presentations")
        }
        if "ix_presentations_profile_driver_id" in indexes:
            op.drop_index(
                "ix_presentations_profile_driver_id",
                table_name="presentations",
            )
        with op.batch_alter_table("presentations") as batch:
            if "profile_backfilled_at" in columns:
                batch.drop_column("profile_backfilled_at")
            if "profile_schema_version" in columns:
                batch.drop_column("profile_schema_version")
            if "profile_driver_id" in columns:
                batch.drop_column("profile_driver_id")
    if "imageasset" in tables:
        columns = _columns("imageasset")
        indexes = {
            index["name"]
            for index in sa.inspect(op.get_bind()).get_indexes("imageasset")
        }
        if "ix_imageasset_canonical_asset_id" in indexes:
            op.drop_index(
                "ix_imageasset_canonical_asset_id",
                table_name="imageasset",
            )
        with op.batch_alter_table("imageasset") as batch:
            if "canonicalized_at" in columns:
                batch.drop_column("canonicalized_at")
            if "canonicalization_error_code" in columns:
                batch.drop_column("canonicalization_error_code")
            if "canonicalization_attempts" in columns:
                batch.drop_column("canonicalization_attempts")
            if "canonical_asset_id" in columns:
                batch.drop_column("canonical_asset_id")
