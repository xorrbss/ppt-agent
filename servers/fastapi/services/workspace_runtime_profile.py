from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Any

from sqlalchemy.engine import URL, make_url


class WorkspaceRuntimeKind(str, Enum):
    SQLITE_LOCAL = "sqlite-local"
    POSTGRESQL_CLUSTER = "postgresql-cluster"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class WorkspaceRuntimeProfile:
    dialect: str
    kind: WorkspaceRuntimeKind
    supported: bool


@dataclass(frozen=True)
class ChatFirstWorkspaceRollout:
    """One authoritative interpretation of rollout flag and SQL runtime intent.

    ``reason`` describes why commands are unavailable.  Runtime declaration
    validation is intentionally skipped while the feature flag is off so an
    operator can roll back to the legacy paths even if a stale declaration is
    present.  ``runtime_error`` remains available for guarding read-only
    workspace tables on an unsupported dialect.
    """

    requested: bool
    enabled: bool
    profile: WorkspaceRuntimeProfile
    reason: str | None
    runtime_error: str | None

    @property
    def workspace_reads_supported(self) -> bool:
        # Timeline/history/artifact reads depend only on the physical runtime.
        # A missing or mismatched *write* declaration must not hide durable
        # workspace state during a rollout rollback or configuration incident.
        return self.profile.supported


class UnsupportedWorkspaceRuntimeError(RuntimeError):
    def __init__(self, code: str, dialect: str, declared_profile: str | None = None):
        self.code = code
        self.dialect = dialect
        self.declared_profile = declared_profile
        super().__init__(code)


CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE = "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"
CHAT_FIRST_FEATURE_DISABLED = "chat_first_feature_disabled"


def _dialect_name(database_url_or_dialect: Any) -> str:
    if isinstance(database_url_or_dialect, URL):
        return database_url_or_dialect.get_backend_name().lower()
    if hasattr(database_url_or_dialect, "dialect"):
        return str(database_url_or_dialect.dialect.name).lower()
    value = str(database_url_or_dialect or "").strip().lower()
    if "://" in value:
        return make_url(value).get_backend_name().lower()
    return value.split("+", 1)[0]


def resolve_workspace_runtime_profile(
    database_url_or_dialect: Any,
) -> WorkspaceRuntimeProfile:
    if isinstance(database_url_or_dialect, WorkspaceRuntimeProfile):
        return database_url_or_dialect

    dialect = _dialect_name(database_url_or_dialect)
    if dialect == "sqlite":
        return WorkspaceRuntimeProfile(
            dialect=dialect,
            kind=WorkspaceRuntimeKind.SQLITE_LOCAL,
            supported=True,
        )
    if dialect in {"postgres", "postgresql"}:
        return WorkspaceRuntimeProfile(
            dialect="postgresql",
            kind=WorkspaceRuntimeKind.POSTGRESQL_CLUSTER,
            supported=True,
        )
    return WorkspaceRuntimeProfile(
        dialect=dialect or "unknown",
        kind=WorkspaceRuntimeKind.UNSUPPORTED,
        supported=False,
    )


def require_chat_first_runtime(
    feature_enabled: bool,
    database_url_or_dialect: Any,
    declared_profile: str | None = None,
) -> WorkspaceRuntimeProfile:
    profile = resolve_workspace_runtime_profile(database_url_or_dialect)
    if not feature_enabled:
        return profile
    if not profile.supported:
        raise UnsupportedWorkspaceRuntimeError(
            "chat_first_runtime_unsupported", profile.dialect
        )

    raw_declared = (
        declared_profile
        if declared_profile is not None
        else os.getenv(CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE)
    )
    normalized_declared = str(raw_declared or "").strip().lower()
    if not normalized_declared:
        raise UnsupportedWorkspaceRuntimeError(
            "chat_first_runtime_profile_required", profile.dialect
        )
    try:
        declared_kind = WorkspaceRuntimeKind(normalized_declared)
    except ValueError as error:
        raise UnsupportedWorkspaceRuntimeError(
            "chat_first_runtime_profile_invalid",
            profile.dialect,
            normalized_declared,
        ) from error
    if declared_kind is WorkspaceRuntimeKind.UNSUPPORTED:
        raise UnsupportedWorkspaceRuntimeError(
            "chat_first_runtime_profile_invalid",
            profile.dialect,
            normalized_declared,
        )
    if declared_kind is not profile.kind:
        raise UnsupportedWorkspaceRuntimeError(
            "chat_first_runtime_profile_mismatch",
            profile.dialect,
            normalized_declared,
        )
    return profile


def resolve_chat_first_workspace_rollout(
    feature_enabled: bool,
    database_url_or_dialect: Any,
    declared_profile: str | None = None,
) -> ChatFirstWorkspaceRollout:
    """Resolve the effective Chat-first state without mutating application state."""

    requested = bool(feature_enabled)
    profile = resolve_workspace_runtime_profile(database_url_or_dialect)
    if not requested:
        return ChatFirstWorkspaceRollout(
            requested=False,
            enabled=False,
            profile=profile,
            reason=CHAT_FIRST_FEATURE_DISABLED,
            runtime_error=(
                None if profile.supported else "chat_first_runtime_unsupported"
            ),
        )
    try:
        profile = require_chat_first_runtime(
            True,
            database_url_or_dialect,
            declared_profile=declared_profile,
        )
    except UnsupportedWorkspaceRuntimeError as error:
        return ChatFirstWorkspaceRollout(
            requested=True,
            enabled=False,
            profile=profile,
            reason=error.code,
            runtime_error=error.code,
        )
    return ChatFirstWorkspaceRollout(
        requested=True,
        enabled=True,
        profile=profile,
        reason=None,
        runtime_error=None,
    )


def validate_chat_first_workspace_rollout(
    feature_enabled: bool,
    database_url_or_dialect: Any,
    declared_profile: str | None = None,
) -> ChatFirstWorkspaceRollout:
    """Validate runtime intent when requested while allowing explicit rollback."""

    rollout = resolve_chat_first_workspace_rollout(
        feature_enabled,
        database_url_or_dialect,
        declared_profile=declared_profile,
    )
    if rollout.requested and not rollout.enabled:
        raise UnsupportedWorkspaceRuntimeError(
            rollout.reason or "chat_first_runtime_unsupported",
            rollout.profile.dialect,
            declared_profile,
        )
    return rollout
