"""Export reuse and infrastructure failures cross their real service boundaries."""

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy.exc import OperationalError

from services import isolated_worker_process, public_export_join, svg_export_identity
from services.public_export_errors import PublicExportOperationError
from services.public_export_failure_classification import classified_public_export_error
from services.svg_export_release_gate import require_release_v1_receipt
from services.svg_export_visual_review import release_readiness
from services.svg_final_pptx_review import final_pptx_visual_domain, seal_final_review
from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
from tests.unit import test_svg_export_identity_join as identity_tests
from tests.unit.test_svg_export_identity_join import _request
from tests.unit.test_svg_export_projection_error import _job
from utils.hashing import canonical_json_sha256, sha256_hex

harness = identity_tests.harness


@pytest.mark.parametrize("review_state", ["current", "missing", "wrong_digest"])
def test_renderless_producer_release_reuse_requires_current_artifact_review(harness, review_state):
    from workers import public_svg_pptx_render as office
    from workers.public_svg_pptx_convert import converter_digest

    # The checker digest also binds the renderer's ownership dependency
    # (17b81af57), so hashing the renderer file alone no longer matches it.
    checker_digest = office.checker_digest()

    async def scenario():
        export = await harness()
        producer = await export.operation()
        request = _request(quality_policy="interactive-v1")
        original, _ = await export.export(producer, request)
        assert "finalPptxRender" not in original.resource_metrics["public_export_worker_evidence"]["structuralReadback"]
        await export.publish(producer, original)
        review_claim = await export.operation()
        digest = "sha256:" + original.artifact.sha256
        sources = [sha256_hex(svg.encode()) for svg in request.svgs]
        proof = dict(schemaVersion=office.SCHEMA, status="measured", artifactDigest=digest,
            artifactDigestAfter=digest, hashInvariant=True, openReadOnly=True, openWithWindow=False,
            operationId=str(review_claim.operation_id), renderOperationId=str(review_claim.operation_id),
            parentExportOperationId=str(producer.operation_id), presentationId=str(export.presentation_id),
            sourceRevision=3, sourceDigests=sources, ownerId=str(export.owner_id), tenantId=export.tenant_id,
            pageCount=2, converterDigest=converter_digest(), renderIsolation="windows-job-object", rendererQuit=True,
            checkerCodeDigest=checker_digest,
            availablePhysicalMemoryBytes=office.MINIMUM_AVAILABLE_MEMORY,
            pages=[dict(index=i, pngSha256=str(i + 1) * 64, artifactRef=dict(
                owner_id=str(export.owner_id), tenant_id=export.tenant_id,
                created_by_operation_id=str(review_claim.operation_id), sha256=str(i + 1) * 64,
                kind="slide-png", media_type="image/png")) for i in range(2)])
        proof["evidenceDigest"] = "sha256:" + canonical_json_sha256(proof)
        review = seal_final_review(review_claim, dict(proof=proof, artifactId=str(original.artifact.artifact_id),
            artifactDigest=digest, parentExportOperationId=str(producer.operation_id)),
            dict(promptVersion="svg-required-visual-review/v1", slideCount=2, model="test-model",
                reviewedPngDigests=[page["pngSha256"] for page in proof["pages"]], reviewedPageIndices=[0, 1],
                slides=[dict(index=i, issues=[]) for i in range(2)]))
        final_reviews = {original.artifact.sha256: review}
        if review_state == "missing":
            final_reviews.clear()
        elif review_state == "wrong_digest":
            final_reviews = {"f" * 64: review}

        consumer = await export.operation()
        reused, _ = await export.export(consumer, replace(request, quality_policy="release-v1"))
        assert export.worker_calls == [producer.operation_id]
        assert reused.artifact.sha256 == original.artifact.sha256
        metrics = {**reused.resource_metrics, "final_pptx_visual_review": final_reviews.get(reused.artifact.sha256)}
        source_domain = dict(required=True, status="passed", evidence=dict(outputDigest="sha256:" + "b" * 64,
            measurements=dict(advisoryDigest="sha256:" + "c" * 64)))
        domain = final_pptx_visual_domain(source_domain, worker_metrics=metrics, artifact_digest=digest,
            operation_id=consumer.operation_id, presentation_id=export.presentation_id, source_revision=3,
            source_digests=sources)
        receipt = dict(visualReview=domain, releaseReadiness=release_readiness([domain]))
        if review_state == "current":
            assert receipt["releaseReadiness"] == "ready"
            require_release_v1_receipt(receipt)
            # Even a review fetched under the right key must bind these bytes.
            assert final_pptx_visual_domain(source_domain, worker_metrics=metrics,
                artifact_digest="sha256:" + "f" * 64, operation_id=consumer.operation_id,
                presentation_id=export.presentation_id, source_revision=3, source_digests=sources)["status"] == "failed"
        else:
            assert receipt["releaseReadiness"] == "blocked"
            with pytest.raises(NonRetryableWorkspaceOperationError):
                require_release_v1_receipt(receipt)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["cancel", "crash", "database"])
