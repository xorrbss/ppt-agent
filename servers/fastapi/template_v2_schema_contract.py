"""Semantic completeness checks shared by legacy inference and Phase 1 DDL."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
from typing import Any, Mapping
import uuid

import sqlalchemy as sa


SLIDE_UI_CHECK_CONSTRAINT = "ck_slides_native_ui_or_authored_html"
SLIDE_UI_CHECK_SQL = "ui IS NULL OR html_content IS NULL OR html_content = ''"
TEMPLATE_V2_PRESENTATION_FK = (
    "fk_template_v2_presentation_id_presentations"
)
TEMPLATE_V2_PRESENTATION_INDEX = "ix_template_v2_presentation_id"
TEMPLATE_V2_CANONICAL_COLUMNS = frozenset(
    {
        "id",
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
TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS = frozenset(
    {"presentation_id", "revision"}
)
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
TEMPLATE_V2_LOCAL_STATE_TABLE = "template_v2_local_state"
TEMPLATE_V2_LOCAL_STATE_COLUMNS = frozenset(
    {
        "template_id",
        "presentation_id",
        "revision",
        "created_at",
        "updated_at",
    }
)
TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE = "template_v2_pptx_imports"
TEMPLATE_V2_IMPORT_LIFECYCLE_COLUMNS = frozenset(
    {
        "id",
        "task_id",
        "requested_template_id",
        "draft_template_id",
        "state",
        "attempt_number",
        "attempt_token",
        "lease_expires_at",
        "heartbeat_at",
        "last_started_at",
        "source_retention_expires_at",
        "source_cleanup_token",
        "source_cleanup_lease_expires_at",
        "source_cleanup_attempted_at",
        "source_deleted_at",
        "source_filename",
        "source_media_type",
        "source_size_bytes",
        "source_sha256",
        "source_storage_key",
        "pipeline_version",
        "manifest",
        "created_at",
        "updated_at",
    }
)
TEMPLATE_V2_IMPORT_REVIEW_COLUMNS = frozenset(
    {
        "owner_scope",
        "request_key_hash",
        "request_fingerprint",
        "revision",
        "analysis_result",
        "repeat_suggestions",
        "confirmed_at",
        "cancelled_at",
    }
)
TEMPLATE_V2_LOCAL_STATE_TEMPLATE_FK = (
    "fk_template_v2_local_state_template_id_template_v2"
)
TEMPLATE_V2_LOCAL_STATE_PRESENTATION_FK = (
    "fk_template_v2_local_state_presentation_id_presentations"
)
TEMPLATE_V2_LOCAL_STATE_PRESENTATION_INDEX = (
    "ix_template_v2_local_state_presentation_id"
)
TEMPLATE_V2_LOCAL_STATE_REVISION_CHECK = (
    "ck_template_v2_local_state_revision_positive"
)
TEMPLATE_V2_PRESENTATION_OWNERSHIP_POLICY = "presentation-owned"
TEMPLATE_V2_PRESENTATION_DELETE_POLICY = "explicit-child-first"
TEMPLATE_V2_PRESENTATION_FK_DELETE_ACTIONS = frozenset(
    {"CASCADE", "RESTRICT"}
)
TEMPLATE_V2_LEGACY_DROP_REQUIREMENT = (
    "The presentation deletion service must validate sidecar ownership and "
    "delete template_v2 children before their presentation. A migration that "
    "removes template_v2.presentation_id must preserve and verify that "
    "explicit child-to-parent deletion path in the same transaction."
)


@dataclass(frozen=True)
class PhaseOneSchemaReport:
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
        (
            r"::(?:character varying|text|varchar|boolean|integer|"
            r"timestamp(?: with time zone)?)"
        ),
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


def _is_integer(column: dict) -> bool:
    return isinstance(column.get("type"), sa.Integer)


def _is_datetime(column: dict, *, dialect_name: str) -> bool:
    column_type = column.get("type")
    if not isinstance(column_type, sa.DateTime):
        return False
    # SQLite erases timezone intent during reflection. PostgreSQL preserves it,
    # so require the migration's TIMESTAMP WITH TIME ZONE contract there.
    return (
        dialect_name != "postgresql"
        or getattr(column_type, "timezone", False) is True
    )


def _default_is(column: dict, expected: str) -> bool:
    default = _normalized_sql(column.get("default"))
    if expected == "none":
        return not default
    if expected == "legacy-version":
        return default == "'v1-standard'" or default == "v1-standard"
    if expected == "false":
        return default in {"0", "'0'", "false", "'false'"}
    if expected == "now":
        return default in {"current_timestamp", "now"}
    if expected == "one":
        return default in {"1", "'1'"}
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


def _has_invalid_slide_ui_row(connection) -> bool:
    # Import lazily so Alembic's script discovery remains application-neutral.
    from templates.v2.persistence import canonicalize_slide_ui

    # Do not set stream_results on Alembic's shared Connection. SQLAlchemy 2
    # applies execution_options in place, which would make Alembic's following
    # UPDATE of alembic_version use a PostgreSQL server-side cursor.
    result = connection.execute(sa.text("SELECT ui FROM slides WHERE ui IS NOT NULL"))
    try:
        for row in result:
            try:
                canonicalize_slide_ui(_decode_json_value(row[0]))
            except (TypeError, ValueError, UnicodeError):
                return True
    finally:
        result.close()
    return False


def _has_invalid_uuid_value(connection, table_name: str, column_name: str) -> bool:
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


def validate_template_v2_phase_one_schema(
    connection,
    *,
    allowed_extra_columns: frozenset[str] = frozenset(),
    allowed_presentation_fk_ondelete: frozenset[str] = frozenset(
        {"CASCADE"}
    ),
) -> PhaseOneSchemaReport:
    """Classify safe missing Phase 1 additions separately from corruption.

    A database at f3 has all Phase 1 artifacts missing and is repairable. Once
    an artifact exists, its type/nullability/default/constraint semantics and
    persisted rows must be valid; an ambiguous same-name object is never
    stamped or modified.
    """

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
        elif _normalized_sql(check_sql) != _normalized_sql(SLIDE_UI_CHECK_SQL):
            incompatible.append(
                f"{SLIDE_UI_CHECK_CONSTRAINT} has incorrect SQL semantics"
            )

    if "template_v2" not in tables:
        repairable.append("template_v2 is missing")
        return PhaseOneSchemaReport(
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
    unexpected_columns = (
        actual_columns - TEMPLATE_V2_EXPECTED_COLUMNS - allowed_extra_columns
    )
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
    if "owner_id" in actual_columns:
        nullable.add("presentation_id")
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
            fk
            for fk in foreign_keys
            if fk.get("name") == TEMPLATE_V2_PRESENTATION_FK
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
        not in allowed_presentation_fk_ondelete
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
            f"template_v2 requires non-unique index {TEMPLATE_V2_PRESENTATION_INDEX}"
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
        null_predicate = " OR ".join(f"{name} IS NULL" for name in required)
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
    ):
        if _has_row(
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
        ):
            incompatible.append(
                "template_v2 contains invalid presentation provenance"
            )

    return PhaseOneSchemaReport(
        tuple(repairable),
        tuple(incompatible),
    )


def _foreign_key_matches(
    foreign_keys: dict[str | None, dict],
    *,
    name: str,
    columns: list[str],
    referred_table: str,
    referred_columns: list[str],
    ondelete: str,
) -> bool:
    foreign_key = foreign_keys.get(name)
    return bool(
        foreign_key is not None
        and foreign_key.get("constrained_columns") == columns
        and foreign_key.get("referred_table") == referred_table
        and foreign_key.get("referred_columns") == referred_columns
        and str((foreign_key.get("options") or {}).get("ondelete", "")).upper()
        == ondelete
    )


def validate_template_v2_local_sidecars(connection) -> PhaseOneSchemaReport:
    """Validate canonical/local separation after the sidecar migration.

    The original local columns remain tolerated during the additive cutover,
    but no other local field may leak into the pinned upstream table. Durable
    provenance/revision and PPTX import lifecycle state must live in their
    explicitly named sidecar tables.
    """

    inspector = sa.inspect(connection)
    dialect_name = inspector.bind.dialect.name
    tables = set(inspector.get_table_names())
    repairable: list[str] = []
    incompatible: list[str] = []

    if "template_v2" not in tables:
        incompatible.append("missing canonical table template_v2")
        return PhaseOneSchemaReport(tuple(repairable), tuple(incompatible))

    canonical_columns = {
        column["name"]: column
        for column in inspector.get_columns("template_v2")
    }
    missing_canonical = TEMPLATE_V2_CANONICAL_COLUMNS - set(canonical_columns)
    if missing_canonical:
        incompatible.append(
            "template_v2 missing canonical columns "
            + ", ".join(sorted(missing_canonical))
        )
    unexpected_canonical = (
        set(canonical_columns)
        - TEMPLATE_V2_CANONICAL_COLUMNS
        - TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS
        - {"owner_id"}
    )
    if unexpected_canonical:
        incompatible.append(
            "template_v2 has non-canonical columns outside the transition "
            + ", ".join(sorted(unexpected_canonical))
        )

    if TEMPLATE_V2_LOCAL_STATE_TABLE not in tables:
        repairable.append(f"{TEMPLATE_V2_LOCAL_STATE_TABLE} is missing")
    else:
        local_columns = {
            column["name"]: column
            for column in inspector.get_columns(TEMPLATE_V2_LOCAL_STATE_TABLE)
        }
        if set(local_columns) != TEMPLATE_V2_LOCAL_STATE_COLUMNS:
            incompatible.append(
                f"{TEMPLATE_V2_LOCAL_STATE_TABLE} has incompatible columns"
            )

        local_type_checks = {
            "template_id": _is_unbounded_string,
            "presentation_id": _is_uuid,
            "revision": _is_integer,
        }
        for name, type_check in local_type_checks.items():
            column = local_columns.get(name)
            if column is None:
                continue
            if not type_check(column):
                incompatible.append(
                    f"{TEMPLATE_V2_LOCAL_STATE_TABLE}.{name} "
                    "has an incompatible type"
                )
            if column.get("nullable", True):
                incompatible.append(
                    f"{TEMPLATE_V2_LOCAL_STATE_TABLE}.{name} must be NOT NULL"
                )
        for name in ("created_at", "updated_at"):
            column = local_columns.get(name)
            if column is None:
                continue
            if not _is_datetime(column, dialect_name=dialect_name):
                incompatible.append(
                    f"{TEMPLATE_V2_LOCAL_STATE_TABLE}.{name} "
                    "has an incompatible type"
                )
            if column.get("nullable", True):
                incompatible.append(
                    f"{TEMPLATE_V2_LOCAL_STATE_TABLE}.{name} must be NOT NULL"
                )

        expected_defaults = {
            "template_id": "none",
            "presentation_id": "none",
            "revision": "one",
            "created_at": "now",
            "updated_at": "now",
        }
        for name, expected in expected_defaults.items():
            column = local_columns.get(name)
            if column is not None and not _default_is(column, expected):
                incompatible.append(
                    f"{TEMPLATE_V2_LOCAL_STATE_TABLE}.{name} "
                    "has an incorrect server default"
                )

        primary_key = set(
            inspector.get_pk_constraint(TEMPLATE_V2_LOCAL_STATE_TABLE).get(
                "constrained_columns"
            )
            or []
        )
        if primary_key != {"template_id"}:
            incompatible.append(
                f"{TEMPLATE_V2_LOCAL_STATE_TABLE} primary key must be template_id"
            )

        foreign_keys = {
            foreign_key.get("name"): foreign_key
            for foreign_key in inspector.get_foreign_keys(
                TEMPLATE_V2_LOCAL_STATE_TABLE
            )
        }
        if not _foreign_key_matches(
            foreign_keys,
            name=TEMPLATE_V2_LOCAL_STATE_TEMPLATE_FK,
            columns=["template_id"],
            referred_table="template_v2",
            referred_columns=["id"],
            ondelete="CASCADE",
        ):
            incompatible.append(
                f"{TEMPLATE_V2_LOCAL_STATE_TABLE} requires named FK "
                f"{TEMPLATE_V2_LOCAL_STATE_TEMPLATE_FK}"
            )
        if not any(
            _foreign_key_matches(
                foreign_keys,
                name=TEMPLATE_V2_LOCAL_STATE_PRESENTATION_FK,
                columns=["presentation_id"],
                referred_table="presentations",
                referred_columns=["id"],
                ondelete=delete_action,
            )
            for delete_action in TEMPLATE_V2_PRESENTATION_FK_DELETE_ACTIONS
        ):
            incompatible.append(
                f"{TEMPLATE_V2_LOCAL_STATE_TABLE} requires named FK "
                f"{TEMPLATE_V2_LOCAL_STATE_PRESENTATION_FK}"
            )

        indexes = {
            index.get("name"): index
            for index in inspector.get_indexes(TEMPLATE_V2_LOCAL_STATE_TABLE)
        }
        presentation_index = indexes.get(
            TEMPLATE_V2_LOCAL_STATE_PRESENTATION_INDEX
        )
        if (
            presentation_index is None
            or presentation_index.get("column_names") != ["presentation_id"]
            or presentation_index.get("unique", False)
        ):
            incompatible.append(
                f"{TEMPLATE_V2_LOCAL_STATE_TABLE} requires non-unique index "
                f"{TEMPLATE_V2_LOCAL_STATE_PRESENTATION_INDEX}"
            )

        checks = {
            check.get("name"): check.get("sqltext")
            for check in inspector.get_check_constraints(
                TEMPLATE_V2_LOCAL_STATE_TABLE
            )
        }
        revision_check = checks.get(TEMPLATE_V2_LOCAL_STATE_REVISION_CHECK)
        if revision_check is None or _normalized_sql(
            revision_check
        ) != _normalized_sql("revision >= 1"):
            incompatible.append(
                f"{TEMPLATE_V2_LOCAL_STATE_TABLE} requires positive revision "
                "constraint"
            )

        if set(local_columns) == TEMPLATE_V2_LOCAL_STATE_COLUMNS:
            transitional_columns = {
                "presentation_id",
                "revision",
            }.issubset(canonical_columns)
            if transitional_columns and _has_row(
                connection,
                """
                SELECT 1
                FROM template_v2 AS template
                LEFT JOIN template_v2_local_state AS local_state
                  ON local_state.template_id = template.id
                WHERE local_state.template_id IS NULL
                   OR local_state.presentation_id <> template.presentation_id
                   OR local_state.revision <> template.revision
                   OR local_state.revision < 1
                LIMIT 1
                """,
            ):
                incompatible.append(
                    "template_v2 local-state backfill is incomplete or divergent"
                )
            if _has_row(
                connection,
                """
                SELECT 1
                FROM template_v2_local_state AS local_state
                LEFT JOIN template_v2 AS template
                  ON template.id = local_state.template_id
                LEFT JOIN presentations AS source
                  ON source.id = local_state.presentation_id
                WHERE template.id IS NULL
                   OR source.id IS NULL
                LIMIT 1
                """,
            ):
                incompatible.append(
                    "template_v2 local-state contains orphan ownership rows"
                )

    if TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE not in tables:
        incompatible.append(
            f"missing import lifecycle sidecar {TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE}"
        )
    else:
        import_columns = {
            column["name"]
            for column in inspector.get_columns(
                TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE
            )
        }
        allowed_import_column_sets = {
            TEMPLATE_V2_IMPORT_LIFECYCLE_COLUMNS,
            (
                TEMPLATE_V2_IMPORT_LIFECYCLE_COLUMNS
                | TEMPLATE_V2_IMPORT_REVIEW_COLUMNS
            ),
        }
        if import_columns not in allowed_import_column_sets:
            incompatible.append(
                f"{TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE} has incompatible columns"
            )

        import_foreign_keys = {
            foreign_key.get("name"): foreign_key
            for foreign_key in inspector.get_foreign_keys(
                TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE
            )
        }
        if not _foreign_key_matches(
            import_foreign_keys,
            name="fk_template_v2_pptx_imports_draft_template_id",
            columns=["draft_template_id"],
            referred_table="template_v2",
            referred_columns=["id"],
            ondelete="SET NULL",
        ):
            incompatible.append(
                f"{TEMPLATE_V2_IMPORT_LIFECYCLE_TABLE} requires its canonical "
                "draft-template bridge"
            )

    return PhaseOneSchemaReport(tuple(repairable), tuple(incompatible))


def template_v2_presentation_fk_delete_action(inspector) -> str | None:
    """Return the normalized delete action for the transitional ownership FK."""

    if not hasattr(inspector, "get_foreign_keys"):
        return None
    foreign_key = next(
        (
            candidate
            for candidate in inspector.get_foreign_keys("template_v2")
            if candidate.get("name") == TEMPLATE_V2_PRESENTATION_FK
        ),
        None,
    )
    if foreign_key is None:
        return None
    return str(
        (foreign_key.get("options") or {}).get("ondelete", "")
    ).upper()


def template_v2_local_state_presentation_fk_delete_action(
    inspector,
) -> str | None:
    """Return the normalized delete action for the sidecar ownership FK."""

    if not hasattr(inspector, "get_foreign_keys"):
        return None
    foreign_key = next(
        (
            candidate
            for candidate in inspector.get_foreign_keys(
                TEMPLATE_V2_LOCAL_STATE_TABLE
            )
            if candidate.get("name")
            == TEMPLATE_V2_LOCAL_STATE_PRESENTATION_FK
        ),
        None,
    )
    if foreign_key is None:
        return None
    return str(
        (foreign_key.get("options") or {}).get("ondelete", "")
    ).upper()


def require_template_v2_legacy_provenance_drop_data_ready(connection) -> None:
    """Require the data half of a future legacy-column drop contract.

    This is a necessary precondition, not permission to drop the legacy
    column. The dropping migration must also install and verify a replacement
    presentation-owned delete path as described by
    ``TEMPLATE_V2_LEGACY_DROP_REQUIREMENT``. Both the old CASCADE and the
    hardened RESTRICT transition are valid here; the latter relies on the
    explicit child-first service.
    """

    inspector = sa.inspect(connection)
    template_columns = {
        column["name"] for column in inspector.get_columns("template_v2")
    }
    missing_transitional = (
        TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS - template_columns
    )
    if missing_transitional:
        raise RuntimeError(
            "Template V2 legacy provenance drop precondition must run before "
            "removing transitional columns: "
            + ", ".join(sorted(missing_transitional))
        )

    report = validate_template_v2_local_sidecars(connection)
    report.require_compatible()
    if report.repairable:
        raise RuntimeError(
            "Template V2 legacy provenance drop data is incomplete: "
            + "; ".join(report.repairable)
        )

    legacy_foreign_keys = {
        foreign_key.get("name"): foreign_key
        for foreign_key in inspector.get_foreign_keys("template_v2")
    }
    ownership_foreign_key = legacy_foreign_keys.get(
        TEMPLATE_V2_PRESENTATION_FK
    )
    delete_action = str(
        (ownership_foreign_key or {}).get("options", {}).get("ondelete", "")
    ).upper()
    if (
        ownership_foreign_key is None
        or ownership_foreign_key.get("constrained_columns")
        != ["presentation_id"]
        or ownership_foreign_key.get("referred_table") != "presentations"
        or ownership_foreign_key.get("referred_columns") != ["id"]
        or delete_action not in TEMPLATE_V2_PRESENTATION_FK_DELETE_ACTIONS
    ):
        raise RuntimeError(
            "Template V2 legacy provenance drop data requires a known "
            "presentation ownership foreign key"
        )

# Fail-closed chart/table mutation contract.
# Unknown JSON fields are cloned and retained; only known unsafe state is
# rejected or, for an explicit chart-type transition, deliberately cleared.
CHART_TYPES = frozenset(
    {
        "area",
        "bar",
        "bubble",
        "donut",
        "horizontal_bar",
        "horizontal_stacked_bar",
        "line",
        "pie",
        "polar_area",
        "radar",
        "scatter",
        "stacked_bar",
    }
)
AXIS_CHART_TYPES = frozenset(
    {
        "area",
        "bar",
        "horizontal_bar",
        "horizontal_stacked_bar",
        "line",
        "stacked_bar",
    }
)
DATA_LABEL_POSITIONS = frozenset({"base", "mid", "top", "outside"})
AXIS_FIELDS = (
    "x_axis",
    "y_axis",
    "x_axis_title",
    "y_axis_title",
    "axis_color",
    "x_axis_grid",
    "y_axis_grid",
    "grid_color",
)


def _contract_ok(value: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "value": value}


def _contract_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}


def _clone_contract_json_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        cloned = deepcopy(dict(value))
    except (TypeError, ValueError):
        return None

    active: set[int] = set()

    def visit(item: Any) -> bool:
        if item is None or isinstance(item, (str, bool, int)):
            return True
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, list):
            identity = id(item)
            if identity in active:
                return False
            active.add(identity)
            valid = all(visit(child) for child in item)
            active.remove(identity)
            return valid
        if isinstance(item, dict):
            identity = id(item)
            if identity in active or any(
                not isinstance(key, str) for key in item
            ):
                return False
            active.add(identity)
            valid = all(visit(child) for child in item.values())
            active.remove(identity)
            return valid
        return False

    return cloned if visit(cloned) else None


def chart_contract_capabilities(chart_type: Any) -> dict[str, Any]:
    if not isinstance(chart_type, str) or chart_type not in CHART_TYPES:
        return _contract_error("unsupported_chart_type")
    axis_options = chart_type in AXIS_CHART_TYPES
    if chart_type in {"line", "area"}:
        positions = ["top", "outside"]
    elif axis_options:
        positions = ["base", "mid", "top", "outside"]
    else:
        positions = []
    return _contract_ok(
        {
            "axis_options": axis_options,
            "data_label_positions": positions,
            "multiple_series": chart_type not in {"pie", "donut"},
        }
    )


def _contract_meaningful(element: Mapping[str, Any], field: str) -> bool:
    return field in element and element[field] is not None


def _validate_chart_contract_value(chart: dict[str, Any]) -> dict[str, Any]:
    if chart.get("type") != "chart":
        return _contract_error("not_chart")
    chart_type = chart.get("chart_type")
    capabilities = chart_contract_capabilities(chart_type)
    if not capabilities["ok"]:
        return capabilities
    policy = capabilities["value"]

    categories = chart.get("categories")
    if categories is not None and (
        not isinstance(categories, list)
        or len(categories) > 24
        or any(not isinstance(item, str) for item in categories)
    ):
        return _contract_error("invalid_chart_categories")

    series_list = chart.get("series")
    if series_list is not None:
        if not isinstance(series_list, list) or len(series_list) > 12:
            return _contract_error("invalid_chart_series")
        if not policy["multiple_series"] and len(series_list) > 1:
            return _contract_error("chart_series_limit")
        for series in series_list:
            if (
                not isinstance(series, dict)
                or not isinstance(series.get("name"), str)
                or not isinstance(series.get("values"), list)
                or len(series["values"]) > 24
                or any(
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not math.isfinite(item)
                    for item in series["values"]
                )
            ):
                return _contract_error("invalid_chart_series")
            if categories is not None and len(series["values"]) != len(
                categories
            ):
                return _contract_error("chart_category_mismatch")

    if any(_contract_meaningful(chart, field) for field in AXIS_FIELDS) and not policy[
        "axis_options"
    ]:
        return _contract_error("stale_chart_axis_options")
    if _contract_meaningful(chart, "data_labels"):
        data_labels = chart["data_labels"]
        if (
            not isinstance(data_labels, str)
            or data_labels not in DATA_LABEL_POSITIONS
        ):
            return _contract_error("invalid_chart_data_labels")
        if data_labels not in policy["data_label_positions"]:
            return _contract_error("stale_chart_data_labels")

    size = chart.get("size")
    if size is not None:
        if not isinstance(size, dict):
            return _contract_error("invalid_chart_size")
        width = size.get("width")
        height = size.get("height")
        if (
            isinstance(width, bool)
            or not isinstance(width, (int, float))
            or not math.isfinite(width)
            or isinstance(height, bool)
            or not isinstance(height, (int, float))
            or not math.isfinite(height)
            or width < 80
            or height < 60
        ):
            return _contract_error("invalid_chart_size")
    return _contract_ok(chart)


def validate_chart_contract(chart: Any) -> dict[str, Any]:
    cloned = _clone_contract_json_object(chart)
    return (
        _validate_chart_contract_value(cloned)
        if cloned is not None
        else _contract_error("element_must_be_json_object")
    )


def normalize_chart_contract(
    chart: Any,
    *,
    previous_type: Any = None,
) -> dict[str, Any]:
    cloned = _clone_contract_json_object(chart)
    if cloned is None:
        return _contract_error("element_must_be_json_object")
    if cloned.get("type") != "chart":
        return _contract_error("not_chart")
    chart_type = cloned.get("chart_type")
    if not isinstance(chart_type, str) or chart_type not in CHART_TYPES:
        return _contract_error("unsupported_chart_type")

    # Mirrors the explicit legacy coercion in templates.v2.models.Chart.
    if cloned.get("data_labels") is True:
        cloned["data_labels"] = "top"
    elif cloned.get("data_labels") is False:
        cloned.pop("data_labels", None)

    if previous_type is not None:
        if not isinstance(previous_type, str) or previous_type not in CHART_TYPES:
            return _contract_error("unsupported_previous_chart_type")
        if previous_type != chart_type:
            policy = chart_contract_capabilities(chart_type)["value"]
            if not policy["axis_options"]:
                for field in AXIS_FIELDS:
                    cloned.pop(field, None)
            if _contract_meaningful(cloned, "data_labels") and cloned[
                "data_labels"
            ] not in policy["data_label_positions"]:
                cloned.pop("data_labels", None)
    return _validate_chart_contract_value(cloned)


def _valid_contract_cell(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if _contract_meaningful(value, "stroke") or _contract_meaningful(
        value, "border"
    ):
        # TableCell has no canonical border field. The HTML renderer's
        # undeclared `stroke` support is not enough to make it persistable.
        return False
    return (
        _valid_contract_font(value.get("font"))
        and isinstance(value.get("runs"), list)
        and all(
            isinstance(run, dict)
            and isinstance(run.get("text"), str)
            and _valid_contract_font(run.get("font"))
            for run in value["runs"]
        )
    )


def _valid_contract_font(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    for field in ("family", "color"):
        if _contract_meaningful(value, field) and not isinstance(
            value[field], str
        ):
            return False
    for field in ("bold", "italic", "underline", "ellipsis"):
        if _contract_meaningful(value, field) and not isinstance(
            value[field], bool
        ):
            return False
    for field in ("size", "line_height", "letter_spacing", "opacity"):
        if _contract_meaningful(value, field):
            number = value[field]
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
            ):
                return False
    return True


def _contract_bound(table: Mapping[str, Any], field: str) -> int | None:
    value = table.get(field)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def _validate_table_contract_value(table: dict[str, Any]) -> dict[str, Any]:
    if table.get("type") != "table":
        return _contract_error("not_table")
    columns = table.get("columns")
    rows = table.get("rows")
    if not isinstance(columns, list) or not all(
        _valid_contract_cell(cell) for cell in columns
    ):
        return _contract_error("invalid_table_columns")
    if not isinstance(rows, list) or not all(
        isinstance(row, list) and all(_valid_contract_cell(cell) for cell in row)
        for row in rows
    ):
        return _contract_error("invalid_table_rows")
    if any(len(row) != len(columns) for row in rows):
        return _contract_error("table_not_rectangular")

    min_columns = _contract_bound(table, "min_columns")
    max_columns = _contract_bound(table, "max_columns")
    min_rows = _contract_bound(table, "min_rows")
    max_rows = _contract_bound(table, "max_rows")
    if None in {min_columns, max_columns, min_rows, max_rows}:
        return _contract_error("table_bounds_required")
    assert min_columns is not None
    assert max_columns is not None
    assert min_rows is not None
    assert max_rows is not None
    if min_columns > max_columns or min_rows > max_rows:
        return _contract_error("invalid_table_bounds")
    if not min_columns <= len(columns) <= max_columns:
        return _contract_error("table_column_count_out_of_bounds")
    # `columns` is the header row. Bounds apply only to body rows in `rows`.
    if not min_rows <= len(rows) <= max_rows:
        return _contract_error("table_body_row_count_out_of_bounds")
    return _contract_ok(table)


def validate_table_contract(table: Any) -> dict[str, Any]:
    cloned = _clone_contract_json_object(table)
    return (
        _validate_table_contract_value(cloned)
        if cloned is not None
        else _contract_error("element_must_be_json_object")
    )


def normalize_table_contract(table: Any) -> dict[str, Any]:
    cloned = _clone_contract_json_object(table)
    if cloned is None:
        return _contract_error("element_must_be_json_object")
    columns = cloned.get("columns")
    rows = cloned.get("rows")
    if (
        cloned.get("type") != "table"
        or not isinstance(columns, list)
        or not isinstance(rows, list)
    ):
        return _validate_table_contract_value(cloned)
    fields = ("min_columns", "max_columns", "min_rows", "max_rows")
    missing = [
        field for field in fields if cloned.get(field) is None
    ]
    if len(missing) == len(fields):
        cloned.update(
            {
                "min_columns": len(columns),
                "max_columns": len(columns),
                "min_rows": len(rows),
                "max_rows": len(rows),
            }
        )
    elif missing:
        return _contract_error("partial_table_bounds")
    return _validate_table_contract_value(cloned)


def build_chart_contract_edit_patches(
    chart: Any,
    edit: Any,
) -> dict[str, Any]:
    """Return toolbar-safe patches after validating the complete candidate."""

    validated = validate_chart_contract(chart)
    if not validated["ok"]:
        return validated
    if not isinstance(edit, Mapping):
        return _contract_error("invalid_chart_edit")
    candidate = validated["value"]
    patches: list[dict[str, Any]] = []
    kind = edit.get("kind")

    if kind == "chart-type":
        previous_type = candidate.get("chart_type")
        next_type = edit.get("value")
        if (
            previous_type not in CHART_TYPES
            or next_type not in CHART_TYPES
        ):
            return _contract_error("unsupported_chart_type_edit")
        if previous_type == next_type:
            return {"ok": True, "value": []}
        policy = chart_contract_capabilities(next_type)["value"]
        candidate["chart_type"] = next_type
        patches.append({"path": ["chart_type"], "value": next_type})
        if not policy["axis_options"]:
            for field in AXIS_FIELDS:
                if _contract_meaningful(candidate, field):
                    candidate[field] = None
                    patches.append({"path": [field], "value": None})
        if _contract_meaningful(
            candidate, "data_labels"
        ) and candidate["data_labels"] not in policy["data_label_positions"]:
            candidate["data_labels"] = None
            patches.append({"path": ["data_labels"], "value": None})
    elif kind == "data-labels":
        value = edit.get("value")
        policy = chart_contract_capabilities(candidate.get("chart_type"))[
            "value"
        ]
        if value is not None and (
            not isinstance(value, str)
            or value not in policy["data_label_positions"]
        ):
            return _contract_error("invalid_chart_data_labels_edit")
        if candidate.get("data_labels") == value:
            return {"ok": True, "value": []}
        candidate["data_labels"] = value
        patches.append({"path": ["data_labels"], "value": value})
    elif kind == "category-count":
        next_count = edit.get("value")
        categories = candidate.get("categories")
        series_list = candidate.get("series")
        if (
            isinstance(next_count, bool)
            or not isinstance(next_count, int)
            or not 0 <= next_count <= 24
            or not isinstance(categories, list)
            or not isinstance(series_list, list)
        ):
            return _contract_error("invalid_chart_category_count_edit")
        if next_count == len(categories):
            return {"ok": True, "value": []}
        del categories[next_count:]
        while len(categories) < next_count:
            categories.append(f"Category {len(categories) + 1}")
        patches.append({"path": ["categories"], "value": categories})
        for series_index, series in enumerate(series_list):
            values = series["values"]
            del values[next_count:]
            values.extend([0] * (next_count - len(values)))
            patches.append(
                {
                    "path": ["series", series_index, "values"],
                    "value": values,
                }
            )
    elif kind == "series-count":
        next_count = edit.get("value")
        categories = candidate.get("categories")
        series_list = candidate.get("series")
        policy = chart_contract_capabilities(candidate.get("chart_type"))[
            "value"
        ]
        limit = 12 if policy["multiple_series"] else 1
        if (
            isinstance(next_count, bool)
            or not isinstance(next_count, int)
            or not 0 <= next_count <= limit
            or not isinstance(categories, list)
            or not isinstance(series_list, list)
        ):
            return _contract_error("invalid_chart_series_count_edit")
        if next_count == len(series_list):
            return {"ok": True, "value": []}
        del series_list[next_count:]
        while len(series_list) < next_count:
            series_list.append(
                {
                    "name": f"Series {len(series_list) + 1}",
                    "values": [0] * len(categories),
                }
            )
        patches.append({"path": ["series"], "value": series_list})
    elif kind == "category-label":
        index = edit.get("categoryIndex")
        value = edit.get("value")
        categories = candidate.get("categories")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not isinstance(value, str)
            or not isinstance(categories, list)
            or not 0 <= index < len(categories)
        ):
            return _contract_error("invalid_chart_category_edit")
        categories[index] = value
        patches.append(
            {"path": ["categories", index], "value": value}
        )
    elif kind in {"series-name", "series-value"}:
        series_index = edit.get("seriesIndex")
        series_list = candidate.get("series")
        if (
            isinstance(series_index, bool)
            or not isinstance(series_index, int)
            or not isinstance(series_list, list)
            or not 0 <= series_index < len(series_list)
            or not isinstance(series_list[series_index], dict)
        ):
            return _contract_error("invalid_chart_series_edit")
        selected = series_list[series_index]
        value = edit.get("value")
        if kind == "series-name":
            if not isinstance(value, str):
                return _contract_error("invalid_chart_series_name_edit")
            selected["name"] = value
            patches.append(
                {
                    "path": ["series", series_index, "name"],
                    "value": value,
                }
            )
        else:
            category_index = edit.get("categoryIndex")
            values = selected.get("values")
            if (
                isinstance(category_index, bool)
                or not isinstance(category_index, int)
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not isinstance(values, list)
                or not 0 <= category_index < len(values)
            ):
                return _contract_error("invalid_chart_series_value_edit")
            values[category_index] = value
            patches.append(
                {
                    "path": [
                        "series",
                        series_index,
                        "values",
                        category_index,
                    ],
                    "value": value,
                }
            )
    else:
        return _contract_error("invalid_chart_edit")

    result = _validate_chart_contract_value(candidate)
    return {"ok": True, "value": patches} if result["ok"] else result


def _blank_contract_table_cell() -> dict[str, Any]:
    return {"runs": [{"text": ""}]}


def build_table_contract_edit_patches(
    table: Any,
    edit: Any,
) -> dict[str, Any]:
    """Return rectangular, bounds-safe table patches without style guesses."""

    validated = validate_table_contract(table)
    if not validated["ok"]:
        return validated
    if not isinstance(edit, Mapping):
        return _contract_error("invalid_table_edit")
    candidate = validated["value"]
    columns = candidate["columns"]
    rows = candidate["rows"]
    patches: list[dict[str, Any]] = []
    kind = edit.get("kind")

    if kind in {"run-text", "cell-font"}:
        section = edit.get("section")
        row_index = edit.get("rowIndex")
        column_index = edit.get("columnIndex")
        if (
            section not in {"header", "body"}
            or isinstance(column_index, bool)
            or not isinstance(column_index, int)
            or column_index < 0
        ):
            return _contract_error(
                "invalid_table_text_edit"
                if kind == "run-text"
                else "invalid_table_font_edit"
            )
        if section == "header":
            cell = (
                columns[column_index]
                if column_index < len(columns)
                else None
            )
            cell_path = ["columns", column_index]
        else:
            if (
                isinstance(row_index, bool)
                or not isinstance(row_index, int)
                or row_index < 0
                or row_index >= len(rows)
                or column_index >= len(rows[row_index])
            ):
                return _contract_error(
                    "invalid_table_text_edit"
                    if kind == "run-text"
                    else "invalid_table_font_edit"
                )
            cell = rows[row_index][column_index]
            cell_path = [
                "rows",
                row_index,
                column_index,
            ]
        if not isinstance(cell, dict):
            return _contract_error(
                "invalid_table_text_edit"
                if kind == "run-text"
                else "invalid_table_font_edit"
            )
        value = edit.get("value")
        if kind == "run-text":
            run_index = edit.get("runIndex")
            if (
                isinstance(run_index, bool)
                or not isinstance(run_index, int)
                or run_index < 0
                or not isinstance(value, str)
                or not isinstance(cell.get("runs"), list)
                or run_index >= len(cell["runs"])
                or not isinstance(cell["runs"][run_index], dict)
            ):
                return _contract_error("invalid_table_text_edit")
            cell["runs"][run_index]["text"] = value
            patches.append(
                {
                    "path": cell_path
                    + ["runs", run_index, "text"],
                    "value": value,
                }
            )
        else:
            field = edit.get("field")
            if field not in {
                "size",
                "family",
                "color",
                "bold",
                "italic",
                "underline",
            }:
                return _contract_error("invalid_table_font_edit")
            if field in {"bold", "italic", "underline"}:
                valid_value = isinstance(value, bool)
            elif field == "size":
                valid_value = (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and math.isfinite(value)
                )
            else:
                valid_value = isinstance(value, str)
            if not valid_value:
                return _contract_error("invalid_table_font_edit")
            font = cell.get("font")
            if not isinstance(font, dict):
                font = {}
                cell["font"] = font
            font[field] = value
            patches.append(
                {
                    "path": cell_path + ["font", field],
                    "value": value,
                }
            )
    elif kind == "row-count":
        next_count = edit.get("value")
        if (
            isinstance(next_count, bool)
            or not isinstance(next_count, int)
            or next_count < 0
            or not candidate["min_rows"]
            <= next_count
            <= candidate["max_rows"]
        ):
            return _contract_error("table_row_count_edit_out_of_bounds")
        if next_count == len(rows):
            return {"ok": True, "value": []}
        del rows[next_count:]
        while len(rows) < next_count:
            rows.append(
                [_blank_contract_table_cell() for _ in columns]
            )
        patches.append({"path": ["rows"], "value": rows})
    elif kind == "column-count":
        next_count = edit.get("value")
        if (
            isinstance(next_count, bool)
            or not isinstance(next_count, int)
            or next_count < 1
            or not candidate["min_columns"]
            <= next_count
            <= candidate["max_columns"]
        ):
            return _contract_error("table_column_count_edit_out_of_bounds")
        if next_count == len(columns):
            return {"ok": True, "value": []}
        del columns[next_count:]
        for row in rows:
            del row[next_count:]
        while len(columns) < next_count:
            columns.append(_blank_contract_table_cell())
            for row in rows:
                row.append(_blank_contract_table_cell())
        patches.extend(
            [
                {"path": ["columns"], "value": columns},
                {"path": ["rows"], "value": rows},
            ]
        )
    else:
        return _contract_error("invalid_table_edit")

    result = _validate_table_contract_value(candidate)
    return {"ok": True, "value": patches} if result["ok"] else result
