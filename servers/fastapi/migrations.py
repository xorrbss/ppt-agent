import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sys
import time

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import DateTime, Integer, create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from migration_runtime_settings_schema import (
    SLIDESPEC_ARTIFACT_BUNDLE_COLUMNS,
    SLIDESPEC_PUBLICATION_ATTEMPT_COLUMNS,
    provider_settings_sync_schema_revision,
)
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
REVISION_SMART_PRESENTATION_GENERATION = "f3a7c1d9e5b2"
REVISION_CHAT_TEMPLATE_SCOPE_REPAIR = "f2c4d6e8a0b1"
REVISION_AUTHORED_SLIDE_CHECKPOINTS = "a3c5e7f9b1d2"
REVISION_AUTHORED_RESUME_COUNT = "b4d6f8a1c3e5"
REVISION_AUTHORED_EXACT_PROVENANCE = "c5e7f9a1b3d4"
REVISION_CHAT_FIRST_WORKSPACE = "d6f8a1c3e5b7"
REVISION_ASYNC_PRESENTATION_TASK_TZ = "e7a9c2d4f6b8"
REVISION_DURABLE_DELETION = "f8b0d3e5a7c9"
REVISION_ACCOUNT_DELETION_FENCE = "fa1b2c3d4e5f"
REVISION_SLIDESPEC_ARTIFACT_BUNDLES = "fb2c4d6e8a0b"
REVISION_SLIDESPEC_PUBLICATION_ATTEMPTS = "fc3d5e7f9a1b"
# Repairs a cleanup journal that predates the redrive columns.  It adds no new
# schema of its own, so no `_schema_revision` step detects it: a database is
# stamped at the revision above and this one then tops up whatever is missing.
REVISION_CLEANUP_JOURNAL_REDRIVE = "b3d7f1a9c2e4"
REVISION_PRESENTATION_SEARCH_AND_SHARE_METRICS = "c4e8f0a2b6d9"
REVISION_PROVIDER_SETTINGS_SYNC = "d5a9c1e3f7b2"
REVISION_PRESENTATION_RUNTIME_MERGE = "e6b8d0f2a4c6"
REVISION_REFERENCE_SNAPSHOTS = "f7c9e1a3b5d8"
HEAD_REVISION = REVISION_REFERENCE_SNAPSHOTS
ASYNC_PRESENTATION_TASK_TABLE = "async_presentation_generation_tasks"
ASYNC_PRESENTATION_TASK_TIMESTAMP_COLUMNS = ("created_at", "updated_at")
WORKSPACE_TABLE_COLUMNS = {
    "workspace_conversations": {
        "id",
        "owner_id",
        "owner_scope",
        "presentation_id",
        "next_position",
        "version",
        "event_retention_floor_cursor",
        "created_at",
        "updated_at",
    },
    "workspace_messages": {
        "id", "owner_id", "conversation_id", "position", "role", "content",
        "schema_version", "metadata", "created_at",
    },
    "workspace_assets": {
        "id", "owner_id", "conversation_id", "state", "original_name",
        "media_type", "size_bytes", "storage_key", "sha256", "failure_code",
        "failure_detail", "version", "expires_at", "deleted_at", "created_at",
        "updated_at",
    },
    "workspace_message_attachments": {
        "id", "owner_id", "message_id", "asset_id", "ordinal", "display_name",
        "media_type", "size_bytes", "asset_state", "created_at",
    },
    "workspace_operations": {
        "id", "owner_id", "owner_scope", "conversation_id", "presentation_id",
        "retry_of_operation_id", "anchor_position", "kind", "state",
        "idempotency_key_hash", "request_hash", "request_payload",
        "expected_revision", "result_revision", "approval_expires_at",
        "attempt_number", "attempt_token", "fencing_token", "lease_expires_at",
        "heartbeat_at", "next_attempt_at", "retry_count", "max_attempts",
        "backoff_seconds", "cancel_requested_at", "cancelled_at", "progress",
        "result", "error_code", "error_detail", "version",
        "next_event_sequence", "started_at", "completed_at", "created_at",
        "updated_at",
    },
    "workspace_operation_events": {
        "cursor", "owner_id", "operation_id", "sequence", "operation_version",
        "event_type", "schema_version", "payload", "created_at",
    },
    "workspace_outbox": {
        "id", "owner_id", "owner_scope", "operation_id", "event_cursor",
        "topic", "status", "idempotency_key_hash", "payload_hash", "payload",
        "delivery_attempts", "available_at", "lease_expires_at", "heartbeat_at",
        "delivered_at", "last_error", "created_at", "updated_at",
    },
    "workspace_action_receipts": {
        "id", "owner_id", "owner_scope", "operation_id", "result_operation_id",
        "action_id", "action_type", "status", "idempotency_key_hash",
        "request_hash", "result_hash", "request_payload", "result_payload",
        "source_revision", "result_revision", "created_at", "updated_at",
    },
    "workspace_artifacts": {
        "id",
        "owner_id",
        "operation_id",
        "presentation_id",
        "kind",
        "status",
        "storage_key",
        "sha256",
        "size_bytes",
        "source_revision",
        "expires_at",
        "cleanup_token",
        "cleanup_lease_expires_at",
        "cleanup_attempts",
        "cleanup_error",
        "deleted_at",
        "created_at",
        "updated_at",
    },
}
WORKSPACE_CHECK_CONSTRAINTS = {
    "workspace_conversations": {
        "ck_workspace_conversations_next_position",
        "ck_workspace_conversations_version",
        "ck_workspace_conversations_event_retention_floor_cursor",
    },
    "workspace_messages": {
        "ck_workspace_messages_position",
        "ck_workspace_messages_role",
    },
    "workspace_assets": {
        "ck_workspace_assets_state",
        "ck_workspace_assets_size",
        "ck_workspace_assets_version",
    },
    "workspace_message_attachments": {
        "ck_workspace_message_attachments_ordinal",
        "ck_workspace_message_attachments_asset_state",
    },
    "workspace_operations": {
        "ck_workspace_operations_state",
        "ck_workspace_operations_anchor_position",
        "ck_workspace_operations_attempt_number",
        "ck_workspace_operations_fencing_token",
        "ck_workspace_operations_retry_count",
        "ck_workspace_operations_max_attempts",
        "ck_workspace_operations_version",
        "ck_workspace_operations_next_event_sequence",
    },
    "workspace_operation_events": {
        "ck_workspace_operation_events_sequence",
        "ck_workspace_operation_events_operation_version",
    },
    "workspace_outbox": {
        "ck_workspace_outbox_status",
        "ck_workspace_outbox_delivery_attempts",
    },
    "workspace_action_receipts": {"ck_workspace_action_receipts_status"},
    "workspace_artifacts": {
        "ck_workspace_artifacts_status",
        "ck_workspace_artifacts_size",
        "ck_workspace_artifacts_cleanup_attempts",
    },
}
WORKSPACE_UNIQUE_CONSTRAINTS = {
    "workspace_conversations": {"uq_workspace_conversations_scope_presentation"},
    "workspace_messages": {"uq_workspace_messages_conversation_position"},
    "workspace_assets": {"uq_workspace_assets_storage_key"},
    "workspace_message_attachments": {
        "uq_workspace_message_attachments_asset",
        "uq_workspace_message_attachments_ordinal",
    },
    "workspace_operations": {
        "uq_workspace_operations_conversation_position",
        "uq_workspace_operations_command_idempotency",
        "uq_workspace_operations_retry_of",
    },
    "workspace_operation_events": {
        "uq_workspace_operation_events_operation_sequence"
    },
    "workspace_outbox": {"uq_workspace_outbox_scope_idempotency"},
    "workspace_action_receipts": {
        "uq_workspace_action_receipts_action_idempotency",
        "uq_workspace_action_receipts_operation_action",
    },
    "workspace_artifacts": {"uq_workspace_artifacts_storage_key"},
}
WORKSPACE_INDEXES = {
    "workspace_conversations": {
        "ix_workspace_conversations_owner_presentation_updated"
    },
    "workspace_messages": {
        "ix_workspace_messages_conversation_id",
        "ix_workspace_messages_owner_created",
    },
    "workspace_assets": {
        "ix_workspace_assets_owner_state_updated",
        "ix_workspace_assets_expiry",
    },
    "workspace_message_attachments": {
        "ix_workspace_message_attachments_asset"
    },
    "workspace_operations": {
        "ix_workspace_operations_conversation_id",
        "ix_workspace_operations_presentation_id",
        "ix_workspace_operations_dispatch",
        "ix_workspace_operations_owner_created",
    },
    "workspace_operation_events": {
        "ix_workspace_operation_events_operation_cursor",
        "ix_workspace_operation_events_owner_cursor",
    },
    "workspace_outbox": {"ix_workspace_outbox_delivery"},
    "workspace_action_receipts": {
        "ix_workspace_action_receipts_operation_created"
    },
    "workspace_artifacts": {
        "ix_workspace_artifacts_cleanup",
        "ix_workspace_artifacts_operation",
    },
}
# constrained columns, referred table, ON DELETE action
WORKSPACE_FOREIGN_KEYS = {
    "workspace_conversations": {
        (("owner_id",), "user", "CASCADE"),
        (("presentation_id",), "presentations", "CASCADE"),
    },
    "workspace_messages": {
        (("owner_id",), "user", "CASCADE"),
        (("conversation_id",), "workspace_conversations", "CASCADE"),
    },
    "workspace_assets": {
        (("owner_id",), "user", "CASCADE"),
        (("conversation_id",), "workspace_conversations", "CASCADE"),
    },
    "workspace_message_attachments": {
        (("owner_id",), "user", "CASCADE"),
        (("message_id",), "workspace_messages", "CASCADE"),
        (("asset_id",), "workspace_assets", "RESTRICT"),
    },
    "workspace_operations": {
        (("owner_id",), "user", "CASCADE"),
        (("conversation_id",), "workspace_conversations", "CASCADE"),
        (("presentation_id",), "presentations", "CASCADE"),
        (("retry_of_operation_id",), "workspace_operations", "SET NULL"),
    },
    "workspace_operation_events": {
        (("owner_id",), "user", "CASCADE"),
        (("operation_id",), "workspace_operations", "CASCADE"),
    },
    "workspace_outbox": {
        (("owner_id",), "user", "CASCADE"),
        (("operation_id",), "workspace_operations", "CASCADE"),
        (("event_cursor",), "workspace_operation_events", "CASCADE"),
    },
    "workspace_action_receipts": {
        (("owner_id",), "user", "CASCADE"),
        (("operation_id",), "workspace_operations", "CASCADE"),
        (("result_operation_id",), "workspace_operations", "SET NULL"),
    },
    "workspace_artifacts": {
        (("owner_id",), "user", "CASCADE"),
        (("operation_id",), "workspace_operations", "CASCADE"),
        (("presentation_id",), "presentations", "CASCADE"),
    },
}
DURABLE_DELETION_PRESENTATION_COLUMNS = {
    "deleted_at",
    "deletion_operation_id",
}
DURABLE_ATTACHMENT_SNAPSHOT_COLUMNS = {
    "asset_id_snapshot",
    "asset_sha256",
    "asset_failure_code",
    "asset_failure_detail",
}
WORKSPACE_CLEANUP_JOURNAL_COLUMNS = {
    "id",
    "owner_id_snapshot",
    "presentation_id_snapshot",
    "operation_id_snapshot",
    "storage_namespace",
    "resource_kind",
    "storage_key",
    "sha256",
    "size_bytes",
    "source_revision",
    "resource_metadata",
    "idempotency_key_hash",
    "status",
    "cleanup_token",
    "cleanup_lease_expires_at",
    "next_attempt_at",
    "cleanup_attempts",
    "max_attempts",
    "fencing_token",
    "redrive_count",
    "last_redriven_at",
    "last_redriven_by_snapshot",
    "last_error",
    "deleted_at",
    "created_at",
    "updated_at",
}
WORKSPACE_CLEANUP_JOURNAL_CHECKS = {
    "ck_workspace_cleanup_journal_status",
    "ck_workspace_cleanup_journal_size",
    "ck_workspace_cleanup_journal_attempts",
    "ck_workspace_cleanup_journal_max_attempts",
    "ck_workspace_cleanup_journal_fencing_token",
    "ck_workspace_cleanup_journal_redrive_count",
}
WORKSPACE_CLEANUP_JOURNAL_INDEXES = {
    "ix_workspace_cleanup_journal_dispatch",
    "ix_workspace_cleanup_journal_presentation",
    "ix_workspace_cleanup_journal_owner",
}
ACCOUNT_DELETION_USER_COLUMNS = {
    "deletion_state",
    "deletion_generation",
    "deletion_requested_at",
}
ACCOUNT_DELETION_USER_CHECKS = {
    "ck_user_deletion_state",
    "ck_user_deletion_generation",
    "ck_user_deletion_marker",
}
ACCOUNT_DELETION_USER_INDEXES = {"ix_user_deletion_state_requested_at"}
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


