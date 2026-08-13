from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.provider_credential_reference import ProviderCredentialReference
from models.sql.provider_runtime_reservation import (
    ProviderRuntimeBudget,
    ProviderRuntimeCircuit,
    ProviderRuntimeReservation,
)
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from models.sql.user import User
from services.account_deletion_service import mark_account_deleting
from services.provider_credential_reference_service import (
    revoke_account_provider_credentials,
)
from services.provider_credential_vault import (
    CredentialScope,
    CredentialVaultError,
    InMemoryCredentialBackend,
    ProviderCredentialVault,
)
from utils.datetime_utils import get_current_utc_datetime


FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(path: Path) -> None:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, "head")


def test_account_deletion_revokes_provider_secret_and_cascades_job_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "provider-account-deletion.db"
        _upgrade(path)
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_foreign_keys(connection, _record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = uuid.uuid4()
        presentation_id = uuid.uuid4()
        job_id = uuid.uuid4()
        task_id = f"task-{uuid.uuid4()}"
        budget_id = uuid.uuid4()
        reservation_id = uuid.uuid4()
        receipt_id = uuid.uuid4()
        circuit_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        backend = InMemoryCredentialBackend(backend_id="server_kms")
        vault = ProviderCredentialVault(backend)
        scope = CredentialScope("tenant-a", owner_id)
        reference = vault.store(
            scope=scope,
            provider_id="openai",
            secret="provider-secret-never-persisted",
        )
        try:
            async with sessions() as session, session.begin():
                session.add(
                    User(
                        id=owner_id,
                        username="provider-delete-owner",
                        hashed_password="test-only",
                    )
                )
                await session.flush()
                session.add(
                    PresentationModel(
                        id=presentation_id,
                        owner_id=owner_id,
                        content="provider deletion fixture",
                        n_slides=1,
                        language="English",
                        layout={},
                    )
                )
                session.add(
                    AsyncPresentationGenerationTaskModel(
                        id=task_id,
                        owner_id=owner_id,
                        status="PROCESSING",
                    )
                )
                await session.flush()
                session.add(
                    PresentationGenerationJob(
                        id=job_id,
                        task_id=task_id,
                        presentation_id=presentation_id,
                        request_sha256="a" * 64,
                        request_payload={},
                    )
                )
                await session.flush()
                session.add(
                    ProviderCredentialReference(
                        id=credential_id,
                        tenant_id="tenant-a",
                        user_id=owner_id,
                        provider="openai",
                        backend=reference.backend,
                        credential_ref=reference.storage_value,
                    )
                )
                session.add(
                    ProviderRuntimeBudget(
                        id=budget_id,
                        tenant_id="tenant-a",
                        owner_id=owner_id,
                        job_id=job_id,
                        estimated_limit=10,
                        actual_limit=10,
                    )
                )
                session.add(
                    ProviderRuntimeReservation(
                        id=reservation_id,
                        budget_id=budget_id,
                        reservation_key_hash=sha256(b"account-delete").hexdigest(),
                        estimated_cost=1,
                        lease_token_hash=sha256(b"lease").hexdigest(),
                        lease_expires_at=get_current_utc_datetime(),
                    )
                )
                session.add(
                    ProviderUsageReceipt(
                        id=receipt_id,
                        job_id=job_id,
                        operation="generate",
                        stage="author",
                        provider="openai",
                        model="test-model",
                        receipt_key_hash=sha256(b"usage-receipt").hexdigest(),
                        retry_class="initial",
                        outcome="succeeded",
                        latency_ms=1,
                        usage_state="unavailable",
                    )
                )
                session.add(
                    ProviderRuntimeCircuit(
                        id=circuit_id,
                        service_key="openai.shared",
                        rpm_window_started_at=get_current_utc_datetime(),
                    )
                )

            assert vault.resolve(reference, scope=scope).reveal() == (
                "provider-secret-never-persisted"
            )
            async with sessions() as session:
                marker = await mark_account_deleting(session, user_id=owner_id)
                assert marker.generation == 1
            async with sessions() as session:
                assert (
                    await revoke_account_provider_credentials(
                        session,
                        owner_id=owner_id,
                        vault=vault,
                    )
                    == 1
                )
                await session.execute(delete(User).where(User.id == owner_id))
                await session.commit()

            with pytest.raises(CredentialVaultError, match="credential_not_found"):
                vault.resolve(reference, scope=scope)
            async with sessions() as session:
                assert await session.get(User, owner_id) is None
                assert await session.get(PresentationGenerationJob, job_id) is None
                assert await session.get(ProviderCredentialReference, credential_id) is None
                assert await session.get(ProviderRuntimeBudget, budget_id) is None
                assert await session.get(ProviderRuntimeReservation, reservation_id) is None
                assert await session.get(ProviderUsageReceipt, receipt_id) is None
                assert await session.get(ProviderRuntimeCircuit, circuit_id) is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
