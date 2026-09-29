"""Durable original-first render captures for authored local fit.

The original capture is deliberately stored outside the mutable authored
render pointer.  A later fitted render may replace that pointer, while this
content-bound checkpoint remains available to a fenced retry of the same
workspace operation.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import select

from utils.hashing import canonical_json_bytes, sha256_prefixed
from models.sql.public_artifact import PublicArtifact, PublicSlideCheckpoint
from models.sql.workspace import WorkspaceOperation
from services.artifact_store import (
    configured_public_artifact_service,
    put_slide_checkpoint_if_current,
)
from services.authored_admission_capture import canonical_capture_html
from services.authored_checkpoint_artifacts import atomic_write, checkpoint_root
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_checkpoint_fence import owned_job, public_checkpoint_binding
from services.database import async_session_maker
from utils.slide_capture_local_fit import local_fit_render_identity
from utils.slide_capture_result import RenderCapture


SCHEMA_VERSION = "authored-original-capture-checkpoint/v1"
STAGE_VERSION = "authored-original-capture/v1"


def _canonical_json(value: object) -> bytes:
    return canonical_json_bytes(value)


def _sha256(payload: bytes) -> str:
    return sha256_prefixed(payload)


def _html_digest(html: str) -> str:
    return _sha256(html.encode("utf-8"))


def _normalized_policy_identity(
    policy: Mapping[str, Any], *, html_digest: str
) -> dict[str, Any]:
    """Make raw and prepared HTML-bound policies compare as one identity."""

    normalized = dict(policy)
    for key in ("author_revision", "original_html_digest"):
        value = normalized.get(key)
        if isinstance(value, str) and value == html_digest:
            normalized[key] = "$canonicalHtml"
    return normalized


def _policy_for_prepared_html(
    policy: Mapping[str, Any], *, original_html: str, prepared_html: str
) -> dict[str, Any]:
    """Mirror capture ownership's rebinding of HTML-addressed policy fields."""

    prepared_policy = dict(policy)
    original_digest = _html_digest(original_html)
    prepared_digest = _html_digest(prepared_html)
    if original_digest == prepared_digest:
        return prepared_policy
    for key in ("author_revision", "original_html_digest"):
        if prepared_policy.get(key) == original_digest:
            prepared_policy[key] = prepared_digest
    return prepared_policy


def _input_identity(
    *, html: str, policy: Mapping[str, Any], renderer_identity: str
) -> str:
    html_digest = _html_digest(html)
    return _sha256(
        _canonical_json(
            {
                "htmlDigest": html_digest,
                "policy": _normalized_policy_identity(
                    policy, html_digest=html_digest
                ),
                "rendererIdentity": renderer_identity,
                "fitMode": False,
            }
        )
    )


def _encode_bundle(
    *, slide_index: int, capture: RenderCapture, policy: Mapping[str, Any]
) -> tuple[bytes, str]:
    html = capture.prepared_html
    metrics = capture.browser_metrics
    fit_report = capture.fit_report
    prepared_policy = capture.prepared_fit_policy or policy
    if (
        not isinstance(html, str)
        or not html
        or not isinstance(metrics, Mapping)
        or not isinstance(fit_report, Mapping)
    ):
        raise ValueError("original_capture_checkpoint_incomplete")
    if (
        capture.prepared_fit_policy is not None
        and dict(capture.prepared_fit_policy) != dict(policy)
    ):
        raise ValueError("original_capture_checkpoint_policy_mismatch")
    if (
        capture.fitted_html is not None
        or fit_report.get("requested") is not False
        or fit_report.get("changed") is True
        or bool(fit_report.get("fit_proposal"))
    ):
        raise ValueError("original_capture_checkpoint_not_fit_disabled")
    html_digest = _html_digest(html)
    if capture.html_digest != html_digest:
        raise ValueError("original_capture_checkpoint_html_mismatch")
    renderer_identity = metrics.get("local_fit_render_identity")
    expected_renderer_identity = local_fit_render_identity(
        html, prepared_policy, fit=False
    )
    if renderer_identity != expected_renderer_identity:
        raise ValueError("original_capture_checkpoint_renderer_mismatch")
    png = bytes(capture.png)
    png_digest = _sha256(png)
    if capture.png_digest != png_digest:
        raise ValueError("original_capture_checkpoint_png_mismatch")
    metrics_value = dict(metrics)
    metrics_digest = _sha256(_canonical_json(metrics_value))
    if capture.metrics_digest != metrics_digest:
        raise ValueError("original_capture_checkpoint_metrics_mismatch")
    identity = _input_identity(
        html=html,
        policy=prepared_policy,
        renderer_identity=expected_renderer_identity,
    )
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "slideIndex": slide_index,
        "inputDigest": identity,
        "html": html,
        "htmlDigest": html_digest,
        "policy": dict(prepared_policy),
        "policyDigest": _sha256(
            _canonical_json(
                _normalized_policy_identity(
                    prepared_policy, html_digest=html_digest
                )
            )
        ),
        "rendererIdentity": expected_renderer_identity,
        "fitMode": False,
        "fittedHtml": None,
        "fittedHtmlDigest": None,
        "fitReport": dict(fit_report),
        "fitReportDigest": _sha256(_canonical_json(dict(fit_report))),
        "pngBase64": base64.b64encode(png).decode("ascii"),
        "pngDigest": png_digest,
        "browserMetrics": metrics_value,
        "metricsDigest": metrics_digest,
    }
    document["checkpointDigest"] = _sha256(_canonical_json(document))
    return _canonical_json(document), identity


