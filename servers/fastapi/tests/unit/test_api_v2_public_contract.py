from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.v2.contracts import (
    AuthorizedDownloadGrant,
    CancelOperationReceiptV2,
    OperationEventPageV2,
    OperationEventV2,
    OperationLinksV2,
    OperationReceiptV2,
    OperationStatusV2,
    PublicArtifactV2,
    QualityReceiptV3,
)
from api.v2.orchestration import (
    PublicRequestActor,
    PublicV2OrchestrationError,
    get_public_request_actor,
)
from api.v2.router import API_V2_PUBLIC_ROUTER


def test_public_v2_contract_delegates_async_surfaces_and_fails_closed_quality() -> None:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    actor = PublicRequestActor(
        owner_id=uuid.uuid4(),
        tenant_id="tenant-a",
        correlation_id="request-a",
    )

    def receipt(operation_id: str, presentation_id: str) -> OperationReceiptV2:
        base = f"/api/v2/operations/{operation_id}"
        return OperationReceiptV2(
            operation_id=operation_id,
            receipt_id=f"rcpt_{operation_id}",
            presentation_id=presentation_id,
            status="queued",
            accepted_at=now,
            absolute_deadline_at=now + timedelta(minutes=5),
            links=OperationLinksV2(
                self=base,
                events=f"{base}/events",
                cancel=f"{base}/cancel",
            ),
        )

    class FakeOrchestrator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def submit_generation(self, **kwargs):
            self.calls.append(("generation", kwargs))
            return receipt("op_generate", "prs_generated")

        async def submit_export(self, **kwargs):
            self.calls.append(("export", kwargs))
            return receipt("op_export", kwargs["presentation_id"])

        async def get_operation(self, **kwargs):
            self.calls.append(("status", kwargs))
            if kwargs["operation_id"] == "op_error":
                raise PublicV2OrchestrationError(
                    status_code=409,
                    reason_code="stale_revision",
                    message="private authority diagnostic",
                    retry_after_seconds=7,
                )
            return OperationStatusV2(
                operation_id=kwargs["operation_id"],
                presentation_id="prs_generated",
                kind="presentation.generate",
                status="running",
                phase="authoring",
                progress_percent=30,
                accepted_at=now,
                updated_at=now + timedelta(seconds=10),
                absolute_deadline_at=now + timedelta(minutes=5),
            )

        async def list_operation_events(self, **kwargs):
            self.calls.append(("events", kwargs))
            return OperationEventPageV2(
                operation_id=kwargs["operation_id"],
                events=[
                    OperationEventV2(
                        event_id="evt_1",
                        operation_id=kwargs["operation_id"],
                        sequence=1,
                        occurred_at=now,
                        status="queued",
                        phase="admission",
                        progress_percent=0,
                    )
                ],
            )

        async def cancel_operation(self, **kwargs):
            self.calls.append(("cancel", kwargs))
            return CancelOperationReceiptV2(
                operation_id=kwargs["operation_id"],
                status="running",
                cancellation_requested=True,
                requested_at=now + timedelta(seconds=20),
            )

        async def authorize_download(self, **kwargs):
            self.calls.append(("download", kwargs))
            return AuthorizedDownloadGrant(
                url="https://download.example.test/signed/artifact?signature=opaque",
                issued_at=now,
                expires_at=now + timedelta(minutes=3),
                etag='"sha256-opaque"',
            )

    orchestrator = FakeOrchestrator()
    app = FastAPI()
    app.include_router(API_V2_PUBLIC_ROUTER)
    app.dependency_overrides[get_public_request_actor] = lambda: actor
    app.state.public_v2_orchestrator = orchestrator

    with TestClient(app, follow_redirects=False) as client:
        generated = client.post(
            "/api/v2/presentations/generations",
            headers={"Idempotency-Key": "generate-key"},
            json={
                "schemaVersion": "presentation-generation-request/v2",
                "content": "A bounded public deck",
                "slideCount": 5,
            },
        )
        assert generated.status_code == 202
        assert generated.json() == {
            "schemaVersion": "operation-receipt/v2",
            "operationId": "op_generate",
            "receiptId": "rcpt_op_generate",
            "presentationId": "prs_generated",
            "status": "queued",
            "duplicate": False,
            "joinedExistingWork": False,
            "acceptedAt": "2026-08-30T00:00:00Z",
            "absoluteDeadlineAt": "2026-08-30T00:05:00Z",
            "links": {
                "self": "/api/v2/operations/op_generate",
                "events": "/api/v2/operations/op_generate/events",
                "cancel": "/api/v2/operations/op_generate/cancel",
            },
        }

        exported = client.post(
            "/api/v2/presentations/prs_generated/exports",
            headers={"Idempotency-Key": "export-key"},
            json={
                "schemaVersion": "presentation-export-request/v2",
                "sourceRevision": 42,
                "output": {
                    "format": "pptx",
                    "mode": "fidelity-raster",
                    "fallbackPolicy": None,
                    "fontEmbedding": "none",
                    "requiredCapabilities": [],
                },
                "qualityPolicy": "interactive-v1",
            },
        )
        assert exported.status_code == 202
        assert exported.json()["operationId"] == "op_export"

        operation = client.get("/api/v2/operations/op_generate")
        assert operation.status_code == 200
        assert operation.json()["phase"] == "authoring"

        events = client.get("/api/v2/operations/op_generate/events?limit=25")
        assert events.status_code == 200
        assert events.json()["events"][0]["status"] == "queued"

        cancelled = client.post("/api/v2/operations/op_generate/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["cancellationRequested"] is True

        downloaded = client.get("/api/v2/artifacts/artifact_1/download")
        assert downloaded.status_code == 303
        assert downloaded.headers["location"].startswith(
            "https://download.example.test/signed/artifact"
        )
        assert downloaded.headers["cache-control"] == "no-store"

        invalid_export = client.post(
            "/api/v2/presentations/prs_generated/exports",
            headers={"Idempotency-Key": "invalid-key"},
            json={
                "schemaVersion": "presentation-export-request/v2",
                "sourceRevision": 42,
                "output": {"format": "pdf", "mode": "editable-hybrid"},
            },
        )
        assert invalid_export.status_code == 422

        authority_error = client.get("/api/v2/operations/op_error")
        assert authority_error.status_code == 409
        assert authority_error.json() == {
            "detail": {"reasonCode": "stale_revision"}
        }
        assert authority_error.headers["retry-after"] == "7"
        assert "private authority diagnostic" not in authority_error.text

        del app.state.public_v2_orchestrator
        unbound = client.get("/api/v2/operations/op_generate")
        assert unbound.status_code == 503
        assert unbound.json()["detail"]["reasonCode"] == (
            "operation_authority_unavailable"
        )

    assert [name for name, _ in orchestrator.calls] == [
        "generation",
        "export",
        "status",
        "events",
        "cancel",
        "download",
        "status",
    ]
    generation_call = orchestrator.calls[0][1]
    assert isinstance(generation_call, dict)
    assert generation_call["actor"] == actor
    assert generation_call["idempotency_key"] == "generate-key"

    evidence = {
        "checkerVersion": "geometry/1",
        "inputDigest": "sha256:" + "1" * 64,
        "outputDigest": "sha256:" + "2" * 64,
        "measurements": {"overflowCount": 0},
        "thresholds": {"maxOverflowCount": 0},
    }
    quality_payload = {
        "schemaVersion": "quality-receipt/v3",
        "qualityReceiptId": "qrcpt_1",
        "operationId": "op_generate",
        "presentationId": "prs_generated",
        "artifactDigest": "sha256:" + "3" * 64,
        "sourceRevision": 42,
        "sceneDigest": "sha256:" + "4" * 64,
        "capabilityRegistryDigest": "sha256:" + "5" * 64,
        "exporterVersion": "exporter/1",
        "evaluatedAt": now,
        "contentBrief": {
            "required": True,
            "status": "passed",
            "evidence": evidence,
        },
        "authoring": {
            "required": True,
            "status": "passed",
            "evidence": evidence,
        },
        "geometryFont": {"required": True, "status": "passed"},
        "imageContentIntegrity": {"required": False, "status": "not_required"},
        "visualReview": {
            "required": False,
            "status": "unavailable",
            "reasonCode": "visual_reviewer_unavailable",
        },
        "performance": {
            "required": False,
            "status": "not_assessed",
            "reasonCode": "benchmark_not_run",
        },
        "nativeCoverageOoxml": {"required": False, "status": "not_required"},
        "releaseReadiness": "blocked",
    }
    try:
        QualityReceiptV3.model_validate(quality_payload)
    except ValidationError as error:
        assert "passed quality status requires evidence" in str(error)
    else:
        raise AssertionError("missing evidence must never produce a passed quality result")

    quality_payload["geometryFont"] = {
        "required": True,
        "status": "passed",
        "evidence": evidence,
    }
    quality = QualityReceiptV3.model_validate(quality_payload)
    assert quality.visual_review.status == "unavailable"
    assert quality.release_readiness == "blocked"

    artifact = PublicArtifactV2(
        artifact_id="artifact_1",
        operation_id="op_generate",
        presentation_id="prs_generated",
        kind="presentation-pptx",
        digest=quality.artifact_digest,
        size_bytes=1_024,
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        download="/api/v2/artifacts/artifact_1/download",
    )
    bound_status = OperationStatusV2(
        operation_id="op_generate",
        presentation_id="prs_generated",
        kind="presentation.generate",
        status="succeeded",
        phase="publishing",
        progress_percent=100,
        accepted_at=now,
        updated_at=now + timedelta(minutes=2),
        absolute_deadline_at=now + timedelta(minutes=5),
        quality_receipt=quality,
        release_readiness="blocked",
        artifacts=[artifact],
    )
    assert bound_status.artifacts[0].digest == quality.artifact_digest

    for invalid_update, expected_message in (
        (
            {"operation_id": "op_other", "artifacts": []},
            "qualityReceipt must match the status operationId",
        ),
        (
            {"artifacts": []},
            "qualityReceipt requires a non-empty artifacts list",
        ),
        (
            {"artifacts": [artifact.model_copy(update={"presentation_id": "prs_other"})]},
            "artifacts must match the status operationId and presentationId",
        ),
        (
            {
                "artifacts": [
                    artifact.model_copy(update={"digest": "sha256:" + "9" * 64})
                ]
            },
            "at least one artifact digest must match",
        ),
    ):
        invalid_status_payload = bound_status.model_dump()
        invalid_status_payload.update(invalid_update)
        try:
            OperationStatusV2.model_validate(invalid_status_payload)
        except ValidationError as error:
            assert expected_message in str(error)
        else:
            raise AssertionError(expected_message)

    quality_payload["imageContentIntegrity"] = {
        "required": True,
        "status": "not_required",
    }
    try:
        QualityReceiptV3.model_validate(quality_payload)
    except ValidationError as error:
        assert "required quality check cannot have not_required status" in str(error)
    else:
        raise AssertionError("required checks must never be reported as not_required")

    quality_payload["imageContentIntegrity"] = {
        "required": False,
        "status": "not_required",
    }
    quality_payload["releaseReadiness"] = "ready"
    ready_with_optional_unavailable = QualityReceiptV3.model_validate(quality_payload)
    assert ready_with_optional_unavailable.release_readiness == "ready"

    quality_payload["contentBrief"] = {
        "required": True,
        "status": "degraded",
    }
    quality_payload["releaseReadiness"] = "blocked"
    try:
        QualityReceiptV3.model_validate(quality_payload)
    except ValidationError as error:
        assert "degraded quality status requires reasonCode" in str(error)
    else:
        raise AssertionError("non-passed quality states must explain their reason")

    try:
        AuthorizedDownloadGrant(
            url=r"C:\artifacts\private.pptx",
            issued_at=now,
            expires_at=now + timedelta(minutes=3),
        )
    except ValidationError as error:
        assert "authorized HTTP(S) URL" in str(error) or "unsafe" in str(error)
    else:
        raise AssertionError("local paths must never be accepted as download grants")

    try:
        AuthorizedDownloadGrant(
            url="http://downloads.example.test/signed/artifact",
            issued_at=now,
            expires_at=now + timedelta(minutes=3),
        )
    except ValidationError as error:
        assert "must use HTTPS except for explicit loopback" in str(error)
    else:
        raise AssertionError("external plain HTTP download grants must be rejected")

    loopback_grant = AuthorizedDownloadGrant(
        url="http://127.0.0.1:9000/signed/artifact",
        issued_at=now,
        expires_at=now + timedelta(minutes=3),
    )
    assert loopback_grant.url.startswith("http://127.0.0.1:")
