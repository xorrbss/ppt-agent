import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sys
import time

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Integer, create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from template_v2_schema_contract import (
    SLIDE_UI_CHECK_CONSTRAINT,
    TEMPLATE_V2_PRESENTATION_FK_DELETE_ACTIONS,
    template_v2_local_state_presentation_fk_delete_action,
    template_v2_presentation_fk_delete_action,
    validate_template_v2_local_sidecars,
    validate_template_v2_phase_one_schema,
)
from template_v2_schema_contract import (
    TEMPLATE_V2_EXPECTED_COLUMNS as PHASE_ONE_EXPECTED_COLUMNS,
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
REVISION_TEMPLATE_V2_IMPORT_REVIEW = "1b2c3d4e5f6a"
REVISION_TEMPLATE_V2_REVISION_JOURNAL = "2c3d4e5f6a7b"
REVISION_DURABLE_GENERATION_JOBS = "3d4e5f6a7b8c"
REVISION_MULTI_USER_AUTH = "c9f1a2b3d4e5"
REVISION_PROVIDER_SETTINGS = "d0a2b4c6e8f1"
REVISION_PRIMARY_ADMIN = "e1b3c5d7f9a2"
REVISION_CHAT_TEMPLATE_SCOPE_REPAIR = "f2c4d6e8a0b1"
REVISION_AUTHORED_SLIDE_CHECKPOINTS = "a3c5e7f9b1d2"
REVISION_AUTHORED_RESUME_COUNT = "b4d6f8a1c3e5"
REVISION_AUTHORED_EXACT_PROVENANCE = "c5e7f9a1b3d4"
DURABLE_PRESENTATION_GENERATION_JOB_COLUMNS = {
    "id",
    "task_id",
    "presentation_id",
    "idempotency_key_hash",
    "request_id",
    "request_sha256",
    "request_payload",
    "template_v2_target",
    "export_cookie_required",
    "state",
    "attempt_number",
    "attempt_token",
    "lease_expires_at",
    "heartbeat_at",
    "last_started_at",
    "published_at",
    "failed_at",
    "created_at",
    "updated_at",
}
AUTHORED_PRESENTATION_GENERATION_JOB_COLUMNS = {
    "total_slides",
    "completed_slides",
    "current_stage",
    "last_error",
    "resume_allowed",
    "authored_outline",
}
AUTHORED_RESUME_COUNT_COLUMNS = {"resume_count"}
PRESENTATION_GENERATION_JOB_COLUMNS = (
    DURABLE_PRESENTATION_GENERATION_JOB_COLUMNS
    | AUTHORED_PRESENTATION_GENERATION_JOB_COLUMNS
    | AUTHORED_RESUME_COUNT_COLUMNS
)
AUTHORED_SLIDE_CHECKPOINT_COLUMNS = {
    "id",
    "job_id",
    "slide_index",
    "role",
    "author_status",
    "illustrate_status",
    "render_status",
    "persist_status",
    "author_html",
    "illustrated_html",
    "render_path",
    "render_sha256",
    "failed_stage",
    "error",
    "attempt_token",
    "author_started_at",
    "author_completed_at",
    "illustrate_started_at",
    "illustrate_completed_at",
    "render_started_at",
    "render_completed_at",
    "persist_started_at",
    "persist_completed_at",
    "failed_at",
    "created_at",
    "updated_at",
}
AUTHORED_SLIDE_CHECKPOINT_CONSTRAINTS = {
    "ck_authored_slide_checkpoints_slide_index_nonnegative",
    "ck_authored_slide_checkpoints_author_status",
    "ck_authored_slide_checkpoints_illustrate_status",
    "ck_authored_slide_checkpoints_render_status",
    "ck_authored_slide_checkpoints_persist_status",
}
AUTHORED_SLIDE_CHECKPOINT_INDEX = (
    "ix_authored_slide_checkpoints_job_persist"
)
AUTHORED_SLIDE_CHECKPOINT_UNIQUE = (
    "uq_authored_slide_checkpoints_job_slide"
)
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
TEMPLATE_V2_IMPORT_REVIEW_COLUMNS = {
    "owner_scope",
    "request_key_hash",
    "request_fingerprint",
    "revision",
    "analysis_result",
    "repeat_suggestions",
    "confirmed_at",
    "cancelled_at",
}
TEMPLATE_V2_EXPECTED_COLUMNS = set(PHASE_ONE_EXPECTED_COLUMNS)
# One stable, application-specific signed bigint key. PostgreSQL session advisory
# locks serialize Alembic ownership across API and worker replicas.
MIGRATION_ADVISORY_LOCK_ID = 0x5050544147454E54
MIGRATION_ADVISORY_LOCK_TIMEOUT_SECONDS = 30.0
MIGRATION_ADVISORY_LOCK_POLL_SECONDS = 0.25


def _validate_authored_checkpoint_schema(
    inspector,
    tables: set[str],
    job_columns: set[str],
    *,
    validate_constraints: bool,
) -> bool:
    """Return whether the authored checkpoint revision is fully present.

    No marker means the schema is the previous cumulative revision. Any marker
    requires the complete additive contract so a partially applied migration is
    never stamped past its missing DDL.
    """

    present_job_columns = (
        AUTHORED_PRESENTATION_GENERATION_JOB_COLUMNS & job_columns
    )
    has_checkpoint_table = "authored_slide_checkpoints" in tables
    if not present_job_columns and not has_checkpoint_table:
        return False
    if (
        present_job_columns != AUTHORED_PRESENTATION_GENERATION_JOB_COLUMNS
        or not has_checkpoint_table
    ):
        raise RuntimeError(
            "Authored slide checkpoint schema is only partially applied"
        )

    checkpoint_columns = {
        column["name"]
        for column in inspector.get_columns("authored_slide_checkpoints")
    }
    if not AUTHORED_SLIDE_CHECKPOINT_COLUMNS.issubset(checkpoint_columns):
        raise RuntimeError(
            "Authored slide checkpoint table is only partially applied"
        )
    if not validate_constraints:
        return True

    indexes = {
        index.get("name")
        for index in inspector.get_indexes("authored_slide_checkpoints")
    }
    if AUTHORED_SLIDE_CHECKPOINT_INDEX not in indexes:
        raise RuntimeError(
            "Authored slide checkpoint progress index is missing"
        )
    unique_constraints = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints(
            "authored_slide_checkpoints"
        )
    }
    if AUTHORED_SLIDE_CHECKPOINT_UNIQUE not in unique_constraints:
        raise RuntimeError(
            "Authored slide checkpoint job/slide uniqueness is missing"
        )
    check_constraints = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints(
            "authored_slide_checkpoints"
        )
    }
    if not AUTHORED_SLIDE_CHECKPOINT_CONSTRAINTS.issubset(
        check_constraints
    ):
        raise RuntimeError(
            "Authored slide checkpoint state constraints are incomplete"
        )
    job_foreign_keys = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys(
            "authored_slide_checkpoints"
        )
        if foreign_key.get("constrained_columns") == ["job_id"]
        and foreign_key.get("referred_table") == "presentation_generation_jobs"
        and foreign_key.get("referred_columns") == ["id"]
    ]
    if len(job_foreign_keys) != 1:
        raise RuntimeError(
            "Authored slide checkpoint job foreign key is missing"
        )
    ondelete = str(
        (job_foreign_keys[0].get("options") or {}).get("ondelete") or ""
    ).upper()
    if ondelete != "CASCADE":
        raise RuntimeError(
            "Authored slide checkpoint job foreign key must cascade deletes"
        )
    return True


