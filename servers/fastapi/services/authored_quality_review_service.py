"""Post-generation quality review for saved AI-authored presentations.

The review renders the persisted authored HTML, critiques the resulting images in
parallel, and optionally re-authors only the slides with visible defects. The live
deck is checkpointed before any replacement so the operation can be undone through
the existing presentation version history.
"""

import asyncio
import os
import traceback
import uuid
from typing import Iterable, List

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
from services.database import async_session_maker
from utils.asset_directory_utils import get_images_directory
from utils.authored_styles import resolve_authored_style
from utils.datetime_utils import get_current_utc_datetime
from utils.llm_calls.author_deck import prepare_authored_deck
from utils.llm_calls.author_slide import Brand
from utils.llm_calls.author_vision_qa import critique_authored, revise_authored_deck
from utils.slide_capture import render_html_to_png


def _is_authored_slide(slide: SlideModel) -> bool:
    return (
        slide.layout_group == "authored"
        or (slide.layout or "").startswith("authored:")
        or (
            isinstance(slide.content, dict)
            and slide.content.get("__authored__") is True
        )
    )


async def _set_status(
    session: AsyncSession,
    task: AsyncPresentationGenerationTaskModel,
    *,
    status: str,
    message: str,
    data: dict | None = None,
    error: dict | None = None,
) -> None:
    task.status = status
    task.message = message
    task.data = data
    task.error = error
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.commit()


def _resolve_positions(
    request: AuthoredQualityReviewRequest, slide_count: int
) -> List[int]:
    if request.scope == "all":
        return list(range(slide_count))
    if len(request.slide_indices) != 1:
        raise ValueError("Current-slide review requires exactly one slide index")
    index = request.slide_indices[0]
    if index < 0 or index >= slide_count:
        raise ValueError("Slide index is outside the presentation")
    return [index]


def _saved_brand(presentation: PresentationModel) -> Brand:
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    language = str(theme.get("language") or presentation.language or "Korean")
    return Brand(
        topic=presentation.title or presentation.content or "Presentation",
        language=language,
        primary=str(theme.get("primary") or "#2563EB"),
        fonts=str(
            theme.get("fonts")
            or ("Noto Sans KR" if language.lower().startswith("ko") else "Inter")
        ),
        primary_is_explicit=True,
        fonts_are_explicit=True,
    )


def _saved_role(slide: SlideModel, fallback: str) -> str:
    if isinstance(slide.content, dict) and slide.content.get("role"):
        return str(slide.content["role"])
    layout = slide.layout or ""
    if layout.startswith("authored:") and layout.partition(":")[2]:
        return layout.partition(":")[2]
    return fallback


def _serialize_critiques(positions: Iterable[int], critiques: list) -> List[dict]:
    results: List[dict] = []
    for position, critique in zip(positions, critiques):
        if critique is None or not critique.needs_fix:
            continue
        results.append(
            {
                "slide_index": position,
                "issues": [
                    issue.model_dump(mode="json") for issue in critique.issues
                ],
            }
        )
    return results


def _write_reviewed_png(
    presentation_id: uuid.UUID, slide_position: int, png: bytes
) -> str:
    relative_directory = os.path.join("authored", str(presentation_id))
    absolute_directory = os.path.join(get_images_directory(), relative_directory)
    os.makedirs(absolute_directory, exist_ok=True)
    filename = f"slide_{slide_position}.png"
    with open(os.path.join(absolute_directory, filename), "wb") as image_file:
        image_file.write(png)
    return f"{relative_directory.replace(os.sep, '/')}/{filename}"


