"""Physical-runtime admission for the workspace operation store.

The workspace owns every presentation mutation, so this module answers one
question: can the configured SQL runtime host the operation store safely?  It
is a correctness guard on the dialect, not a rollout control - there is no
legacy path left to fall back to.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


class UnsupportedWorkspaceRuntimeError(RuntimeError):
    def __init__(self, code: str, dialect: str):
        self.code = code
        self.dialect = dialect
        super().__init__(code)


# Kept as the published error code for an unusable SQL runtime. It is part of
# the v1 workspace error contract, so it outlives the rollout it was named for.
WORKSPACE_RUNTIME_UNSUPPORTED = "chat_first_runtime_unsupported"


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


def require_supported_workspace_runtime(
    database_url_or_dialect: Any,
) -> WorkspaceRuntimeProfile:
    """Raise unless the configured dialect can host the operation store."""

    profile = resolve_workspace_runtime_profile(database_url_or_dialect)
    if not profile.supported:
        raise UnsupportedWorkspaceRuntimeError(
            WORKSPACE_RUNTIME_UNSUPPORTED, profile.dialect
        )
    return profile