def _foreign_key_signatures(inspector, table: str) -> set[tuple]:
    signatures = {
        (
            tuple(foreign_key.get("constrained_columns") or ()),
            foreign_key.get("referred_table"),
            str((foreign_key.get("options") or {}).get("ondelete") or "").upper(),
        )
        for foreign_key in inspector.get_foreign_keys(table)
    }
    bind = getattr(inspector, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect != "sqlite":
        return signatures

    # SQLAlchemy's SQLite Inspector currently drops ON DELETE metadata for an
    # inline FK added with ALTER TABLE, even though sqlite_master and PRAGMA
    # retain and enforce it. Read the authoritative SQLite catalogue so legacy
    # inference still distinguishes SET NULL from a partially applied NO ACTION
    # bridge. ``table`` is selected only from the static contract above.
    statement = f'PRAGMA foreign_key_list("{table}")'
    if hasattr(bind, "exec_driver_sql"):
        rows = bind.exec_driver_sql(statement).mappings().all()
    else:
        with bind.connect() as connection:
            rows = connection.exec_driver_sql(statement).mappings().all()
    signatures.update(
        {
            (
                (row["from"],),
                row["table"],
                str(row["on_delete"] or "").upper(),
            )
            for row in rows
        }
    )
    return signatures


def _workspace_schema_applied(
    inspector,
    tables: set[str],
    presentation_columns: set[str],
    job_columns: set[str],
) -> bool:
    """Reject partial Gate-B schemas rather than stamping beyond missing DDL."""

    table_markers = set(WORKSPACE_TABLE_COLUMNS) & tables
    column_markers = {
        marker
        for marker, present in (
            ("presentations.revision", "revision" in presentation_columns),
            (
                "presentation_generation_jobs.workspace_operation_id",
                "workspace_operation_id" in job_columns,
            ),
        )
        if present
    }
    if not table_markers and not column_markers:
        return False
    if table_markers != set(WORKSPACE_TABLE_COLUMNS) or len(column_markers) != 2:
        raise RuntimeError("Chat-first workspace schema is only partially applied")
    for table, expected_columns in WORKSPACE_TABLE_COLUMNS.items():
        actual_columns = {
            column["name"] if isinstance(column, dict) else column
            for column in inspector.get_columns(table)
        }
        if not expected_columns.issubset(actual_columns):
            raise RuntimeError(
                f"Chat-first workspace table {table} is only partially applied"
            )
        check_names = {
            constraint.get("name")
            for constraint in inspector.get_check_constraints(table)
        }
        if not WORKSPACE_CHECK_CONSTRAINTS[table].issubset(check_names):
            raise RuntimeError(
                f"Chat-first workspace table {table} is missing check constraints"
            )
        unique_names = {
            constraint.get("name")
            for constraint in inspector.get_unique_constraints(table)
        }
        if not WORKSPACE_UNIQUE_CONSTRAINTS[table].issubset(unique_names):
            raise RuntimeError(
                f"Chat-first workspace table {table} is missing unique constraints"
            )
        index_names = {index.get("name") for index in inspector.get_indexes(table)}
        if not WORKSPACE_INDEXES[table].issubset(index_names):
            raise RuntimeError(
                f"Chat-first workspace table {table} is missing indexes"
            )
        foreign_keys = _foreign_key_signatures(inspector, table)
        expected_foreign_keys = WORKSPACE_FOREIGN_KEYS[table]
        missing_foreign_keys = expected_foreign_keys - foreign_keys
        # f8 replaces the attachment's live RESTRICT pointer with a nullable
        # SET NULL pointer.  The d6 prefix remains complete in either state;
        # the stricter f8 validator below distinguishes them.
        if table == "workspace_message_attachments" and missing_foreign_keys == {
            (("asset_id",), "workspace_assets", "RESTRICT")
        }:
            attachment_columns = {
                column["name"] if isinstance(column, dict) else column
                for column in inspector.get_columns(table)
            }
            if (
                DURABLE_ATTACHMENT_SNAPSHOT_COLUMNS.issubset(
                    attachment_columns
                )
                and (
                    ("asset_id",),
                    "workspace_assets",
                    "SET NULL",
                )
                in foreign_keys
            ):
                missing_foreign_keys = set()
        if missing_foreign_keys:
            raise RuntimeError(
                f"Chat-first workspace table {table} is missing foreign keys"
            )

    presentation_checks = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("presentations")
    }
    if "ck_presentations_revision_nonnegative" not in presentation_checks:
        raise RuntimeError(
            "Chat-first presentations revision constraint is only partially applied"
        )
    job_indexes = {
        index.get("name"): bool(index.get("unique"))
        for index in inspector.get_indexes("presentation_generation_jobs")
    }
    if not job_indexes.get(
        "ix_presentation_generation_jobs_workspace_operation_id"
    ):
        raise RuntimeError(
            "Chat-first generation job operation index is only partially applied"
        )
    job_foreign_keys = _foreign_key_signatures(
        inspector, "presentation_generation_jobs"
    )
    if (
        ("workspace_operation_id",),
        "workspace_operations",
        "SET NULL",
    ) not in job_foreign_keys:
        raise RuntimeError(
            "Chat-first generation job operation foreign key is only partially applied"
        )
    return True


