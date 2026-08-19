"""Wiring tests for source-image reuse in ordinary authored generation.

These cover the seam only: the gate, the three decks that must never see a
source image, the degradation posture, and the guarantee that the authoring
call and the substitution step are handed the identical candidate set. The
extraction, registry, selection and substitution modules have their own suites.
"""

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import authored_presentation_pipeline as pipeline
from services import authored_source_images as seam
from services.source_images.models import SourceImageCandidate
from utils.get_env import is_authored_source_images_enabled
from utils.llm_calls.author_slide import Brand
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)


@pytest.fixture(autouse=True)
def _app_data(tmp_path, monkeypatch):
    """The pipeline builds an ImageGenerationService rooted in app data.

    CI exports APP_DATA_DIRECTORY, but a bare local run does not, and without it
    the directory helper fails with an unrelated TypeError that hides whatever
    these tests are actually asserting.
    """
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))


def _run(coro):
    return asyncio.run(coro)


_VALID = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>제목</h1></body></html>"
)


def _candidate(asset_id: str, heading: str, slide_index: int = 0):
    return SourceImageCandidate(
        asset_id=asset_id,
        path=Path(f"/tmp/{asset_id}.png"),
        mime_type="image/png",
        width=1600,
        height=900,
        digest=asset_id * 4,
        source_slide_index=slide_index,
        source_heading=heading,
    )


def _outline(n):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"슬라이드 {i} 내용") for i in range(n)]
    )


def _brand():
    return Brand(
        topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR"
    )


def _style(illustration_prompt="flat vector"):
    return SimpleNamespace(
        id="default",
        reference_mode="style_reference",
        illustration_prompt=illustration_prompt,
    )


def _request(**overrides):
    request = SimpleNamespace(files=None)
    request._reference_snapshots = ()
    request._authored_master_resolution = None
    for key, value in overrides.items():
        setattr(request, key, value)
    return request


