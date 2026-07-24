import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Integer, create_engine, inspect, text

from template_v2_schema_contract import (
    SLIDE_UI_CHECK_CONSTRAINT,
    TEMPLATE_V2_EXPECTED_COLUMNS as PHASE_ONE_EXPECTED_COLUMNS,
    TEMPLATE_V2_PRESENTATION_FK_DELETE_ACTIONS,
    template_v2_local_state_presentation_fk_delete_action,
    template_v2_presentation_fk_delete_action,
    validate_template_v2_local_sidecars,
    validate_template_v2_phase_one_schema,
)
from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url
from utils.get_env import get_migrate_database_on_startup_env


LEGACY_BASELINE_REVISION = "00b3c27a13bc"
# Revision before 95b5127e93cd (template_create_infos); used when DB has theme but not that table.
REVISION_BEFORE_TEMPLATE_CREATE_INFO = "82abdbc476a7"
REVISION_TEMPLATE_CREATE_INFO = "95b5127e93cd"
REVISION_CHAT_HISTORY = "c7b70d0f31b1"
REVISION_DECK_PLAN = "c8d1e2f3a4b5"
REVISION_PRESENTATION_MODE = "d1f2a3b4c5e6"
REVISION_PRESENTATION_VERSIONS = "e2f3a4b5c6d7"
REVISION_SHARE_TOKEN = "f3a4b5c6d7e8"
REVISION_TEMPLATE_V2_PHASE_ONE = "a4b5c6d7e8f9"
REVISION_TEMPLATE_V2_PPTX_IMPORTS = "b5c6d7e8f9a0"
REVISION_TEMPLATE_V2_STUDIO = "c6d7e8f9a0b1"
REVISION_TEMPLATE_V2_IMPORT_LEASES = "d7e8f9a0b1c2"
REVISION_TEMPLATE_V2_SOURCE_RETENTION = "e8f9a0b1c2d3"
REVISION_TEMPLATE_V2_LOCAL_STATE = "f9a0b1c2d3e4"
REVISION_TEMPLATE_V2_DELETE_SAFETY = "0a1b2c3d4e5f"
TEMPLATE_V2_IMPORT_LEASE_COLUMNS = {
    "attempt_number",
    "attempt_token",
    "lease_expires_at",
    "heartbeat_at",
    "last_started_at",
}
TEMPLATE_V2_SOURCE_RETENTION_COLUMNS = {
    "source_retention_expires_at",
    "source_cleanup_token",
    "source_cleanup_lease_expires_at",
    "source_cleanup_attempted_at",
    "source_deleted_at",
}
TEMPLATE_V2_EXPECTED_COLUMNS = set(PHASE_ONE_EXPECTED_COLUMNS)


async def migrate_database_on_startup() -> None:
    if get_migrate_database_on_startup_env() not in ["true", "True"]:
        return

    try:
        await asyncio.to_thread(_run_migrations)
        print("Migrations run successfully", flush=True)
    except Exception as exc:
        print(f"Error running migrations: {exc}", flush=True)
        raise


def _run_migrations() -> None:
    # migrations.py lives at servers/fastapi/migrations.py
    # so parents[0] = servers/fastapi/, where alembic/ lives alongside it.
    base_dir = Path(__file__).resolve().parents[0]
    config = Config()
    config.set_main_option("script_location", str(base_dir / "alembic"))

    database_url, _ = get_database_url_and_connect_args()

    # Alembic uses synchronous engines; strip async driver prefixes.
    database_url = to_sync_sqlalchemy_url(database_url)

    config.set_main_option("sqlalchemy.url", database_url)
    _repair_orphan_alembic_revision(config, database_url)
    _stamp_legacy_database_if_needed(config, database_url)

    try:
        command.upgrade(config, "head")
    except Exception:
        # Safety net for edge cases; legacy DBs are stamped proactively above.
        if _is_unversioned_populated_database(database_url):
            _stamp_legacy_database_if_needed(config, database_url)
            command.upgrade(config, "head")
            return
        raise


def _repair_orphan_alembic_revision(config: Config, database_url: str) -> None:
    """
    If alembic_version points at a revision id that no longer exists in alembic/versions
    (removed branch, old image, etc.), re-stamp from the live schema so upgrade can run.
    """
    script = ScriptDirectory.from_config(config)
    known = {rev.revision for rev in script.walk_revisions()}
    heads = script.get_heads()
    if len(heads) != 1:
        return
    head = heads[0]

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables:
                return
            version_num = connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar_one_or_none()
            if not version_num or version_num in known:
                return
            print(
                f"Alembic revision {version_num!r} is missing from the codebase; "
                "inferring applied migrations from schema and re-stamping.",
                flush=True,
            )
            target = _infer_revision_from_schema(inspector, tables, head)
            command.stamp(config, target)
    finally:
        engine.dispose()