def _account_deletion_schema_revision(
    inspector,
    tables: set[str],
    previous_revision: str,
) -> str:
    """Validate the durable user-deletion marker as an atomic contract."""

    if "user" not in tables:
        return previous_revision
    column_definitions = {
        column["name"]: column for column in inspector.get_columns("user")
    }
    present_columns = ACCOUNT_DELETION_USER_COLUMNS & set(column_definitions)
    check_names = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("user")
    }
    index_definitions = {
        index.get("name"): index for index in inspector.get_indexes("user")
    }
    present_checks = ACCOUNT_DELETION_USER_CHECKS & check_names
    present_indexes = ACCOUNT_DELETION_USER_INDEXES & set(index_definitions)
    if not present_columns and not present_checks and not present_indexes:
        return previous_revision
    if previous_revision != REVISION_DURABLE_DELETION:
        raise RuntimeError(
            "Account deletion fence exists without durable cleanup prerequisites"
        )
    if present_columns != ACCOUNT_DELETION_USER_COLUMNS:
        raise RuntimeError("Account deletion fence columns are only partially applied")

    for column_name, expected_nullable in (
        ("deletion_state", False),
        ("deletion_generation", False),
        ("deletion_requested_at", True),
    ):
        nullable = column_definitions[column_name].get("nullable")
        if nullable is not expected_nullable:
            raise RuntimeError(
                "Account deletion fence column "
                f"{column_name} has incompatible nullability"
            )
    if present_checks != ACCOUNT_DELETION_USER_CHECKS:
        raise RuntimeError("Account deletion fence checks are incomplete")
    if present_indexes != ACCOUNT_DELETION_USER_INDEXES:
        raise RuntimeError("Account deletion fence index is incomplete")
    deletion_index = index_definitions["ix_user_deletion_state_requested_at"]
    if tuple(deletion_index.get("column_names") or ()) != (
        "deletion_state",
        "deletion_requested_at",
    ) or bool(deletion_index.get("unique")):
        raise RuntimeError("Account deletion fence index is incompatible")
    return _slidespec_schema_revision(
        inspector,
        tables,
        REVISION_ACCOUNT_DELETION_FENCE,
    )


