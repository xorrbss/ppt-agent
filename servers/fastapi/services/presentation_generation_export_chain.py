"""Chain a workspace export command onto a completed generation.

``POST /presentation/generate`` accepts ``export_as``: "export the deck as soon
as generation finishes".  That used to run the exporter inside the request
(``utils/export_utils.py`` posting to the Next ``/api/export-presentation``
route and reading a filesystem ``path`` back).  That route now only admits a
command and returns a receipt, so the in-process call could no longer complete.

Generation-time export is therefore submitted the way every other export is:
one ``export.pptx`` / ``export.pdf`` workspace command, admitted, queued, and
executed by the isolated export worker.  The caller receives the operation id
and follows the operation to its artifact.

This module is the single place that decides "generation finished and an export
was requested".  No caller branches on ``export_as`` itself.

Duplicate submissions need no special case here.  Two layers already resolve
them:

* an identical chained submission collapses onto the same workspace operation
  through the deterministic idempotency key built below
  (``create_workspace_command`` returns the existing operation with
  ``duplicate=True``);
* a chained export that races a manual export of the same deck meets
  ``services/public_export_join.py`` at execution time, where the non-leading
  flight joins the leader's export identity and returns the leader's bytes
  instead of producing a second artifact.

A chained export must never turn a published deck into a failed generation.
Submission failures are recorded on the response as a stable error code and
logged; the deck itself stays published.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import logging
from types import MappingProxyType
from typing import Mapping, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_and_path import PresentationPathAndEditPath
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.workspace import WorkspaceCommandRequest
from services.workspace_operation_runner import wake_workspace_operation_runner
from services.workspace_service import (
    WorkspaceServiceError,
    create_workspace_command,
)


LOGGER = logging.getLogger(__name__)

#: ``export_as`` value -> durable workspace command kind.
EXPORT_COMMAND_TYPES: Mapping[str, str] = MappingProxyType(
    {"pptx": "export.pptx", "pdf": "export.pdf"}
)

#: Human-readable command text, matching what the editor's export menu submits.
_EXPORT_COMMAND_TEXT: Mapping[str, str] = MappingProxyType(
    {
        "export.pptx": "Export presentation as PowerPoint",
        "export.pdf": "Export presentation as PDF",
    }
)

#: Recorded instead of an operation id when submission was refused. The deck is
#: published either way; only the chained export is missing.
CHAIN_SUBMISSION_FAILED = "generation_export_chain_not_submitted"


@dataclass(frozen=True, slots=True)
class ChainedGenerationExport:
    """Outcome of one chained export submission."""

    command_type: str
    operation_id: Optional[uuid.UUID]
    duplicate: bool
    error_code: Optional[str] = None


def chained_export_idempotency_key(
    *,
    presentation_id: uuid.UUID,
    revision: int,
    command_type: str,
) -> str:
    """Derive a stable key so a retried generation reuses one export operation.

    The key is bound to the published revision: a regenerated deck exports
    again, while a resumed or replayed generation of the same revision adopts
    the operation that is already in flight.
    """

    digest = sha256(
        "".join(
            ("presentation.generate.export", str(presentation_id), str(revision), command_type)
        ).encode("utf-8")
    ).hexdigest()
    return f"generation-export:{digest}"


async def _submit_chained_export(
    sql_session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    command_type: str,
) -> ChainedGenerationExport:
    presentation = await sql_session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise WorkspaceServiceError("presentation_not_found", status_code=404)
    command = WorkspaceCommandRequest(
        command=_EXPORT_COMMAND_TEXT[command_type],
        command_type=command_type,
        idempotency_key=chained_export_idempotency_key(
            presentation_id=presentation_id,
            revision=presentation.revision,
            command_type=command_type,
        ),
        expected_revision=presentation.revision,
        # Export options stay unset so the server-side defaults govern. The
        # chain must not pin `pptx_mode`/`font_embedding`, or it would freeze a
        # default the export owner is still choosing.
        context={},
        asset_ids=[],
    )
    operation, duplicate = await create_workspace_command(
        sql_session,
        presentation_id=presentation_id,
        request=command,
    )
    await sql_session.commit()
    wake_workspace_operation_runner()
    return ChainedGenerationExport(
        command_type=command_type,
        operation_id=operation.id,
        duplicate=duplicate,
    )


async def chain_generation_export(
    sql_session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    export_as: str,
) -> ChainedGenerationExport:
    """Submit the export command a finished generation asked for."""

    command_type = EXPORT_COMMAND_TYPES[export_as]
    try:
        return await _submit_chained_export(
            sql_session,
            presentation_id=presentation_id,
            command_type=command_type,
        )
    except Exception as error:  # noqa: BLE001 - a published deck must survive this
        try:
            await sql_session.rollback()
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "Failed to roll back after a refused chained export for %s",
                presentation_id,
            )
        code = getattr(error, "code", None)
        LOGGER.warning(
            "Chained %s for presentation %s was not submitted (%s)",
            command_type,
            presentation_id,
            code or type(error).__name__,
            exc_info=True,
        )
        return ChainedGenerationExport(
            command_type=command_type,
            operation_id=None,
            duplicate=False,
            error_code=(code if isinstance(code, str) else CHAIN_SUBMISSION_FAILED),
        )


async def finalize_generation_export_chain(
    sql_session: AsyncSession,
    *,
    response: PresentationPathAndEditPath,
    export_as: str,
    async_status: Optional[AsyncPresentationGenerationTaskModel] = None,
) -> PresentationPathAndEditPath:
    """Decide once that generation finished with an export requested.

    Returns the generation response carrying the chained export operation, and
    keeps the durable async-task projection in step so a polling client sees
    the same operation id the synchronous response carries.
    """

    if export_as not in EXPORT_COMMAND_TYPES:
        return response
    chained = await chain_generation_export(
        sql_session,
        presentation_id=response.presentation_id,
        export_as=export_as,
    )
    enriched = response.model_copy(
        update={
            "export_operation_id": chained.operation_id,
            "export_error": chained.error_code,
        }
    )
    if async_status is not None:
        data = dict(async_status.data or {})
        data.update(
            {
                "export_operation_id": (
                    str(chained.operation_id)
                    if chained.operation_id is not None
                    else None
                ),
                "export_error": chained.error_code,
            }
        )
        async_status.data = data
        async_status.updated_at = datetime.now(timezone.utc)
        sql_session.add(async_status)
        await sql_session.commit()
    return enriched


__all__ = [
    "CHAIN_SUBMISSION_FAILED",
    "ChainedGenerationExport",
    "EXPORT_COMMAND_TYPES",
    "chain_generation_export",
    "chained_export_idempotency_key",
    "finalize_generation_export_chain",
]
