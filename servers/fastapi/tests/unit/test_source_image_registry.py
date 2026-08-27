"""Unit tests for the deck source-image registry."""

from __future__ import annotations

import asyncio
import base64
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
    SourceImageCandidate,
)
from services.source_images.registry import (
    ManifestImageOccurrence,
    register_manifest_image_occurrences,
    register_source_images,
)
from utils.authored_illustrations import (
    enforce_strict_source_image_size_floor,
    validate_bound_source_images,
    validate_visible_source_images,
)
from utils.slide_capture import ensure_browser_metrics, render_html_to_png


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
) -> None:
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
    data_uri = "data:image/png;base64," + base64.b64encode(payload).decode()
    html = f'<html><body><img src="{data_uri}"><img src="{data_uri}"></body></html>'
    offered = {candidate.asset_id: candidate for candidate in candidates}
    assert validate_bound_source_images(html, offered) == html
    with pytest.raises(ValueError, match="authored_source_image_binding_mismatch"):
        validate_bound_source_images(
            f'<html><body><img src="{data_uri}"></body></html>',
            offered,
        )


def test_strict_source_visibility_rejects_hidden_tiny_offcanvas_and_strip(
    tmp_path: Path,
) -> None:
    payload = _image_bytes(320, 240)
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / "source.png"
    path.write_bytes(payload)
    candidate = SourceImageCandidate(
        asset_id="occurrence-1",
        path=path,
        mime_type="image/png",
        width=320,
        height=240,
        digest=digest,
        source_slide_index=0,
    )
    data_uri = "data:image/png;base64," + base64.b64encode(payload).decode()
    html = f'<html><body><img data-qid="source.image" src="{data_uri}"></body></html>'

    def metrics(*, bbox, style=None, image=None):
        return {
            "width": 1280,
            "height": 720,
            "elements": [
                {
                    "qid": "source.image",
                    "tag": "img",
                    "bbox": bbox,
                    "computed_style": {
                        "display": "block",
                        "visibility": "visible",
                        "opacity": "1",
                        **(style or {}),
                    },
                    "occluded_fraction": 0,
                    "image": {
                        "src": data_uri,
                        "complete": True,
                        "naturalWidth": 320,
                        "naturalHeight": 240,
                        "cssVisible": True,
                        "effectiveOpacity": 1,
                        "viewportIntersectionRatio": 1,
                        **(image or {}),
                    },
                }
            ],
        }

    offered = {candidate.asset_id: candidate}
    visible = metrics(bbox={"x": 80, "y": 80, "width": 320, "height": 240})
    assert validate_visible_source_images(html, offered, visible) == html

    hidden_cases = [
        metrics(
            bbox={"x": 80, "y": 80, "width": 320, "height": 240},
            style={"display": "none"},
            image={"cssVisible": False},
        ),
        metrics(
            bbox={"x": 80, "y": 80, "width": 320, "height": 240},
            image={"effectiveOpacity": 0.001},
        ),
        metrics(bbox={"x": 80, "y": 80, "width": 1, "height": 1}),
        metrics(
            bbox={"x": -9999, "y": 80, "width": 320, "height": 240},
            image={"viewportIntersectionRatio": 0},
        ),
        metrics(bbox={"x": 40, "y": 80, "width": 1100, "height": 24}),
    ]
    for hidden in hidden_cases:
        with pytest.raises(ValueError, match="authored_source_image_visibility_mismatch"):
            validate_visible_source_images(html, offered, hidden)


def test_large_source_evidence_requires_material_rendered_size(tmp_path: Path) -> None:
    payload = _image_bytes(1600, 900)
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / "screenshot.png"
    path.write_bytes(payload)
    candidate = SourceImageCandidate(
        asset_id="screenshot-occurrence",
        path=path,
        mime_type="image/png",
        width=1600,
        height=900,
        digest=digest,
        source_slide_index=24,
    )
    data_uri = "data:image/png;base64," + base64.b64encode(payload).decode()
    html = f'<html><body><img data-qid="source.screenshot" src="{data_uri}"></body></html>'
    offered = {candidate.asset_id: candidate}

    def metrics(width: float, height: float):
        return {
            "width": 1280,
            "height": 720,
            "elements": [
                {
                    "qid": "source.screenshot",
                    "tag": "img",
                    "bbox": {"x": 40, "y": 40, "width": width, "height": height},
                    "computed_style": {
                        "display": "block",
                        "visibility": "visible",
                        "opacity": "1",
                    },
                    "occluded_fraction": 0,
                    "image": {
                        "src": data_uri,
                        "complete": True,
                        "naturalWidth": 1600,
                        "naturalHeight": 900,
                        "cssVisible": True,
                        "effectiveOpacity": 1,
                        "viewportIntersectionRatio": 1,
                    },
                }
            ],
        }

    # s22-like evidence is large enough to read; s25's 68x38 thumbnails are not.
    assert validate_visible_source_images(html, offered, metrics(154, 86)) == html
    with pytest.raises(ValueError, match="authored_source_image_visibility_mismatch"):
        validate_visible_source_images(html, offered, metrics(68, 38))


@async_test
async def test_dense_source_gallery_enforces_evidence_floor_before_render(
    tmp_path: Path,
) -> None:
    specs = [
        (1600, 900, "red", "shot-1", "shot"),
        (1600, 900, "green", "shot-2", "shot"),
        (1600, 900, "blue", "shot-3", "shot"),
        (256, 256, "orange", "icon-1", "icon"),
        (256, 256, "purple", "icon-2", "icon"),
        (256, 256, "yellow", "icon-3", "icon"),
    ]
    offered: dict[str, SourceImageCandidate] = {}
    tags: list[str] = []
    for width, height, color, asset_id, kind in specs:
        payload = _image_bytes(width, height, color=color)
        path = tmp_path / f"{asset_id}.png"
        path.write_bytes(payload)
        candidate = SourceImageCandidate(
            asset_id=asset_id,
            path=path,
            mime_type="image/png",
            width=width,
            height=height,
            digest=hashlib.sha256(payload).hexdigest(),
            source_slide_index=25,
        )
        offered[asset_id] = candidate
        data_uri = "data:image/png;base64," + base64.b64encode(payload).decode()
        tags.append(
            f'<img class="{kind}" data-qid="source.{asset_id}" src="{data_uri}">'
        )
    html = (
        "<!doctype html><html><head><style>"
        "html,body{margin:0;width:1280px;height:720px;overflow:hidden}"
        ".gallery{display:flex;gap:12px;padding:40px}"
        ".shot{width:113px;height:71px;object-fit:contain}"
        ".icon{width:26px;height:26px;object-fit:contain}"
        "</style></head><body><div class=\"gallery\" data-qid=\"gallery\">"
        + "".join(tags)
        + "</div></body></html>"
    )

    sealed = enforce_strict_source_image_size_floor(html, offered)
    assert sealed == enforce_strict_source_image_size_floor(sealed, offered)
    assert sealed.count('id="authored-source-visibility-floor"') == 1
    assert sealed.count("min-width:144px;min-height:64px") == 3
    assert "source.icon-1\"]{min-width" not in sealed

    await render_html_to_png(sealed)
    metrics = await ensure_browser_metrics(sealed)
    assert validate_visible_source_images(sealed, offered, metrics) == sealed


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
