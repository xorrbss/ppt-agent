from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

Sha256Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Confidence = Annotated[FiniteFloat, Field(ge=0, le=1)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
CredentialReference = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$"),
]

_HOSTNAME = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class VisionAdapterModel(BaseModel):
    """Closed JSON boundary for provider metadata, policy, and output."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class VisionProviderMetadata(VisionAdapterModel):
    id: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    version: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    capabilities: Annotated[
        tuple[Literal["ocr", "visual-structure"], ...],
        Field(min_length=1, max_length=2),
    ]
    execution: Literal["local", "remote"]
    status: Literal["available", "unavailable"]
    network_access: StrictBool
    external_ai: StrictBool
    egress_hosts: Annotated[tuple[StrictStr, ...], Field(max_length=8)] = ()
    credential_reference: CredentialReference | None = None
    max_requests_per_analysis: PositiveInt = 1

    @model_validator(mode="after")
    def validate_execution_facts(self) -> VisionProviderMetadata:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate_vision_provider_capability")
        if len(self.egress_hosts) != len(set(self.egress_hosts)):
            raise ValueError("duplicate_vision_provider_egress_host")
        if any(
            host != host.lower() or _HOSTNAME.fullmatch(host) is None
            for host in self.egress_hosts
        ):
            raise ValueError("invalid_vision_provider_egress_host")
        if self.execution == "local":
            if self.network_access or self.egress_hosts:
                raise ValueError("local_vision_provider_cannot_declare_egress")
            if self.credential_reference is not None:
                raise ValueError("local_vision_provider_cannot_require_credentials")
        elif not self.network_access or not self.egress_hosts:
            raise ValueError("remote_vision_provider_requires_bounded_egress")
        return self


class VisionExecutionPolicy(VisionAdapterModel):
    """Per-import policy. Defaults are deliberately unusable/fail-closed."""

    enabled: StrictBool = False
    allowed_provider_ids: tuple[StrictStr, ...] = ()
    allowed_egress_hosts: tuple[StrictStr, ...] = ()
    allow_credentials: StrictBool = False
    allowed_credential_references: tuple[CredentialReference, ...] = ()
    max_slides: PositiveInt = 1
    max_input_bytes: PositiveInt = 5 * 1024 * 1024
    max_provider_requests: PositiveInt = 1
    max_estimated_cost_microusd: NonNegativeInt = 0
    max_actual_cost_microusd: NonNegativeInt = 0
    timeout_ms: PositiveInt = 10_000
    minimum_confidence: Confidence = 0.8

    @model_validator(mode="after")
    def validate_allowlists(self) -> VisionExecutionPolicy:
        for values, error in (
            (
                self.allowed_provider_ids,
                "duplicate_vision_policy_provider_id",
            ),
            (
                self.allowed_egress_hosts,
                "duplicate_vision_policy_egress_host",
            ),
            (
                self.allowed_credential_references,
                "duplicate_vision_policy_credential_reference",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(error)
        if any(
            host != host.lower() or _HOSTNAME.fullmatch(host) is None
            for host in self.allowed_egress_hosts
        ):
            raise ValueError("invalid_vision_policy_egress_host")
        if not self.allow_credentials and self.allowed_credential_references:
            raise ValueError("credential_allowlist_requires_credential_access")
        return self


@dataclass(frozen=True)
class VisionSlideInput:
    """Ephemeral preview bytes passed to a provider; never a serializable model."""

    slide_index: int
    media_type: Literal["image/png", "image/jpeg"]
    sha256: str
    payload: bytes

    def __post_init__(self) -> None:
        if isinstance(self.slide_index, bool) or self.slide_index < 1:
            raise ValueError("vision_slide_index_must_be_positive")
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("invalid_vision_slide_digest")
        if not isinstance(self.payload, bytes) or not self.payload:
            raise ValueError("vision_slide_payload_required")
        if hashlib.sha256(self.payload).hexdigest() != self.sha256:
            raise ValueError("vision_slide_digest_mismatch")


@dataclass(frozen=True)
class VisionProviderRequest:
    """Ephemeral request. It contains no credential value or external URL."""

    source_sha256: str
    slides: tuple[VisionSlideInput, ...]

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None:
            raise ValueError("invalid_vision_source_digest")
        if not self.slides:
            raise ValueError("vision_request_requires_slides")
        indexes = [slide.slide_index for slide in self.slides]
        if len(indexes) != len(set(indexes)):
            raise ValueError("duplicate_vision_slide_index")


class VisionSlideResult(VisionAdapterModel):
    slide_index: PositiveInt
    confidence: Confidence
    ocr_text: Annotated[StrictStr, Field(max_length=20_000)] | None = None
    diagnostic_codes: Annotated[
        tuple[Annotated[StrictStr, Field(min_length=1, max_length=80)], ...],
        Field(max_length=32),
    ] = ()

    @model_validator(mode="after")
    def validate_diagnostics(self) -> VisionSlideResult:
        if len(self.diagnostic_codes) != len(set(self.diagnostic_codes)):
            raise ValueError("duplicate_vision_diagnostic_code")
        return self


class VisionProviderResponse(VisionAdapterModel):
    contract_version: Literal[1] = 1
    provider_id: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    source_sha256: Sha256Digest
    request_count: PositiveInt
    actual_cost_microusd: NonNegativeInt
    slides: Annotated[tuple[VisionSlideResult, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_slide_identity(self) -> VisionProviderResponse:
        indexes = [slide.slide_index for slide in self.slides]
        if len(indexes) != len(set(indexes)):
            raise ValueError("duplicate_vision_result_slide_index")
        return self


class VisionAdapterResult(VisionAdapterModel):
    contract_version: Literal[1] = 1
    provider: VisionProviderMetadata
    response: VisionProviderResponse
    review_required: StrictBool
    low_confidence_slide_indices: tuple[PositiveInt, ...]


class VisionAdapterError(RuntimeError):
    """Content-free failure with a stable code suitable for task persistence."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@runtime_checkable
