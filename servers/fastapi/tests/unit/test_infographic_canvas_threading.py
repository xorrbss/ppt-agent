"""The deck canvas height reaches every figure brief the authoring model sees.

Three routes build a slide prompt with a figure brief — the single-slide author
call, the batched author call and the vision-QA full re-author — and each must
state the bottom content line of the deck's own canvas: y=768px on a numbered
1188x840 deck, never the 16:9 y=648px.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from models.authored_infographic_plan import (
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
    InfographicNode,
)
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.generation_core.authoring import author_batch, author_slide
from services.generation_core.authoring import author_vision_qa as vqa_mod
from services.generation_core.authoring.author_deck import (
    _planned_batch_request,
    author_planned_slide,
    prepare_authored_deck,
)
from services.generation_core.authoring.author_slide import Brand

_FIGURE_INDEX = 1


class _Captured(BaseException):
    """Stops the author call once its prompt exists; nothing may swallow it."""


@pytest.fixture(autouse=True)
def _pinned_knobs(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith("AUTHORED_"):
            monkeypatch.delenv(key, raising=False)


def _brand() -> Brand:
    return Brand(topic="스마트팩토리 전환", language="Korean", primary="#1E5EFF", fonts="Noto Sans KR")


def _canvas(width: int, height: int) -> SimpleNamespace:
    return SimpleNamespace(
        width_px=width,
        height_px=height,
        safe_area=SimpleNamespace(x=64, y=64, width=width - 128, height=height - 128),
    )


def _figure() -> AuthoredSlideInfographic:
    return AuthoredSlideInfographic(
        index=_FIGURE_INDEX,
        archetype="process",
        weight="standard",
        takeaway="검증 후 개통한다",
        nodes=(
            InfographicNode(label="진단", desc="수집 범위 확정"),
            InfographicNode(label="구축", desc="표준 설비 연결"),
            InfographicNode(label="확산", desc="전 라인 적용"),
        ),
    )


def _deck(width: int, height: int):
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content="# 스마트팩토리 전환 전략"),
            SlideOutlineModel(
                content="## 추진 단계\n- 진단: 수집 범위 확정\n- 구축: 표준 설비 연결\n- 확산: 전 라인 적용"
            ),
            SlideOutlineModel(content="## 기대 효과\n- 불량률 절감"),
        ]
    )
    plan = AuthoredInfographicPlan(
        slides=(
            AuthoredSlideInfographic(index=0, archetype="none", weight="quiet"),
            _figure(),
            AuthoredSlideInfographic(index=2, archetype="none", weight="quiet"),
        )
    )
    return prepare_authored_deck(
        outline,
        _brand(),
        page_number_mode="template",
        infographic_plan=plan,
        canvas=_canvas(width, height),
    )


def _planned_prompt(plan, monkeypatch) -> str:
    captured: list[str] = []
    real = author_slide._author_prompt

    def capture(*args, **kwargs):
        captured.append(real(*args, **kwargs))
        raise _Captured

    monkeypatch.setattr(author_slide, "_author_prompt", capture)
    with pytest.raises(_Captured):
        asyncio.run(author_planned_slide(plan, _FIGURE_INDEX, raise_on_failure=True))
    return captured[0]


def test_a4_landscape_deck_keeps_its_figure_briefs() -> None:
    plan = _deck(1188, 840)
    assert plan.canvas_height_px == 840
    assert plan.infographic_briefs
    assert plan.infographic_briefs[_FIGURE_INDEX].archetype == "process"


def test_the_single_slide_prompt_states_the_a4_bottom_line(monkeypatch) -> None:
    prompt = _planned_prompt(_deck(1188, 840), monkeypatch)
    assert "FIGURE FOR THIS SLIDE" in prompt
    assert "y=768px" in prompt
    assert "y=648px" not in prompt


def test_the_single_slide_prompt_keeps_the_16x9_bottom_line(monkeypatch) -> None:
    prompt = _planned_prompt(_deck(1280, 720), monkeypatch)
    assert "FIGURE FOR THIS SLIDE" in prompt
    assert "y=648px" in prompt
    assert "y=768px" not in prompt


def test_the_batched_prompt_states_the_a4_bottom_line() -> None:
    plan = _deck(1188, 840)
    request = _planned_batch_request(
        plan, _FIGURE_INDEX, source_images=(), require_all_source_images=False, failure=None
    )
    assert request.canvas_height_px == 840
    prepared = author_batch._prepare_slide(
        request, design_system=plan.design_system, brand=plan.brand, slide_count=len(plan.contents)
    )
    assert "FIGURE FOR THIS SLIDE" in prepared.prompt
    assert "y=768px" in prepared.prompt
    assert "y=648px" not in prepared.prompt


def test_the_vision_qa_reauthor_passes_the_deck_canvas_height(monkeypatch) -> None:
    plan = _deck(1188, 840)
    figure = plan.infographic_briefs[_FIGURE_INDEX]
    captured: list[str] = []

    async def fake_critique(pngs, contexts=None, **_kwargs):
        return [vqa_mod.SlideCritique(needs_fix=True)]

    async def fake_author(content, design_system, brand, role, index, n, **kwargs):
        forwarded = {
            key: kwargs[key]
            for key in ("page_number_mode", "infographic", "canvas_height_px")
            if key in kwargs
        }
        captured.append(
            author_slide._author_prompt(content, design_system, brand, role, index, n, **forwarded)
        )
        raise RuntimeError("stop after the prompt")

    monkeypatch.setattr(vqa_mod, "critique_authored", fake_critique)
    monkeypatch.setattr(vqa_mod, "author_slide_html", fake_author)
    asyncio.run(
        vqa_mod.revise_authored_deck(
            ["<!DOCTYPE html><html><body></body></html>"],
            [b"original-png"],
            [plan.contents[_FIGURE_INDEX]],
            [plan.roles[_FIGURE_INDEX]],
            plan.brand,
            plan.design_system,
            slide_indices=[_FIGURE_INDEX],
            total_slides=len(plan.contents),
            infographic_briefs=[figure],
            canvas=_canvas(1188, 840),
        )
    )
    assert captured, "the re-author was never asked for the slide"
    assert "y=768px" in captured[0]
    assert "y=648px" not in captured[0]
