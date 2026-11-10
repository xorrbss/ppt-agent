"""Wiring tests for source-image reuse in ordinary authored generation.

These cover the seam only: the gate, the three decks that must never see a
source image, the degradation posture, and the guarantee that the authoring
call and the substitution step are handed the identical candidate set. The
extraction, registry, selection and substitution modules have their own suites.
"""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import authored_source_images as seam
from services.source_images.models import SourceImageCandidate
from utils.get_env import is_authored_source_images_enabled
from utils.authored_image_slots import source_slot_id_for_asset
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
    # Production now reopens every candidate and verifies the sealed digest
    # before selection.  Keep these wiring fixtures byte-backed so a missing
    # test file cannot accidentally exercise the degradation branch.
    payload = (f"verified-source-image:{asset_id}").encode("utf-8")
    path = Path(os.environ["APP_DATA_DIRECTORY"]) / f"{asset_id}.png"
    path.write_bytes(payload)
    return SourceImageCandidate(
        asset_id=asset_id,
        path=path,
        mime_type="image/png",
        width=1600,
        height=900,
        digest=hashlib.sha256(payload).hexdigest(),
        source_slide_index=slide_index,
        source_heading=heading,
    )


def _outline(n):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"슬라이드 {i} 내용") for i in range(n)]
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
    # Matches ``GeneratePresentationRequest``'s private-attribute default.
    request._source_content_authored_reflow = False
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
    async def fake_register(session, *, presentation_id, upload_paths, **_kwargs):
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
        payload=lambda: {"documents": []},
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


def test_master_owned_decks_keep_body_source_images(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    path = tmp_path / "a.png"
    value = b"verified-body-image"
    path.write_bytes(value)
    candidate = SourceImageCandidate(
        asset_id="a",
        path=path,
        mime_type="image/png",
        width=1600,
        height=900,
        digest=hashlib.sha256(value).hexdigest(),
        source_slide_index=0,
        source_heading="매출",
    )
    _install_registry(monkeypatch, calls, result=[candidate])

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

    assert [candidate.asset_id for candidate in got] == ["a"]
    assert len(calls) == 1


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


def test_a_raising_registry_fails_closed_and_rolls_back(monkeypatch):
    monkeypatch.setenv("AUTHORED_SOURCE_IMAGES_ENABLED", "true")
    calls = []
    _install_registry(monkeypatch, calls, error=RuntimeError("storage is gone"))

    session = _Session()
    with pytest.raises(RuntimeError, match="authored_source_image_registration_failed"):
        _run(seam.collect_source_image_candidates(
            _request(files=["/tmp/deck.pptx"]), _style(), uuid.uuid4(), session
        ))
    assert session.commits == 0
    # The failed write must not be left pending for the publication transaction.
    assert session.rollbacks == 1


def test_an_unresolvable_upload_fails_closed_before_registration(monkeypatch):
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
    with pytest.raises(RuntimeError, match="authored_source_image_registration_failed"):
        _run(seam.collect_source_image_candidates(
            request, _style(), uuid.uuid4(), _Session()
        ))
    assert calls == []


# --------------------------------------------------------------------------
# per-slide planning
# --------------------------------------------------------------------------


def test_plan_returns_none_when_the_deck_has_no_candidates():
    assert seam.plan_slide_source_images(("a", "b"), ()) is None


def test_plan_reuses_all_relevant_candidates_on_each_slide():
    contents = ("매출 현황\n분기별 매출 추이", "매출 현황\n같은 주제 반복")
    candidates = [_candidate("first", "매출 현황"), _candidate("second", "매출 현황")]

    per_slide = seam.plan_slide_source_images(contents, candidates)

    expected = sorted([source_slot_id_for_asset("first"), source_slot_id_for_asset("second")])
    assert [sorted(offer) for offer in per_slide] == [expected, expected]


def test_plan_offers_multiple_pictures_per_slide():
    contents = ("매출 현황\n분기별 매출 추이",)
    candidates = [_candidate("first", "매출 현황"), _candidate("second", "매출 현황")]

    per_slide = seam.plan_slide_source_images(contents, candidates)

    assert len(per_slide) == 1
    assert len(per_slide[0]) == 2


def test_plan_allows_direct_upload_reuse_across_slides():
    contents = (
        "표지\n편집 가능한 덱",
        "기능 화면\n첨부한 features.png를 img 요소로 배치",
    )
    direct = _candidate("direct", None)

    per_slide = seam.plan_slide_source_images(contents, [direct])

    assert per_slide == [
        {source_slot_id_for_asset("direct"): direct},
        {source_slot_id_for_asset("direct"): direct},
    ]


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