class VisionProvider(Protocol):
    @property
    def metadata(self) -> VisionProviderMetadata: ...

    def estimate_cost_microusd(
        self,
        request: VisionProviderRequest,
        /,
    ) -> int: ...

    async def analyze(
        self,
        request: VisionProviderRequest,
        /,
    ) -> VisionProviderResponse: ...


def _preflight(
    provider: VisionProvider,
    request: VisionProviderRequest,
    policy: VisionExecutionPolicy,
) -> VisionProviderMetadata:
    metadata = provider.metadata
    if not policy.enabled:
        raise VisionAdapterError("vision_provider_disabled")
    if metadata.status != "available":
        raise VisionAdapterError("vision_provider_unavailable")
    if metadata.id not in policy.allowed_provider_ids:
        raise VisionAdapterError("vision_provider_not_allowlisted")
    if not set(metadata.egress_hosts).issubset(policy.allowed_egress_hosts):
        raise VisionAdapterError("vision_provider_egress_not_allowlisted")
    if metadata.credential_reference is not None:
        if not policy.allow_credentials:
            raise VisionAdapterError("vision_provider_credentials_not_allowed")
        if metadata.credential_reference not in policy.allowed_credential_references:
            raise VisionAdapterError(
                "vision_provider_credential_reference_not_allowlisted"
            )
    if len(request.slides) > policy.max_slides:
        raise VisionAdapterError("vision_provider_slide_limit_exceeded")
    if sum(len(slide.payload) for slide in request.slides) > policy.max_input_bytes:
        raise VisionAdapterError("vision_provider_input_limit_exceeded")
    if metadata.max_requests_per_analysis > policy.max_provider_requests:
        raise VisionAdapterError("vision_provider_request_limit_exceeded")
    estimate = provider.estimate_cost_microusd(request)
    if isinstance(estimate, bool) or not isinstance(estimate, int) or estimate < 0:
        raise VisionAdapterError("vision_provider_invalid_cost_estimate")
    if estimate > policy.max_estimated_cost_microusd:
        raise VisionAdapterError("vision_provider_estimated_cost_limit_exceeded")
    return metadata


async def _invoke_with_cancellation(
    invocation: Awaitable[VisionProviderResponse],
    *,
    cancellation: asyncio.Event | None,
    timeout_ms: int,
) -> VisionProviderResponse:
    provider_task = asyncio.create_task(invocation)
    cancellation_task = (
        asyncio.create_task(cancellation.wait()) if cancellation is not None else None
    )
    wait_for = (
        {provider_task, cancellation_task}
        if cancellation_task is not None
        else {provider_task}
    )
    done, _ = await asyncio.wait(
        wait_for,
        timeout=timeout_ms / 1000,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if provider_task in done:
        if cancellation_task is not None:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)
        return await provider_task

    provider_task.cancel()
    await asyncio.gather(provider_task, return_exceptions=True)
    if cancellation_task is not None:
        cancellation_task.cancel()
        await asyncio.gather(cancellation_task, return_exceptions=True)
    if cancellation is not None and cancellation.is_set():
        raise VisionAdapterError("vision_provider_cancelled")
    raise VisionAdapterError("vision_provider_timeout")


async def run_vision_provider(
    provider: VisionProvider,
    request: VisionProviderRequest,
    policy: VisionExecutionPolicy,
    *,
    cancellation: asyncio.Event | None = None,
) -> VisionAdapterResult:
    """Run one injected provider behind bounded, fail-closed policy checks."""

    if cancellation is not None and cancellation.is_set():
        raise VisionAdapterError("vision_provider_cancelled")
    metadata = _preflight(provider, request, policy)
    response = await _invoke_with_cancellation(
        provider.analyze(request),
        cancellation=cancellation,
        timeout_ms=policy.timeout_ms,
    )
    if response.provider_id != metadata.id:
        raise VisionAdapterError("vision_provider_response_identity_mismatch")
    if response.source_sha256 != request.source_sha256:
        raise VisionAdapterError("vision_provider_response_source_mismatch")
    expected_indexes = {slide.slide_index for slide in request.slides}
    actual_indexes = {slide.slide_index for slide in response.slides}
    if actual_indexes != expected_indexes:
        raise VisionAdapterError("vision_provider_response_slide_mismatch")
    if response.request_count > policy.max_provider_requests:
        raise VisionAdapterError("vision_provider_actual_request_limit_exceeded")
    if response.request_count > metadata.max_requests_per_analysis:
        raise VisionAdapterError("vision_provider_undeclared_request_count_exceeded")
    if response.actual_cost_microusd > policy.max_actual_cost_microusd:
        raise VisionAdapterError("vision_provider_actual_cost_limit_exceeded")
    low_confidence = tuple(
        slide.slide_index
        for slide in response.slides
        if slide.confidence < policy.minimum_confidence
    )
    return VisionAdapterResult(
        provider=metadata,
        response=response,
        review_required=bool(low_confidence),
        low_confidence_slide_indices=low_confidence,
    )
