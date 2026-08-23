"""Durable artifact CAS and fenced PowerPoint COM qualification control plane."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.release_authority_v3 import (
    ComQualificationCreateRequestV3,
    ComQualificationJobV3,
    ComQualificationTransitionRequestV3,
    DurableArtifactPutRequestV3,
    DurableArtifactRefV3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_V3,
)
from models.sql.authored_release_evidence_v3 import (
    AuthoredComQualificationJobV3,
    AuthoredReleaseArtifactV3,
)
from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest
from services.authored_vnext.semantic.release_authority_v3 import (
    consume_authority_nonce_v3,
    validate_actual_product_authority_v3,
)
from utils.datetime_utils import get_current_utc_datetime


_LEASE_TTL = timedelta(seconds=900)
_ORDER = (
    "captured",
    "planned",
    "assembled",
    "inputOoxmlVerified",
    "firstOpenObserved",
    "savedCopyVerified",
    "reopenObserved",
    "comVerified",
    "sealed",
)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _artifact_ref(record: AuthoredReleaseArtifactV3) -> DurableArtifactRefV3:
    return DurableArtifactRefV3(
        export_attempt_id=record.export_attempt_id,
        artifact_id=record.artifact_id,
        version_id=record.version_id,
        sha256=record.sha256,
        byte_length=record.byte_length,
        media_type=record.media_type,
        encryption_key_ref=record.encryption_key_ref,
        storage_generation=record.storage_generation,
        state=record.state,
    )


def _job(record: AuthoredComQualificationJobV3) -> ComQualificationJobV3:
    return ComQualificationJobV3(
        job_id=record.id,
        export_attempt_id=record.export_attempt_id,
        evidence_policy_digest=record.evidence_policy_digest,
        authority_domain="actual_product",
        state=record.state,
        transition_sequence=record.transition_sequence,
        lease_owner=record.lease_owner,
        lease_token=record.lease_token,
        lease_expires_at=record.lease_expires_at,
        heartbeat_at=record.heartbeat_at,
        attempt_count=record.attempt_count,
        evidence=dict(record.evidence),
        result_digest=(canonical_digest(record.evidence) if record.state == "sealed" else None),
        failure_code=record.failure_code,
    )


async def put_durable_artifact_v3(
    session: AsyncSession, *, request: DurableArtifactPutRequestV3, tenant_id: str
) -> DurableArtifactRefV3:
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except ValueError as error:
        raise SemanticContractError("authored_release_artifact_base64_invalid") from error
    observed_digest = "sha256:" + sha256(content).hexdigest()
    if not content or observed_digest != request.sha256:
        raise SemanticContractError("authored_release_artifact_digest_mismatch")
    existing = await session.scalar(
        select(AuthoredReleaseArtifactV3).where(
            AuthoredReleaseArtifactV3.tenant_id == tenant_id,
            AuthoredReleaseArtifactV3.export_attempt_id == request.export_attempt_id,
            AuthoredReleaseArtifactV3.artifact_id == request.artifact_id,
        )
    )
    if existing is not None:
        readback_digest = "sha256:" + sha256(bytes(existing.content_bytes)).hexdigest()
        if (
            existing.sha256 != request.sha256
            or readback_digest != existing.sha256
            or existing.byte_length != len(content)
            or bytes(existing.content_bytes) != content
            or existing.media_type != request.media_type
            or existing.encryption_key_ref != request.encryption_key_ref
            or existing.storage_generation != request.storage_generation
        ):
            raise SemanticContractError("authored_release_artifact_cas_conflict")
        return _artifact_ref(existing)
    record = AuthoredReleaseArtifactV3(
        tenant_id=tenant_id,
        export_attempt_id=request.export_attempt_id,
        artifact_id=request.artifact_id,
        sha256=request.sha256,
        byte_length=len(content),
        media_type=request.media_type,
        encryption_key_ref=request.encryption_key_ref,
        storage_generation=request.storage_generation,
        state="durable",
        content_bytes=content,
    )
    session.add(record)
    await session.flush()
    if "sha256:" + sha256(bytes(record.content_bytes)).hexdigest() != record.sha256:
        raise SemanticContractError("authored_release_artifact_readback_mismatch")
    return _artifact_ref(record)


async def bind_durable_artifact_v3(
    session: AsyncSession,
    *,
    artifact_id: str,
    export_attempt_id,
    expected_version_id,
    expected_sha256: str,
    tenant_id: str,
) -> DurableArtifactRefV3:
    record = await session.scalar(
        select(AuthoredReleaseArtifactV3).where(
            AuthoredReleaseArtifactV3.tenant_id == tenant_id,
            AuthoredReleaseArtifactV3.export_attempt_id == export_attempt_id,
            AuthoredReleaseArtifactV3.artifact_id == artifact_id,
        )
    )
    if (
        record is None
        or record.version_id != expected_version_id
        or record.sha256 != expected_sha256
        or "sha256:" + sha256(bytes(record.content_bytes)).hexdigest() != expected_sha256
    ):
        raise SemanticContractError("authored_release_artifact_binding_mismatch")
    if record.state == "bound":
        return _artifact_ref(record)
    result = await session.execute(
        update(AuthoredReleaseArtifactV3)
        .where(
            AuthoredReleaseArtifactV3.version_id == record.version_id,
            AuthoredReleaseArtifactV3.state == "durable",
            AuthoredReleaseArtifactV3.sha256 == expected_sha256,
        )
        .values(state="bound")
    )
    if result.rowcount != 1:
        raise SemanticContractError("authored_release_artifact_cas_conflict")
    await session.flush()
    await session.refresh(record)
    return _artifact_ref(record)


async def create_com_qualification_job_v3(
    session: AsyncSession, *, request: ComQualificationCreateRequestV3, tenant_id: str
) -> ComQualificationJobV3:
    existing = await session.scalar(
        select(AuthoredComQualificationJobV3).where(
            AuthoredComQualificationJobV3.tenant_id == tenant_id,
            AuthoredComQualificationJobV3.export_attempt_id == request.export_attempt_id,
            AuthoredComQualificationJobV3.evidence_policy_digest == request.evidence_policy_digest,
            AuthoredComQualificationJobV3.authority_domain == "actual_product",
        )
    )
    if existing is not None:
        return _job(existing)
    record = AuthoredComQualificationJobV3(
        tenant_id=tenant_id,
        export_attempt_id=request.export_attempt_id,
        evidence_policy_digest=request.evidence_policy_digest,
        authority_domain="actual_product",
    )
    session.add(record)
    await session.flush()
    return _job(record)


async def claim_com_qualification_job_v3(
    session: AsyncSession,
    *,
    job_id,
    worker_id: str,
    tenant_id: str,
    now: datetime | None = None,
) -> ComQualificationJobV3:
    observed_at = _utc(now or get_current_utc_datetime())
    token = secrets.token_urlsafe(32)
    result = await session.execute(
        update(AuthoredComQualificationJobV3)
        .where(
            AuthoredComQualificationJobV3.id == job_id,
            AuthoredComQualificationJobV3.tenant_id == tenant_id,
            AuthoredComQualificationJobV3.state.not_in(("sealed", "rejected")),
            AuthoredComQualificationJobV3.attempt_count < 2,
            (
                AuthoredComQualificationJobV3.lease_expires_at.is_(None)
                | (AuthoredComQualificationJobV3.lease_expires_at <= observed_at)
            ),
        )
        .values(
            lease_owner=worker_id,
            lease_token=token,
            lease_expires_at=observed_at + _LEASE_TTL,
            heartbeat_at=observed_at,
            attempt_count=AuthoredComQualificationJobV3.attempt_count + 1,
            updated_at=observed_at,
        )
    )
    if result.rowcount != 1:
        raise SemanticContractError("authored_com_qualification_claim_conflict")
    record = await session.get(AuthoredComQualificationJobV3, job_id)
    assert record is not None
    return _job(record)


def _sealed_evidence_error(evidence: dict[str, Any]) -> str | None:
    if evidence.get("repairDialogs") != 0:
        return "repair_dialog_observed"
    if evidence.get("powerPointSurvivorPids") != []:
        return "powerpoint_process_survived"
    if evidence.get("desktopInterference") is not False:
        return "desktop_interference_observed"
    if evidence.get("ooxmlRepairObserved") is not False:
        return "ooxml_repair_observed"
    if evidence.get("schemaVersion") != "authored-com-qualification-evidence/v3":
        return "qualification_evidence_schema_invalid"
    saved = evidence.get("savedCopyPptxDigest")
    if not isinstance(saved, str) or evidence.get("reopenInputPptxDigest") != saved:
        return "reopen_digest_mismatch"
    required = (
        "inputPptxDigest",
        "savedCopyPptxDigest",
        "reopenInputPptxDigest",
        "firstOpenSnapshotDigest",
        "reopenSnapshotDigest",
        "workerAttestationDigest",
        "environmentAttestationDigest",
        "comSnapshotDigest",
        "ooxmlReadbackDigest",
    )
    if any(not isinstance(evidence.get(key), str) for key in required):
        return "qualification_evidence_incomplete"
    artifact_digests = evidence.get("artifactDigests")
    if (
        not isinstance(artifact_digests, dict)
        or list(artifact_digests) != list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3)
        or any(not isinstance(value, str) or not value.startswith("sha256:") for value in artifact_digests.values())
        or evidence.get("inputPptxDigest") != artifact_digests.get("hybrid-input-pptx")
        or evidence.get("savedCopyPptxDigest") != artifact_digests.get("hybrid-saved-pptx")
        or evidence.get("firstOpenSnapshotDigest") != artifact_digests.get("hybrid-first-open-slide-01-png")
        or evidence.get("reopenSnapshotDigest") != artifact_digests.get("hybrid-reopen-slide-01-png")
        or evidence.get("comSnapshotDigest") != artifact_digests.get("hybrid-reopen-com-json")
        or evidence.get("ooxmlReadbackDigest") != artifact_digests.get("hybrid-saved-ooxml-json")
        or evidence.get("comState")
        != {"firstOpen": "observed", "savedCopy": "verified", "reopen": "observed", "readback": "verified"}
    ):
        return "qualification_artifact_evidence_invalid"
    return None


async def _sealed_artifact_error(
    session: AsyncSession, *, record: AuthoredComQualificationJobV3, evidence: dict[str, Any]
) -> str | None:
    error = _sealed_evidence_error(evidence)
    if error is not None:
        return error
    records = list(
        await session.scalars(
            select(AuthoredReleaseArtifactV3).where(
                AuthoredReleaseArtifactV3.tenant_id == record.tenant_id,
                AuthoredReleaseArtifactV3.export_attempt_id == record.export_attempt_id,
                AuthoredReleaseArtifactV3.artifact_id.in_(list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3)),
            )
        )
    )
    by_id = {item.artifact_id: item for item in records}
    artifact_digests = evidence["artifactDigests"]
    if any(
        (item := by_id.get(artifact_id)) is None
        or item.state != "bound"
        or item.sha256 != artifact_digests[artifact_id]
        or "sha256:" + sha256(bytes(item.content_bytes)).hexdigest() != item.sha256
        for artifact_id in GATE1_GRAPH_BASE_ARTIFACT_IDS_V3
    ):
        return "qualification_artifact_readback_mismatch"
    return None


async def transition_com_qualification_job_v3(
    session: AsyncSession,
    *,
    job_id,
    request: ComQualificationTransitionRequestV3,
    tenant_id: str,
    now: datetime | None = None,
) -> ComQualificationJobV3:
    observed_at = _utc(now or get_current_utc_datetime())
    signed_transition = request.model_dump(mode="json", by_alias=True)
    signed_transition.pop("authority", None)
    transition_authority_digest = validate_actual_product_authority_v3(
        signed_transition, request.authority, now=observed_at
    )
    nonce_is_fresh = await consume_authority_nonce_v3(
        session,
        authority=request.authority,
        subject_digest=transition_authority_digest,
    )
    record = await session.get(AuthoredComQualificationJobV3, job_id)
    if (
        not nonce_is_fresh
        and record is not None
        and record.state == request.next_state
        and record.transition_sequence == request.expected_transition_sequence + 1
    ):
        return _job(record)
    if (
        record is None
        or record.tenant_id != tenant_id
        or record.lease_owner != request.worker_id
        or record.lease_token != request.lease_token
        or record.lease_expires_at is None
        or _utc(record.lease_expires_at) <= observed_at
        or record.state != request.expected_state
        or record.transition_sequence != request.expected_transition_sequence
    ):
        raise SemanticContractError("authored_com_qualification_stale_cas")
    valid_next = request.next_state == "rejected" or (
        request.expected_state in _ORDER
        and _ORDER.index(request.expected_state) + 1 < len(_ORDER)
        and request.next_state == _ORDER[_ORDER.index(request.expected_state) + 1]
    )
    if not valid_next:
        raise SemanticContractError("authored_com_qualification_transition_invalid")
    merged = {**dict(record.evidence), **request.evidence}
    next_state = request.next_state
    failure_code = request.failure_code
    if next_state == "sealed":
        failure_code = await _sealed_artifact_error(session, record=record, evidence=merged)
        if failure_code is not None:
            next_state = "rejected"
    if next_state == "rejected" and not failure_code:
        raise SemanticContractError("authored_com_qualification_failure_code_required")
    result = await session.execute(
        update(AuthoredComQualificationJobV3)
        .where(
            AuthoredComQualificationJobV3.id == job_id,
            AuthoredComQualificationJobV3.state == request.expected_state,
            AuthoredComQualificationJobV3.transition_sequence == request.expected_transition_sequence,
            AuthoredComQualificationJobV3.lease_token == request.lease_token,
        )
        .values(
            state=next_state,
            transition_sequence=request.expected_transition_sequence + 1,
            evidence=merged,
            failure_code=failure_code,
            heartbeat_at=observed_at,
            lease_expires_at=(None if next_state in {"sealed", "rejected"} else observed_at + _LEASE_TTL),
            lease_owner=None if next_state in {"sealed", "rejected"} else request.worker_id,
            lease_token=None if next_state in {"sealed", "rejected"} else request.lease_token,
            updated_at=observed_at,
        )
    )
    if result.rowcount != 1:
        raise SemanticContractError("authored_com_qualification_stale_cas")
    await session.flush()
    await session.refresh(record)
    return _job(record)


async def get_com_qualification_job_v3(session: AsyncSession, *, job_id, tenant_id: str) -> ComQualificationJobV3:
    record = await session.get(AuthoredComQualificationJobV3, job_id)
    if record is None or record.tenant_id != tenant_id:
        raise SemanticContractError("authored_com_qualification_not_found")
    return _job(record)


__all__ = [
    "bind_durable_artifact_v3",
    "claim_com_qualification_job_v3",
    "create_com_qualification_job_v3",
    "get_com_qualification_job_v3",
    "put_durable_artifact_v3",
    "transition_com_qualification_job_v3",
]