class _Session:
    """Just enough AsyncSession surface for the collection helper."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


# --------------------------------------------------------------------------
# gate reader
# --------------------------------------------------------------------------


def test_gate_defaults_off(monkeypatch):
    monkeypatch.delenv("AUTHORED_SOURCE_IMAGES_ENABLED", raising=False)
    assert is_authored_source_images_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_gate_reads_the_usual_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", value)
    assert is_authored_source_images_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "off", "maybe"])
def test_gate_treats_everything_else_as_off(monkeypatch, value):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", value)
    assert is_authored_source_images_enabled() is False


# --------------------------------------------------------------------------
# candidate collection at the generation entry
# --------------------------------------------------------------------------


def _install_registry(monkeypatch, calls, result=None, error=None):
    async def fake_register(session, *, presentation_id, upload_paths):
        calls.append((presentation_id, list(upload_paths)))
        if error is not None:
            raise error
        return list(result or [])

    monkeypatch.setattr(seam, "register_source_images", fake_register)


def test_gate_off_never_touches_the_registry(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "false")
    calls = []
    _install_registry(monkeypatch, calls, result=[_candidate("a", "매출")])

    session = _Session()
    got = _run(
        seam.collect_source_image_candidates(
            _request(files=["/tmp/deck.pptx"]), _style(), uuid.uuid4(), session
        )
    )

    assert got == []
    assert calls == []
    assert session.commits == 0


def test_gate_on_registers_every_reachable_upload(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    expected = [_candidate("a", "매출 현황")]
    _install_registry(monkeypatch, calls, result=expected)

    presentation_id = uuid.uuid4()
    session = _Session()
    got = _run(
        seam.collect_source_image_candidates(
            _request(files=["/tmp/deck.pptx", "/tmp/photo.png"]),
            _style(),
            presentation_id,
            session,
        )
    )

    assert got == expected
    assert calls == [
        (presentation_id, [Path("/tmp/deck.pptx"), Path("/tmp/photo.png")])
    ]
    # The registry only flushes, so the seam owns the commit.
    assert session.commits == 1


def test_reference_snapshot_assets_are_resolved_to_local_paths(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    asset_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    resolved = []

    class _Gateway:
        async def resolve_for_processing(
            self, session, *, asset_id, kind, conversation_id
        ):
            resolved.append((asset_id, kind, conversation_id))
            return object(), Path("/private/store/deck.pptx")

    monkeypatch.setattr(seam, "AssetGateway", _Gateway)
    calls = []
    _install_registry(monkeypatch, calls, result=[_candidate("a", "매출")])

    snapshot = SimpleNamespace(
        conversation_id=conversation_id,
        asset_bindings=(
            SimpleNamespace(asset_id=asset_id, kind="conversation_attachment"),
        ),
    )
    request = _request()
    request._reference_snapshots = (snapshot,)

    _run(
        seam.collect_source_image_candidates(
            request, _style(), uuid.uuid4(), _Session()
        )
    )

    assert resolved == [(asset_id, "conversation_attachment", conversation_id)]
    assert calls[0][1] == [Path("/private/store/deck.pptx")]


def test_master_owned_decks_are_skipped(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    _install_registry(monkeypatch, calls, result=[_candidate("a", "매출")])

    got = _run(
        seam.collect_source_image_candidates(
            _request(
                files=["/tmp/deck.pptx"],
                _authored_master_resolution=SimpleNamespace(owner_id=7),
            ),
            _style(),
            uuid.uuid4(),
            _Session(),
        )
    )

    assert got == []
    assert calls == []


def test_exact_reconstruction_is_skipped(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    _install_registry(monkeypatch, calls, result=[_candidate("a", "매출")])

    style = _style()
    style.reference_mode = "exact_reconstruction"
    got = _run(
        seam.collect_source_image_candidates(
            _request(files=["/tmp/deck.pptx"]), style, uuid.uuid4(), _Session()
        )
    )

    assert got == []
    assert calls == []


def test_no_uploads_means_no_registry_call(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    _install_registry(monkeypatch, calls, result=[_candidate("a", "매출")])

    got = _run(
        seam.collect_source_image_candidates(
            _request(), _style(), uuid.uuid4(), _Session()
        )
    )

    assert got == []
    assert calls == []


def test_a_raising_registry_degrades_instead_of_failing_the_deck(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    _install_registry(monkeypatch, calls, error=RuntimeError("storage is gone"))

    session = _Session()
    got = _run(
        seam.collect_source_image_candidates(
            _request(files=["/tmp/deck.pptx"]), _style(), uuid.uuid4(), session
        )
    )

    assert got == []
    assert session.commits == 0
    # The failed write must not be left pending for the publication transaction.
    assert session.rollbacks == 1


def test_an_unresolvable_upload_degrades_instead_of_failing_the_deck(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")

    class _Gateway:
        async def resolve_for_processing(self, session, **kwargs):
            raise RuntimeError("asset quarantined")

    monkeypatch.setattr(seam, "AssetGateway", _Gateway)
    calls = []
    _install_registry(monkeypatch, calls, result=[_candidate("a", "매출")])

    request = _request()
    request._reference_snapshots = (
        SimpleNamespace(
            conversation_id=None,
            asset_bindings=(SimpleNamespace(asset_id=uuid.uuid4(), kind="x"),),
        ),
    )
    got = _run(
        seam.collect_source_image_candidates(
            request, _style(), uuid.uuid4(), _Session()
        )
    )

    assert got == []
    assert calls == []


# --------------------------------------------------------------------------
# per-slide planning
# --------------------------------------------------------------------------


def test_plan_returns_none_when_the_deck_has_no_candidates():
    assert seam.plan_slide_source_images(("a", "b"), ()) is None


def test_plan_spends_each_candidate_on_at_most_one_slide():
    contents = ("매출 현황\n분기별 매출 추이", "매출 현황\n같은 주제 반복")
    candidates = [_candidate("first", "매출 현황"), _candidate("second", "매출 현황")]

    per_slide = seam.plan_slide_source_images(contents, candidates)

    assert [sorted(offer) for offer in per_slide] == [["first"], ["second"]]


def test_plan_offers_at_most_one_picture_per_slide():
    contents = ("매출 현황\n분기별 매출 추이",)
    candidates = [_candidate("first", "매출 현황"), _candidate("second", "매출 현황")]

    per_slide = seam.plan_slide_source_images(contents, candidates)

    assert len(per_slide) == 1
    assert len(per_slide[0]) == 1


def test_plan_swallows_a_failing_ranking(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("ranking exploded")

    monkeypatch.setattr(seam, "select_source_images", boom)

    assert seam.plan_slide_source_images(("a",), [_candidate("x", "매출")]) is None


def test_plan_leaves_an_unrelated_slide_without_an_offer():
    contents = ("창고 안전 수칙\n지게차 운행 규정",)
    candidates = [_candidate("first", "분기 매출 실적")]

    assert seam.plan_slide_source_images(contents, candidates) == [{}]


# --------------------------------------------------------------------------
# pipeline threading: author and substitution must agree
# --------------------------------------------------------------------------


def _install_pipeline_fakes(monkeypatch, author_calls, illustrate_calls, contents):
    plan = SimpleNamespace(contents=contents, design_system="DS")

    async def fake_author(selected_plan, index, **kwargs):
        author_calls.append((index, kwargs))
        return _VALID

    async def fake_illustrate(html, style, image_service, slide_index=0, **kwargs):
        illustrate_calls.append((slide_index, kwargs))
        return html

    async def fake_render(_html):
        return b"png"

    monkeypatch.setattr(
        pipeline, "prepare_authored_deck", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(pipeline, "author_planned_slide", fake_author)
    monkeypatch.setattr(pipeline, "apply_authored_illustration", fake_illustrate)
    monkeypatch.setattr(pipeline, "render_html_to_png", fake_render)


def test_gate_off_sends_no_source_images_to_either_consumer(monkeypatch):
    author_calls, illustrate_calls = [], []
    _install_pipeline_fakes(
        monkeypatch, author_calls, illustrate_calls, ("매출 현황\n본문",)
    )

    _run(
        pipeline._author_illustrate_render_pipeline(
            _outline(1), _brand(), _style()
        )
    )

    assert author_calls == [(0, {})]
    # The substitution step must not even see the keyword: ``None`` is what
    # keeps ``data-asset-id`` inert, and omitting it is the same default.
    assert illustrate_calls == [(0, {})]


def test_gate_on_hands_both_consumers_the_identical_set(monkeypatch):
    author_calls, illustrate_calls = [], []
    _install_pipeline_fakes(
        monkeypatch,
        author_calls,
        illustrate_calls,
        ("매출 현황\n분기별 매출 추이", "창고 안전 수칙\n지게차 운행 규정"),
    )
    candidates = [
        _candidate("sales", "매출 현황"),
        _candidate("safety", "창고 안전 수칙", slide_index=4),
    ]

    _run(
        pipeline._author_illustrate_render_pipeline(
            _outline(2), _brand(), _style(), source_images=candidates
        )
    )

    authored = {
        index: {candidate.asset_id for candidate in kwargs["source_images"]}
        for index, kwargs in author_calls
    }
    substituted = {
        index: set(kwargs["source_images"]) for index, kwargs in illustrate_calls
    }
    assert authored == {0: {"sales"}, 1: {"safety"}}
    assert substituted == authored


def test_a_slide_without_an_offer_still_gets_an_inert_empty_mapping(monkeypatch):
    author_calls, illustrate_calls = [], []
    _install_pipeline_fakes(
        monkeypatch,
        author_calls,
        illustrate_calls,
        ("매출 현황\n분기별 매출 추이", "완전히 다른 주제\n관련 없는 본문"),
    )

    _run(
        pipeline._author_illustrate_render_pipeline(
            _outline(2),
            _brand(),
            _style(),
            source_images=[_candidate("sales", "매출 현황")],
        )
    )

    offers = dict(author_calls)
    assert "source_images" in offers[0]
    # Nothing was offered, so the author is told nothing; substitution still
    # receives the mapping so a hallucinated id resolves to nothing.
    assert "source_images" not in offers[1]
    assert dict(illustrate_calls)[1]["source_images"] == {}


def test_exact_reconstruction_drops_candidates_inside_the_pipeline(monkeypatch):
    author_calls, illustrate_calls = [], []
    _install_pipeline_fakes(
        monkeypatch, author_calls, illustrate_calls, ("매출 현황\n본문",)
    )
    style = _style(illustration_prompt=None)
    style.reference_mode = "exact_reconstruction"

    _run(
        pipeline._author_illustrate_render_pipeline(
            _outline(1),
            _brand(),
            style,
            source_images=[_candidate("sales", "매출 현황")],
        )
    )

    assert author_calls == [(0, {})]
    assert illustrate_calls == []


def test_source_images_run_the_illustrate_stage_without_an_image_provider(
    monkeypatch,
):
    author_calls, illustrate_calls = [], []
    _install_pipeline_fakes(
        monkeypatch, author_calls, illustrate_calls, ("매출 현황\n분기별 매출 추이",)
    )

    _run(
        pipeline._author_illustrate_render_pipeline(
            _outline(1),
            _brand(),
            # No illustration prompt, so there is no image service at all.
            _style(illustration_prompt=None),
            source_images=[_candidate("sales", "매출 현황")],
        )
    )

    assert len(illustrate_calls) == 1
    assert set(illustrate_calls[0][1]["source_images"]) == {"sales"}


def test_failing_selection_degrades_instead_of_failing_the_deck(monkeypatch):
    author_calls, illustrate_calls = [], []
    _install_pipeline_fakes(
        monkeypatch, author_calls, illustrate_calls, ("매출 현황\n본문",)
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("ranking exploded")

    monkeypatch.setattr(seam, "select_source_images", boom)

    result = _run(
        pipeline._author_illustrate_render_pipeline(
            _outline(1),
            _brand(),
            _style(),
            source_images=[_candidate("sales", "매출 현황")],
        )
    )

    assert len(result.deck.htmls) == 1
    assert author_calls == [(0, {})]
    assert illustrate_calls == [(0, {})]
