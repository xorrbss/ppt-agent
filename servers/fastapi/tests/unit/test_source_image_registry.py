"""Unit tests for the deck source-image registry."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from functools import wraps
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import pytest
from PIL import Image

from services.source_images import registry
from services.source_images.extract import ExtractedSourceImage
from services.source_images.models import (
    FURNITURE_REPEAT_THRESHOLD,
    MIN_SOURCE_IMAGE_EDGE_PX,
)
from services.source_images.registry import (
    ManifestImageOccurrence,
    register_manifest_image_occurrences,
    register_source_images,
)


def async_test(fn):
    """Repo convention: drive one coroutine per test without a plugin."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


class FakeSession:
    """Just enough AsyncSession surface for the registry's write path.

    Rows added stay addressable by primary key, so a second registration of the
    same deck sees the row the first one wrote — which is what makes the
    idempotency the registry relies on observable in a test.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_count = 0
        self._by_id: dict[Any, Any] = {}

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self._by_id[getattr(obj, "id", None)] = obj

    async def get(self, _model: Any, primary_key: Any) -> Any:
        return self._by_id.get(primary_key)

    async def flush(self) -> None:
        self.flush_count += 1


def _image_bytes(
    width: int, height: int, fmt: str = "PNG", color: str = "red"
) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format=fmt)
    return buffer.getvalue()


def _write_image(
    path: Path,
    width: int = 640,
    height: int = 480,
    fmt: str = "PNG",
    color: str = "red",
) -> bytes:
    payload = _image_bytes(width, height, fmt, color)
    path.write_bytes(payload)
    return payload


def _extracted(
    payload: bytes,
    *,
    slide_index: int,
    heading: Optional[str] = None,
    width: int = 640,
    height: int = 480,
    mime_type: str = "image/png",
) -> ExtractedSourceImage:
    return ExtractedSourceImage(
        payload=payload,
        mime_type=mime_type,
        width=width,
        height=height,
        digest=hashlib.sha256(payload).hexdigest(),
        source_slide_index=slide_index,
        source_heading=heading,
    )


@pytest.fixture
def app_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the writable-path helper at a per-test directory."""

    root = tmp_path / "app_data"
    root.mkdir()
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(root))
    return root


@pytest.fixture
def fake_extractor(monkeypatch: pytest.MonkeyPatch):
    """Replace the PPTX extractor with a per-path scripted result."""

    scripted: dict[str, list[ExtractedSourceImage]] = {}

    def _extract(pptx_path: Path) -> list[ExtractedSourceImage]:
        return scripted.get(Path(pptx_path).name, [])

    monkeypatch.setattr(registry, "extract_pptx_images", _extract)
    return scripted


@async_test
async def test_direct_upload_becomes_one_persisted_candidate(
    tmp_path: Path, app_data: Path
) -> None:
    upload = tmp_path / "photo.png"
    payload = _write_image(upload, 800, 600)
    session = FakeSession()
    presentation_id = uuid.uuid4()

    candidates = await register_source_images(
        session, presentation_id=presentation_id, upload_paths=[upload]
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.mime_type == "image/png"
    assert (candidate.width, candidate.height) == (800, 600)
    assert candidate.digest == hashlib.sha256(payload).hexdigest()
    assert candidate.source_slide_index is None
    assert candidate.path.read_bytes() == payload
    # Storage is scoped to the deck so it can be swept with the presentation.
    assert candidate.path.parent.name == str(presentation_id)
    assert session.flush_count == 1


@async_test
async def test_persisted_asset_is_uploaded_and_carries_source_metadata(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    payload = _image_bytes(700, 500)
    fake_extractor["deck.pptx"] = [
        _extracted(payload, slide_index=4, heading="Revenue", width=700, height=500)
    ]
    session = FakeSession()
    presentation_id = uuid.uuid4()

    candidates = await register_source_images(
        session,
        presentation_id=presentation_id,
        upload_paths=[tmp_path / "deck.pptx"],
    )

    assert len(session.added) == 1
    asset = session.added[0]
    assert asset.is_uploaded is True
    assert asset.path == str(candidates[0].path)
    assert asset.extras == {
        "presentation_id": str(presentation_id),
        "digest": hashlib.sha256(payload).hexdigest(),
        "mime_type": "image/png",
        "width": 700,
        "height": 500,
        "source_slide_index": 4,
        "source_heading": "Revenue",
        "source_text": None,
        "original_name": None,
        "ocr_text": None,
        "source_locator": None,
        "source_occurrence_id": None,
    }
    # The asset id is the identifier later stages resolve.
    assert candidates[0].asset_id == str(asset.id)


@async_test
async def test_unsupported_mime_type_is_dropped(
    tmp_path: Path, app_data: Path
) -> None:
    gif = tmp_path / "animation.gif"
    _write_image(gif, 900, 900, fmt="GIF")
    session = FakeSession()

    candidates = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[gif]
    )

    assert candidates == []
    assert session.added == []


@async_test
async def test_image_below_minimum_edge_is_dropped(
    tmp_path: Path, app_data: Path
) -> None:
    # Wide but short: the *shorter* edge is what disqualifies it.
    small = tmp_path / "rule.png"
    _write_image(small, 1200, MIN_SOURCE_IMAGE_EDGE_PX - 1)
    session = FakeSession()

    candidates = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[small]
    )

    assert candidates == []


