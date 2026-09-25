"""The one synchronous view of the async database.

A few subsystems are synchronous by design (the authored-vNext repository's
database-native CAS runs in ``asyncio.to_thread``).  They still have to talk
to *this* database, not to whatever URL the environment happens to resolve at
the moment they are called: resolving independently produced a third
connection that saw a different SQLite file than ``sql_engine`` whenever the
engine was rebound (tests, embedded harnesses), which surfaced as
``no such table: user`` in the middle of an otherwise complete generation.

So the sync engine is derived from ``services.database.sql_engine`` rather
than from the environment, and exactly one is held at a time: rebinding
``sql_engine`` to a different URL disposes the previous sync engine and
builds the matching one. Sessions come from :func:`sync_session_maker`; the
owner/tenant scoping hooks are installed on ``sqlalchemy.orm.Session`` itself
(see ``services.database_owner_scope.register_owner_scope_hooks``), so they
apply to these sessions too.

Split out of ``services/database.py`` (see ``docs/file-size-policy.md``);
``get_sync_engine`` and ``sync_session_maker`` stay importable from there.
``services.database`` is imported lazily inside the functions below (rather
than at module scope) so this module can be imported before ``database.py``
finishes defining ``sql_engine`` without a circular-import failure.
"""
from __future__ import annotations

import threading

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker

from utils.db_utils import get_pool_kwargs, to_sync_sqlalchemy_url


_sync_engine_lock = threading.Lock()
_sync_engine_state: dict[str, object] = {"url": None, "engine": None, "maker": None}


def _sync_database_url() -> str:
    from services.database import sql_engine

    return to_sync_sqlalchemy_url(sql_engine.url.render_as_string(hide_password=False))


def _build_sync_engine(sync_url: str) -> Engine:
    if "sqlite" in sync_url:
        engine = create_engine(
            sync_url,
            connect_args={"check_same_thread": False, "timeout": 30.0},
        )
        event.listen(engine, "connect", _configure_sqlite_sync_connection)
        return engine
    return create_engine(sync_url, **get_pool_kwargs())


def _configure_sqlite_sync_connection(dbapi_connection, _connection_record) -> None:
    from services.database import _configure_sqlite_connection

    _configure_sqlite_connection(dbapi_connection)


def _current_sync_state() -> dict[str, object]:
    sync_url = _sync_database_url()
    with _sync_engine_lock:
        if _sync_engine_state["url"] != sync_url:
            previous = _sync_engine_state["engine"]
            engine = _build_sync_engine(sync_url)
            _sync_engine_state.update(
                url=sync_url,
                engine=engine,
                maker=sessionmaker(engine, expire_on_commit=False),
            )
            if previous is not None:
                # Only reached when ``sql_engine`` itself was rebound, so no
                # caller can still hold a session on the old database.
                previous.dispose()
        return dict(_sync_engine_state)


def get_sync_engine() -> Engine:
    """The synchronous engine for the database ``sql_engine`` is bound to."""

    return _current_sync_state()["engine"]


def sync_session_maker() -> sessionmaker:
    """Session factory for the synchronous view of the configured database."""

    return _current_sync_state()["maker"]


__all__ = [
    "get_sync_engine",
    "sync_session_maker",
]
