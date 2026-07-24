"""Semantic completeness checks shared by legacy inference and Phase 1 DDL."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
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
        if import_columns != TEMPLATE_V2_IMPORT_LIFECYCLE_COLUMNS:
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
