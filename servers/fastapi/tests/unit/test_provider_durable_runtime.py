from __future__ import annotations

import asyncio
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.user import User
from models.sql.provider_runtime_reservation import (
    ProviderRuntimeBudget,
    ProviderRuntimeCircuit,
    ProviderRuntimeReservation,
)
from services.provider_durable_runtime import (
    admit_provider_rpm_and_circuit,
    recover_expired_reservations,
    reconcile_provider_call,
    register_durable_budget,
    reserve_provider_call,
)
from services.provider_runtime_primitives import (
    BudgetExceededError,
    CircuitOpenError,
)
from utils.datetime_utils import get_current_utc_datetime


# Both FK targets must be registered in the shared production metadata before
# the isolated provider tables are compiled. Import the real mappings so this
# test cannot leave a reduced fake ``user`` table behind for later tests.
assert User.__tablename__ == "user"
assert PresentationGenerationJob.__tablename__ == "presentation_generation_jobs"


def test_direct_service_import_registers_provider_fk_targets() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import services.provider_durable_runtime; "
                "from sqlmodel import SQLModel; "
                "assert 'user' in SQLModel.metadata.tables; "
                "assert 'presentation_generation_jobs' in SQLModel.metadata.tables"
            ),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


async def _sessions(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for table in (
            ProviderRuntimeBudget.__table__,
            ProviderRuntimeCircuit.__table__,
            ProviderRuntimeReservation.__table__,
        ):
            await connection.run_sync(table.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_durable_reservation_reconciles_actual_cost_exactly_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, sessions = await _sessions(tmp_path / "durable.db")
        try:
            async with sessions() as session:
                budget = await register_durable_budget(
                    session,
                    tenant_id="tenant-a",
                    owner_id=uuid.uuid4(),
                    job_id=uuid.uuid4(),
                    estimated_limit=10,
                    actual_limit=12,
                )
                lease = await reserve_provider_call(
                    session,
                    budget_id=budget.id,
                    reservation_key_hash=sha256(b"request-1").hexdigest(),
                    estimated_cost=4,
                )
                receipt = await reconcile_provider_call(
                    session, lease, actual_cost=5, outcome="success"
                )
                assert receipt.state == "reconciled"
                assert receipt.actual_cost == 5
                replay = await reconcile_provider_call(
                    session, lease, actual_cost=5, outcome="success"
                )
                assert replay.id == receipt.id
                refreshed = await session.get(ProviderRuntimeBudget, budget.id)
                assert refreshed is not None
                assert refreshed.estimated_reserved == 0
                assert refreshed.estimated_consumed == 4
                assert refreshed.actual_consumed == 5
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_budget_and_duplicate_reservation_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, sessions = await _sessions(tmp_path / "budget.db")
        try:
            async with sessions() as session:
                budget = await register_durable_budget(
                    session,
                    tenant_id="tenant-a",
                    owner_id=uuid.uuid4(),
                    job_id=uuid.uuid4(),
                    estimated_limit=3,
                    actual_limit=None,
                )
                digest = sha256(b"idempotent").hexdigest()
                await reserve_provider_call(
                    session,
                    budget_id=budget.id,
                    reservation_key_hash=digest,
                    estimated_cost=2,
                )
                with pytest.raises(ValueError, match="duplicate"):
                    await reserve_provider_call(
                        session,
                        budget_id=budget.id,
                        reservation_key_hash=digest,
                        estimated_cost=2,
                    )
                with pytest.raises(BudgetExceededError):
                    await reserve_provider_call(
                        session,
                        budget_id=budget.id,
                        reservation_key_hash=sha256(b"request-2").hexdigest(),
                        estimated_cost=2,
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unknown_cost_can_be_reconciled_once_when_usage_arrives(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, sessions = await _sessions(tmp_path / "late-usage.db")
        try:
            async with sessions() as session:
                budget = await register_durable_budget(
                    session,
                    tenant_id="tenant-a",
                    owner_id=uuid.uuid4(),
                    job_id=uuid.uuid4(),
                    estimated_limit=10,
                    actual_limit=10,
                )
                lease = await reserve_provider_call(
                    session,
                    budget_id=budget.id,
                    reservation_key_hash=sha256(b"late-usage").hexdigest(),
                    estimated_cost=3,
                )
                uncertain = await reconcile_provider_call(
                    session, lease, actual_cost=None, outcome="success"
                )
                assert uncertain.state == "uncertain"
                reconciled = await reconcile_provider_call(
                    session, lease, actual_cost=4, outcome="success"
                )
                assert reconciled.state == "reconciled"
                assert reconciled.actual_cost == 4
                replay = await reconcile_provider_call(
                    session, lease, actual_cost=4, outcome="success"
                )
                assert replay.id == reconciled.id
                refreshed = await session.get(ProviderRuntimeBudget, budget.id)
                assert refreshed is not None
                assert refreshed.actual_consumed == 4
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_crash_recovery_charges_estimate_and_keeps_actual_unknown(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, sessions = await _sessions(tmp_path / "recovery.db")
        try:
            async with sessions() as session:
                budget = await register_durable_budget(
                    session,
                    tenant_id="tenant-a",
                    owner_id=uuid.uuid4(),
                    job_id=uuid.uuid4(),
                    estimated_limit=10,
                    actual_limit=10,
                )
                lease = await reserve_provider_call(
                    session,
                    budget_id=budget.id,
                    reservation_key_hash=sha256(b"crash").hexdigest(),
                    estimated_cost=3,
                )
                row = await session.get(ProviderRuntimeReservation, lease.reservation_id)
                assert row is not None
                row.lease_expires_at = get_current_utc_datetime() - timedelta(seconds=1)
                assert await recover_expired_reservations(session) == 1
                assert row.state == "uncertain"
                assert row.actual_cost is None
                refreshed = await session.get(ProviderRuntimeBudget, budget.id)
                assert refreshed is not None
                assert refreshed.estimated_consumed == 3
                assert refreshed.estimated_reserved == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_durable_rpm_and_circuit_are_shared_database_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, sessions = await _sessions(tmp_path / "circuit.db")
        try:
            async with sessions() as session:
                await admit_provider_rpm_and_circuit(
                    session,
                    service_key="openai.chat",
                    requests_per_minute=1,
                    failure_threshold=1,
                    reset_seconds=30,
                )
                with pytest.raises(RuntimeError, match="rpm_limit"):
                    await admit_provider_rpm_and_circuit(
                        session,
                        service_key="openai.chat",
                        requests_per_minute=1,
                        failure_threshold=1,
                        reset_seconds=30,
                    )
                circuit = await session.scalar(
                    select(ProviderRuntimeCircuit).where(
                        ProviderRuntimeCircuit.service_key == "openai.chat"
                    )
                )
                assert circuit is not None
                circuit.state = "open"
                circuit.open_until = get_current_utc_datetime() + timedelta(seconds=30)
                with pytest.raises(CircuitOpenError):
                    await admit_provider_rpm_and_circuit(
                        session,
                        service_key="openai.chat",
                        requests_per_minute=10,
                        failure_threshold=1,
                        reset_seconds=30,
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