@async_test
async def test_image_exactly_at_minimum_edge_is_kept(
    tmp_path: Path, app_data: Path
) -> None:
    edge = tmp_path / "square.png"
    _write_image(edge, MIN_SOURCE_IMAGE_EDGE_PX, MIN_SOURCE_IMAGE_EDGE_PX)
    session = FakeSession()

    candidates = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[edge]
    )

    assert len(candidates) == 1


@async_test
async def test_non_image_upload_is_ignored(tmp_path: Path, app_data: Path) -> None:
    stray = tmp_path / "notes.txt"
    stray.write_text("not a picture", encoding="utf-8")
    session = FakeSession()

    candidates = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[stray]
    )

    assert candidates == []


@async_test
async def test_duplicate_digest_keeps_the_first_occurrence(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    payload = _image_bytes(640, 480)
    fake_extractor["deck.pptx"] = [
        _extracted(payload, slide_index=1, heading="First"),
        _extracted(payload, slide_index=2, heading="Second"),
    ]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[tmp_path / "deck.pptx"],
    )

    assert len(candidates) == 1
    assert candidates[0].source_slide_index == 1
    assert candidates[0].source_heading == "First"
    assert len(session.added) == 1


@async_test
async def test_picture_repeated_across_enough_slides_is_furniture(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    logo = _image_bytes(400, 400, color="blue")
    content = _image_bytes(640, 480, color="green")
    fake_extractor["deck.pptx"] = [
        _extracted(logo, slide_index=index)
        for index in range(FURNITURE_REPEAT_THRESHOLD)
    ] + [_extracted(content, slide_index=0)]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[tmp_path / "deck.pptx"],
    )

    assert [candidate.digest for candidate in candidates] == [
        hashlib.sha256(content).hexdigest()
    ]


@async_test
async def test_repeats_below_the_threshold_survive(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    payload = _image_bytes(640, 480, color="blue")
    fake_extractor["deck.pptx"] = [
        _extracted(payload, slide_index=index)
        for index in range(FURNITURE_REPEAT_THRESHOLD - 1)
    ]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[tmp_path / "deck.pptx"],
    )

    assert len(candidates) == 1


@async_test
async def test_repeats_on_one_slide_are_not_furniture(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    # A picture placed several times on a single slide is still slide content;
    # only distinct slides make it deck furniture.
    payload = _image_bytes(640, 480, color="blue")
    fake_extractor["deck.pptx"] = [
        _extracted(payload, slide_index=2)
        for _ in range(FURNITURE_REPEAT_THRESHOLD + 2)
    ]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[tmp_path / "deck.pptx"],
    )

    assert len(candidates) == 1


@async_test
async def test_repeated_direct_uploads_dedupe_without_furniture_drop(
    tmp_path: Path, app_data: Path
) -> None:
    payload = _image_bytes(640, 480)
    uploads = []
    for index in range(FURNITURE_REPEAT_THRESHOLD + 1):
        upload = tmp_path / f"copy-{index}.png"
        upload.write_bytes(payload)
        uploads.append(upload)
    session = FakeSession()

    candidates = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=uploads
    )

    # No source slide means no repeat evidence, so dedupe alone applies.
    assert len(candidates) == 1


