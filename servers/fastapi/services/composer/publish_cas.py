"""The Composer's publication fence and the generation lane's durable CAS.

Inside a ``public-operation/v2`` generation operation the handler
(``services.presentation_generation_operation_handler``) requires that the
generation itself already left the operation ``succeeded`` with a
``result_revision`` and a Mapping ``result`` -- see
``require_public_generation_publication``.  The authored path does this by
staging a workspace artifact (``prepare_workspace_generation_artifact``) and
completing the operation, presentation revision, linked job and terminal event
in one fenced transaction (``publish_workspace_generation_artifact``).  The
chat/remake CAS ``publish_presentation_revision`` is *not* that helper: its
terminal event carries no admission fence, so the sealed admission rejects it
with ``public_operation_fence_conflict``.  This module mirrors the authored
path for the Composer; the non-workspace lane (no operation) keeps the
presentation-only CAS.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from models.sql.workspace import WorkspaceOperation
from services.composer.receipt import ComposerPublicationError

LOGGER = logging.getLogger(__name__)

#: Artifact helper codes that mean another attempt or owner now holds the
#: operation; the attempt may be retried under a fresh claim.
_RETRYABLE_ARTIFACT_CODES = frozenset({"operation_lease_lost", "revision_conflict"})
FENCE_LOST_CODE = "composer_operation_fence_lost"


@dataclass(frozen=True)
class PublicationFence:
    """Everything the publication CAS needs to prove it still owns the attempt."""

    operation_id: uuid.UUID | None
    attempt_token: str | None
    fencing_token: int | None
    job_id: uuid.UUID | None

    @property
    def workspace_lane(self) -> bool:
        return self.operation_id is not None

    @property
    def has_job_claim(self) -> bool:
        return self.job_id is not None and bool(self.attempt_token)


def fence_inputs(request: Any, generation_job: Any) -> PublicationFence:
    operation_id = getattr(generation_job, "workspace_operation_id", None)
    attempt_token = getattr(generation_job, "attempt_token", None) or getattr(
        request, "_workspace_operation_attempt_token", None
    )
    fencing_token = getattr(generation_job, "_workspace_fencing_token", None)
    if fencing_token is None:
        fencing_token = getattr(request, "_workspace_operation_fencing_token", None)
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
        fencing_token = None
    job_id = getattr(generation_job, "id", None)
    return PublicationFence(
        operation_id=operation_id if isinstance(operation_id, uuid.UUID) else None,
        attempt_token=str(attempt_token) if attempt_token else None,
        fencing_token=fencing_token,
        job_id=job_id if isinstance(job_id, uuid.UUID) else None,
    )


async def require_operation_fence(session: Any, *, fence: PublicationFence, expected_revision: int) -> None:
    """Fail fast before any external write when the attempt no longer owns the operation."""

    if fence.operation_id is None:
        return
    operation = await session.get(WorkspaceOperation, fence.operation_id)
    if (
        operation is None
        or operation.state != "running"
        or not fence.attempt_token
        or operation.attempt_token != fence.attempt_token
        or operation.expected_revision != expected_revision
    ):
        raise ComposerPublicationError(FENCE_LOST_CODE, retryable=True)


async def stage_workspace_artifact(
    session: Any,
    *,
    fence: PublicationFence,
    request: Any,
    presentation_id: uuid.UUID,
    pngs: Sequence[bytes],
    title: str,
    expected_revision: int,
) -> Any | None:
    """Reserve the operation's durable artifact before the publication fence.

    Mirrors the authored path: the image export is assembled and promoted into
    the operation-owned artifact namespace with an independent session, then
    the account-deletion fence is acquired.  Returns ``None`` outside the
    workspace lane.
    """

    if fence.operation_id is None:
        return None
    # Lazy: these modules pull the authored runtime and the settings registry.
    from services.authored_presentation_artifacts import _build_authored_export
    from services.workspace_artifact_publication_service import (
        WorkspaceArtifactPublicationError,
        fence_workspace_generation_publication,
        prepare_workspace_generation_artifact,
    )

    export_path = await asyncio.to_thread(_build_authored_export, request, presentation_id, list(pngs), title)
    try:
        artifact = await prepare_workspace_generation_artifact(
            session,
            source_path=export_path,
            operation_id=fence.operation_id,
            presentation_id=presentation_id,
            source_revision=expected_revision + 1,
        )
    except WorkspaceArtifactPublicationError as error:
        raise ComposerPublicationError(error.code) from error
    try:
        await fence_workspace_generation_publication(session, artifact=artifact)
    except WorkspaceArtifactPublicationError as error:
        await compensate_artifact(session, artifact)
        raise ComposerPublicationError(error.code, retryable=error.code in _RETRYABLE_ARTIFACT_CODES) from error
    return artifact


async def publish_workspace_artifact(
    session: Any,
    *,
    artifact: Any,
    fence: PublicationFence,
    expected_revision: int,
    result_evidence: dict[str, object],
) -> int:
    """Complete operation, presentation revision, linked job and terminal event; commits.

    From here the artifact helper owns reconciliation and compensation: a
    returned revision means the whole tuple committed (including after an
    ambiguous commit acknowledgement) and any raised error is unpublished.
    """

    from services.workspace_artifact_publication_service import (
        WorkspaceArtifactPublicationError,
        publish_workspace_generation_artifact,
    )

    try:
        return int(
            await publish_workspace_generation_artifact(
                session,
                artifact=artifact,
                expected_revision=expected_revision,
                attempt_token=fence.attempt_token or "",
                result_evidence=result_evidence,
            )
        )
    except WorkspaceArtifactPublicationError as error:
        retryable = error.code in _RETRYABLE_ARTIFACT_CODES
        raise ComposerPublicationError(
            FENCE_LOST_CODE if retryable else error.code, retryable=retryable
        ) from error


async def compensate_artifact(session: Any, artifact: Any) -> None:
    """Release a prepared artifact that never reached the publication helper."""

    from services.workspace_artifact_publication_service import compensate_workspace_artifact

    try:
        await compensate_workspace_artifact(session, artifact)
    except Exception:
        # The artifact service durably records cleanup_pending before raising.
        # Never hide the original publication failure behind the cleanup one.
        LOGGER.warning(
            "Unable to compensate unpublished composer workspace artifact %s",
            getattr(artifact, "artifact_id", None),
            exc_info=True,
        )


__all__ = [
    "FENCE_LOST_CODE",
    "PublicationFence",
    "compensate_artifact",
    "fence_inputs",
    "publish_workspace_artifact",
    "require_operation_fence",
    "stage_workspace_artifact",
]