def _slidespec_schema_revision(
    inspector,
    tables: set[str],
    previous_revision: str,
) -> str:
    """Recognize only complete SlideSpec persistence revisions.

    These tables hold activation evidence, so a partial schema must never be
    stamped past a migration that can repair it.
    """

    artifact_table = "slidespec_artifact_bundles"
    publication_table = "slidespec_publication_attempts"
    has_artifacts = artifact_table in tables
    has_publications = publication_table in tables
    if not has_artifacts and not has_publications:
        return previous_revision
    if has_publications and not has_artifacts:
        raise RuntimeError(
            "SlideSpec publication attempts exist without artifact bundles"
        )
    if previous_revision != REVISION_ACCOUNT_DELETION_FENCE:
        raise RuntimeError(
            "SlideSpec artifact schema exists without persistence prerequisites"
        )

    artifact_columns = {
        column["name"] for column in inspector.get_columns(artifact_table)
    }
    if artifact_columns != SLIDESPEC_ARTIFACT_BUNDLE_COLUMNS:
        raise RuntimeError(
            "SlideSpec artifact bundle schema is only partially applied"
        )
    artifact_indexes = {
        index.get("name"): index for index in inspector.get_indexes(artifact_table)
    }
    artifact_index = artifact_indexes.get(
        "ix_slidespec_artifact_bundles_presentation_slide_created"
    )
    if artifact_index is None or tuple(
        artifact_index.get("column_names") or ()
    ) != ("presentation_id", "slide_index", "created_at") or bool(
        artifact_index.get("unique")
    ):
        raise RuntimeError("SlideSpec artifact bundle index is incompatible")
    if not has_publications:
        return REVISION_SLIDESPEC_ARTIFACT_BUNDLES

    publication_columns = {
        column["name"] for column in inspector.get_columns(publication_table)
    }
    if publication_columns != SLIDESPEC_PUBLICATION_ATTEMPT_COLUMNS:
        raise RuntimeError(
            "SlideSpec publication attempt schema is only partially applied"
        )
    publication_indexes = {
        index.get("name"): index
        for index in inspector.get_indexes(publication_table)
    }
    publication_index = publication_indexes.get(
        "ix_slidespec_publication_attempts_slide_created"
    )
    if publication_index is None or tuple(
        publication_index.get("column_names") or ()
    ) != ("slide_id", "created_at") or bool(publication_index.get("unique")):
        raise RuntimeError("SlideSpec publication attempt index is incompatible")
    provider_revision = provider_settings_sync_schema_revision(
        inspector,
        tables,
        REVISION_SLIDESPEC_PUBLICATION_ATTEMPTS,
        prerequisite_revision=REVISION_SLIDESPEC_PUBLICATION_ATTEMPTS,
        applied_revision=REVISION_PROVIDER_SETTINGS_SYNC,
    )
    if provider_revision != REVISION_PROVIDER_SETTINGS_SYNC:
        return provider_revision

    presentation_columns = {
        column["name"] for column in inspector.get_columns("presentations")
    }
    smart_columns = {"generation_mode", "community_design_ids"}
    # The two former branch heads can be recognized as the joined head only
    # after the merge repair itself has run. If any marker is absent, stamp the
    # provider-settings branch and let Alembic idempotently apply/repair the
    # Smart branch plus merge revision instead of skipping physical DDL.
    if smart_columns.issubset(presentation_columns) and "fonts" in presentation_columns:
        if "reference_snapshots" not in tables:
            return REVISION_PRESENTATION_RUNTIME_MERGE
        snapshot_columns = {
            column["name"]
            for column in inspector.get_columns("reference_snapshots")
        }
        required_snapshot_columns = {
            "id",
            "owner_id",
            "conversation_id",
            "provider_id",
            "source_revision",
            "source_fingerprint",
            "normalized_payload",
            "asset_bindings",
            "content_sha256",
            "schema_version",
            "expires_at",
            "created_at",
        }
        if not required_snapshot_columns.issubset(snapshot_columns):
            raise RuntimeError("Reference snapshot schema is incompatible")
        return HEAD_REVISION
    return provider_revision


