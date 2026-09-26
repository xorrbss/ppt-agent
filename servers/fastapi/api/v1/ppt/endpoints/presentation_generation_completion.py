"""Persist the final generation verdict before submitting a chained export."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_and_path import PresentationPathAndEditPath
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel


async def record_generation_completion(
    sql_session: AsyncSession,
    *,
    async_status: AsyncPresentationGenerationTaskModel | None,
    resolved_strategy: str,
    strategy_reason: str,
    presentation_id: str,
    response: PresentationPathAndEditPath,
) -> None:
    if resolved_strategy == "svg" and async_status is not None:
        # The per-page verdict publication recorded, not the deck mode.
        from services.generation_core.drivers.svg.publish import svg_completion_status

        message, gate_status, pages = await svg_completion_status(sql_session, presentation_id)
        async_status.status = "completed"
        async_status.message = message
        async_status.data = {
            **response.model_dump(mode="json"),
            "quality_gate_status": gate_status,
            "quality_pages": pages,
            "generation_strategy_reason": strategy_reason,
        }
        async_status.error, async_status.updated_at = None, datetime.now(timezone.utc)
        sql_session.add(async_status)
        # Export submission may roll back its session, so completion commits first.
        await sql_session.commit()
    elif async_status is not None and strategy_reason != "explicit":
        # Preserve the authored pipeline's status/message when recording fallback.
        data = dict(async_status.data or {})
        data["generation_strategy_reason"] = strategy_reason
        async_status.data = data
        async_status.updated_at = datetime.now(timezone.utc)
        sql_session.add(async_status)
        await sql_session.commit()
