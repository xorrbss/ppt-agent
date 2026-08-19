"""How the deck-scoped illustration plan reaches, and binds, each authored slide.

The plan exists because authored slides are written independently and in
parallel: a slide's own prompt cannot see how many siblings already claimed a
picture, and steering the count from inside that prompt gave the same real
77-slide deck 4, then 73, then 1 illustrated slides across runs. These tests pin
the three properties that make the deck-level decision authoritative — it
reaches every slide's prompt, it is enforced on the slide that ignored it, and
its absence changes nothing — plus the round trip that lets a later remake
replay it.
"""

import asyncio
import io
import uuid
from types import SimpleNamespace

from PIL import Image

from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from services import authored_deck_plans as deck_plans_mod
from services import authored_presentation_pipeline as pipeline_mod
from services import authored_presentation_service as authored_service
from services.authored_deck_illustration_plan import load_illustration_plan
from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    author_planned_slide,
    prepare_authored_deck,
)
from utils.llm_calls.author_slide import Brand
from models.authored_header_contract import default_header_contract


def _run(coro):
    return asyncio.run(coro)


async def _author_all(plan, count):
    return await asyncio.gather(
        *[author_planned_slide(plan, index) for index in range(count)]
    )


def _outline(n):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"슬라이드 {i} 내용") for i in range(n)]
    )


def _brand():
    return Brand(
        topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR"
    )


def _slide_html(index: int, *, slot: bool) -> str:
    body = f"<h1>slide-{index}</h1>"
    if slot:
        body += '<img data-illust-prompt="an abstract skyline">'
    return (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
        f"<body>{body}</body></html>"
    )


def _illustrating_style():
    return SimpleNamespace(
        id="editorial",
        brief="- Editorial",
        illustration_prompt="editorial illustration",
        reference_mode="style_reference",
    )


class _FakeImageService:
    """Writes a real PNG so the slot receives a genuine inlined data URI."""

    def __init__(self, directory):
        self.directory = directory
        self.prompts = []

    async def generate_image(self, prompt):
        self.prompts.append(prompt)
        path = self.directory / f"illustration-{len(self.prompts)}.png"
        buffer = io.BytesIO()
        Image.new("RGB", (64, 36), (12, 34, 56)).save(buffer, format="PNG")
        path.write_bytes(buffer.getvalue())
        return SimpleNamespace(path=str(path))


def _capture_prompt_inputs(monkeypatch):
    captured: dict[int, dict] = {}

    async def fake_author_slide_html(*args, **kwargs):
        index = args[4]
        captured[index] = {
            "illustration_allowed": kwargs.get("illustration_allowed"),
            "instructions": kwargs.get("instructions"),
        }
        return _slide_html(index, slot=False)

    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake_author_slide_html)
    return captured


def test_deck_plan_decides_illustrations_slide_by_slide_in_the_prompt(monkeypatch):
    """Each slide is told the deck's answer for itself, never asked to guess."""
    captured = _capture_prompt_inputs(monkeypatch)
    plan = prepare_authored_deck(
        _outline(4),
        _brand(),
        None,
        illustration_plan=AuthoredIllustrationPlan(slide_indices=(1, 3)),
        instructions="이미지는 꼭 필요한 곳에만",
    )

    assert plan.illustration_slots == (False, True, False, True)
    _run(_author_all(plan, 4))

    assert [captured[index]["illustration_allowed"] for index in range(4)] == [
        False,
        True,
        False,
        True,
    ]
    assert captured[2]["instructions"] == "이미지는 꼭 필요한 곳에만"


def test_a_deck_without_a_plan_still_lets_the_model_decide(monkeypatch):
    """No plan must mean exactly the behaviour this fork had before plans."""
    captured = _capture_prompt_inputs(monkeypatch)
    plan = prepare_authored_deck(_outline(3), _brand(), None)

    assert plan.illustration_slots == ()
    assert plan.instructions is None
    _run(_author_all(plan, 3))

    assert [captured[index]["illustration_allowed"] for index in range(3)] == [
        None,
        None,
        None,
    ]
    assert [captured[index]["instructions"] for index in range(3)] == [
        None,
        None,
        None,
    ]


