from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from enums.async_task_status import AsyncTaskStatus
from models.async_task_read import AsyncTaskReadModel
from services.async_task_read_service import (
    get_async_task as get_async_task_read,
    list_async_task_reads,
)
from services.database import get_async_session


API_V1_ASYNC_TASKS_ROUTER = APIRouter(
    prefix="/api/v1/async-tasks",
    tags=["Async Tasks"],
)


@API_V1_ASYNC_TASKS_ROUTER.get(
    "",
    response_model=list[AsyncTaskReadModel],
)
async def list_async_tasks(
    task_type: str | None = Query(default=None, alias="type"),
    status: AsyncTaskStatus | None = Query(default=None),
    created_at: datetime | None = Query(
        default=None,
        description=(
            "Only include tasks created at or after this timestamp. "
            "Alias for created_at_from."
        ),
    ),
    created_at_from: datetime | None = Query(
        default=None,
        description="Only include tasks created at or after this timestamp",
    ),
    created_at_to: datetime | None = Query(
        default=None,
        description="Only include tasks created at or before this timestamp",
    ),
    order_by: Literal["created_at", "updated_at"] = Query(default="created_at"),
    order: Literal["asc", "desc"] = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sql_session: AsyncSession = Depends(get_async_session),
) -> list[AsyncTaskReadModel]:
    return await list_async_task_reads(
        task_type=task_type,
        status=status,
        created_at=created_at,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
        order_by=order_by,
        order=order,
        limit=limit,
        offset=offset,
        sql_session=sql_session,
    )


async def _get_or_404(
    task_id: str,
    sql_session: AsyncSession,
) -> AsyncTaskReadModel:
    task = await get_async_task_read(task_id, sql_session)
    if task is None:
        raise HTTPException(status_code=404, detail="No async task found")
    return task


@API_V1_ASYNC_TASKS_ROUTER.get(
    "/status/{id}",
    response_model=AsyncTaskReadModel,
)
async def check_async_task_status(
    id: str = Path(description="ID of the async task"),
    sql_session: AsyncSession = Depends(get_async_session),
) -> AsyncTaskReadModel:
    return await _get_or_404(id, sql_session)


@API_V1_ASYNC_TASKS_ROUTER.get(
    "/{id}",
    response_model=AsyncTaskReadModel,
)
async def get_async_task(
    id: str = Path(description="ID of the async task"),
    sql_session: AsyncSession = Depends(get_async_session),
) -> AsyncTaskReadModel:
    return await _get_or_404(id, sql_session)