def test_concurrent_svg_waiter_reclaims_after_leader_abnormal_exit(harness, monkeypatch, failure):
    async def scenario():
        export = await harness()
        leader, waiter = await export.operation(), await export.operation()
        entered, polled, released = asyncio.Event(), asyncio.Event(), asyncio.Event()
        fail = asyncio.Event()
        resolve = public_export_join.resolve_export_join

        async def wait_for_release(_seconds):
            polled.set()
            await released.wait()

        async def joined(*args, **kwargs):
            return await resolve(*args, **kwargs, sleep=wait_for_release)

        monkeypatch.setattr(svg_export_identity, "resolve_export_join", joined)

        async def broken_worker(_refs):
            entered.set()
            await fail.wait()
            if failure == "crash":
                raise isolated_worker_process.IsolatedWorkerCrashedError(-9)
            raise OperationalError(None, None, Exception("connection lost"))

        leading = asyncio.create_task(svg_export_identity.export_svg_through_identity(
            leader, tenant_id=export.tenant_id, request=_request(), session_factory=export.sessions,
            grant=None, execute=broken_worker))
        await asyncio.wait_for(entered.wait(), 10)
        waiting = asyncio.create_task(export.export(waiter, _request()))
        await asyncio.wait_for(polled.wait(), 10)
        if failure == "cancel":
            leading.cancel()
        else:
            fail.set()
        expected = (asyncio.CancelledError if failure == "cancel" else
                    isolated_worker_process.IsolatedWorkerCrashedError if failure == "crash" else OperationalError)
        with pytest.raises(expected):
            await leading
        failed, = await export.identity_rows()
        assert failed.state == "failed"
        released.set()
        result, _ = await asyncio.wait_for(waiting, 10)
        current, = await export.identity_rows()
        assert current.state == "succeeded"
        assert current.fencing_token == failed.fencing_token + 1
        assert result.artifact.created_by_operation_id == waiter.operation_id
        assert export.worker_calls == [waiter.operation_id]

    asyncio.run(scenario())


@pytest.mark.parametrize("sqlstate", ["55P03", "08006", "42P01"])
def test_final_asset_lock_timeout_crosses_svg_retry_classifier(monkeypatch, sqlstate):
    from services import artifact_store, svg_document_mutations, svg_export_operation

    async def scenario():
        claim = SimpleNamespace(operation_id=uuid.uuid4(), presentation_id=uuid.uuid4(), owner_id=uuid.uuid4(),
            attempt_number=1, attempt_token="attempt", fencing_token=1, expected_revision=3, kind="export.pptx")
        context = SimpleNamespace(claim=claim, raise_if_cancelled=AsyncMock())
        session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(owner_id=claim.owner_id, revision=3)),
            execute=AsyncMock(), get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        verification = SimpleNamespace(revalidate=AsyncMock())
        monkeypatch.setattr(svg_document_mutations, "_PublicationSourceVerification", lambda *_a, **_k: verification)
        original = Exception("private SQL")
        original.sqlstate = sqlstate
        db_error = OperationalError(None, None, original)
        monkeypatch.setattr(artifact_store, "_locked_current_operation", AsyncMock(side_effect=db_error))
        expected_type = PublicExportOperationError if sqlstate == "55P03" else OperationalError
        with pytest.raises(expected_type) as raised:
            await svg_export_operation._build_slide_asset_bindings(context, slides=[], service=object(),
                session=session, tenant_id="tenant", export_format="pptx", canvas=None, grant=None)
        if sqlstate == "55P03":
            assert raised.value.__cause__ is db_error
        else:
            assert raised.value is db_error
        assert "lock_timeout" in str(session.execute.await_args.args[0])
        result = classified_public_export_error(raised.value, strategy="svg")
        assert result.retryable is (sqlstate != "42P01")
        assert result.detail == {"reason_code": (
            "svg_export_asset_lock_timeout" if sqlstate == "55P03" else "OperationalError")}

    asyncio.run(scenario())


