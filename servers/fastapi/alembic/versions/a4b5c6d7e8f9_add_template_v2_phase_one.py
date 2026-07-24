"""add Template V2 Phase 1 persistence

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-24 00:00:00.000000
"""

from dataclasses import dataclass
import json
import re
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SLIDE_UI_CHECK_CONSTRAINT = "ck_slides_native_ui_or_authored_html"
SLIDE_UI_CHECK_SQL = "ui IS NULL OR html_content IS NULL OR html_content = ''"
TEMPLATE_V2_PRESENTATION_FK = (
    "fk_template_v2_presentation_id_presentations"
)
TEMPLATE_V2_PRESENTATION_INDEX = "ix_template_v2_presentation_id"
TEMPLATE_V2_EXPECTED_COLUMNS = frozenset(
    {
        "id",
        "presentation_id",
        "name",
        "description",
        "raw_layouts",
        "components",
        "merged_components",
        "layouts",
        "assets",
        "is_default",
        "created_at",
        "updated_at",
    }
)


@dataclass(frozen=True)
class _FrozenPhaseOneSchemaReport:
    """The schema contract as it existed when this revision was authored."""

    repairable: tuple[str, ...]
    incompatible: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.repairable and not self.incompatible

    def require_compatible(self) -> None:
        if self.incompatible:
            raise RuntimeError(
                "Template V2 Phase 1 schema is incompatible: "
                + "; ".join(self.incompatible)
            )


def _normalized_sql(value: object) -> str:
    normalized = str(value or "").lower()
    normalized = re.sub(
        r"::(?:character varying|text|varchar|boolean|timestamp(?: with time zone)?)",
        "",
        normalized,
    )
    return re.sub(r'[\s()"`\[\]]', "", normalized)


def _is_unbounded_string(column: dict) -> bool:
    column_type = column.get("type")
    return (
        isinstance(column_type, sa.String)
        and getattr(column_type, "length", None) is None
    )


def _is_uuid(column: dict) -> bool:
    column_type = column.get("type")
    if isinstance(column_type, sa.Uuid):
        return True
    return isinstance(column_type, sa.CHAR) and column_type.length in {32, 36}


def _is_json(column: dict) -> bool:
    return isinstance(column.get("type"), sa.JSON)


def _is_boolean(column: dict) -> bool:
    return isinstance(column.get("type"), sa.Boolean)


def _is_datetime(column: dict, *, dialect_name: str) -> bool:
    column_type = column.get("type")
    if not isinstance(column_type, sa.DateTime):
        return False
    return (
        dialect_name != "postgresql"
        or getattr(column_type, "timezone", False) is True
    )


def _default_is(column: dict, expected: str) -> bool:
    default = _normalized_sql(column.get("default"))
    if expected == "none":
        return not default
    if expected == "legacy-version":
        return default in {"'v1-standard'", "v1-standard"}
    if expected == "false":
        return default in {"0", "'0'", "false", "'false'"}
    if expected == "now":
        return default in {"current_timestamp", "now"}
    raise AssertionError(expected)


def _has_row(connection, sql: str) -> bool:
    return connection.execute(sa.text(sql)).first() is not None