def _decode_bundle(
    payload: bytes,
    *,
    slide_index: int,
    expected_html: str,
    policy: Mapping[str, Any],
) -> RenderCapture | None:
    try:
        document = json.loads(payload.decode("utf-8"))
        if not isinstance(document, dict):
            return None
        checkpoint_digest = document.pop("checkpointDigest", None)
        if checkpoint_digest != _sha256(_canonical_json(document)):
            return None
        if (
            document.get("schemaVersion") != SCHEMA_VERSION
            or document.get("slideIndex") != slide_index
            or document.get("fitMode") is not False
        ):
            return None
        html = document.get("html")
        stored_policy = document.get("policy")
        metrics = document.get("browserMetrics")
        fit_report = document.get("fitReport")
        if (
            not isinstance(html, str)
            or not isinstance(stored_policy, dict)
            or not isinstance(metrics, dict)
            or not isinstance(fit_report, dict)
        ):
            return None
        if (
            document.get("fittedHtml") is not None
            or document.get("fittedHtmlDigest") is not None
            or fit_report.get("requested") is not False
            or fit_report.get("changed") is True
            or bool(fit_report.get("fit_proposal"))
            or document.get("fitReportDigest")
            != _sha256(_canonical_json(fit_report))
        ):
            return None
        html_digest = _html_digest(html)
        if document.get("htmlDigest") != html_digest or html != expected_html:
            return None
        normalized_expected_policy = _normalized_policy_identity(
            policy, html_digest=html_digest
        )
        policy_digest = _sha256(_canonical_json(normalized_expected_policy))
        if (
            document.get("policyDigest") != policy_digest
            or _normalized_policy_identity(
                stored_policy, html_digest=html_digest
            )
            != normalized_expected_policy
        ):
            return None
        renderer_identity = local_fit_render_identity(
            html, stored_policy, fit=False
        )
        if document.get("rendererIdentity") != renderer_identity:
            return None
        identity = _input_identity(
            html=html,
            policy=stored_policy,
            renderer_identity=renderer_identity,
        )
        if document.get("inputDigest") != identity:
            return None
        png = base64.b64decode(document["pngBase64"], validate=True)
        if document.get("pngDigest") != _sha256(png):
            return None
        if document.get("metricsDigest") != _sha256(_canonical_json(metrics)):
            return None
        if metrics.get("local_fit_render_identity") != renderer_identity:
            return None
        return RenderCapture.from_payload(
            html_digest=html_digest,
            png=png,
            browser_metrics=metrics,
            runtime_counters={},
            fitted_html=None,
            fit_report=fit_report,
            prepared_html=html,
            prepared_fit_policy=stored_policy,
        )
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def _local_path(job_id: uuid.UUID, slide_index: int, identity: str) -> Path:
    root = checkpoint_root()
    job_directory = (root / str(job_id)).resolve()
    if root not in job_directory.parents:
        raise ValueError("invalid_checkpoint_path")
    job_directory.mkdir(parents=True, exist_ok=True)
    return job_directory / (
        f"original-slide-{slide_index}-{identity.removeprefix('sha256:')}.json"
    )