def _authored_resume_count_applied(
    job_columns: set[str], has_authored_checkpoints: bool
) -> bool:
    """Return whether the resume-count revision is present.

    The column is additive on top of the checkpoint revision; its presence
    without that base marks a partially applied or hand-edited schema.
    """

    has_resume_count = bool(AUTHORED_RESUME_COUNT_COLUMNS & job_columns)
    if has_resume_count and not has_authored_checkpoints:
        raise RuntimeError(
            "Authored resume count column exists without checkpoint schema"
        )
    return has_resume_count


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
    with _migration_owner_lock(database_url):
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


@contextmanager
def _migration_owner_lock(database_url: str) -> Iterator[None]:
    """Serialize PostgreSQL migrations while preserving local SQLite behavior."""

    if make_url(database_url).get_backend_name() != "postgresql":
        yield
        return

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection = connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            deadline = (
                time.monotonic() + MIGRATION_ADVISORY_LOCK_TIMEOUT_SECONDS
            )
            acquired = False
            while not acquired:
                acquired = bool(
                    connection.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
                    ).scalar_one()
                )
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("postgresql_migration_lock_timeout")
                time.sleep(MIGRATION_ADVISORY_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                active_error = sys.exc_info()[0] is not None
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
                    )
                except Exception:
                    if not active_error:
                        raise
                    print(
                        "Error releasing PostgreSQL migration lock; "
                        "preserving the migration failure",
                        flush=True,
                    )
    finally:
        engine.dispose()


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
            allowed_extra_columns=frozenset({"revision", "owner_id"}),
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
            present_review_columns = (
                TEMPLATE_V2_IMPORT_REVIEW_COLUMNS & import_columns
            )
            if not present_review_columns:
                return REVISION_TEMPLATE_V2_DELETE_SAFETY
            if not TEMPLATE_V2_IMPORT_REVIEW_COLUMNS.issubset(import_columns):
                raise RuntimeError(
                    "Template V2 PPTX review schema is only partially applied"
                )
            if (
                "uq_template_v2_pptx_imports_owner_request_key"
                not in dispatch_indexes
            ):
                raise RuntimeError(
                    "Template V2 PPTX owner request-key index is missing"
                )
            if "template_v2_revisions" not in tables:
                return REVISION_TEMPLATE_V2_IMPORT_REVIEW
            has_job_table = "presentation_generation_jobs" in tables
            has_lifecycle = "lifecycle_status" in presentation_columns
            if not has_job_table and not has_lifecycle:
                if "authored_slide_checkpoints" in tables:
                    raise RuntimeError(
                        "Authored slide checkpoint schema exists without "
                        "durable generation jobs"
                    )
                return REVISION_TEMPLATE_V2_REVISION_JOURNAL
            if has_job_table != has_lifecycle:
                raise RuntimeError(
                    "Durable generation schema is only partially applied"
                )
            job_columns = {
                column["name"]
                for column in inspector.get_columns(
                    "presentation_generation_jobs"
                )
            }
            if not DURABLE_PRESENTATION_GENERATION_JOB_COLUMNS.issubset(
                job_columns
            ):
                raise RuntimeError(
                    "Durable generation job schema is only partially applied"
                )
            has_authored_checkpoints = _validate_authored_checkpoint_schema(
                inspector,
                tables,
                job_columns,
                validate_constraints=True,
            )
            has_authored_resume_count = _authored_resume_count_applied(
                job_columns, has_authored_checkpoints
            )
            if "user" not in tables:
                if has_authored_checkpoints:
                    raise RuntimeError(
                        "Authored slide checkpoint schema exists without "
                        "multi-user prerequisites"
                    )
                return REVISION_DURABLE_GENERATION_JOBS
            if "provider_settings" not in tables:
                if has_authored_checkpoints:
                    raise RuntimeError(
                        "Authored slide checkpoint schema exists without "
                        "provider settings prerequisites"
                    )
                return REVISION_MULTI_USER_AUTH
            user_columns = {
                column["name"]
                for column in inspector.get_columns("user")
            }
            if "admin_slot" not in user_columns:
                if has_authored_checkpoints:
                    raise RuntimeError(
                        "Authored slide checkpoint schema exists without "
                        "primary-admin prerequisites"
                    )
                return REVISION_PROVIDER_SETTINGS
            chat_columns = {
                column["name"]
                for column in inspector.get_columns("chat_history_messages")
            }
            if "template_v2_id" not in chat_columns:
                if has_authored_checkpoints:
                    raise RuntimeError(
                        "Authored slide checkpoint schema exists without "
                        "chat-scope prerequisites"
                    )
                return REVISION_PRIMARY_ADMIN
            if has_authored_resume_count:
                return (
                    REVISION_AUTHORED_EXACT_PROVENANCE
                    if "authored_exact_provenance" in tables
                    else REVISION_AUTHORED_RESUME_COUNT
                )
            return (
                REVISION_AUTHORED_SLIDE_CHECKPOINTS
                if has_authored_checkpoints
                else REVISION_CHAT_TEMPLATE_SCOPE_REPAIR
            )
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
        present_review_columns = TEMPLATE_V2_IMPORT_REVIEW_COLUMNS & import_columns
        if not present_review_columns:
            return REVISION_TEMPLATE_V2_DELETE_SAFETY
        if not TEMPLATE_V2_IMPORT_REVIEW_COLUMNS.issubset(import_columns):
            raise RuntimeError(
                "Template V2 PPTX review schema is only partially applied"
            )
        if "template_v2_revisions" not in tables:
            return REVISION_TEMPLATE_V2_IMPORT_REVIEW
        has_job_table = "presentation_generation_jobs" in tables
        has_lifecycle = "lifecycle_status" in presentation_columns
        if not has_job_table and not has_lifecycle:
            if "authored_slide_checkpoints" in tables:
                raise RuntimeError(
                    "Authored slide checkpoint schema exists without "
                    "durable generation jobs"
                )
            return REVISION_TEMPLATE_V2_REVISION_JOURNAL
        if has_job_table != has_lifecycle:
            raise RuntimeError(
                "Durable generation schema is only partially applied"
            )
        job_columns = {
            column["name"] if isinstance(column, dict) else column
            for column in inspector.get_columns(
                "presentation_generation_jobs"
            )
        }
        if not DURABLE_PRESENTATION_GENERATION_JOB_COLUMNS.issubset(
            job_columns
        ):
            raise RuntimeError(
                "Durable generation job schema is only partially applied"
            )
        has_authored_checkpoints = _validate_authored_checkpoint_schema(
            inspector,
            tables,
            job_columns,
            validate_constraints=False,
        )
        if _authored_resume_count_applied(
            job_columns, has_authored_checkpoints
        ):
            return (
                REVISION_AUTHORED_EXACT_PROVENANCE
                if "authored_exact_provenance" in tables
                else REVISION_AUTHORED_RESUME_COUNT
            )
        return (
            REVISION_AUTHORED_SLIDE_CHECKPOINTS
            if has_authored_checkpoints
            else REVISION_DURABLE_GENERATION_JOBS
        )
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
        "presentation_generation_jobs",
        "authored_slide_checkpoints",
        "authored_exact_provenance",
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