def _durable_deletion_schema_revision(
    inspector,
    tables: set[str],
    presentation_columns: set[str],
    previous_revision: str,
) -> str:
    """Validate f8 as one atomic physical contract or return its predecessor."""

    attachment_table = "workspace_message_attachments"
    cleanup_table = "workspace_cleanup_journal"
    attachment_columns = (
        {
            column["name"] if isinstance(column, dict) else column
            for column in inspector.get_columns(attachment_table)
        }
        if attachment_table in tables
        else set()
    )
    present_presentation_markers = (
        DURABLE_DELETION_PRESENTATION_COLUMNS & presentation_columns
    )
    present_attachment_markers = (
        DURABLE_ATTACHMENT_SNAPSHOT_COLUMNS & attachment_columns
    )
    has_cleanup_table = cleanup_table in tables
    if (
        not present_presentation_markers
        and not present_attachment_markers
        and not has_cleanup_table
    ):
        return _account_deletion_schema_revision(
            inspector,
            tables,
            previous_revision,
        )

    if present_presentation_markers != DURABLE_DELETION_PRESENTATION_COLUMNS:
        raise RuntimeError(
            "Durable presentation deletion schema is only partially applied"
        )
    if present_attachment_markers != DURABLE_ATTACHMENT_SNAPSHOT_COLUMNS:
        raise RuntimeError(
            "Immutable attachment snapshot schema is only partially applied"
        )
    if not has_cleanup_table:
        raise RuntimeError("Workspace cleanup journal schema is missing")

    attachment_column_definitions = {
        column["name"]: column
        for column in inspector.get_columns(attachment_table)
    }
    if attachment_column_definitions["asset_id"].get("nullable") is not True:
        raise RuntimeError("Workspace attachment live asset pointer must be nullable")
    if attachment_column_definitions["asset_id_snapshot"].get("nullable") is not False:
        raise RuntimeError("Workspace attachment asset snapshot must be non-null")
    attachment_foreign_keys = _foreign_key_signatures(
        inspector, attachment_table
    )
    if (
        (("asset_id",), "workspace_assets", "SET NULL")
        not in attachment_foreign_keys
    ):
        raise RuntimeError(
            "Workspace attachment live asset pointer must use ON DELETE SET NULL"
        )
    attachment_uniques = {
        constraint.get("name"): tuple(
            constraint.get("column_names")
            or constraint.get("constrained_columns")
            or ()
        )
        for constraint in inspector.get_unique_constraints(attachment_table)
    }
    if attachment_uniques.get("uq_workspace_message_attachments_asset") != (
        "message_id",
        "asset_id_snapshot",
    ):
        raise RuntimeError(
            "Workspace attachment snapshot uniqueness is only partially applied"
        )

    cleanup_columns = {
        column["name"] if isinstance(column, dict) else column
        for column in inspector.get_columns(cleanup_table)
    }
    if not WORKSPACE_CLEANUP_JOURNAL_COLUMNS.issubset(cleanup_columns):
        raise RuntimeError("Workspace cleanup journal columns are incomplete")
    cleanup_checks = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints(cleanup_table)
    }
    if not WORKSPACE_CLEANUP_JOURNAL_CHECKS.issubset(cleanup_checks):
        raise RuntimeError("Workspace cleanup journal checks are incomplete")
    cleanup_uniques = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints(cleanup_table)
    }
    if "uq_workspace_cleanup_journal_idempotency" not in cleanup_uniques:
        raise RuntimeError("Workspace cleanup journal idempotency is missing")
    cleanup_indexes = {
        index.get("name") for index in inspector.get_indexes(cleanup_table)
    }
    if not WORKSPACE_CLEANUP_JOURNAL_INDEXES.issubset(cleanup_indexes):
        raise RuntimeError("Workspace cleanup journal indexes are incomplete")
    if inspector.get_foreign_keys(cleanup_table):
        raise RuntimeError(
            "Workspace cleanup journal snapshot identifiers must not have foreign keys"
        )
    return _account_deletion_schema_revision(
        inspector,
        tables,
        REVISION_DURABLE_DELETION,
    )


