from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from models.sql.provider_runtime_reservation import ProviderRuntimeCircuit
from services.provider_durable_runtime import (
    admit_provider_rpm_and_circuit,
    record_provider_circuit_outcome,
)
from utils.datetime_utils import get_current_utc_datetime


def _sqlite_provider_worker(
    database_path: str,
    service_key: str,
    operation: str,
    start_gate,
    result_queue,
) -> None:
    async def scenario() -> str:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path}",
            connect_args={"timeout": 30.0},
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                if operation == "rpm":
                    try:
                        await admit_provider_rpm_and_circuit(
                            session,
                            service_key=service_key,
                            requests_per_minute=1,
                            failure_threshold=2,
                            reset_seconds=30,
                        )
                        await session.commit()
                        return "admitted"
                    except RuntimeError as error:
                        await session.rollback()
                        if str(error) == "provider_rpm_limit_reached":
                            return "rpm-limited"
                        raise
                await record_provider_circuit_outcome(
                    session,
                    service_key=service_key,
                    outcome="retryable_failure",
                    failure_threshold=2,
                    reset_seconds=30,
                )
                await session.commit()
                return "failure-recorded"
        finally:
            await engine.dispose()

    try:
        if not start_gate.wait(timeout=15):
            raise TimeoutError("provider worker start gate was not released")
        result_queue.put(("ok", asyncio.run(scenario())))
    except BaseException as error:
        result_queue.put(("error", type(error).__name__, str(error)))
        raise


def _run_workers(path: Path, *, service_key: str, operation: str) -> list[tuple]:
    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_sqlite_provider_worker,
            args=(str(path), service_key, operation, start_gate, result_queue),
        )
        for _index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        start_gate.set()
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0, 0]
        return [result_queue.get(timeout=5) for _process in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()


def test_sqlite_rpm_and_circuit_updates_are_atomic_across_processes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-rpm-circuit.db"
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )
    ProviderRuntimeCircuit.__table__.create(engine)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                ProviderRuntimeCircuit(
                    service_key="provider.circuit",
                    rpm_window_started_at=get_current_utc_datetime(),
                )
            )

        rpm_results = _run_workers(
            path, service_key="provider.rpm", operation="rpm"
        )
        assert sorted(result[1] for result in rpm_results) == [
            "admitted",
            "rpm-limited",
        ]
        assert {result[0] for result in rpm_results} == {"ok"}

        circuit_results = _run_workers(
            path, service_key="provider.circuit", operation="circuit"
        )
        assert circuit_results == [
            ("ok", "failure-recorded"),
            ("ok", "failure-recorded"),
        ]

        with Session(engine) as session:
            rpm = session.query(ProviderRuntimeCircuit).filter_by(
                service_key="provider.rpm"
            ).one()
            assert (rpm.rpm_count, rpm.version) == (1, 1)
            circuit = session.query(ProviderRuntimeCircuit).filter_by(
                service_key="provider.circuit"
            ).one()
            assert circuit.state == "open"
            assert circuit.consecutive_failures == 2
            assert circuit.version == 2
            assert circuit.open_until is not None
    finally:
        engine.dispose()
