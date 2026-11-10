"""Stable SQL/CAS repository facade for authored-vNext composition."""

from __future__ import annotations

from functools import wraps
import time
from typing import Any, Callable
import uuid

from sqlalchemy import Engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from models.sql.authored_vnext_repository import (
    AUTHORED_VNEXT_SQL_TABLES,
    AuthoredVNextEntityHead,
)
from models.sql.user import User

from .sql_repository_composition import AuthoredVNextCompositionRepositoryMixin
from .sql_repository_types import (
    ExportJobConflictError,
    ExportableDeck,
    OperationFenceError,
    validate_exportable_deck_payload,
)


SQLITE_LOCK_RETRY_DELAYS = (0.025, 0.075, 0.15)


def _is_sqlite_lock(error: OperationalError, dialect_name: str) -> bool:
    if dialect_name != "sqlite":
        return False
    original = error.orig
    sqlite_error_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_error_code, int) and sqlite_error_code & 0xFF in {5, 6}:
        return True
    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message


def sqlite_write_retry(
    engine_of: Callable[[Any], Engine],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Build a method decorator that retries only transient SQLite writer locks.

    ``engine_of(self)`` returns the engine whose dialect decides whether an
    ``OperationalError`` is a lock. Lock recovery is bounded and never consumes
    an API attempt; any other error, or the last lock, re-raises.
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(method)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            for attempt in range(len(SQLITE_LOCK_RETRY_DELAYS) + 1):
                try:
                    return method(self, *args, **kwargs)
                except OperationalError as error:
                    if (
                        not _is_sqlite_lock(error, engine_of(self).dialect.name)
                        or attempt == len(SQLITE_LOCK_RETRY_DELAYS)
                    ):
                        raise
                    time.sleep(SQLITE_LOCK_RETRY_DELAYS[attempt])
            raise AssertionError("unreachable sqlite retry state")

        return wrapped

    return decorator


_retry_sqlite_write = sqlite_write_retry(lambda repository: repository.engine)


class AuthoredVNextSqlRepository(AuthoredVNextCompositionRepositoryMixin):
    """Owner/tenant scoped synchronous repository with database-native CAS."""

    def __init__(
        self,
        engine: Engine,
        *,
        owner_scope: str,
        tenant_id: str,
        owner_id: uuid.UUID | None = None,
        sessions: sessionmaker | None = None,
    ) -> None:
        if not owner_scope.strip() or not tenant_id.strip():
            raise ValueError("owner_scope and tenant_id are required")
        if owner_id is None:
            raise ValueError("owner_id is required for authored vNext persistence")
        if owner_scope != str(owner_id):
            raise ValueError("owner_scope must equal the canonical owner_id")
        self.engine = engine
        self.owner_scope = owner_scope
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self._sessions = sessions or sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_application_database(
        cls,
        *,
        owner_scope: str,
        tenant_id: str,
        owner_id: uuid.UUID | None = None,
    ) -> "AuthoredVNextSqlRepository":
        """Bind to the process's one configured database.

        This used to resolve its own URL from the environment and build a
        private engine.  That made it a third connection: whenever the
        application engine was bound to something other than the environment's
        default -- an embedded harness, an integration test, any caller that
        rebinds ``services.database.sql_engine`` -- this repository silently
        wrote to a different database, and the publication step at the end of a
        fully generated deck failed against an empty schema.  The synchronous
        view of ``services.database.sql_engine`` is the only correct target, and
        it carries the same owner/tenant ORM hooks because those are registered
        on ``sqlalchemy.orm.Session`` itself.
        """

        from services.database import get_sync_engine, sync_session_maker

        return cls(
            get_sync_engine(),
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            owner_id=owner_id,
            sessions=sync_session_maker(),
        )

    @staticmethod
    def create_schema(engine: Engine) -> None:
        """Test/dev helper; production must use the Alembic migration."""
        for table in AUTHORED_VNEXT_SQL_TABLES:
            table.create(engine, checkfirst=True)

    def _scope(self, model: type[Any]) -> tuple[Any, ...]:
        return (
            model.owner_id == self.owner_id,
            model.owner_scope == self.owner_scope,
            model.tenant_id == self.tenant_id,
        )

    def _head(
        self, session: Session, kind: str, entity_id: str
    ) -> AuthoredVNextEntityHead | None:
        return session.scalar(
            select(AuthoredVNextEntityHead).where(
                *self._scope(AuthoredVNextEntityHead),
                AuthoredVNextEntityHead.entity_kind == kind,
                AuthoredVNextEntityHead.entity_id == entity_id,
            )
        )

    def _active_account_generation(
        self,
        session: Session,
        *,
        expected_generation: int | None = None,
    ) -> int:
        predicates = [
            User.id == self.owner_id,
            User.is_active.is_(True),
            User.deletion_state == "active",
        ]
        if expected_generation is not None:
            predicates.append(User.deletion_generation == expected_generation)
        generation = session.scalar(
            select(User.deletion_generation).where(*predicates).with_for_update()
        )
        if generation is None:
            raise OperationFenceError()
        return int(generation)

    # The mixin keeps each mutation in its own transaction. Retrying here means
    # SQLite writer contention is resolved inside the original user operation.
    @_retry_sqlite_write
    def append_master(self, *args: Any, **kwargs: Any) -> None:
        return super().append_master(*args, **kwargs)

    @_retry_sqlite_write
    def append_deck(self, *args: Any, **kwargs: Any) -> None:
        return super().append_deck(*args, **kwargs)


__all__ = [
    "AuthoredVNextSqlRepository",
    "ExportJobConflictError",
    "ExportableDeck",
    "OperationFenceError",
    "validate_exportable_deck_payload",
]