def test_transport_close_during_worker_execution_is_transient(tmp_path, monkeypatch):
    from services.trusted_executor import wire_transport as wire
    from services.trusted_executor.contracts import SandboxFilesystemSpec

    @dataclass
    class Limits:
        deadline_seconds: float = 10
        temporary_bytes: int = 0

    @dataclass
    class Spec:
        filesystem: object
        limits: object
        identity: object = None
        executable: Path = Path(sys.executable)
        arguments: tuple = ()

        def require_os_enforcement(self, report):
            pass

    async def scenario():
        job = _job()
        sandbox = tmp_path / "attempt"
        filesystem = SandboxFilesystemSpec(sandbox_root=sandbox, input_root=sandbox / "input",
            temporary_root=sandbox / "tmp", staged_inputs=(sandbox / "input" / job.input_artifact.storage_key,))
        entered, cancelled = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()

        class Supervisor:
            def launch(self, *_args, **_kwargs):
                loop.call_soon_threadsafe(entered.set)
                return object()

            def cancel(self, **_kwargs):
                loop.call_soon_threadsafe(cancelled.set)

            def close(self):
                pass

        async def communicate_result():
            await cancelled.wait()
            return wire._CapturedProcess(stdout=b"", stderr_tail=b"", exit_code=1, stdout_overflow=False)

        def communicate(*_args):
            return asyncio.run_coroutine_threadsafe(communicate_result(), loop).result(timeout=10)

        monkeypatch.setattr(wire, "_communicate", communicate)
        monkeypatch.setattr(wire, "_input_bundle_references", lambda *_a: ())
        authority = SimpleNamespace(stage=AsyncMock(), publish=AsyncMock())
        transport = wire.SupervisedStdioWireTransport(artifact_authority=authority,
            isolation_report=SimpleNamespace(), supervisor_factory=lambda *_a: Supervisor())

        class Dispatcher:
            async def execute(self, **kwargs):
                return await transport.execute(work_item=SimpleNamespace(launch_spec=Spec(filesystem, Limits())),
                    job=kwargs["job"], payload=kwargs["payload"],
                    environment={"PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(tmp_path / "capability.key")})

        monkeypatch.setattr(isolated_worker_process, "worker_environment", lambda *_a, **_k: {})
        task = asyncio.create_task(isolated_worker_process.execute_isolated_worker_job(
            job, presentation_id=uuid.uuid4(), command=[sys.executable], dispatcher=Dispatcher()))
        await asyncio.wait_for(entered.wait(), 10)
        await transport.close()
        with pytest.raises(isolated_worker_process.IsolatedWorkerProcessError) as raised:
            await asyncio.wait_for(task, 10)
        assert raised.value.code == "trusted_worker_transport_closed"
        classified = classified_public_export_error(raised.value, strategy="svg")
        assert classified.retryable is True
        assert classified.detail == {"reason_code": "trusted_worker_transport_closed"}
        authored = classified_public_export_error(raised.value)
        legacy = classified_public_export_error(isolated_worker_process.IsolatedWorkerProcessError(
            "public worker result exceeded the wire limit"))
        assert (authored.retryable, authored.code, authored.detail) == (legacy.retryable, legacy.code, legacy.detail)
        authority.publish.assert_not_awaited()
        assert not sandbox.exists()
        assert transport._staging_capacity._reserved_bytes == 0

    asyncio.run(scenario())
