"""Concurrent holders of one content-addressed artifact key.

The CAS key is ``owner + sha256``: two decks of the same owner that crop the
same pixels share it. Reference holders (writers, registrars, verifiers) must
not exclude each other; only lifecycle transitions and physical delete are
exclusive. Regression for the int1 run, where deck1's analysis persist held
the key while deck2's generation admission verified its crops and got
``artifact.cas_key_busy`` (surfaced as a generic HTTP 500).
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from hashlib import sha256
import io
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.artifact_store import (
    DurableArtifactService,
    LocalDevelopmentArtifactObjectStore,
    require_artifact_key_guard,
    try_artifact_key_guard,
)

PAYLOAD = b"\x89PNG shared crop bytes"


def _ref(owner_id: uuid.UUID) -> ArtifactRef:
    digest = sha256(PAYLOAD).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="default",
        kind="image-derivative",
        sha256=digest,
        size_bytes=len(PAYLOAD),
        media_type="image/png",
        storage_key=artifact_storage_key(owner_id=owner_id, tenant_id="default", digest=digest),
        created_by_operation_id=uuid.uuid4(),
        retention_class="checkpoint",
    )


class AdvisoryLockTable:
    """PostgreSQL transaction advisory-lock compatibility, per session."""

    def __init__(self) -> None:
        self.held: dict[int, dict[int, str]] = {}

    def try_lock(self, owner: int, key: int, *, shared: bool) -> bool:
        holders = self.held.setdefault(key, {})
        others = [mode for holder, mode in holders.items() if holder != owner]
        if any(mode == "exclusive" for mode in others) or (others and not shared):
            return False
        if holders.get(owner) != "exclusive":
            holders[owner] = "shared" if shared else "exclusive"
        return True

    def release(self, owner: int) -> None:
        for holders in self.held.values():
            holders.pop(owner, None)


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class PostgresSessionDouble:
    """Only the SQL boundary register_ref/persist_stream touch; not a DB."""

    def __init__(self, locks: AdvisoryLockTable) -> None:
        self.locks = locks
        self.no_autoflush = nullcontext()
        self.rows: list[object] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def scalar(self, statement, params=None):
        sql = str(statement)
        if "transaction_isolation" in sql:
            return "read committed"
        if "pg_try_advisory_xact_lock" in sql:
            return self.locks.try_lock(
                id(self), params["artifact_cas_key"], shared="_shared(" in sql,
            )
        raise AssertionError(f"unexpected statement: {sql}")

    def begin_nested(self) -> _Nested:
        return _Nested()

    def add(self, row: object) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.locks.release(id(self))


class GatedStore(LocalDevelopmentArtifactObjectStore):
    """Parks the first caller of ``gated`` while it holds the key guard."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._gate_used = False

    async def _gate(self) -> None:
        if not self._gate_used:
            self._gate_used = True
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), timeout=5)

    async def exists(self, artifact) -> bool:
        await self._gate()
        return await super().exists(artifact)


def test_two_registrations_of_one_cas_key_both_succeed(tmp_path: Path) -> None:
    async def scenario() -> None:
        owner_id = uuid.uuid4()
        store = GatedStore(tmp_path / "objects")
        first, second = _ref(owner_id), _ref(owner_id)
        assert first.storage_key == second.storage_key
        await LocalDevelopmentArtifactObjectStore(tmp_path / "objects").put_stream(first, io.BytesIO(PAYLOAD))
        service = DurableArtifactService(store)
        locks = AdvisoryLockTable()
        writer, verifier, collector = (PostgresSessionDouble(locks) for _ in range(3))

        in_flight = asyncio.create_task(service.register_ref(writer, first))
        await asyncio.wait_for(store.entered.wait(), timeout=5)
        # A concurrent owner of the same bytes neither fails nor waits.
        assert await asyncio.wait_for(service.register_ref(verifier, second), timeout=5) == second
        # Physical delete stays excluded while any holder is in flight.
        assert not await try_artifact_key_guard(collector, first, for_physical_delete=True)
        store.release.set()
        assert await in_flight == first
        await writer.commit()
        assert not await try_artifact_key_guard(collector, first, for_physical_delete=True)
        await verifier.commit()
        assert await try_artifact_key_guard(collector, first, for_physical_delete=True)
        # And a holder cannot enter while delete holds the key exclusively.
        late = PostgresSessionDouble(locks)
        assert not await try_artifact_key_guard(late, first, shared=True)
        assert [row.storage_key for row in writer.rows + verifier.rows] == [first.storage_key] * 2

    asyncio.run(scenario())


