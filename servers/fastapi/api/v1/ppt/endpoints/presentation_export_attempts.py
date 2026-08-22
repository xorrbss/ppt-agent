"""Internal durable-attempt contract used by the Next.js Hybrid exporter."""

from __future__ import annotations

from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_async_session
from services.presentation_export_direct_attempt_service import (
    begin_direct_hybrid_export_attempt,
    complete_direct_hybrid_export_attempt,
    fail_direct_hybrid_export_attempt,
    list_direct_hybrid_export_attempt_history,
)
from services.presentation_export_capabilities import PresentationExportContractError


PRESENTATION_EXPORT_ATTEMPT_ROUTER = APIRouter()


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class BeginHybridAttemptRequest(_CamelModel):
    expected_presentation_revision: int = Field(ge=0)
    renderer_source_sha256: str
    idempotency_key: str = Field(min_length=1, max_length=256)

    @field_validator("renderer_source_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
            raise ValueError("rendererSourceSha256 must be SHA-256")
        return normalized


class CompleteHybridAttemptRequest(_CamelModel):
    pptx_sha256: str
    pptx_size_bytes: int = Field(ge=22, le=512 * 1024 * 1024)
    artifact_path: str = Field(min_length=1, max_length=4096)
    quality: dict[str, Any]
    coverage: dict[str, Any]
    power_point_qa: dict[str, Any] | None = None

    @field_validator("pptx_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return BeginHybridAttemptRequest.validate_digest(value)


class FailHybridAttemptRequest(_CamelModel):
    failure_code: str = Field(min_length=1, max_length=128)


def _raise_contract(error: PresentationExportContractError) -> None:
    status = 404 if error.code.endswith("not_found") else 409
    raise HTTPException(status_code=status, detail=error.code) from error


@PRESENTATION_EXPORT_ATTEMPT_ROUTER.post(
    "/{presentation_id}/export-attempts/hybrid/begin"
)
async def begin_hybrid_attempt(
    presentation_id: uuid.UUID,
    request: BeginHybridAttemptRequest,
    session: AsyncSession = Depends(get_async_session),
):
    try:
        attempt, evidence = await begin_direct_hybrid_export_attempt(
            session,
            presentation_id=presentation_id,
            expected_revision=request.expected_presentation_revision,
            renderer_source_sha256=request.renderer_source_sha256,
            idempotency_key=request.idempotency_key,
        )
    except PresentationExportContractError as error:
        _raise_contract(error)
    return {
        "exportAttemptId": str(attempt.id),
        "presentationRevision": evidence["presentation_revision"],
        "canonicalSourceDigest": evidence["canonical_source_digest"],
        "rendererSourceDigest": evidence["renderer_source_digest"],
        "htmlSourceDigest": evidence["html_source_digest"],
        "compositionDigest": evidence["composition_digest"],
    }


@PRESENTATION_EXPORT_ATTEMPT_ROUTER.post(
    "/{presentation_id}/export-attempts/hybrid/{attempt_id}/complete"
)
async def complete_hybrid_attempt(
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    request: CompleteHybridAttemptRequest,
    session: AsyncSession = Depends(get_async_session),
):
    try:
        attempt, finalized = await complete_direct_hybrid_export_attempt(
            session,
            presentation_id=presentation_id,
            attempt_id=attempt_id,
            pptx_sha256=request.pptx_sha256,
            pptx_size_bytes=request.pptx_size_bytes,
            artifact_path=request.artifact_path,
            quality=request.quality,
            coverage=request.coverage,
            powerpoint_qa=request.power_point_qa,
        )
    except PresentationExportContractError as error:
        try:
            await fail_direct_hybrid_export_attempt(
                session,
                presentation_id=presentation_id,
                attempt_id=attempt_id,
                failure_code=error.code,
            )
        except PresentationExportContractError:
            await session.rollback()
        _raise_contract(error)
    return {
        "exportAttemptId": str(attempt.id),
        "state": attempt.state,
        "presentationRevision": attempt.source_revision,
        "canonicalSourceDigest": attempt.source_snapshot_sha256,
        "pptxSha256": attempt.artifact_sha256,
        **finalized,
    }


@PRESENTATION_EXPORT_ATTEMPT_ROUTER.get(
    "/{presentation_id}/export-attempts/hybrid/history"
)
async def hybrid_attempt_history(
    presentation_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
):
    return await list_direct_hybrid_export_attempt_history(
        session,
        presentation_id=presentation_id,
    )


@PRESENTATION_EXPORT_ATTEMPT_ROUTER.post(
    "/{presentation_id}/export-attempts/hybrid/{attempt_id}/fail",
    status_code=204,
)
async def fail_hybrid_attempt(
    presentation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    request: FailHybridAttemptRequest,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    try:
        await fail_direct_hybrid_export_attempt(
            session,
            presentation_id=presentation_id,
            attempt_id=attempt_id,
            failure_code=request.failure_code,
        )
    except PresentationExportContractError as error:
        _raise_contract(error)
    return Response(status_code=204)


__all__ = ["PRESENTATION_EXPORT_ATTEMPT_ROUTER"]
