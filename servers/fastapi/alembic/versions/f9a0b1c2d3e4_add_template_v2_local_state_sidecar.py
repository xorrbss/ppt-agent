"""add Template V2 local state sidecar

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-24
"""

from collections.abc import Sequence
import re

from alembic import op
import sqlalchemy as sa


revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "template_v2_local_state"
PRESENTATION_INDEX = "ix_template_v2_local_state_presentation_id"
REVISION_CHECK = "ck_template_v2_local_state_revision_positive"
TEMPLATE_FK = "fk_template_v2_local_state_template_id_template_v2"
PRESENTATION_FK = (
    "fk_template_v2_local_state_presentation_id_presentations"
)
EXPECTED_COLUMNS = frozenset(
    {
        "template_id",
        "presentation_id",
        "revision",
        "created_at",
        "updated_at",
    }
)


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _normalized_sql(value: object) -> str:
    normalized = str(value or "").lower()
    normalized = re.sub(
        r"::(?:integer|timestamp(?: with time zone)?)",
        "",
        normalized,
    )
    return re.sub(r'[\s()"`\[\]]', "", normalized)


def _require_compatible_existing_sidecar() -> None:
    inspector = sa.inspect(op.get_bind())
    dialect_name = inspector.bind.dialect.name
    columns = {
        column["name"]: column
        for column in inspector.get_columns(TABLE_NAME)
    }
    if set(columns) != EXPECTED_COLUMNS:
        raise RuntimeError(
            "Template V2 local-state sidecar has incompatible columns"
        )

    template_type = columns["template_id"]["type"]
    presentation_type = columns["presentation_id"]["type"]
    revision_type = columns["revision"]["type"]
    if not (
        isinstance(template_type, sa.String)
        and getattr(template_type, "length", None) is None
    ):
        raise RuntimeError(
            "Template V2 local-state template_id has incompatible type"
        )
    if not (
        isinstance(presentation_type, sa.Uuid)
        or (
            isinstance(presentation_type, sa.CHAR)
            and presentation_type.length in {32, 36}
        )
    ):
        raise RuntimeError(
            "Template V2 local-state presentation_id has incompatible type"
        )
    if not isinstance(revision_type, sa.Integer):
        raise RuntimeError(
            "Template V2 local-state revision has incompatible type"
        )
    for name in ("created_at", "updated_at"):
        column_type = columns[name]["type"]
        if not isinstance(column_type, sa.DateTime) or (
            dialect_name == "postgresql"
            and getattr(column_type, "timezone", False) is not True
        ):
            raise RuntimeError(
                f"Template V2 local-state {name} has incompatible type"
            )
    if any(column.get("nullable", True) for column in columns.values()):
        raise RuntimeError(
            "Template V2 local-state sidecar columns must be NOT NULL"
        )
    if _normalized_sql(columns["revision"].get("default")) not in {"1", "'1'"}:
        raise RuntimeError(
            "Template V2 local-state revision must default to 1"
        )
    for name in ("created_at", "updated_at"):
        if _normalized_sql(columns[name].get("default")) not in {
            "current_timestamp",
            "now",
        }:
            raise RuntimeError(
                f"Template V2 local-state {name} must default to current time"
            )
    if set(
        inspector.get_pk_constraint(TABLE_NAME).get("constrained_columns") or []
    ) != {"template_id"}:
        raise RuntimeError(
            "Template V2 local-state sidecar has an incompatible primary key"
        )

    foreign_keys = {
        foreign_key.get("name"): foreign_key
        for foreign_key in inspector.get_foreign_keys(TABLE_NAME)
    }
    expected_foreign_keys = {
        TEMPLATE_FK: (["template_id"], "template_v2", ["id"], "CASCADE"),
        PRESENTATION_FK: (
            ["presentation_id"],
            "presentations",
            ["id"],
            "CASCADE",
        ),
    }
    for name, (columns_, table, referred_columns, ondelete) in (
        expected_foreign_keys.items()
    ):
        foreign_key = foreign_keys.get(name)
        if (
            foreign_key is None
            or foreign_key.get("constrained_columns") != columns_
            or foreign_key.get("referred_table") != table
            or foreign_key.get("referred_columns") != referred_columns
            or str(
                (foreign_key.get("options") or {}).get("ondelete", "")
            ).upper()
            != ondelete
        ):
            raise RuntimeError(
                f"Template V2 local-state sidecar requires named FK {name}"
            )

    indexes = {
        index.get("name"): index
        for index in inspector.get_indexes(TABLE_NAME)
    }
    presentation_index = indexes.get(PRESENTATION_INDEX)
    if (
        presentation_index is None
        or presentation_index.get("column_names") != ["presentation_id"]
        or presentation_index.get("unique", False)
    ):
        raise RuntimeError(
            "Template V2 local-state sidecar requires presentation index"
        )
    checks = {
        check.get("name"): check.get("sqltext")
        for check in inspector.get_check_constraints(TABLE_NAME)
    }
    if _normalized_sql(checks.get(REVISION_CHECK)) != _normalized_sql(
        "revision >= 1"
    ):
        raise RuntimeError(
            "Template V2 local-state sidecar requires positive revision check"
        )


def _create_sidecar() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name=REVISION_CHECK),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["template_v2.id"],
            name=TEMPLATE_FK,
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            name=PRESENTATION_FK,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("template_id"),
    )
    # This presentation FK owns the sidecar row only. The transitional
    # template_v2.presentation_id FK remains the presentation-owned delete
    # path for the canonical row until a future migration installs and tests
    # a replacement path atomically with dropping that legacy column.
    op.create_index(
        PRESENTATION_INDEX,
        TABLE_NAME,
        ["presentation_id"],
        unique=False,
    )


def _backfill_local_state() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO template_v2_local_state
                (
                    template_id,
                    presentation_id,
                    revision,
                    created_at,
                    updated_at
                )
            SELECT
                template.id,
                template.presentation_id,
                template.revision,
                template.created_at,
                template.updated_at
            FROM template_v2 AS template
            WHERE NOT EXISTS (
                SELECT 1
                FROM template_v2_local_state AS local_state
                WHERE local_state.template_id = template.id
            )
            """
        )
    )

    incompatible = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM template_v2 AS template
            LEFT JOIN template_v2_local_state AS local_state
              ON local_state.template_id = template.id
            WHERE local_state.template_id IS NULL
               OR local_state.presentation_id <> template.presentation_id
               OR local_state.revision <> template.revision
            LIMIT 1
            """
        )
    ).first()
    if incompatible is not None:
        raise RuntimeError(
            "Template V2 local-state backfill did not preserve provenance "
            "and revision"
        )


def upgrade() -> None:
    if not _has_table("template_v2"):
        raise RuntimeError("Template V2 local-state sidecar requires template_v2")
    if not _has_table(TABLE_NAME):
        _create_sidecar()
    else:
        _require_compatible_existing_sidecar()
    _backfill_local_state()


def downgrade() -> None:
    if not _has_table(TABLE_NAME):
        return

    connection = op.get_bind()
    sidecar_only_state = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM template_v2_local_state AS local_state
            LEFT JOIN template_v2 AS template
              ON template.id = local_state.template_id
            WHERE template.id IS NULL
               OR template.presentation_id <> local_state.presentation_id
               OR template.revision <> local_state.revision
            LIMIT 1
            """
        )
    ).first()
    if sidecar_only_state is not None:
        raise RuntimeError(
            "Template V2 local-state downgrade would discard newer local state"
        )

    op.drop_index(PRESENTATION_INDEX, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