@async_test
async def test_order_follows_uploads_then_extraction(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    first = tmp_path / "first.png"
    _write_image(first, 640, 480, color="red")
    last = tmp_path / "last.png"
    _write_image(last, 640, 480, color="black")
    slide_one = _image_bytes(640, 480, color="green")
    slide_two = _image_bytes(640, 480, color="blue")
    fake_extractor["deck.pptx"] = [
        _extracted(slide_one, slide_index=0),
        _extracted(slide_two, slide_index=1),
    ]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[first, tmp_path / "deck.pptx", last],
    )

    assert [candidate.digest for candidate in candidates] == [
        hashlib.sha256(first.read_bytes()).hexdigest(),
        hashlib.sha256(slide_one).hexdigest(),
        hashlib.sha256(slide_two).hexdigest(),
        hashlib.sha256(last.read_bytes()).hexdigest(),
    ]


@async_test
async def test_no_uploads_persists_nothing(app_data: Path) -> None:
    session = FakeSession()

    candidates = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[]
    )

    assert candidates == []
    # Nothing to write means no transaction noise for the caller.
    assert session.flush_count == 0


@async_test
async def test_deck_suffixes_route_to_the_extractor(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    payload = _image_bytes(640, 480)
    for name in ("deck.PPTX", "macro.pptm", "legacy.ppt"):
        fake_extractor[name] = [_extracted(payload, slide_index=0)]
    session = FakeSession()

    for name in ("deck.PPTX", "macro.pptm", "legacy.ppt"):
        candidates = await register_source_images(
            FakeSession(), presentation_id=uuid.uuid4(), upload_paths=[tmp_path / name]
        )
        assert len(candidates) == 1, name

    assert session.added == []


@async_test
async def test_registration_is_idempotent_across_generation_retries(
    tmp_path: Path, app_data: Path
) -> None:
    """A retry must offer the ids the checkpointed slide HTML already names.

    Generation is durable and resumable: an attempt replays slide HTML that
    already carries data-asset-id, while registration runs again from the top.
    Fresh ids would strand that HTML and leak a row per attempt.
    """
    upload = tmp_path / "photo.png"
    _write_image(upload, 800, 600)
    session = FakeSession()
    presentation_id = uuid.uuid4()

    first = await register_source_images(
        session, presentation_id=presentation_id, upload_paths=[upload]
    )
    added_after_first = len(session.added)
    second = await register_source_images(
        session, presentation_id=presentation_id, upload_paths=[upload]
    )

    assert [item.asset_id for item in first] == [item.asset_id for item in second]
    assert len(session.added) == added_after_first


@async_test
async def test_manifest_occurrences_keep_repeated_bytes_as_distinct_required_slots(
    app_data: Path,
    monkeypatch,
) -> None:
    from contextvars import ContextVar
    from copy import deepcopy
    from types import SimpleNamespace
    from utils.authored_image_slots import source_slot_id_for_asset
    from services.svg_presentation_prepare import _restore_pinned_source_image_plan

    owner = uuid.uuid4()
    monkeypatch.setattr("utils.owner_context._CURRENT_OWNER_ID", ContextVar("registry-owner", default=owner))
    payload = _image_bytes(320, 240)
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    session = FakeSession()
    presentation_id = uuid.uuid4()
    occurrences = [
        ManifestImageOccurrence(
            occurrence_id="slide-1:image:7",
            payload=payload,
            mime_type="image/png",
            content_digest=digest,
            source_slide_index=0,
        ),
        ManifestImageOccurrence(
            occurrence_id="slide-1:image:9",
            payload=payload,
            mime_type="image/png",
            content_digest=digest,
            source_slide_index=0,
        ),
    ]

    candidates = await register_manifest_image_occurrences(
        session,
        presentation_id=presentation_id,
        occurrences=occurrences,
    )

    assert len(candidates) == 2
    assert candidates[0].asset_id != candidates[1].asset_id
    assert candidates[0].path == candidates[1].path
    original_rows = {row.id: (row.path, deepcopy(row.extras), row.owner_id) for row in session.added}
    original_bytes = candidates[0].path.read_bytes()
    inherited = SimpleNamespace(
        slots=[SimpleNamespace(slot_id=source_slot_id_for_asset(candidate.asset_id),
                               source_sha256=candidate.digest) for candidate in candidates],
        offered_pages=[SimpleNamespace(page_index=0,
            slot_ids=tuple(source_slot_id_for_asset(candidate.asset_id) for candidate in candidates))])
    child_id = uuid.uuid4()
    fresh = await register_manifest_image_occurrences(session, presentation_id=child_id, occurrences=occurrences)
    with pytest.raises(ValueError, match="svg_asset_source_plan_conflict"):
        _restore_pinned_source_image_plan(inherited, fresh,
            [{source_slot_id_for_asset(candidate.asset_id): candidate for candidate in fresh}])
    added_before_inheritance = len(session.added)
    for target_id in (child_id, uuid.uuid4()):
        inherited_candidates = await register_manifest_image_occurrences(session,
            presentation_id=target_id, occurrences=occurrences, source_identity_presentation_id=presentation_id)
        assert [candidate.asset_id for candidate in inherited_candidates] == [candidate.asset_id for candidate in candidates]
        assert all(str(target_id) in str(candidate.path) for candidate in inherited_candidates)
        assert all(candidate.path.read_bytes() == original_bytes for candidate in inherited_candidates)
        restored = _restore_pinned_source_image_plan(inherited, inherited_candidates,
            [{source_slot_id_for_asset(candidate.asset_id): candidate for candidate in inherited_candidates}])
        assert set(restored[0]) == set(inherited.offered_pages[0].slot_ids)
    assert len(session.added) == added_before_inheritance
    for asset_id, (path, extras, asset_owner) in original_rows.items():
        row = session._by_id[asset_id]
        assert (row.path, row.extras, row.owner_id) == (path, extras, asset_owner)
        assert await asyncio.to_thread(Path(path).read_bytes) == original_bytes
    first_id = uuid.UUID(candidates[0].asset_id)
    first = session._by_id[first_id]
    for violation in ("owner", "metadata", "bytes", "missing"):
        if violation == "owner":
            first.owner_id = uuid.uuid4()
        elif violation == "metadata":
            first.extras = {**first.extras, "source_occurrence_id": "another-occurrence"}
        elif violation == "bytes":
            await asyncio.to_thread(Path(first.path).write_bytes, b"changed source bytes")
        else:
            session._by_id.pop(first_id)
        with pytest.raises(ValueError, match="source_content_image_identity_unproven|source_content_image_digest_mismatch"):
            await register_manifest_image_occurrences(session, presentation_id=uuid.uuid4(),
                occurrences=occurrences, source_identity_presentation_id=presentation_id)
        first.owner_id = original_rows[first_id][2]
        first.extras = deepcopy(original_rows[first_id][1])
        await asyncio.to_thread(Path(first.path).write_bytes, original_bytes)
        session._by_id[first_id] = first


@async_test
async def test_the_same_picture_in_two_decks_gets_two_ids(
    tmp_path: Path, app_data: Path
) -> None:
    """The id is scoped to the deck, so one deck can never address another's."""
    upload = tmp_path / "photo.png"
    _write_image(upload, 800, 600)
    session = FakeSession()

    first = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[upload]
    )
    second = await register_source_images(
        session, presentation_id=uuid.uuid4(), upload_paths=[upload]
    )

    assert first[0].asset_id != second[0].asset_id