def _decode_json_value(value: object) -> object:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _is_non_empty_bounded_string(value: object, maximum: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum


def _is_frozen_slide_ui(value: object) -> bool:
    """Validate the revision-owned persisted layout envelope.

    Element-level authoring validation belongs to the application boundary.
    This migration freezes only the storage contract needed to distinguish a
    native layout from unrelated JSON without importing evolving app models.
    """

    if not isinstance(value, dict) or set(value) != {
        "id",
        "description",
        "components",
    }:
        return False
    if not _is_non_empty_bounded_string(value["id"], 80):
        return False
    description = value["description"]
    if not isinstance(description, str) or not 10 <= len(description) <= 300:
        return False
    components = value["components"]
    if not isinstance(components, list):
        return False

    component_ids: list[str] = []
    for component in components:
        if not isinstance(component, dict) or set(component) != {
            "id",
            "description",
            "position",
            "elements",
        }:
            return False
        component_id = component["id"]
        if not _is_non_empty_bounded_string(component_id, 80):
            return False
        component_ids.append(component_id)
        component_description = component["description"]
        if (
            not isinstance(component_description, str)
            or not 10 <= len(component_description) <= 300
        ):
            return False
        position = component["position"]
        if not isinstance(position, dict) or set(position) != {"x", "y"}:
            return False
        if any(
            isinstance(position[axis], bool)
            or not isinstance(position[axis], (int, float))
            for axis in ("x", "y")
        ):
            return False
        elements = component["elements"]
        if not isinstance(elements, list) or not elements:
            return False
        if any(
            not isinstance(element, dict)
            or element.get("type")
            not in {
                "text",
                "container",
                "image",
                "text-list",
                "table",
                "vector",
                "chart",
                "infographic",
                "flex",
                "grid",
                "group",
            }
            for element in elements
        ):
            return False
    return len(component_ids) == len(set(component_ids))


def _has_invalid_slide_ui_row(connection) -> bool:
    result = connection.execute(
        sa.text("SELECT ui FROM slides WHERE ui IS NOT NULL")
    )
    try:
        for row in result:
            try:
                value = _decode_json_value(row[0])
            except (TypeError, ValueError):
                return True
            if not _is_frozen_slide_ui(value):
                return True
    finally:
        result.close()
    return False


def _has_invalid_uuid_value(
    connection,
    table_name: str,
    column_name: str,
) -> bool:
    result = connection.execute(
        sa.text(
            f"SELECT {column_name} FROM {table_name} "
            f"WHERE {column_name} IS NOT NULL"
        )
    )
    try:
        for row in result:
            value = row[0]
            if isinstance(value, uuid.UUID):
                continue
            try:
                uuid.UUID(str(value))
            except (AttributeError, TypeError, ValueError):
                return True
    finally:
        result.close()
    return False


def _validate_frozen_phase_one_schema(
    connection,
) -> _FrozenPhaseOneSchemaReport:
    """Classify missing additions separately from incompatible same-name data."""

    inspector = sa.inspect(connection)
    dialect_name = inspector.bind.dialect.name
    tables = set(inspector.get_table_names())
    repairable: list[str] = []
    incompatible: list[str] = []

    for table_name, required_columns in {
        "presentations": {"id"},
        "slides": {"id", "html_content"},
    }.items():
        if table_name not in tables:
            incompatible.append(f"missing base table {table_name}")
            continue
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
        missing = required_columns - set(columns)
        if missing:
            incompatible.append(
                f"{table_name} missing base columns {', '.join(sorted(missing))}"
            )
        primary_key = set(
            inspector.get_pk_constraint(table_name).get("constrained_columns")
            or []
        )
        if primary_key != {"id"}:
            incompatible.append(f"{table_name} primary key must be exactly id")

    if "presentations" in tables:
        presentation_columns = {
            column["name"]: column
            for column in inspector.get_columns("presentations")
        }
        version = presentation_columns.get("version")
        if version is None:
            repairable.append("presentations.version is missing")
        else:
            if not _is_unbounded_string(version):
                incompatible.append(
                    "presentations.version must be an unbounded string"
                )
            if version.get("nullable", True):
                incompatible.append("presentations.version must be NOT NULL")
            if not _default_is(version, "legacy-version"):
                incompatible.append(
                    "presentations.version must default to v1-standard"
                )
            if _has_row(
                connection,
                "SELECT 1 FROM presentations WHERE version IS NULL LIMIT 1",
            ):
                incompatible.append("presentations contains NULL version rows")

    if "slides" in tables:
        slide_columns = {
            column["name"]: column
            for column in inspector.get_columns("slides")
        }
        ui = slide_columns.get("ui")
        if ui is None:
            repairable.append("slides.ui is missing")
        else:
            if not _is_json(ui):
                incompatible.append("slides.ui must be JSON")
            if not ui.get("nullable", True):
                incompatible.append("slides.ui must be nullable")
            if not _default_is(ui, "none"):
                incompatible.append("slides.ui must not have a server default")
            if _has_row(
                connection,
                """
                SELECT 1 FROM slides
                WHERE ui IS NOT NULL
                  AND html_content IS NOT NULL
                  AND html_content <> ''
                LIMIT 1
                """,
            ):
                incompatible.append(
                    "slides contains mixed native UI and authored HTML rows"
                )
            if _has_invalid_slide_ui_row(connection):
                incompatible.append(
                    "slides contains invalid native UI payload rows"
                )

        checks = {
            constraint.get("name"): constraint.get("sqltext")
            for constraint in inspector.get_check_constraints("slides")
        }
        check_sql = checks.get(SLIDE_UI_CHECK_CONSTRAINT)
        if check_sql is None:
            repairable.append(f"{SLIDE_UI_CHECK_CONSTRAINT} is missing")
        elif _normalized_sql(check_sql) != _normalized_sql(
            SLIDE_UI_CHECK_SQL
        ):
            incompatible.append(
                f"{SLIDE_UI_CHECK_CONSTRAINT} has incorrect SQL semantics"
            )

    if "template_v2" not in tables:
        repairable.append("template_v2 is missing")
        return _FrozenPhaseOneSchemaReport(
            tuple(repairable),
            tuple(incompatible),
        )

    columns = {
        column["name"]: column
        for column in inspector.get_columns("template_v2")
    }
    actual_columns = set(columns)
    missing_columns = TEMPLATE_V2_EXPECTED_COLUMNS - actual_columns
    if missing_columns:
        incompatible.append(
            "template_v2 missing columns "
            + ", ".join(sorted(missing_columns))
        )
    unexpected_columns = actual_columns - TEMPLATE_V2_EXPECTED_COLUMNS
    if unexpected_columns:
        incompatible.append(
            "template_v2 has unexpected columns "
            + ", ".join(sorted(unexpected_columns))
        )

    expected_types = {
        "id": _is_unbounded_string,
        "presentation_id": _is_uuid,
        "name": _is_unbounded_string,
        "description": _is_unbounded_string,
        "raw_layouts": _is_json,
        "components": _is_json,
        "merged_components": _is_json,
        "layouts": _is_json,
        "assets": _is_json,
        "is_default": _is_boolean,
    }
    nullable = {
        "description",
        "raw_layouts",
        "components",
        "merged_components",
        "layouts",
        "assets",
    }
    for name, type_check in expected_types.items():
        column = columns.get(name)
        if column is None:
            continue
        if not type_check(column):
            incompatible.append(f"template_v2.{name} has an incompatible type")
        if bool(column.get("nullable", True)) != (name in nullable):
            incompatible.append(
                f"template_v2.{name} has incorrect nullability"
            )
    for name in ("created_at", "updated_at"):
        column = columns.get(name)
        if column is None:
            continue
        if not _is_datetime(column, dialect_name=dialect_name):
            incompatible.append(f"template_v2.{name} has an incompatible type")
        if column.get("nullable", True):
            incompatible.append(
                f"template_v2.{name} has incorrect nullability"
            )

    expected_defaults = {
        "id": "none",
        "presentation_id": "none",
        "name": "none",
        "description": "none",
        "raw_layouts": "none",
        "components": "none",
        "merged_components": "none",
        "layouts": "none",
        "assets": "none",
        "is_default": "false",
        "created_at": "now",
        "updated_at": "now",
    }
    for name, expected_default in expected_defaults.items():
        column = columns.get(name)
        if column is not None and not _default_is(column, expected_default):
            incompatible.append(
                f"template_v2.{name} has an incorrect server default"
            )

    primary_key = set(
        inspector.get_pk_constraint("template_v2").get("constrained_columns")
        or []
    )
    if primary_key != {"id"}:
        incompatible.append("template_v2 primary key must be exactly id")

    foreign_keys = inspector.get_foreign_keys("template_v2")
    provenance_fk = next(
        (
            foreign_key
            for foreign_key in foreign_keys
            if foreign_key.get("name") == TEMPLATE_V2_PRESENTATION_FK
        ),
        None,
    )
    if provenance_fk is None:
        incompatible.append(
            f"template_v2 requires named FK {TEMPLATE_V2_PRESENTATION_FK}"
        )
    elif (
        provenance_fk.get("constrained_columns") != ["presentation_id"]
        or provenance_fk.get("referred_table") != "presentations"
        or provenance_fk.get("referred_columns") != ["id"]
        or str((provenance_fk.get("options") or {}).get("ondelete", "")).upper()
        != "CASCADE"
    ):
        incompatible.append(
            f"{TEMPLATE_V2_PRESENTATION_FK} has incorrect semantics"
        )

    indexes = {
        index.get("name"): index
        for index in inspector.get_indexes("template_v2")
    }
    provenance_index = indexes.get(TEMPLATE_V2_PRESENTATION_INDEX)
    if (
        provenance_index is None
        or provenance_index.get("column_names") != ["presentation_id"]
        or provenance_index.get("unique", False)
    ):
        incompatible.append(
            "template_v2 requires non-unique index "
            + TEMPLATE_V2_PRESENTATION_INDEX
        )

    required = [
        "id",
        "presentation_id",
        "name",
        "is_default",
        "created_at",
        "updated_at",
    ]
    if all(name in columns for name in required):
        null_predicate = " OR ".join(
            f"{name} IS NULL" for name in required
        )
        if _has_row(
            connection,
            f"SELECT 1 FROM template_v2 WHERE {null_predicate} LIMIT 1",
        ):
            incompatible.append("template_v2 contains NULL required fields")
        if _has_invalid_uuid_value(
            connection,
            "template_v2",
            "presentation_id",
        ):
            incompatible.append(
                "template_v2 contains invalid presentation UUID values"
            )

    if (
        "presentation_id" in columns
        and "presentations" in tables
        and {"id", "version", "mode"}.issubset(
            {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
        )
        and _has_row(
            connection,
            """
            SELECT 1
            FROM template_v2 AS template
            LEFT JOIN presentations AS source
              ON source.id = template.presentation_id
            WHERE source.id IS NULL
               OR source.version IS NULL
               OR source.version <> 'v2-standard'
               OR source.mode IS NULL
               OR source.mode <> 'template'
            LIMIT 1
            """,
        )
    ):
        incompatible.append(
            "template_v2 contains invalid presentation provenance"
        )

    return _FrozenPhaseOneSchemaReport(
        tuple(repairable),
        tuple(incompatible),
    )


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names() and column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


def _has_check_constraint(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names() and constraint_name in {
        constraint.get("name")
        for constraint in inspector.get_check_constraints(table_name)
    }


def _preflight_schema() -> None:
    _validate_frozen_phase_one_schema(op.get_bind()).require_compatible()


def upgrade() -> None:
    # Refuse an ambiguous partial table before applying any Phase 1 DDL.
    _preflight_schema()

    if _has_table("presentations") and not _has_column("presentations", "version"):
        op.add_column(
            "presentations",
            sa.Column(
                "version",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=False,
                server_default="v1-standard",
            ),
        )

    if _has_table("slides") and not _has_column("slides", "ui"):
        op.add_column(
            "slides",
            sa.Column("ui", sa.JSON(none_as_null=True), nullable=True),
        )
    if _has_table("slides") and not _has_check_constraint(
        "slides", SLIDE_UI_CHECK_CONSTRAINT
    ):
        with op.batch_alter_table("slides") as batch_op:
            batch_op.create_check_constraint(
                SLIDE_UI_CHECK_CONSTRAINT,
                SLIDE_UI_CHECK_SQL,
            )

    if not _has_table("template_v2"):
        op.create_table(
            "template_v2",
            sa.Column(
                "id", sqlmodel.sql.sqltypes.AutoString(), nullable=False
            ),
            sa.Column("presentation_id", sa.Uuid(), nullable=False),
            sa.Column(
                "name", sqlmodel.sql.sqltypes.AutoString(), nullable=False
            ),
            sa.Column(
                "description",
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            ),
            sa.Column("raw_layouts", sa.JSON(), nullable=True),
            sa.Column("components", sa.JSON(), nullable=True),
            sa.Column("merged_components", sa.JSON(), nullable=True),
            sa.Column("layouts", sa.JSON(), nullable=True),
            sa.Column("assets", sa.JSON(), nullable=True),
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
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
            sa.ForeignKeyConstraint(
                ["presentation_id"],
                ["presentations.id"],
                name=TEMPLATE_V2_PRESENTATION_FK,
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            TEMPLATE_V2_PRESENTATION_INDEX,
            "template_v2",
            ["presentation_id"],
            unique=False,
        )

    postflight = _validate_frozen_phase_one_schema(op.get_bind())
    if not postflight.complete:
        raise RuntimeError(
            "Template V2 Phase 1 migration did not produce a complete schema: "
            + "; ".join(postflight.repairable + postflight.incompatible)
        )


def _require_safe_downgrade() -> None:
    """Refuse to erase Phase 1 data or an ambiguous same-name schema."""

    connection = op.get_bind()
    report = _validate_frozen_phase_one_schema(connection)
    if not report.complete:
        raise RuntimeError(
            "Template V2 Phase 1 downgrade requires the exact frozen schema: "
            + "; ".join(report.repairable + report.incompatible)
        )

    data_guards = (
        (
            "SELECT 1 FROM template_v2 LIMIT 1",
            "template_v2 contains rows",
        ),
        (
            "SELECT 1 FROM slides WHERE ui IS NOT NULL LIMIT 1",
            "slides.ui contains native layouts",
        ),
        (
            """
            SELECT 1 FROM presentations
            WHERE version IS NULL OR version <> 'v1-standard'
            LIMIT 1
            """,
            "presentations.version contains non-legacy values",
        ),
    )
    unsafe = [
        reason
        for sql, reason in data_guards
        if _has_row(connection, sql)
    ]
    if unsafe:
        raise RuntimeError(
            "Template V2 Phase 1 downgrade would discard data: "
            + "; ".join(unsafe)
        )


def downgrade() -> None:
    # Every safety check happens before the first DDL statement. This revision
    # may adopt compatible pre-existing artifacts on upgrade; without durable
    # ownership metadata, populated artifacts are never safe to remove.
    _require_safe_downgrade()

    if _has_table("template_v2"):
        op.drop_table("template_v2")
    if _has_table("slides") and _has_check_constraint(
        "slides", SLIDE_UI_CHECK_CONSTRAINT
    ):
        with op.batch_alter_table("slides") as batch_op:
            batch_op.drop_constraint(
                SLIDE_UI_CHECK_CONSTRAINT,
                type_="check",
            )
    if _has_column("slides", "ui"):
        op.drop_column("slides", "ui")
    if _has_column("presentations", "version"):
        op.drop_column("presentations", "version")
