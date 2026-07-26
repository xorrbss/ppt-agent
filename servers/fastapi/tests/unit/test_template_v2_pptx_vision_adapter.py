from __future__ import annotations

import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from templates.v2.pptx.vision_adapter import (
    VisionAdapterError,
    VisionExecutionPolicy,
    VisionProviderMetadata,
    VisionProviderRequest,
    VisionProviderResponse,
    VisionSlideInput,
    VisionSlideResult,
    run_vision_provider,
)

SOURCE_SHA256 = "a" * 64
PAYLOAD = b"synthetic-preview-payload"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def _request() -> VisionProviderRequest:
    return VisionProviderRequest(
        source_sha256=SOURCE_SHA256,
        slides=(
            VisionSlideInput(
                slide_index=1,
                media_type="image/png",
                sha256=PAYLOAD_SHA256,
                payload=PAYLOAD,
            ),
        ),
    )


def _metadata(**overrides) -> VisionProviderMetadata:
    values = {
        "id": "mock-vision",
        "version": "1",
        "capabilities": ("ocr", "visual-structure"),
        "execution": "remote",
        "status": "available",
        "network_access": True,
        "external_ai": True,
        "egress_hosts": ("vision.example.invalid",),
        "credential_reference": "TEMPLATE_V2_VISION_API_KEY",
        "max_requests_per_analysis": 1,
    }
    values.update(overrides)
    return VisionProviderMetadata(**values)


def _policy(**overrides) -> VisionExecutionPolicy:
    values = {
        "enabled": True,
        "allowed_provider_ids": ("mock-vision",),
        "allowed_egress_hosts": ("vision.example.invalid",),
        "allow_credentials": True,
        "allowed_credential_references": ("TEMPLATE_V2_VISION_API_KEY",),
        "max_slides": 2,
        "max_input_bytes": 1024,
        "max_provider_requests": 1,
        "max_estimated_cost_microusd": 10,
        "max_actual_cost_microusd": 10,
        "timeout_ms": 500,
        "minimum_confidence": 0.8,
    }
    values.update(overrides)
    return VisionExecutionPolicy(**values)


class MockVisionProvider:
    def __init__(
        self,
        *,
        metadata: VisionProviderMetadata | None = None,
        estimate: int = 5,
        confidence: float = 0.95,
        delay: float = 0,
        response_overrides: dict | None = None,
    ):
        self._metadata = metadata or _metadata()
        self._estimate = estimate
        self._confidence = confidence
        self._delay = delay
        self._response_overrides = response_overrides or {}
        self.calls = 0
        self.cancelled = False

    @property
    def metadata(self) -> VisionProviderMetadata:
        return self._metadata

    def estimate_cost_microusd(self, request: VisionProviderRequest) -> int:
        return self._estimate

    async def analyze(
        self,
        request: VisionProviderRequest,
    ) -> VisionProviderResponse:
        self.calls += 1
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        values = {
            "provider_id": self.metadata.id,
            "source_sha256": request.source_sha256,
            "request_count": 1,
            "actual_cost_microusd": 5,
            "slides": tuple(
                VisionSlideResult(
                    slide_index=slide.slide_index,
                    confidence=self._confidence,
                    ocr_text="synthetic text",
                    diagnostic_codes=(),
                )
                for slide in request.slides
            ),
        }
        values.update(self._response_overrides)
        return VisionProviderResponse(**values)


def test_policy_is_default_off_and_does_not_invoke_provider() -> None:
    async def scenario() -> None:
        provider = MockVisionProvider()
        with pytest.raises(
            VisionAdapterError,
            match="vision_provider_disabled",
        ):
            await run_vision_provider(
                provider,
                _request(),
                VisionExecutionPolicy(),
            )
        assert provider.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("metadata", "policy", "expected"),
    [
        (
            _metadata(id="other-provider"),
            _policy(),
            "vision_provider_not_allowlisted",
        ),
        (
            _metadata(egress_hosts=("unapproved.example.invalid",)),
            _policy(),
            "vision_provider_egress_not_allowlisted",
        ),
        (
            _metadata(),
            _policy(allow_credentials=False, allowed_credential_references=()),
            "vision_provider_credentials_not_allowed",
        ),
        (
            _metadata(credential_reference="OTHER_VISION_KEY"),
            _policy(),
            "vision_provider_credential_reference_not_allowlisted",
        ),
    ],
)
def test_identity_egress_and_credential_policy_fail_before_invocation(
    metadata: VisionProviderMetadata,
    policy: VisionExecutionPolicy,
    expected: str,
) -> None:
    async def scenario() -> None:
        provider = MockVisionProvider(metadata=metadata)
        with pytest.raises(VisionAdapterError, match=expected):
            await run_vision_provider(provider, _request(), policy)
        assert provider.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("provider", "policy", "expected"),
    [
        (
            MockVisionProvider(estimate=11),
            _policy(),
            "vision_provider_estimated_cost_limit_exceeded",
        ),
        (
            MockVisionProvider(response_overrides={"actual_cost_microusd": 11}),
            _policy(),
            "vision_provider_actual_cost_limit_exceeded",
        ),
        (
            MockVisionProvider(metadata=_metadata(max_requests_per_analysis=2)),
            _policy(),
            "vision_provider_request_limit_exceeded",
        ),
    ],
)
def test_rate_and_cost_limits_are_fail_closed(
    provider: MockVisionProvider,
    policy: VisionExecutionPolicy,
    expected: str,
) -> None:
    async def scenario() -> None:
        with pytest.raises(VisionAdapterError, match=expected):
            await run_vision_provider(provider, _request(), policy)

    asyncio.run(scenario())


