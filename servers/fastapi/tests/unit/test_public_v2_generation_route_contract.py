from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from api.v1.ppt.endpoints.presentation_generate import (
    _require_local_legacy_async_generation,
)
from api.v2.contracts import (
    OperationLinksV2,
    OperationReceiptV2,
    OperationStatusV2,
    PublicOperationErrorV2,
)
from api.v2.orchestration import (
    PublicRequestActor,
    PublicV2OrchestrationError,
    get_public_request_actor,
)
from api.v2.router import API_V2_PUBLIC_ROUTER


def test_public_generation_route_is_v2_202_and_bounded_to_100_slides() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    actor = PublicRequestActor(
        owner_id=uuid.uuid4(),
        tenant_id="tenant-a",
        correlation_id="request-a",
        scopes=frozenset({"presentations:write"}),
    )

    class DurableOrchestrator:
        request_slide_counts: list[int | None] = []

        async def submit_generation(self, **kwargs) -> OperationReceiptV2:
            request = kwargs["request"]
            self.request_slide_counts.append(request.slide_count)
            if request.slide_count == 99:
                raise PublicV2OrchestrationError(
                    status_code=429,
                    reason_code="public_owner_queue_limit_exceeded",
                    message="private queue authority detail",
                    retry_after_seconds=15,
                )
            operation_id = "op_generation"
            base = f"/api/v2/operations/{operation_id}"
            return OperationReceiptV2(
                operation_id=operation_id,
                receipt_id="receipt_generation",
                presentation_id="presentation_generation",
                status="queued",
                accepted_at=now,
                absolute_deadline_at=now + timedelta(minutes=15),
                links=OperationLinksV2(
                    self=base,
                    events=f"{base}/events",
                    cancel=f"{base}/cancel",
                ),
            )

    orchestrator = DurableOrchestrator()
    app = FastAPI()
    app.include_router(API_V2_PUBLIC_ROUTER)
    app.dependency_overrides[get_public_request_actor] = lambda: actor
    app.state.public_v2_orchestrator = orchestrator

    with TestClient(app) as client:
        admitted = client.post(
            "/api/v2/presentations/generations",
            headers={"Idempotency-Key": "bounded-100"},
            json={
                "schemaVersion": "presentation-generation-request/v2",
                "content": "Generate a public deck",
                "slideCount": 100,
            },
        )
        assert admitted.status_code == 202
        assert admitted.json()["schemaVersion"] == "operation-receipt/v2"
        assert admitted.json()["status"] == "queued"
        assert admitted.json()["links"]["self"] == (
            "/api/v2/operations/op_generation"
        )

        rejected = client.post(
            "/api/v2/presentations/generations",
            headers={"Idempotency-Key": "over-100"},
            json={
                "schemaVersion": "presentation-generation-request/v2",
                "content": "Generate an oversized public deck",
                "slideCount": 101,
            },
        )
        assert rejected.status_code == 422

        authority_rejected = client.post(
            "/api/v2/presentations/generations",
            headers={"Idempotency-Key": "authority-rejected"},
            json={
                "schemaVersion": "presentation-generation-request/v2",
                "content": "Generate when the owner queue is full",
                "slideCount": 99,
            },
        )
        assert authority_rejected.status_code == 429
        assert authority_rejected.json() == {
            "detail": {"reasonCode": "public_owner_queue_limit_exceeded"}
        }
        assert authority_rejected.headers["retry-after"] == "15"
        assert "private queue authority detail" not in authority_rejected.text

    assert orchestrator.request_slide_counts == [100, 99]


def test_public_terminal_contract_requires_safe_error_reason_codes() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    failed = OperationStatusV2(
        operation_id="op_failed",
        presentation_id="presentation_failed",
        kind="presentation.generate",
        status="failed",
        phase="authoring",
        progress_percent=40,
        accepted_at=now,
        updated_at=now + timedelta(minutes=1),
        absolute_deadline_at=now + timedelta(minutes=15),
        error=PublicOperationErrorV2(
            reason_code="provider_budget_exhausted",
            retryable=False,
        ),
    )
    assert failed.model_dump(mode="json", by_alias=True)["error"] == {
        "reasonCode": "provider_budget_exhausted",
        "retryable": False,
    }

    with pytest.raises(ValidationError, match="requires error"):
        OperationStatusV2(
            operation_id="op_failed",
            presentation_id="presentation_failed",
            kind="presentation.generate",
            status="failed",
            accepted_at=now,
            updated_at=now + timedelta(minutes=1),
            absolute_deadline_at=now + timedelta(minutes=15),
        )


def test_legacy_async_generation_is_local_only() -> None:
    _require_local_legacy_async_generation("sqlite")

    with pytest.raises(HTTPException) as rejected:
        _require_local_legacy_async_generation("postgresql")
    assert rejected.value.status_code == 410
    assert rejected.value.detail == {
        "reasonCode": "public_v2_generation_required",
        "replacement": "/api/v2/presentations/generations",
    }
    assert rejected.value.headers == {
        "Deprecation": "true",
        "Link": '</api/v2/presentations/generations>; rel="successor-version"',
    }