def _async_task_timestamp_revision(
    inspector,
    tables: set[str],
) -> str:
    """Infer e7 only from a complete, uniform PostgreSQL timestamp state.

    SQLite has no physical e7 marker and inspectors without a live bind are
    used only by lightweight unit fixtures, so both conservatively remain at
    d6 and let Alembic execute the storage no-op revision.
    """

    bind = getattr(inspector, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect_name is None:
        return REVISION_CHAT_FIRST_WORKSPACE
    if dialect_name == "sqlite":
        return REVISION_CHAT_FIRST_WORKSPACE
    if dialect_name != "postgresql":
        raise RuntimeError(
            "Async presentation task timestamp inference supports only "
            "SQLite and PostgreSQL"
        )
    if ASYNC_PRESENTATION_TASK_TABLE not in tables:
        # e7 intentionally has no DDL when the optional legacy task table is
        # absent, so there is no unapplied physical state to recover.
        return REVISION_ASYNC_PRESENTATION_TASK_TZ

    columns = {
        column["name"]: column
        for column in inspector.get_columns(ASYNC_PRESENTATION_TASK_TABLE)
        if column["name"] in ASYNC_PRESENTATION_TASK_TIMESTAMP_COLUMNS
    }
    if set(columns) != set(ASYNC_PRESENTATION_TASK_TIMESTAMP_COLUMNS):
        raise RuntimeError(
            "Async presentation task timestamp schema is incomplete"
        )

    awareness: list[bool] = []
    for name in ASYNC_PRESENTATION_TASK_TIMESTAMP_COLUMNS:
        column_type = columns[name].get("type")
        if not isinstance(column_type, DateTime):
            raise RuntimeError(
                f"{ASYNC_PRESENTATION_TASK_TABLE}.{name} is not a timestamp"
            )
        awareness.append(bool(column_type.timezone))

    if all(awareness):
        return REVISION_ASYNC_PRESENTATION_TASK_TZ
    if not any(awareness):
        return REVISION_CHAT_FIRST_WORKSPACE
    raise RuntimeError(
        "Async presentation task timestamp timezone schema is only "
        "partially applied"
    )


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
        with engine.begin() as connection:
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
            repaired = connection.execute(
                text(
                    "UPDATE alembic_version SET version_num = :target "
                    "WHERE version_num = :current"
                ),
                {"target": target, "current": version_num},
            )
            if repaired.rowcount != 1:
                raise RuntimeError(
                    "Alembic orphan revision changed during schema repair"
                )
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
                authored_revision = (
                    REVISION_AUTHORED_EXACT_PROVENANCE
                    if "authored_exact_provenance" in tables
                    else REVISION_AUTHORED_RESUME_COUNT
                )
                if authored_revision == REVISION_AUTHORED_EXACT_PROVENANCE:
                    if _workspace_schema_applied(
                        inspector,
                        tables,
                        presentation_columns,
                        job_columns,
                    ):
                        return _durable_deletion_schema_revision(
                            inspector,
                            tables,
                            presentation_columns,
                            _async_task_timestamp_revision(
                                inspector,
                                tables,
                            ),
                        )
                    return authored_revision
                return authored_revision
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
            authored_revision = (
                REVISION_AUTHORED_EXACT_PROVENANCE
                if "authored_exact_provenance" in tables
                else REVISION_AUTHORED_RESUME_COUNT
            )
            if authored_revision == REVISION_AUTHORED_EXACT_PROVENANCE:
                if _workspace_schema_applied(
                    inspector,
                    tables,
                    presentation_columns,
                    job_columns,
                ):
                    return _durable_deletion_schema_revision(
                        inspector,
                        tables,
                        presentation_columns,
                        _async_task_timestamp_revision(
                            inspector,
                            tables,
                        ),
                    )
                return authored_revision
            return authored_revision
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