def test_two_concurrent_writers_of_the_same_bytes_both_succeed(tmp_path: Path) -> None:
    class GatedCommitStore(GatedStore):
        async def exists(self, artifact) -> bool:
            return await LocalDevelopmentArtifactObjectStore.exists(self, artifact)

        async def commit_provisional(self, provisional) -> None:
            await self._gate()
            await super().commit_provisional(provisional)

    async def scenario() -> None:
        owner_id = uuid.uuid4()
        store = GatedCommitStore(tmp_path / "objects")
        service = DurableArtifactService(store)
        locks = AdvisoryLockTable()
        sessions = [PostgresSessionDouble(locks) for _ in range(2)]

        def write(session: PostgresSessionDouble):
            return service.persist_stream(
                session, owner_id=owner_id, tenant_id="default", kind="image-derivative",
                source=io.BytesIO(PAYLOAD), expected_digest=sha256(PAYLOAD).hexdigest(),
                size_bytes=len(PAYLOAD), media_type="image/png",
                created_by_operation_id=uuid.uuid4(), retention_class="checkpoint",
            )

        parked = asyncio.create_task(write(sessions[0]))
        await asyncio.wait_for(store.entered.wait(), timeout=5)
        second = await asyncio.wait_for(write(sessions[1]), timeout=5)
        store.release.set()
        first = await parked
        assert first.storage_key == second.storage_key and first.sha256 == second.sha256
        assert first.artifact_id != second.artifact_id
        assert await store.read_bytes(first) == PAYLOAD == await store.read_bytes(second)

    asyncio.run(scenario())


def test_guard_modes_are_shared_for_holders_and_exclusive_for_delete() -> None:
    locks = AdvisoryLockTable()
    reference = _ref(uuid.uuid4())

    class Recording(PostgresSessionDouble):
        statements: list[str] = []

        async def scalar(self, statement, params=None):
            self.statements.append(str(statement))
            return await super().scalar(statement, params)

    async def scenario() -> None:
        await require_artifact_key_guard(Recording(locks), reference)
        await try_artifact_key_guard(Recording(locks), reference)
        await try_artifact_key_guard(Recording(locks), reference, for_physical_delete=True, shared=True)

    asyncio.run(scenario())
    lock_calls = [sql for sql in Recording.statements if "advisory" in sql]
    assert "pg_try_advisory_xact_lock_shared(" in lock_calls[0]
    assert all("_shared(" not in sql for sql in lock_calls[1:])


def test_postgresql_holders_share_the_key_and_delete_is_excluded() -> None:
    """Opt-in proof on a real server: two connections, one CAS key."""

    configured_url = os.getenv("PPT_AGENT_ARTIFACT_RACE_TEST_URL")
    if not configured_url:
        pytest.skip("PPT_AGENT_ARTIFACT_RACE_TEST_URL is not set")

    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from services.artifact_store import ArtifactKeyBusy

    parsed = make_url(configured_url)
    assert parsed.get_backend_name() == "postgresql" and parsed.host == "127.0.0.1"
    assert parsed.port == 21001 and parsed.database == "cas_guard_race_test"

    async def scenario() -> None:
        engine = create_async_engine(
            parsed.set(drivername="postgresql+asyncpg"), isolation_level="READ COMMITTED",
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        reference = _ref(uuid.uuid4())
        try:
            async with maker() as writer, maker() as verifier, maker() as collector:
                await writer.begin()
                await verifier.begin()
                await require_artifact_key_guard(writer, reference)
                await require_artifact_key_guard(verifier, reference)
                await collector.begin()
                assert not await try_artifact_key_guard(collector, reference, for_physical_delete=True)
                await collector.rollback()
                await writer.commit()
                await verifier.commit()
                await collector.begin()
                assert await try_artifact_key_guard(collector, reference, for_physical_delete=True)
                async with maker() as late:
                    await late.begin()
                    with pytest.raises(ArtifactKeyBusy):
                        await require_artifact_key_guard(late, reference)
                    await late.rollback()
                await collector.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