@async_test
async def test_a_full_slide_backplate_is_not_offered_as_content(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    """Rebuilding from one of this tool's own exports must not recycle pages.

    An authored-hybrid export carries one full-slide raster per page. Offering
    those would let the author paste a picture of an entire old slide onto a
    new one.
    """
    payload = _image_bytes(1280, 720)
    backplate = _extracted(payload, slide_index=0, width=1280, height=720)
    fake_extractor["export.pptx"] = [
        ExtractedSourceImage(**{**backplate.__dict__, "slide_coverage": 1.0})
    ]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[tmp_path / "export.pptx"],
    )

    assert candidates == []


@async_test
async def test_a_large_but_inset_picture_is_still_offered(
    tmp_path: Path, app_data: Path, fake_extractor
) -> None:
    """The cut is on placement, not on pixel size: a big diagram is content."""
    payload = _image_bytes(1600, 900)
    inset = _extracted(payload, slide_index=2, width=1600, height=900)
    fake_extractor["deck.pptx"] = [
        ExtractedSourceImage(**{**inset.__dict__, "slide_coverage": 0.48})
    ]
    session = FakeSession()

    candidates = await register_source_images(
        session,
        presentation_id=uuid.uuid4(),
        upload_paths=[tmp_path / "deck.pptx"],
    )

    assert len(candidates) == 1
