import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from utils.runtime_settings import (
    get_published_process_runtime_settings,
    replace_process_runtime_settings,
)


@pytest.fixture(scope="session", autouse=True)
def process_database_schema(tmp_path_factory):
    """Give implicit process-DB users a private, initialized test database.

    Never create tables in the configured workstation/CI database. Independent
    engines (including empty migration databases) remain owned by their tests.
    Keep the original maker object so captured function defaults are isolated too.
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel

    from services import database

    path = tmp_path_factory.mktemp("process-database") / "process.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(connection, _record):
        database._configure_sqlite_connection(connection)

    original_engine = database.sql_engine
    maker = database.async_session_maker
    original_bind = maker.kw.get("bind")

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    with pytest.MonkeyPatch.context() as patch:
        # Imports may have captured the process engine during collection. Match
        # by identity so tests' own engines and unrelated bindings stay intact.
        for module in list(sys.modules.values()):
            if module is None:
                continue
            for name, value in list(vars(module).items()):
                if value is original_engine:
                    patch.setattr(module, name, engine)
        maker.configure(bind=engine)
        try:
            yield engine
        finally:
            maker.configure(bind=original_bind)
            asyncio.run(engine.dispose())


class FakeAsyncSession:
    def __init__(
        self,
        get_results: dict[Any, Any] | None = None,
        scalar_result: Any = None,
    ):
        self._get_results = get_results or {}
        # Single answer for `session.scalar(...)`; enough for admission lookups
        # that only ask whether one row exists.
        self._scalar_result = scalar_result
        self.added: list[Any] = []
        self.added_all: list[Any] = []
        self.deleted: list[Any] = []
        self.commit_count = 0
        self.rollback_count = 0

    async def get(self, _model: Any, key: Any):
        return self._get_results.get(key)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: list[Any]) -> None:
        self.added_all.extend(list(objs))

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def refresh(self, _obj: Any) -> None:
        return None

    async def execute(self, *_args: Any, **_kwargs: Any):
        class _EmptyResult:
            # Default fake UPDATE/DELETE execution models one successful row.
            # Tests that need a conflict should provide a purpose-built result.
            rowcount = 1

            def scalars(self):
                return self

            def all(self):
                return []

            def __iter__(self):
                return iter([])

        return _EmptyResult()

    async def scalar(self, *_args: Any, **_kwargs: Any):
        return self._scalar_result

    async def scalars(self, *_args: Any, **_kwargs: Any):
        return []


@pytest.fixture(autouse=True)
def isolate_published_runtime_settings():
    """Keep process-level config snapshots from leaking between unit tests."""
    previous = get_published_process_runtime_settings()
    try:
        yield
    finally:
        replace_process_runtime_settings(previous)


@pytest.fixture
def fake_async_session() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def snapshots_dir() -> Path:
    return Path(__file__).parent / "regression" / "snapshots"


@pytest.fixture
def load_snapshot(snapshots_dir: Path):
    def _load(name: str) -> dict[str, Any]:
        path = snapshots_dir / name
        return json.loads(path.read_text(encoding="utf-8"))

    return _load