async def persist_original_capture_checkpoint(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    fencing_token: int | None,
    capture: RenderCapture,
    policy: Mapping[str, Any],
) -> None:
    """Persist the immutable fit-disabled original under the current fence."""

    payload, identity = _encode_bundle(
        slide_index=slide_index, capture=capture, policy=policy
    )
    async with async_session_maker() as session:
        await owned_job(
            session, job_id, attempt_token, fencing_token=fencing_token
        )
        binding = await public_checkpoint_binding(
            job_id,
            attempt_token,
            fencing_token=fencing_token,
            session=session,
        )
        # Match the existing authored artifact route: workspace jobs without a
        # public admission remain on the durable local checkpoint store after
        # ``owned_job`` has fenced their operation.
        if binding is None:
            path = _local_path(job_id, slide_index, identity)
            if path.exists():
                if path.read_bytes() != payload:
                    raise ValueError("original_capture_checkpoint_conflict")
            else:
                atomic_write(path, payload)
            await session.commit()
            return
        owner_id, tenant_id, operation_id = binding
        operation = await session.get(WorkspaceOperation, operation_id)
        if operation is None:
            raise AuthoredStageError(
                slide_index=slide_index,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        service = configured_public_artifact_service()
        reference = await service.persist_bytes(
            session,
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind="checkpoint",
            payload=payload,
            media_type="application/json",
            created_by_operation_id=operation_id,
            retention_class="checkpoint",
        )
        pointer = await put_slide_checkpoint_if_current(
            session,
            operation_id=operation_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            attempt_number=operation.attempt_number,
            attempt_token=attempt_token,
            fencing_token=int(fencing_token),
            revision=0,
            slide_index=slide_index,
            input_digest=identity.removeprefix("sha256:"),
            stage_version=STAGE_VERSION,
            artifact_id=reference.artifact_id,
            operation_kind="generation",
        )
        if pointer is None:
            raise AuthoredStageError(
                slide_index=slide_index,
                stage="persist",
                code="authored_workspace_operation_fence_lost",
                retryable=True,
            )
        await session.commit()


async def load_original_capture_checkpoint(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    fencing_token: int | None,
    expected_html: str,
    policy: Mapping[str, Any],
) -> RenderCapture | None:
    """Load an exact original for a currently fenced retry, or return ``None``."""

    prepared_html = canonical_capture_html(expected_html, index=slide_index)
    prepared_policy = _policy_for_prepared_html(
        policy, original_html=expected_html, prepared_html=prepared_html
    )
    renderer_identity = local_fit_render_identity(
        prepared_html, prepared_policy, fit=False
    )
    identity = _input_identity(
        html=prepared_html,
        policy=prepared_policy,
        renderer_identity=renderer_identity,
    )
    async with async_session_maker() as session:
        await owned_job(
            session, job_id, attempt_token, fencing_token=fencing_token
        )
        binding = await public_checkpoint_binding(
            job_id,
            attempt_token,
            fencing_token=fencing_token,
            session=session,
        )
        if binding is None:
            path = _local_path(job_id, slide_index, identity)
            try:
                payload = path.read_bytes()
            except FileNotFoundError:
                await session.commit()
                return None
            result = _decode_bundle(
                payload,
                slide_index=slide_index,
                expected_html=prepared_html,
                policy=prepared_policy,
            )
            await session.commit()
            return result
        owner_id, tenant_id, operation_id = binding
        artifact = await session.scalar(
            select(PublicArtifact)
            .join(
                PublicSlideCheckpoint,
                PublicSlideCheckpoint.artifact_id == PublicArtifact.id,
            )
            .where(
                PublicSlideCheckpoint.operation_id == operation_id,
                PublicSlideCheckpoint.revision == 0,
                PublicSlideCheckpoint.slide_index == slide_index,
                PublicSlideCheckpoint.input_digest
                == identity.removeprefix("sha256:"),
                PublicSlideCheckpoint.stage_version == STAGE_VERSION,
                PublicArtifact.owner_id == owner_id,
                PublicArtifact.tenant_id == tenant_id,
                PublicArtifact.kind == "checkpoint",
                PublicArtifact.media_type == "application/json",
                PublicArtifact.retention_class == "checkpoint",
                PublicArtifact.lifecycle_state == "committed",
            )
            .order_by(PublicSlideCheckpoint.committed_at.desc())
            .limit(1)
        )
        if artifact is None:
            await session.commit()
            return None
        try:
            payload = await configured_public_artifact_service().read_ref_bytes(
                artifact.to_ref()
            )
        except Exception:
            await session.commit()
            return None
        result = _decode_bundle(
            payload,
            slide_index=slide_index,
            expected_html=prepared_html,
            policy=prepared_policy,
        )
        await session.commit()
        return result


__all__ = [
    "SCHEMA_VERSION",
    "STAGE_VERSION",
    "load_original_capture_checkpoint",
    "persist_original_capture_checkpoint",
]
