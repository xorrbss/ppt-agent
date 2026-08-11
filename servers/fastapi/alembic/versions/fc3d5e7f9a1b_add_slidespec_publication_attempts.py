"""Add SlideSpec candidate staging and append-only publication attempts.

Revision ID: fc3d5e7f9a1b
Revises: fb2c4d6e8a0b
"""

from collections.abc import Sequence
import re

from alembic import op
import sqlalchemy as sa


revision: str = "fc3d5e7f9a1b"
down_revision: str | None = "fb2c4d6e8a0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ARTIFACTS = "slidespec_artifact_bundles"
ATTEMPTS = "slidespec_publication_attempts"
ARTIFACT_STATUS_CHECK = "ck_slidespec_artifact_bundles_status"
ATTEMPT_STATE_CHECK = "ck_slidespec_publication_attempt_state"
ATTEMPT_STATE_UNIQUE = "uq_slidespec_publication_attempt_state"
ATTEMPT_SLIDE_INDEX = "ix_slidespec_publication_attempts_slide_created"
ARTIFACT_STATUS_VALUES = {
    "imported",
    "candidate",
    "shadow",
    "rejected",
    "published",
}
ATTEMPT_STATE_VALUES = {
    "staged",
    "activation_committed",
    "verified",
    "failed",
    "rolled_back",
    "rollback_conflict",
}


def _normalized_check(value: object) -> str:
    normalized = str(value or "").lower().replace('"', "")
    normalized = re.sub(r"::[a-z ]+(?:\[\])?", "", normalized)
    return re.sub(r"[\s()]", "", normalized)


def _check_matches(actual: object, *, column: str, values: set[str]) -> bool:
    normalized = _normalized_check(actual)
    actual_values = set(re.findall(r"'([^']+)'", str(actual or "").lower()))
    if actual_values != values or "or" in normalized:
        return False
    return (f"{column}in" in normalized) or (f"{column}=any" in normalized)


