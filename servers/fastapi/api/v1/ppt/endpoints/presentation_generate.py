"""Presentation generation endpoints (`/generate`, `/generate/async`, `/status/{id}`).

The pipeline itself lives in ``services.presentation_generation_pipeline``;
this module binds it to HTTP: runtime admission, export-cookie hand-off and
the response models. Pipeline names are re-exported for callers and tests
that address them through the endpoint module.
"""

import traceback
from typing import Optional

from fastapi import (
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.ppt.endpoints.presentation_helpers import build_export_cookie_header
from api.v1.ppt.endpoints.presentation_generate_admission import (
    AsyncPresentationGenerationAdmissionResponse,
    AsyncPresentationGenerationTaskResponse,
    PRESENTATION_GENERATE_ROUTER,
    _chat_first_generation_enabled,  # noqa: F401 - addressed through this module
    _require_local_legacy_async_generation,
    _require_local_sync_generation,
)
from api.v1.ppt.endpoints.presentation_generate_errors import generation_admission_http_error
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_service import progress_for_task
from services.database import get_async_session, sql_engine
from services.presentation_generation_job_service import cancel_generation_job
from services import presentation_generation_pipeline as generation_pipeline
from services.presentation_generation_pipeline import (  # noqa: F401 - re-exported
    CHAT_FIRST_WORKSPACE_PROJECTED_HEADER,
    _admit_generation_operation_v2_compatible,
    _durable_request_payload,
    _publish_staged_presentation,
    _request_from_durable_payload,
    _validate_authored_exact_admission,
    _validate_authored_master_pin_admission,
    check_if_api_request_is_valid,
    generate_presentation_handler,
    queue_presentation_generation,
    run_generation_job,
)
from services.workspace_legacy_write_gate import (
    require_legacy_presentation_write_path,
)
from utils.owner_context import get_current_owner_id


@PRESENTATION_GENERATE_ROUTER.post(
    "/generate", response_model=PresentationPathAndEditPath
)
async def generate_presentation_sync(
    request_http: Request,
    request: GeneratePresentationRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    # The synchronous endpoint runs the whole generation inline, so it has no
    # durable operation, lease, revision fence, or timeline card. Preserve it
    # only for the explicit local SQLite compatibility workflow. Cluster/public
    # runtimes receive a deprecation error before validation, provider calls,
    # rendering, or export can execute on the HTTP request lifetime.
    _require_local_sync_generation(sql_engine.url)
    request._request_id = getattr(request_http.state, "request_id", None)
    try:
        (presentation_id,) = await generation_pipeline.check_if_api_request_is_valid(request, sql_session)
        return await generation_pipeline.generate_presentation_handler(
            request,
            presentation_id,
            None,
            export_cookie_header=build_export_cookie_header(request_http),
            sql_session=sql_session,
        )
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Presentation generation failed")


@PRESENTATION_GENERATE_ROUTER.post(
    "/generate/async",
    response_model=AsyncPresentationGenerationAdmissionResponse,
    status_code=202,
    deprecated=True,
)
async def generate_presentation_async(
    request_http: Request,
    request: GeneratePresentationRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    sql_session: AsyncSession = Depends(get_async_session),
):
    # Keep the richer browser payload and response shape while binding both
    # PostgreSQL and local SQLite to the v2 deadline/fence contract. The
    # canonical v2 route remains the preferred public API.
    _require_local_legacy_async_generation(sql_session.get_bind())
    request._request_id = getattr(request_http.state, "request_id", None)
    try:
        queued = await generation_pipeline.queue_presentation_generation(
            request,
            background_tasks,
            sql_session,
            export_cookie_header=build_export_cookie_header(request_http),
            idempotency_key=idempotency_key,
        )
        operation_id = await sql_session.scalar(
            select(PresentationGenerationJob.workspace_operation_id).where(
                PresentationGenerationJob.task_id == queued.id
            )
        )
        return AsyncPresentationGenerationAdmissionResponse(
            **queued.model_dump(mode="json"),
            operation_id=operation_id,
        )

    except Exception as error:
        mapped = generation_admission_http_error(error, request_id=request._request_id)
        if mapped is error:
            raise
        raise mapped from error


@PRESENTATION_GENERATE_ROUTER.get(
    "/status/{id}", response_model=AsyncPresentationGenerationTaskResponse
)
async def check_async_presentation_generation_status(
    id: str = Path(description="ID of the presentation generation task"),
    sql_session: AsyncSession = Depends(get_async_session),
):
    owner_id = get_current_owner_id()
    owner_filter = (
        AsyncPresentationGenerationTaskModel.owner_id.is_(None)
        if owner_id is None
        else AsyncPresentationGenerationTaskModel.owner_id == owner_id
    )
    status = (
        await sql_session.execute(
            select(AsyncPresentationGenerationTaskModel)
            .where(AsyncPresentationGenerationTaskModel.id == id)
            .where(owner_filter)
        )
    ).scalar_one_or_none()
    if not status:
        raise HTTPException(
            status_code=404, detail="No presentation generation task found"
        )
    progress, failure = await progress_for_task(id, sql_session)
    return AsyncPresentationGenerationTaskResponse(
        **status.model_dump(mode="json"),
        progress=progress,
        failure=failure,
    )


@PRESENTATION_GENERATE_ROUTER.post(
    "/status/{id}/cancel", response_model=AsyncPresentationGenerationTaskModel
)
async def cancel_async_presentation_generation(
    id: str = Path(description="ID of the presentation generation task"),
):
    """Durably cancel a queued/running generation task owned by the caller.

    The job is marked terminal so it can never be (re)dispatched; the attempt
    running in this worker is interrupted best-effort. Already-terminal tasks
    are returned unchanged.
    """
    # Chat-first cancellation is an operation action so the operation and its
    # linked compatibility generation job transition together.  Mutating the
    # legacy task row directly would leave the timeline and runner divergent.
    require_legacy_presentation_write_path()
    task = await cancel_generation_job(id)
    if task is None:
        raise HTTPException(
            status_code=404, detail="No presentation generation task found"
        )
    return task
