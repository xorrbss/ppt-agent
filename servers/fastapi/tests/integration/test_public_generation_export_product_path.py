"""Focused disposable-PostgreSQL proof for the public export product seam.

This deliberately injects only the worker *wire transport*.  PostgreSQL,
admission/claim, trusted scheduling, ArtifactRef registration, fenced
publication, and public status projection are all real product classes.
It does not claim S3, Chrome, or PowerPoint coverage.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
import pytest

from api.v2.contracts import QualityReceiptV3
from api.v2.orchestration import PublicRequestActor
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import ArtifactRef, PublicArtifact, PublicArtifactPublication
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.artifact_store import DurableArtifactService, LocalDevelopmentArtifactObjectStore
from services.isolated_worker_contracts import ArtifactRef as WorkerArtifactRef
from services.isolated_worker_contracts import IsolatedWorkerJob
from services.isolated_worker_process import execute_isolated_worker_job
from services.public_operation_admission import (
    PrincipalContext,
    admit_public_operation_v2,
    plan_weighted_resources,
)
from services.public_v2_orchestrator import SqlPublicV2Orchestrator
from services.trusted_executor.product_adapter import TrustedExecutorProductAdapter
from services.trusted_executor.weighted_scheduler import (
    SchedulerCaps,
    SchedulerPolicy,
    WeightedScheduler,
)
from services.trusted_executor.worker_dispatcher import (
    TrustedWorkerDispatcher,
    TrustedWorkerWireResult,
)
from services.workspace_artifact_publication_service import (
    PreparedWorkspaceArtifact,
    publish_workspace_export_artifact,
)
from services.workspace_operation_runner import claim_next_workspace_operation
from utils.db_utils import to_sync_sqlalchemy_url
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_tenant_id,
    set_current_owner_id,
    set_current_tenant_id,
)


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
PG_BIN = Path(r"C:\Program Files\PostgreSQL\15\bin")


def _alembic_config(url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def _disposable_postgres(tmp_path: Path):
    """Start an owned PostgreSQL 15 cluster under pytest's disposable directory."""

    initdb = PG_BIN / "initdb.exe"
    pg_ctl = PG_BIN / "pg_ctl.exe"
    if not initdb.is_file() or not pg_ctl.is_file():
        pytest.fail("PostgreSQL 15 initdb.exe and pg_ctl.exe are required")
    cluster = tmp_path / "postgres-data"
    log = tmp_path / "postgres.log"
    port = _free_local_port()
    subprocess.run(
        [str(initdb), "-D", str(cluster), "-A", "trust", "-U", "postgres", "--no-locale", "-E", "UTF8"],
        check=True,
        capture_output=True,
        text=True,
    )
    started = False
    try:
        subprocess.run(
            [str(pg_ctl), "-D", str(cluster), "-l", str(log), "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started = True
        yield f"postgresql+psycopg://postgres@127.0.0.1:{port}/postgres"
    finally:
        if started:
            subprocess.run(
                [str(pg_ctl), "-D", str(cluster), "-m", "fast", "-w", "stop"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _blocked_quality(*, operation_id: uuid.UUID, presentation_id: uuid.UUID, digest: str) -> dict[str, object]:
    reason = "external_visual_runtime_not_verified"
    receipt = QualityReceiptV3(
        quality_receipt_id="qrcpt_product_path_1",
        operation_id=str(operation_id), presentation_id=str(presentation_id),
        artifact_digest=f"sha256:{digest}", source_revision=0,
        scene_digest="sha256:" + "1" * 64,
        capability_registry_digest="sha256:" + "2" * 64,
        exporter_version="trusted-wire-test/v1",
        evaluated_at=datetime.now(UTC),
        content_brief={"required": False, "status": "not_required"},
        authoring={"required": False, "status": "failed", "reasonCode": reason},
        geometry_font={"required": False, "status": "failed", "reasonCode": reason},
        image_content_integrity={"required": False, "status": "not_required"},
        visual_review={"required": False, "status": "unavailable", "reasonCode": reason},
        performance={"required": False, "status": "not_assessed", "reasonCode": reason},
        native_coverage_ooxml={"required": False, "status": "pending", "reasonCode": reason},
        release_readiness="blocked",
    )
    return receipt.model_dump(mode="json", by_alias=True)


def test_public_export_product_path_uses_postgres_claim_trusted_dispatch_and_fenced_projection(tmp_path: Path) -> None:
    """No subprocess worker, SQLite authority, or synthetic terminal projection is used."""

    async def scenario(sync_url: str) -> None:
        async_url = make_url(to_sync_sqlalchemy_url(sync_url)).set(
            drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False)
        command.upgrade(_alembic_config(sync_url), "head")
        engine = create_async_engine(async_url, poolclass=NullPool)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner = uuid.uuid4()
        tenant = "tenant-product-path"
        presentation_id = uuid.uuid4()
        conversation_id = uuid.uuid4()
        operation_id = uuid.uuid4()
        owner_token = set_current_owner_id(owner)
        tenant_token = set_current_tenant_id(tenant)
        object_store = LocalDevelopmentArtifactObjectStore(tmp_path / "artifact-object-store")
        artifacts = DurableArtifactService(object_store)
        try:
            async with maker() as session:
                session.add(
                    User(id=owner, username="public-product-owner", hashed_password="test")
                )
                await session.flush()
                session.add_all((
                    PresentationModel(id=presentation_id, owner_id=owner, content="deck", n_slides=1, language="en", mode="authored", theme={}),
                    WorkspaceConversation(id=conversation_id, owner_id=owner, owner_scope=str(owner), presentation_id=presentation_id),
                    WorkspaceOperation(
                        id=operation_id, owner_id=owner, owner_scope=str(owner),
                        conversation_id=conversation_id, presentation_id=presentation_id,
                        anchor_position=1, kind="export.pptx", state="queued",
                        idempotency_key_hash="a" * 64, request_hash="b" * 64,
                        request_payload={}, expected_revision=0,
                    ),
                ))
                await session.flush()
                await admit_public_operation_v2(
                    session, principal=PrincipalContext(owner, tenant, "trace-product"),
                    operation_id=operation_id, operation_kind="export", request_plan=None,
                    resource_plan=plan_weighted_resources(
                        pixel_count=1_920 * 1_080, device_pixel_ratio_milli=1000,
                        dom_node_count=100, font_count=1, svg_complexity=0,
                        estimated_memory_mib=64, fidelity_mode="fidelity",
                    ),
                )
                await session.commit()

            claim = await claim_next_workspace_operation(session_factory=maker, allowed_kinds={"export.pptx"})
            assert claim is not None and claim.operation_id == operation_id

            input_bytes = b"trusted export input"
            input_digest = sha256(input_bytes).hexdigest()
            input_ref = ArtifactRef(
                artifact_id=uuid.uuid4(), owner_id=owner, tenant_id=tenant,
                kind="export-bundle", sha256=input_digest, size_bytes=len(input_bytes),
                media_type="application/vnd.presenton.export-bundle+json",
                storage_key=(
                    f"v1/tenant/{sha256(tenant.encode()).hexdigest()}/owner/{owner}/sha256/{input_digest[:2]}/{input_digest}"
                ),
                created_by_operation_id=operation_id, retention_class="ephemeral",
            )
            output_bytes = b"deterministic-wire-pptx-output"
            output_digest = sha256(output_bytes).hexdigest()
            output_ref = ArtifactRef(
                artifact_id=uuid.uuid4(), owner_id=owner, tenant_id=tenant,
                kind="presentation-pptx", sha256=output_digest, size_bytes=len(output_bytes),
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                storage_key=(
                    f"v1/tenant/{sha256(tenant.encode()).hexdigest()}/owner/{owner}/sha256/{output_digest[:2]}/{output_digest}"
                ),
                created_by_operation_id=operation_id, retention_class="published",
            )
            await object_store.put_bytes(input_ref, input_bytes)

            class Transport:
                calls = 0

                async def execute(self, *, work_item, job, payload, environment):
                    self.calls += 1
                    assert work_item.scheduling.operation_id == str(operation_id)
                    assert work_item.launch_spec.identity.presentation_id == presentation_id
                    assert json.loads(payload)["operationId"] == str(operation_id)
                    assert "DATABASE_URL" not in environment
                    await object_store.put_bytes(output_ref, output_bytes)
                    result = {
                        "schemaVersion": "ppt-isolated-worker-result/v1", "role": "export",
                        "operationId": str(operation_id),
                        "attemptTokenSha256": sha256(claim.attempt_token.encode()).hexdigest(),
                        "fencingToken": claim.fencing_token, "inputDigest": input_digest,
                        "ok": True, "errorCode": None,
                        "outputArtifact": output_ref.model_dump(mode="json", by_alias=True),
                    }
                    return TrustedWorkerWireResult(json.dumps(result, separators=(",", ":")).encode())

            transport = Transport()
            dispatcher = TrustedWorkerDispatcher(
                adapter=TrustedExecutorProductAdapter(WeightedScheduler(SchedulerPolicy(SchedulerCaps(1, 1, 1, 1, 1)))),
                transport=transport, staging_root=tmp_path / "trusted-staging", node_id="pg-product-path",
            )
            worker_job = IsolatedWorkerJob(
                role="export", operation_id=operation_id, attempt_token=claim.attempt_token,
                fencing_token=claim.fencing_token, input_digest=input_digest,
                input_artifact=WorkerArtifactRef.from_wire(input_ref.model_dump(mode="json", by_alias=True)),
                capability="deterministic-wire-test", deadline_epoch_seconds=int(time.time()) + 60,
            )
            result = await execute_isolated_worker_job(
                worker_job, presentation_id=presentation_id, command=[sys.executable], dispatcher=dispatcher,
            )
            assert transport.calls == 1 and result.output_artifact == output_ref.model_dump(mode="json", by_alias=True)

            private_path = tmp_path / "workspace-export.pptx"
            private_path.write_bytes(output_bytes)
            workspace_artifact_id = uuid.uuid4()
            async with maker() as session:
                await artifacts.register_ref(session, output_ref)
                session.add(WorkspaceArtifact(
                    id=workspace_artifact_id, owner_id=owner, operation_id=operation_id,
                    presentation_id=presentation_id, kind="export.pptx", status="staging",
                    storage_key="workspace-artifacts/test/product-path.pptx", sha256=output_digest,
                    size_bytes=len(output_bytes), source_revision=0, public_artifact_id=output_ref.artifact_id,
                ))
                await session.flush()
                await publish_workspace_export_artifact(
                    session,
                    artifact=PreparedWorkspaceArtifact(
                        artifact_id=workspace_artifact_id, owner_id=owner, operation_id=operation_id,
                        presentation_id=presentation_id, kind="export.pptx", absolute_path=private_path,
                        storage_key="workspace-artifacts/test/product-path.pptx", public_path="/api/v2/artifacts/hidden/download",
                        sha256=output_digest, size_bytes=len(output_bytes), source_revision=0,
                    ),
                    attempt_token=claim.attempt_token, fencing_token=claim.fencing_token,
                    export_format="pptx", public_artifact_id=output_ref.artifact_id,
                    public_tenant_id=tenant, public_attempt_number=claim.attempt_number,
                    public_quality_digest="3" * 64,
                    public_quality_receipt=_blocked_quality(operation_id=operation_id, presentation_id=presentation_id, digest=output_digest),
                )

            async with maker() as session:
                pointer = await session.scalar(select(PublicArtifactPublication).where(PublicArtifactPublication.operation_id == operation_id))
                artifact = await session.get(PublicArtifact, output_ref.artifact_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                admission = await session.scalar(select(PublicOperationAdmission).where(PublicOperationAdmission.operation_id == operation_id))
                assert pointer is not None and pointer.artifact_id == output_ref.artifact_id and pointer.fencing_token == claim.fencing_token
                assert artifact is not None and artifact.lifecycle_state == "committed"
                assert operation is not None and operation.state == "succeeded"
                assert admission is not None and admission.state == "succeeded"
                assert await session.scalar(select(WorkspaceOperationEvent).where(WorkspaceOperationEvent.operation_id == operation_id)) is not None
                assert await session.scalar(select(WorkspaceOutbox).where(WorkspaceOutbox.operation_id == operation_id)) is not None

            status = await SqlPublicV2Orchestrator(maker, artifact_service_factory=lambda: artifacts).get_operation(
                actor=PublicRequestActor(owner, tenant, "trace-status"), operation_id=str(operation_id)
            )
            assert status.status == "succeeded"
            assert status.release_readiness == "blocked"
            assert [item.artifact_id for item in status.artifacts] == [str(output_ref.artifact_id)]
            assert status.quality_receipt is not None and status.quality_receipt.visual_review.status == "unavailable"
        finally:
            reset_current_tenant_id(tenant_token)
            reset_current_owner_id(owner_token)
            await engine.dispose()

    with _disposable_postgres(tmp_path) as sync_url:
        asyncio.run(scenario(sync_url))
