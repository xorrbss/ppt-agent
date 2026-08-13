"""Live PostgreSQL process-safety coverage for provider RPM and circuits."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import re
from typing import Any
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from models.sql.provider_runtime_reservation import ProviderRuntimeCircuit
from services.provider_durable_runtime import (
    admit_provider_rpm_and_circuit,
    record_provider_circuit_outcome,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.db_utils import to_sync_sqlalchemy_url


_SCHEMA_RE = re.compile(r"^provider_runtime_[a-f0-9]{32}$")


def _postgres_urls() -> tuple[str, str]:
    required = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured:
        if required:
            pytest.fail("PPT_AGENT_POSTGRES_TEST_URL is required")
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL tests")
    sync_url = to_sync_sqlalchemy_url(configured)
    parsed = make_url(sync_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    if not re.search(r"(?:^|[_-])tests?$", parsed.database or "", re.IGNORECASE):
        pytest.fail("PostgreSQL integration database name must end in test or tests")
    query = dict(parsed.query)
    if "sslmode" in query and "ssl" not in query:
        query["ssl"] = query.pop("sslmode")
    async_url = parsed.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)
    return sync_url, async_url


def _provider_worker(
    async_url: str,
    schema: str,
    service_key: str,
    operation: str,
    start_gate: Any,
    result_queue: Any,
) -> None:
    async def scenario() -> str:
        engine = create_async_engine(
            async_url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
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


def _run_workers(
    async_url: str, schema: str, *, service_key: str, operation: str
) -> list[tuple]:
    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_provider_worker,
            args=(
                async_url,
                schema,
                service_key,
                operation,
                start_gate,
                result_queue,
            ),
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


def test_postgresql_rpm_and_circuit_updates_are_atomic_across_processes() -> None:
    sync_url, async_url = _postgres_urls()
    schema = f"provider_runtime_{uuid.uuid4().hex}"
    assert _SCHEMA_RE.fullmatch(schema)
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET search_path TO "{schema}"'))
            ProviderRuntimeCircuit.__table__.create(connection)
            connection.execute(
                ProviderRuntimeCircuit.__table__.insert(),
                {
                    "id": uuid.uuid4(),
                    "service_key": "provider.circuit",
                    "rpm_window_started_at": get_current_utc_datetime(),
                },
            )

        rpm_results = _run_workers(
            async_url, schema, service_key="provider.rpm", operation="rpm"
        )
        assert sorted(result[1] for result in rpm_results) == [
            "admitted",
            "rpm-limited",
        ]
        assert {result[0] for result in rpm_results} == {"ok"}
        circuit_results = _run_workers(
            async_url,
            schema,
            service_key="provider.circuit",
            operation="circuit",
        )
        assert circuit_results == [
            ("ok", "failure-recorded"),
            ("ok", "failure-recorded"),
        ]

        with engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            with Session(bind=connection) as session:
                rpm = session.scalar(
                    select(ProviderRuntimeCircuit).where(
                        ProviderRuntimeCircuit.service_key == "provider.rpm"
                    )
                )
                assert rpm is not None and (rpm.rpm_count, rpm.version) == (1, 1)
                circuit = session.scalar(
                    select(ProviderRuntimeCircuit).where(
                        ProviderRuntimeCircuit.service_key == "provider.circuit"
                    )
                )
                assert circuit is not None
                assert (circuit.state, circuit.consecutive_failures) == ("open", 2)
                assert circuit.version == 2
                assert circuit.open_until is not None
    finally:
        if _SCHEMA_RE.fullmatch(schema):
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