def _infer_revision_from_schema(
    inspector, tables: set[str], _head_revision: str
) -> str:
    """Map only a complete cumulative schema prefix to a fixed revision.

    Returning the dynamic Alembic head for an old marker used to stamp every
    future migration as already applied. Fixed revision IDs make inference
    conservative when a new migration is added.
    """

    presentation_columns = (
        {column["name"] for column in inspector.get_columns("presentations")}
        if "presentations" in tables
        else set()
    )
    slide_columns = (
        {column["name"] for column in inspector.get_columns("slides")}
        if "slides" in tables
        else set()
    )
    template_v2_columns = (
        {column["name"] for column in inspector.get_columns("template_v2")}
        if "template_v2" in tables
        else set()
    )
    template_v2_primary_key = (
        set(inspector.get_pk_constraint("template_v2").get("constrained_columns") or [])
        if "template_v2" in tables
        else set()
    )
    slide_check_constraints = (
        {
            constraint.get("name")
            for constraint in inspector.get_check_constraints("slides")
        }
        if "slides" in tables
        else set()
    )

    has_theme = "theme" in presentation_columns
    has_template_create_info = "template_create_infos" in tables
    has_chat = "chat_history_messages" in tables
    has_deck_plan = "deck_plan" in presentation_columns
    has_mode = "mode" in presentation_columns
    has_versions = "presentation_versions" in tables
    has_share_token = "share_token" in presentation_columns
    if not has_theme:
        return LEGACY_BASELINE_REVISION
    if not has_template_create_info:
        return REVISION_BEFORE_TEMPLATE_CREATE_INFO
    if not has_chat:
        return REVISION_TEMPLATE_CREATE_INFO
    if not has_deck_plan:
        return REVISION_CHAT_HISTORY
    if not has_mode:
        return REVISION_DECK_PLAN
    if not has_versions:
        return REVISION_PRESENTATION_MODE
    if not has_share_token:
        return REVISION_PRESENTATION_VERSIONS

    connection = getattr(inspector, "bind", None)
    if connection is not None:
        report = validate_template_v2_phase_one_schema(
            connection,
            allowed_extra_columns=frozenset({"revision"}),
            allowed_presentation_fk_ondelete=(
                TEMPLATE_V2_PRESENTATION_FK_DELETE_ACTIONS
                if "template_v2_local_state" in tables
                else frozenset({"CASCADE"})
            ),
        )
        report.require_compatible()
        if not report.complete:
            return REVISION_SHARE_TOKEN
        if "template_v2_pptx_imports" not in tables:
            if "revision" in template_v2_columns:
                raise RuntimeError(
                    "Template V2 revision column exists without PPTX import schema"
                )
            return REVISION_TEMPLATE_V2_PHASE_ONE
        if "revision" not in template_v2_columns:
            return REVISION_TEMPLATE_V2_PPTX_IMPORTS
        revision_column = next(
            column
            for column in inspector.get_columns("template_v2")
            if column["name"] == "revision"
        )
        revision_default = (
            str(revision_column.get("default"))
            .lower()
            .replace("::integer", "")
            .strip("'\"() ")
        )
        if (
            not isinstance(revision_column.get("type"), Integer)
            or revision_column.get("nullable", True)
            or revision_default != "1"
        ):
            raise RuntimeError(
                "Template V2 revision column has incompatible schema"
            )
        import_columns = {
            column["name"]
            for column in inspector.get_columns("template_v2_pptx_imports")
        }
        present_lease_columns = (
            TEMPLATE_V2_IMPORT_LEASE_COLUMNS & import_columns
        )
        if not present_lease_columns:
            return REVISION_TEMPLATE_V2_STUDIO
        if not TEMPLATE_V2_IMPORT_LEASE_COLUMNS.issubset(import_columns):
            raise RuntimeError(
                "Template V2 PPTX import lease schema is only partially applied"
            )
        dispatch_indexes = {
            index.get("name")
            for index in inspector.get_indexes("template_v2_pptx_imports")
        }
        if "ix_template_v2_pptx_imports_dispatch" not in dispatch_indexes:
            raise RuntimeError(
                "Template V2 PPTX import lease dispatch index is missing"
            )
        present_retention_columns = (
            TEMPLATE_V2_SOURCE_RETENTION_COLUMNS & import_columns
        )
        if not present_retention_columns:
            return REVISION_TEMPLATE_V2_IMPORT_LEASES
        if not TEMPLATE_V2_SOURCE_RETENTION_COLUMNS.issubset(import_columns):
            raise RuntimeError(
                "Template V2 PPTX source retention schema is only partially applied"
            )
        if (
            "ix_template_v2_pptx_imports_source_cleanup"
            not in dispatch_indexes
        ):
            raise RuntimeError(
                "Template V2 PPTX source cleanup index is missing"
            )
        if "template_v2_local_state" not in tables:
            return REVISION_TEMPLATE_V2_SOURCE_RETENTION
        local_state_report = validate_template_v2_local_sidecars(connection)
        local_state_report.require_compatible()
        if not local_state_report.complete:
            raise RuntimeError(
                "Template V2 local-state sidecar schema is incomplete"
            )
        delete_action = template_v2_presentation_fk_delete_action(inspector)
        sidecar_delete_action = (
            template_v2_local_state_presentation_fk_delete_action(inspector)
        )
        if delete_action == sidecar_delete_action == "CASCADE":
            return REVISION_TEMPLATE_V2_LOCAL_STATE
        if delete_action == sidecar_delete_action == "RESTRICT":
            return REVISION_TEMPLATE_V2_DELETE_SAFETY
        raise RuntimeError(
            "Template V2 presentation ownership FKs have unsupported or "
            "mixed delete actions: "
            f"template={delete_action or 'missing'}, "
            f"sidecar={sidecar_delete_action or 'missing'}"
        )

    # Lightweight fallback for isolated unit inspectors. Runtime inference
    # always has an Inspector.bind and therefore uses the semantic validator.
    has_template_v2_marker = (
        "template_v2" in tables
        and "version" in presentation_columns
        and "ui" in slide_columns
        and TEMPLATE_V2_EXPECTED_COLUMNS.issubset(template_v2_columns)
        and template_v2_primary_key == {"id"}
        and SLIDE_UI_CHECK_CONSTRAINT in slide_check_constraints
    )
    if not has_template_v2_marker:
        return REVISION_SHARE_TOKEN
    if "template_v2_pptx_imports" not in tables:
        return REVISION_TEMPLATE_V2_PHASE_ONE
    if "revision" not in template_v2_columns:
        return REVISION_TEMPLATE_V2_PPTX_IMPORTS
    import_columns = {
        column["name"] if isinstance(column, dict) else column
        for column in inspector.get_columns("template_v2_pptx_imports")
    }
    if not TEMPLATE_V2_IMPORT_LEASE_COLUMNS.issubset(import_columns):
        return REVISION_TEMPLATE_V2_STUDIO
    present_retention_columns = TEMPLATE_V2_SOURCE_RETENTION_COLUMNS & import_columns
    if not present_retention_columns:
        return REVISION_TEMPLATE_V2_IMPORT_LEASES
    if not TEMPLATE_V2_SOURCE_RETENTION_COLUMNS.issubset(import_columns):
        raise RuntimeError(
            "Template V2 PPTX source retention schema is only partially applied"
        )
    if "template_v2_local_state" not in tables:
        return REVISION_TEMPLATE_V2_SOURCE_RETENTION
    local_state_columns = {
        column["name"] if isinstance(column, dict) else column
        for column in inspector.get_columns("template_v2_local_state")
    }
    if local_state_columns != {
        "template_id",
        "presentation_id",
        "revision",
        "created_at",
        "updated_at",
    }:
        raise RuntimeError(
            "Template V2 local-state sidecar schema is only partially applied"
        )
    delete_action = template_v2_presentation_fk_delete_action(inspector)
    sidecar_delete_action = (
        template_v2_local_state_presentation_fk_delete_action(inspector)
    )
    if delete_action == sidecar_delete_action == "RESTRICT":
        return REVISION_TEMPLATE_V2_DELETE_SAFETY
    if delete_action != sidecar_delete_action:
        raise RuntimeError(
            "Template V2 presentation ownership FKs have mixed delete actions"
        )
    return REVISION_TEMPLATE_V2_LOCAL_STATE