def _run_pipeline_with_slots(monkeypatch, tmp_path, *, illustration_plan):
    """Author every slide with an illustration slot, whatever the plan says."""
    service = _FakeImageService(tmp_path)

    async def fake_author(plan, index, **_options):
        return _slide_html(index, slot=True)

    async def fake_render(html):
        return f"png-{html.count('data:image')}".encode()

    monkeypatch.setattr(pipeline_mod, "author_planned_slide", fake_author)
    monkeypatch.setattr(pipeline_mod, "render_html_to_png", fake_render)
    monkeypatch.setattr(pipeline_mod, "get_images_directory", lambda: str(tmp_path))
    monkeypatch.setattr(pipeline_mod, "ImageGenerationService", lambda _dir: service)

    result = _run(
        pipeline_mod._author_illustrate_render_pipeline(
            _outline(3),
            _brand(),
            _illustrating_style(),
            illustration_plan=illustration_plan,
        )
    )
    return result, service


def test_an_excluded_slide_never_gets_an_image_even_if_it_authored_a_slot(
    monkeypatch, tmp_path
):
    """The prompt is a request; the pipeline is the enforcement.

    Slides are authored in parallel, so one can still write a slot the deck did
    not grant it. That slot is withdrawn through the same removal every failure
    uses, so no provider call is made and no empty <img> box survives.
    """
    result, service = _run_pipeline_with_slots(
        monkeypatch,
        tmp_path,
        illustration_plan=AuthoredIllustrationPlan(slide_indices=(1,)),
    )

    assert len(service.prompts) == 1
    assert "data:image" not in result.deck.htmls[0]
    assert "data:image" in result.deck.htmls[1]
    assert "data:image" not in result.deck.htmls[2]
    # Withdrawn slots leave no markup behind at all, not a src-less picture box.
    assert "<img" not in result.deck.htmls[0]
    assert "<img" not in result.deck.htmls[2]
    assert "data-illust-prompt" not in "".join(result.deck.htmls)


def test_a_deck_without_a_plan_illustrates_exactly_as_before(monkeypatch, tmp_path):
    result, service = _run_pipeline_with_slots(
        monkeypatch, tmp_path, illustration_plan=None
    )

    assert len(service.prompts) == 3
    assert all("data:image" in html for html in result.deck.htmls)


def test_the_plan_round_trips_through_the_persisted_theme(
    monkeypatch, fake_async_session
):
    """A published deck carries its plan, so a later remake can replay it."""
    resolved = AuthoredIllustrationPlan(slide_indices=(0, 2))
    style = SimpleNamespace(id="editorial", brief=None, illustration_prompt="art")

    async def fake_resolve_illustration_plan(outline, brand, plan_style=None, **kwargs):
        return resolved

    async def fake_pipeline(*_args, **kwargs):
        assert kwargs["illustration_plan"] is resolved
        return authored_service.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult([_slide_html(0, slot=False)], "DESIGN SYSTEM"),
            pngs=[b"png"],
            elapsed=0.1,
            author_work=0.1,
            illustration_work=0.0,
            render_work=0.1,
        )

    async def fake_header_contract(outline, brand, plan_style=None, **kwargs):
        return default_header_contract(primary=brand.primary)

    monkeypatch.setattr(
        deck_plans_mod, "resolve_illustration_plan", fake_resolve_illustration_plan
    )
    monkeypatch.setattr(
        authored_service, "resolve_authored_style", lambda _style_id: style
    )
    monkeypatch.setattr(
        authored_service, "_author_illustrate_render_pipeline", fake_pipeline
    )
    monkeypatch.setattr(
        authored_service, "resolve_header_contract", fake_header_contract
    )
    monkeypatch.setattr(authored_service, "find_chrome", lambda: True)
    monkeypatch.setattr(
        authored_service,
        "_save_slide_pngs",
        lambda presentation_id, pngs: ["slide.png"],
    )
    monkeypatch.setattr(
        authored_service,
        "_build_authored_export",
        lambda request, presentation_id, pngs, title: "deck.pptx",
    )

    from utils import llm_provider

    monkeypatch.setattr(
        llm_provider, "get_llm_provider", lambda: SimpleNamespace(value="stub")
    )
    monkeypatch.setattr(llm_provider, "get_model", lambda: "stub-model")

    request = GeneratePresentationRequest(
        content="topic", template="authored", authored_style="editorial"
    )
    _run(
        authored_service.generate_authored_presentation(
            request, uuid.uuid4(), _outline(1), "Korean", fake_async_session
        )
    )
    presentation = next(
        item for item in fake_async_session.added
        if isinstance(item, PresentationModel)
    )

    assert presentation.theme["illustrationPlan"]["slide_indices"] == [0, 2]
    assert load_illustration_plan(presentation.theme) == resolved