async def run_authored_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise ValueError("Presentation not found")
    outline = presentation.get_presentation_outline()
    if outline is None or not outline.slides:
        raise ValueError("The saved AI-authored outline is missing")

    slides = list(
        (
            await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
        ).all()
    )
    if not slides or not presentation.is_authored() or not all(
        _is_authored_slide(slide) for slide in slides
    ):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(outline.slides) != len(slides):
        raise ValueError("Saved outline and slide counts do not match")

    positions = _resolve_positions(request, len(slides))
    targets = [slides[position] for position in positions]
    if any(not slide.html_content for slide in targets):
        raise ValueError("One or more slides are missing their editable authored HTML")

    await _set_status(
        session,
        task,
        status="processing",
        message="슬라이드 렌더링을 준비하고 있습니다.",
    )

    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    style = resolve_authored_style(str(theme.get("style") or "default"))
    plan = prepare_authored_deck(outline, _saved_brand(presentation), style)
    contents = [outline.slides[position].content for position in positions]
    roles = [
        _saved_role(target, plan.roles[position])
        for position, target in zip(positions, targets)
    ]
    htmls = [str(target.html_content) for target in targets]

    await _set_status(
        session,
        task,
        status="processing",
        message=f"{len(targets)}개 슬라이드를 렌더링하고 있습니다.",
    )
    pngs = list(await asyncio.gather(*(render_html_to_png(html) for html in htmls)))

    await _set_status(
        session,
        task,
        status="processing",
        message="잘림, 겹침, 정렬, 대비를 병렬로 검사하고 있습니다.",
    )
    initial_critiques = await critique_authored(
        pngs, contexts=[content[:240] for content in contents]
    )
    initial_issues = _serialize_critiques(positions, initial_critiques)

    if request.mode == "analyze_only" or not initial_issues:
        message = (
            "검수가 완료되었습니다. 수정이 필요한 슬라이드가 없습니다."
            if not initial_issues
            else f"검수가 완료되었습니다. {len(initial_issues)}개 슬라이드에서 문제를 확인했습니다."
        )
        await _set_status(
            session,
            task,
            status="completed",
            message=message,
            data={
                "presentation_id": str(presentation_id),
                "mode": request.mode,
                "scope": request.scope,
                "reviewed_count": len(targets),
                "issue_slide_count": len(initial_issues),
                "issues": initial_issues,
                "fixed_count": 0,
                "fixed_slide_indices": [],
                "remaining_issue_count": len(initial_issues),
                "remaining_slide_indices": [
                    issue["slide_index"] for issue in initial_issues
                ],
                "version_saved": False,
            },
        )
        return

    await _set_status(
        session,
        task,
        status="processing",
        message=f"문제가 발견된 {len(initial_issues)}개 슬라이드를 자동 수정하고 있습니다.",
    )
    revised_htmls, revised_pngs, fixed_local_indices = await revise_authored_deck(
        htmls,
        pngs,
        contents,
        roles,
        plan.brand,
        plan.design_system,
        max_cycles=1,
        style=style,
        slide_indices=positions,
        total_slides=len(slides),
    )

    await _set_status(
        session,
        task,
        status="processing",
        message="수정 결과를 다시 검증하고 있습니다.",
    )
    final_critiques = await critique_authored(
        revised_pngs, contexts=[content[:240] for content in contents]
    )
    remaining_issues = _serialize_critiques(positions, final_critiques)

    fixed_positions = [positions[index] for index in fixed_local_indices]
    if fixed_local_indices:
        await version_service.snapshot_slides(
            session,
            presentation_id,
            [slide.model_dump(mode="json") for slide in slides],
            label="고품질 검수 전",
            force=True,
        )
        for local_index in fixed_local_indices:
            position = positions[local_index]
            slide = targets[local_index]
            image_reference = _write_reviewed_png(
                presentation_id, position, revised_pngs[local_index]
            )
            slide.html_content = revised_htmls[local_index]
            slide.content = {
                **(slide.content if isinstance(slide.content, dict) else {}),
                "__authored__": True,
                "image": image_reference,
                "role": roles[local_index],
            }
            slide.properties = {
                **(slide.properties if isinstance(slide.properties, dict) else {}),
                "image": image_reference,
            }
            session.add(slide)
        await session.commit()

    await _set_status(
        session,
        task,
        status="completed",
        message=(
            f"검수가 완료되었습니다. {len(fixed_positions)}개 슬라이드를 수정했습니다."
            if fixed_positions
            else "검수는 완료되었지만 자동 수정할 수 있는 변경은 생성되지 않았습니다."
        ),
        data={
            "presentation_id": str(presentation_id),
            "mode": request.mode,
            "scope": request.scope,
            "reviewed_count": len(targets),
            "issue_slide_count": len(initial_issues),
            "issues": initial_issues,
            "fixed_count": len(fixed_positions),
            "fixed_slide_indices": fixed_positions,
            "remaining_issue_count": len(remaining_issues),
            "remaining_slide_indices": [
                issue["slide_index"] for issue in remaining_issues
            ],
            "remaining_issues": remaining_issues,
            "version_saved": bool(fixed_positions),
        },
    )


async def _run_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task_id: str,
) -> None:
    async with async_session_maker() as session:
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            return
        try:
            await run_authored_quality_review(
                presentation_id, request, task, session
            )
        except Exception as exc:  # noqa: BLE001 - background task boundary
            traceback.print_exc()
            await session.rollback()
            await _set_status(
                session,
                task,
                status="error",
                message="고품질 검수 중 오류가 발생했습니다.",
                error={"detail": str(exc)},
            )


async def queue_authored_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> AsyncPresentationGenerationTaskModel:
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")

    slides = list(
        (
            await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
        ).all()
    )
    if not presentation.is_authored() or not slides or not all(
        _is_authored_slide(slide) for slide in slides
    ):
        raise HTTPException(
            status_code=400,
            detail="High-quality review supports AI-authored presentations only",
        )
    try:
        _resolve_positions(request, len(slides))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="고품질 검수 작업을 대기열에 추가했습니다.",
        data=None,
    )
    session.add(task)
    await session.commit()
    background_tasks.add_task(
        _run_quality_review, presentation_id, request, task.id
    )
    return task