def test_result_is_bound_to_provider_source_and_exact_slide_set() -> None:
    async def scenario() -> None:
        for response_overrides, expected in (
            (
                {"provider_id": "substituted"},
                "vision_provider_response_identity_mismatch",
            ),
            (
                {"source_sha256": "b" * 64},
                "vision_provider_response_source_mismatch",
            ),
            (
                {"slides": (VisionSlideResult(slide_index=2, confidence=0.9),)},
                "vision_provider_response_slide_mismatch",
            ),
        ):
            provider = MockVisionProvider(response_overrides=response_overrides)
            with pytest.raises(VisionAdapterError, match=expected):
                await run_vision_provider(provider, _request(), _policy())

    asyncio.run(scenario())


def test_low_confidence_is_preserved_but_forces_manual_review() -> None:
    async def scenario() -> None:
        result = await run_vision_provider(
            MockVisionProvider(confidence=0.79),
            _request(),
            _policy(),
        )

        assert result.review_required is True
        assert result.low_confidence_slide_indices == (1,)
        assert result.response.slides[0].ocr_text == "synthetic text"

    asyncio.run(scenario())


def test_result_and_nested_response_are_immutable() -> None:
    async def scenario() -> None:
        result = await run_vision_provider(
            MockVisionProvider(),
            _request(),
            _policy(),
        )

        assert isinstance(result.response.slides, tuple)
        with pytest.raises(ValidationError):
            result.response.slides = ()
        with pytest.raises(ValidationError):
            result.response.slides[0].ocr_text = "tampered"
        with pytest.raises(ValidationError):
            result.provider.id = "substituted"
        with pytest.raises(ValidationError):
            result.response = VisionProviderResponse(
                provider_id="substituted",
                source_sha256=SOURCE_SHA256,
                request_count=1,
                actual_cost_microusd=0,
                slides=(VisionSlideResult(slide_index=1, confidence=1.0),),
            )

    asyncio.run(scenario())


def test_timeout_and_cancellation_stop_the_injected_provider() -> None:
    async def scenario() -> None:
        timed_out_provider = MockVisionProvider(delay=0.1)
        with pytest.raises(
            VisionAdapterError,
            match="vision_provider_timeout",
        ):
            await run_vision_provider(
                timed_out_provider,
                _request(),
                _policy(timeout_ms=1),
            )
        assert timed_out_provider.cancelled is True

        cancellation = asyncio.Event()
        cancellation.set()
        provider = MockVisionProvider()
        with pytest.raises(
            VisionAdapterError,
            match="vision_provider_cancelled",
        ):
            await run_vision_provider(
                provider,
                _request(),
                _policy(),
                cancellation=cancellation,
            )
        assert provider.calls == 0

    asyncio.run(scenario())


def test_in_flight_cancellation_stops_the_injected_provider() -> None:
    async def scenario() -> None:
        cancellation = asyncio.Event()
        provider = MockVisionProvider(delay=0.1)

        async def cancel_after_start() -> None:
            while provider.calls == 0:
                await asyncio.sleep(0)
            cancellation.set()

        cancellation_task = asyncio.create_task(cancel_after_start())
        with pytest.raises(
            VisionAdapterError,
            match="vision_provider_cancelled",
        ):
            await run_vision_provider(
                provider,
                _request(),
                _policy(),
                cancellation=cancellation,
            )
        await cancellation_task
        assert provider.cancelled is True

    asyncio.run(scenario())


def test_payload_digest_and_contract_shapes_reject_tampering() -> None:
    with pytest.raises(ValueError, match="vision_slide_digest_mismatch"):
        VisionSlideInput(
            slide_index=1,
            media_type="image/png",
            sha256="0" * 64,
            payload=PAYLOAD,
        )

    with pytest.raises(
        ValidationError,
        match="local_vision_provider_cannot_declare_egress",
    ):
        _metadata(
            execution="local",
            network_access=True,
            egress_hosts=("vision.example.invalid",),
            credential_reference=None,
        )