def _attempt_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_bundle_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("slide_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=96), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _upgrade_artifact_status_check() -> None:
    inspector = sa.inspect(op.get_bind())
    checks = {
        constraint.get("name"): constraint.get("sqltext")
        for constraint in inspector.get_check_constraints(ARTIFACTS)
    }
    current = checks.get(ARTIFACT_STATUS_CHECK)
    if current is not None and _check_matches(
        current,
        column="status",
        values=ARTIFACT_STATUS_VALUES,
    ):
        return
    if current is not None and "status" not in _normalized_check(current):
        raise RuntimeError(f"{ARTIFACT_STATUS_CHECK} has an unexpected expression")

    # SQLite batch mode reflects the whole table before recreating it.  Some
    # repairable legacy databases reached this revision with the artifact
    # table present before its referenced ``slides`` table.  Following foreign
    # keys during reflection would make the migration fail with
    # ``NoSuchTableError`` before Alembic can finish repairing the lineage.
    with op.batch_alter_table(
        ARTIFACTS,
        reflect_kwargs={"resolve_fks": False},
    ) as batch:
        if current is not None:
            batch.drop_constraint(ARTIFACT_STATUS_CHECK, type_="check")
        batch.create_check_constraint(
            ARTIFACT_STATUS_CHECK,
            "status IN ('imported', 'candidate', 'shadow', 'rejected', 'published')",
        )


def _create_attempts_table() -> None:
    op.create_table(
        ATTEMPTS,
        *_attempt_columns(),
        sa.CheckConstraint(
            "state IN ('staged', 'activation_committed', 'verified', 'failed', 'rolled_back', 'rollback_conflict')",
            name=ATTEMPT_STATE_CHECK,
        ),
        sa.ForeignKeyConstraint(
            ["artifact_bundle_id"],
            ["slidespec_artifact_bundles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["presentation_id"], ["presentations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slide_id"], ["slides.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            "state",
            name=ATTEMPT_STATE_UNIQUE,
        ),
    )
    op.create_index(
        ATTEMPT_SLIDE_INDEX,
        ATTEMPTS,
        ["slide_id", "created_at"],
        unique=False,
    )


def _upgrade_attempts_table() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns(ATTEMPTS)}
    if "id" not in columns:
        raise RuntimeError(f"{ATTEMPTS} is only partially applied; missing id")
    row_count = op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {ATTEMPTS}")).scalar_one()
    for column in _attempt_columns():
        if column.name not in columns:
            if not column.nullable and row_count:
                raise RuntimeError(
                    f"{ATTEMPTS} is only partially applied; cannot safely backfill non-null column {column.name}"
                )
            op.add_column(ATTEMPTS, column)

    inspector = sa.inspect(op.get_bind())
    primary_key = tuple(inspector.get_pk_constraint(ATTEMPTS).get("constrained_columns") or ())
    if primary_key and primary_key != ("id",):
        raise RuntimeError(f"{ATTEMPTS} has an unexpected primary key {primary_key!r}")

    checks = {
        constraint.get("name"): constraint.get("sqltext")
        for constraint in inspector.get_check_constraints(ATTEMPTS)
    }
    state_check = checks.get(ATTEMPT_STATE_CHECK)
    if state_check is not None and not _check_matches(
        state_check,
        column="state",
        values=ATTEMPT_STATE_VALUES,
    ):
        raise RuntimeError(f"{ATTEMPT_STATE_CHECK} has an unexpected expression")

    uniques = {
        constraint.get("name"): tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(ATTEMPTS)
    }
    named_unique = uniques.get(ATTEMPT_STATE_UNIQUE)
    if named_unique is not None and named_unique != ("attempt_id", "state"):
        raise RuntimeError(f"{ATTEMPT_STATE_UNIQUE} has unexpected columns")
    has_target_unique = ("attempt_id", "state") in uniques.values()

    expected_foreign_keys = {
        ("artifact_bundle_id",): ARTIFACTS,
        ("presentation_id",): "presentations",
        ("slide_id",): "slides",
    }
    foreign_keys = {
        tuple(constraint.get("constrained_columns") or ()): constraint
        for constraint in inspector.get_foreign_keys(ATTEMPTS)
    }
    missing_foreign_keys: list[tuple[tuple[str, ...], str]] = []
    for constrained_columns, referred_table in expected_foreign_keys.items():
        current = foreign_keys.get(constrained_columns)
        if current is None:
            missing_foreign_keys.append((constrained_columns, referred_table))
            continue
        ondelete = (current.get("options") or {}).get("ondelete")
        if current.get("referred_table") != referred_table or ondelete != "CASCADE":
            raise RuntimeError(f"{ATTEMPTS} has an incompatible foreign key on {constrained_columns!r}")

    if not primary_key or state_check is None or not has_target_unique or missing_foreign_keys:
        with op.batch_alter_table(ATTEMPTS) as batch:
            if not primary_key:
                batch.create_primary_key(f"pk_{ATTEMPTS}", ["id"])
            if state_check is None:
                batch.create_check_constraint(
                    ATTEMPT_STATE_CHECK,
                    "state IN ('staged', 'activation_committed', 'verified', "
                    "'failed', 'rolled_back', 'rollback_conflict')",
                )
            if not has_target_unique:
                batch.create_unique_constraint(ATTEMPT_STATE_UNIQUE, ["attempt_id", "state"])
            for constrained_columns, referred_table in missing_foreign_keys:
                column = constrained_columns[0]
                batch.create_foreign_key(
                    f"fk_{ATTEMPTS}_{column}",
                    referred_table,
                    [column],
                    ["id"],
                    ondelete="CASCADE",
                )

    indexes = {
        index.get("name"): (
            tuple(index.get("column_names") or ()),
            bool(index.get("unique")),
        )
        for index in sa.inspect(op.get_bind()).get_indexes(ATTEMPTS)
    }
    if ATTEMPT_SLIDE_INDEX in indexes and indexes[ATTEMPT_SLIDE_INDEX] != (
        ("slide_id", "created_at"),
        False,
    ):
        raise RuntimeError(f"{ATTEMPT_SLIDE_INDEX} has unexpected columns")
    if ATTEMPT_SLIDE_INDEX not in indexes:
        op.create_index(
            ATTEMPT_SLIDE_INDEX,
            ATTEMPTS,
            ["slide_id", "created_at"],
            unique=False,
        )


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if ARTIFACTS not in tables:
        raise RuntimeError(f"{ATTEMPTS} requires existing {ARTIFACTS}")
    _upgrade_artifact_status_check()
    if ATTEMPTS not in tables:
        _create_attempts_table()
    else:
        _upgrade_attempts_table()


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
        batch.drop_constraint("ck_slidespec_artifact_bundles_status", type_="check")
        batch.create_check_constraint(
            "ck_slidespec_artifact_bundles_status",
            "status IN ('imported', 'shadow', 'rejected', 'published')",
        )
