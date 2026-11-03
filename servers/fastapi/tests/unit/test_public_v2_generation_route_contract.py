from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from api.v1.ppt.endpoints.presentation_generate_admission import (
    _require_local_legacy_async_generation,
)
from models.public_v2_contracts import (
    GenerationRouteReceiptV1,
    OperationLinksV2,
    OperationReceiptV2,
    OperationStatusV2,
    PresentationGenerationRequestV2,
    PublicOperationErrorV2,
)
from api.v2.orchestration import (
    PublicRequestActor,
    PublicV2OrchestrationError,
    get_public_request_actor,
)
from api.v2.router import API_V2_PUBLIC_ROUTER
from services import public_v2_orchestrator


def test_generation_mode_contract_and_server_owned_route_receipt() -> None:
    # The design-pack route (registered-template) was removed on 2026-11-03.
    with pytest.raises(ValidationError, match="registered_template_removed"):
        PresentationGenerationRequestV2(
            content="Registered template deck",
            generationMode="registered-template",
        )
    with pytest.raises(ValidationError, match="capability_not_supported"):
        PresentationGenerationRequestV2(
            content="Editable deck",
            generationMode="editable-first",
        )

    request = PresentationGenerationRequestV2(
        content="Brand style deck",
        generationMode="brand-style",
        strategy="authored",
    )
    captured: dict[str, object] = {}

    def resolve(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"selectedRoute": "authored-fallback", "fallbackReason": "authored-route"}

    receipt = asyncio.run(
        public_v2_orchestrator._resolve_generation_route_receipt(
            request,
            slide_count=10,
            resolver=resolve,
        )
    )
    assert captured == {
        "generation_mode": "brand-style",
        "source_mode": None,
        "authored_style": None,
        "slide_count": 10,
        "aspect_ratio": "16:9",
    }
    assert receipt.selected_route == "authored-fallback"
    assert receipt.design_pack_pin is None
    assert receipt.output_capability == "fidelity"
    sealed = receipt.model_dump(mode="json", by_alias=True)
    assert GenerationRouteReceiptV1.model_validate(sealed) == receipt
    with pytest.raises(ValidationError, match="receiptDigest"):
        GenerationRouteReceiptV1.model_validate(
            {**sealed, "receiptDigest": "sha256:" + ("0" * 64)}
        )

    # A resolver may no longer select the removed source-native route.
    def removed_route(**kwargs: object) -> dict[str, object]:
        return {"selectedRoute": "source-native", "narrativeIrDigest": "sha256:" + ("2" * 64)}

    with pytest.raises(PublicV2OrchestrationError) as removed:
        asyncio.run(
            public_v2_orchestrator._resolve_generation_route_receipt(
                request, slide_count=10, resolver=removed_route,
            )
        )
    assert removed.value.status_code == 503
    assert removed.value.reason_code == "generation_route_resolution_invalid"

    # The installed resolver: an explicit authored request takes the authored route.
    installed = asyncio.run(
        public_v2_orchestrator._resolve_generation_route_receipt(
            request,
            slide_count=10,
            resolver=None,
        )
    )
    assert installed.selected_route == "authored-fallback"
    assert installed.fallback_reason == "authored-route"
    assert public_v2_orchestrator._effective_generation_engine(request) == "deck-ir"

    # The same generationMode with strategy omitted is svg by default now --
    # it never reaches the authored resolver at all.
    svg_default_request = PresentationGenerationRequestV2(
        content="Brand style deck",
        generationMode="brand-style",
    )
    svg_default = asyncio.run(
        public_v2_orchestrator._resolve_generation_route_receipt(
            svg_default_request,
            slide_count=10,
            resolver=None,
        )
    )
    assert svg_default.selected_route == "svg-native"
    assert svg_default.fallback_reason is None
    assert public_v2_orchestrator._effective_generation_engine(svg_default_request) == (
        "authored"
    )

    legacy = PresentationGenerationRequestV2(
        content="Compatibility deck",
        generationEngine="authored",
    )
    assert public_v2_orchestrator._effective_generation_engine(legacy) == "authored"


def test_historical_source_native_receipts_still_validate() -> None:
    # Operations admitted to the removed design-pack route keep their sealed
    # receipt; status projection and the export integrity gate still read it.
    unsigned = {
        "schemaVersion": "generation-route-receipt/v1",
        "requestedMode": "registered-template",
        "selectedRoute": "source-native",
        "designPackPin": {"designPackId": "pack-1", "version": 1, "digest": "sha256:" + "1" * 64},
        "narrativeIrDigest": None,
        "fallbackReason": None,
        "outputCapability": "fidelity",
    }
    receipt = GenerationRouteReceiptV1.model_validate(
        {**unsigned, "receiptDigest": public_v2_orchestrator._generation_route_digest(unsigned)})
    assert receipt.selected_route == "source-native"


@pytest.mark.parametrize("mode", ["auto", "free-design"])
def test_source_restyle_uses_authored_reflow(mode: str) -> None:
    from services.public_generation_route import GenerationRouteError, resolve_authored_generation_route

    request = PresentationGenerationRequestV2(
        content="Preserve the source document",
        generationMode=mode,
        sourceMode="source_content_restyle",
        sourceContentManifestPin={
            "manifestId": str(uuid.uuid4()),
            "digest": "sha256:" + ("4" * 64),
        },
        contentPolicy={
            "text": "verbatim",
            "preserveAllTableCells": False,
            "preserveAllChartValues": False,
            "preserveAllImageOccurrences": True,
            "preserveSourceOrder": True,
            "preserveSourceSlideCount": True,
            "allowSlideExpansion": False,
            "allowSummarization": False,
            "overflowPolicy": "same_page_recompose_or_fail",
        },
        authoredStyle="devax-first-ten",
        slideCount=43,
    )
    captured: dict[str, object] = {}

    def resolve(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return resolve_authored_generation_route(**kwargs)

    receipt = asyncio.run(
        public_v2_orchestrator._resolve_generation_route_receipt(
            request,
            slide_count=43,
            resolver=resolve,
        )
    )
    assert captured["source_mode"] == "source_content_restyle"
    assert captured["authored_style"] == "devax-first-ten"
    assert receipt.selected_route == "authored-fallback"
    assert receipt.fallback_reason == "source-content-authored-reflow"
    assert receipt.design_pack_pin is None
    assert public_v2_orchestrator._effective_generation_engine(request) == "authored"
    with pytest.raises(GenerationRouteError, match="source_content_template_required"):
        resolve_authored_generation_route(source_mode="source_content_restyle", authored_style=" ")


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


def test_legacy_async_generation_accepts_supported_workspace_runtimes() -> None:
    _require_local_legacy_async_generation("sqlite")
    _require_local_legacy_async_generation("postgresql")

    with pytest.raises(HTTPException) as rejected:
        _require_local_legacy_async_generation("mysql")
    assert rejected.value.status_code == 501