def _stamp_legacy_database_if_needed(config: Config, database_url: str) -> None:
    """
    If the DB has app tables but no migration reference in alembic_version,
    treat it as a legacy DB and stamp the latest revision already reflected by
    the live schema before upgrading.
    """
    if not _is_unversioned_populated_database(database_url):
        return

    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    head = heads[0] if len(heads) == 1 else script.get_base()
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            target_revision = _infer_revision_from_schema(
                inspector, set(inspector.get_table_names()), head
            )
    finally:
        engine.dispose()

    print(
        "Detected legacy database without migration reference. "
        f"Stamping revision to {target_revision} before upgrading.",
        flush=True,
    )
    command.stamp(config, target_revision)


def _is_unversioned_populated_database(database_url: str) -> bool:
    known_app_tables = {
        "presentations",
        "slides",
        "templates",
        "keyvaluesqlmodel",
        "imageasset",
        "presentation_layout_codes",
        "async_presentation_generation_tasks",
        "webhook_subscriptions",
        "template_create_infos",
        "chat_history_messages",
        "presentation_versions",
        "template_v2",
        "template_v2_local_state",
        "template_v2_pptx_imports",
    }
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            has_alembic_version_table = "alembic_version" in table_names
            has_applied_revision = False
            if has_alembic_version_table:
                revision_count = connection.execute(
                    text("SELECT COUNT(*) FROM alembic_version")
                ).scalar_one()
                has_applied_revision = revision_count > 0
            has_known_app_tables = len(table_names.intersection(known_app_tables)) > 0
            return has_known_app_tables and not has_applied_revision
    finally:
        engine.dispose()
