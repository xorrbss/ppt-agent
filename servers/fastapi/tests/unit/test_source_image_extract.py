"""Unit tests for PPTX source-image extraction.

Fixtures are assembled in-test with ``zipfile`` so no binary artefact is
committed and each case can bend exactly one part of the package.
"""

from __future__ import annotations

import dataclasses
import hashlib
from io import BytesIO
from pathlib import Path
import zipfile

from PIL import Image
import pytest

from services.source_images.extract import ExtractedSourceImage, extract_pptx_images

RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_RELATIONSHIP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _png_bytes(width: int = 640, height: int = 480, color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(width: int = 800, height: int = 600, color: str = "blue") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _webp_bytes(width: int = 320, height: int = 240, color: str = "green") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="WEBP")
    return buffer.getvalue()


def _title_shape(text: str) -> str:
    return (
        "<p:sp>"
        '<p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/>'
        '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        "<p:spPr/>"
        f"<p:txBody><a:bodyPr/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>"
        "</p:sp>"
    )


def _body_shape(text: str) -> str:
    return (
        "<p:sp>"
        '<p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        "<p:spPr/>"
        f"<p:txBody><a:bodyPr/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>"
        "</p:sp>"
    )


def _picture(relationship_id: str, extent: tuple[int, int] | None = None) -> str:
    """A picture shape, optionally stating the extent it was placed at (EMU)."""
    properties = (
        "<p:spPr/>"
        if extent is None
        else (
            f'<p:spPr><a:xfrm><a:off x="0" y="0"/>'
            f'<a:ext cx="{extent[0]}" cy="{extent[1]}"/></a:xfrm></p:spPr>'
        )
    )
    return (
        "<p:pic>"
        f'<p:nvPicPr><p:cNvPr id="4" name="Picture {relationship_id}"/>'
        "<p:cNvPicPr/><p:nvPr/></p:nvPicPr>"
        f'<p:blipFill><a:blip r:embed="{relationship_id}"/><a:stretch/></p:blipFill>'
        f"{properties}"
        "</p:pic>"
    )


def _slide_xml(*shapes: str) -> bytes:
    body = "".join(shapes)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:sld xmlns:a="{DRAWING_NS}" xmlns:p="{PRESENTATION_NS}" xmlns:r="{OFFICE_RELATIONSHIP_NS}">'
        f"<p:cSld><p:spTree>{body}</p:spTree></p:cSld>"
        "</p:sld>"
    ).encode("utf-8")


def _rels_xml(relations: dict[str, tuple[str, str]]) -> bytes:
    entries = "".join(
        f'<Relationship Id="{identifier}" Type="{OFFICE_RELATIONSHIP_NS}/{kind}" Target="{target}"/>'
        for identifier, (kind, target) in relations.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{RELATIONSHIP_NS}">{entries}</Relationships>'
    ).encode("utf-8")


def _presentation_xml(slide_relationship_ids: list[str]) -> bytes:
    slides = "".join(
        f'<p:sldId id="{256 + index}" r:id="{identifier}"/>'
        for index, identifier in enumerate(slide_relationship_ids)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:presentation xmlns:p="{PRESENTATION_NS}" xmlns:r="{OFFICE_RELATIONSHIP_NS}">'
        f"<p:sldIdLst>{slides}</p:sldIdLst>"
        '<p:sldSz cx="12192000" cy="6858000"/>'
        "</p:presentation>"
    ).encode("utf-8")


def _write_pptx(path: Path, parts: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return path


def _deck(
    path: Path,
    slides: list[tuple[bytes, dict[str, tuple[str, str]]]],
    media: dict[str, bytes],
) -> Path:
    """Assemble a package from per-slide XML plus that slide's relationships."""

    parts: dict[str, bytes] = {
        "ppt/presentation.xml": _presentation_xml([f"rIdSlide{index}" for index in range(1, len(slides) + 1)]),
        "ppt/_rels/presentation.xml.rels": _rels_xml(
            {
                f"rIdSlide{index}": ("slide", f"slides/slide{index}.xml")
                for index in range(1, len(slides) + 1)
            }
        ),
    }
    for index, (slide_xml, relations) in enumerate(slides, start=1):
        parts[f"ppt/slides/slide{index}.xml"] = slide_xml
        parts[f"ppt/slides/_rels/slide{index}.xml.rels"] = _rels_xml(relations)
    for name, payload in media.items():
        parts[f"ppt/media/{name}"] = payload
    return _write_pptx(path, parts)


@pytest.fixture
def two_slide_deck(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    first = _png_bytes(640, 480, "red")
    second = _jpeg_bytes(800, 600, "blue")
    path = _deck(
        tmp_path / "two.pptx",
        [
            (
                _slide_xml(_title_shape("Market Overview"), _picture("rId1")),
                {"rId1": ("image", "../media/image1.png")},
            ),
            (
                _slide_xml(_title_shape("Roadmap"), _picture("rId1")),
                {"rId1": ("image", "../media/image2.jpeg")},
            ),
        ],
        {"image1.png": first, "image2.jpeg": second},
    )
    return path, first, second


def test_extracts_one_image_per_slide(two_slide_deck: tuple[Path, bytes, bytes]) -> None:
    path, first, second = two_slide_deck

    images = extract_pptx_images(path)

    assert [image.source_slide_index for image in images] == [0, 1]
    assert [image.source_heading for image in images] == ["Market Overview", "Roadmap"]
    assert [image.mime_type for image in images] == ["image/png", "image/jpeg"]
    assert [(image.width, image.height) for image in images] == [(640, 480), (800, 600)]
    assert [image.payload for image in images] == [first, second]


def test_digest_is_bare_sha256_hex(two_slide_deck: tuple[Path, bytes, bytes]) -> None:
    path, first, _ = two_slide_deck

    images = extract_pptx_images(path)

    assert images[0].digest == hashlib.sha256(first).hexdigest()
    assert images[0].digest != images[1].digest


def test_result_is_the_documented_dataclass(two_slide_deck: tuple[Path, bytes, bytes]) -> None:
    path, _, _ = two_slide_deck

    images = extract_pptx_images(path)

    assert all(isinstance(image, ExtractedSourceImage) for image in images)
    with pytest.raises(dataclasses.FrozenInstanceError):
        images[0].width = 1  # type: ignore[misc]


def test_repeated_image_is_reported_once_per_slide(tmp_path: Path) -> None:
    shared = _png_bytes(500, 500, "green")
    path = _deck(
        tmp_path / "repeat.pptx",
        [
            (
                _slide_xml(_title_shape("One"), _picture("rId1")),
                {"rId1": ("image", "../media/logo.png")},
            ),
            (
                _slide_xml(_title_shape("Two"), _picture("rId9")),
                {"rId9": ("image", "../media/logo.png")},
            ),
            (
                _slide_xml(_title_shape("Three"), _picture("rId1")),
                {"rId1": ("image", "../media/logo.png")},
            ),
        ],
        {"logo.png": shared},
    )

    images = extract_pptx_images(path)

    assert len(images) == 3
    assert {image.digest for image in images} == {hashlib.sha256(shared).hexdigest()}
    assert [image.source_slide_index for image in images] == [0, 1, 2]


def test_same_image_placed_twice_on_one_slide_counts_once(tmp_path: Path) -> None:
    payload = _png_bytes(400, 400, "purple")
    path = _deck(
        tmp_path / "twice.pptx",
        [
            (
                _slide_xml(_title_shape("Duplicated"), _picture("rId1"), _picture("rId2")),
                {"rId1": ("image", "../media/pic.png"), "rId2": ("image", "../media/pic.png")},
            )
        ],
        {"pic.png": payload},
    )

    images = extract_pptx_images(path)

    assert len(images) == 1
    assert images[0].source_slide_index == 0


def test_slide_order_follows_the_slide_id_list(tmp_path: Path) -> None:
    first = _png_bytes(300, 300, "red")
    second = _png_bytes(320, 320, "blue")
    parts: dict[str, bytes] = {
        # The presentation lists slide2 first; filename order must not win.
        "ppt/presentation.xml": _presentation_xml(["rIdB", "rIdA"]),
        "ppt/_rels/presentation.xml.rels": _rels_xml(
            {
                "rIdA": ("slide", "slides/slide1.xml"),
                "rIdB": ("slide", "slides/slide2.xml"),
            }
        ),
        "ppt/slides/slide1.xml": _slide_xml(_title_shape("Second"), _picture("rId1")),
        "ppt/slides/_rels/slide1.xml.rels": _rels_xml({"rId1": ("image", "../media/a.png")}),
        "ppt/slides/slide2.xml": _slide_xml(_title_shape("First"), _picture("rId1")),
        "ppt/slides/_rels/slide2.xml.rels": _rels_xml({"rId1": ("image", "../media/b.png")}),
        "ppt/media/a.png": first,
        "ppt/media/b.png": second,
    }

    images = extract_pptx_images(_write_pptx(tmp_path / "order.pptx", parts))

    assert [image.source_heading for image in images] == ["First", "Second"]
    assert [image.source_slide_index for image in images] == [0, 1]


def test_heading_falls_back_to_first_text_then_none(tmp_path: Path) -> None:
    payload = _png_bytes()
    path = _deck(
        tmp_path / "headings.pptx",
        [
            (
                _slide_xml(_body_shape("   "), _body_shape("Quarterly results"), _picture("rId1")),
                {"rId1": ("image", "../media/pic.png")},
            ),
            (
                _slide_xml(_picture("rId1")),
                {"rId1": ("image", "../media/pic.png")},
            ),
        ],
        {"pic.png": payload},
    )

    images = extract_pptx_images(path)

    assert images[0].source_heading == "Quarterly results"
    assert images[1].source_heading is None


def test_long_heading_is_trimmed(tmp_path: Path) -> None:
    path = _deck(
        tmp_path / "long.pptx",
        [
            (
                _slide_xml(_title_shape("word " * 80), _picture("rId1")),
                {"rId1": ("image", "../media/pic.png")},
            )
        ],
        {"pic.png": _png_bytes()},
    )

    images = extract_pptx_images(path)

    assert len(images[0].source_heading or "") <= 120


def test_unsupported_media_type_is_skipped(tmp_path: Path) -> None:
    supported = _png_bytes(420, 420, "orange")
    path = _deck(
        tmp_path / "mixed.pptx",
        [
            (
                _slide_xml(_title_shape("Mixed"), _picture("rId1"), _picture("rId2"), _picture("rId3")),
                {
                    "rId1": ("image", "../media/diagram.svg"),
                    "rId2": ("image", "../media/motion.gif"),
                    "rId3": ("image", "../media/photo.png"),
                },
            )
        ],
        {
            "diagram.svg": b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>",
            "motion.gif": b"GIF89a" + b"\x00" * 32,
            "photo.png": supported,
        },
    )

    images = extract_pptx_images(path)

    assert [image.mime_type for image in images] == ["image/png"]
    assert images[0].payload == supported


def test_webp_is_supported(tmp_path: Path) -> None:
    payload = _webp_bytes(320, 240)
    path = _deck(
        tmp_path / "webp.pptx",
        [
            (
                _slide_xml(_title_shape("WebP"), _picture("rId1")),
                {"rId1": ("image", "../media/pic.webp")},
            )
        ],
        {"pic.webp": payload},
    )

    images = extract_pptx_images(path)

    assert [(image.mime_type, image.width, image.height) for image in images] == [("image/webp", 320, 240)]


def test_extension_and_magic_bytes_must_agree(tmp_path: Path) -> None:
    path = _deck(
        tmp_path / "mislabelled.pptx",
        [
            (
                _slide_xml(_title_shape("Mislabelled"), _picture("rId1")),
                {"rId1": ("image", "../media/photo.jpeg")},
            )
        ],
        # PNG bytes wearing a .jpeg extension.
        {"photo.jpeg": _png_bytes()},
    )

    assert extract_pptx_images(path) == []


def test_undecodable_payload_is_skipped(tmp_path: Path) -> None:
    truncated = _png_bytes()[:40]
    path = _deck(
        tmp_path / "truncated.pptx",
        [
            (
                _slide_xml(_title_shape("Broken"), _picture("rId1")),
                {"rId1": ("image", "../media/broken.png")},
            )
        ],
        {"broken.png": truncated},
    )

    assert extract_pptx_images(path) == []


def test_corrupt_package_returns_empty_list(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pptx"
    corrupt.write_bytes(b"PK\x03\x04 this is not a zip central directory")

    assert extract_pptx_images(corrupt) == []


def test_non_pptx_zip_returns_empty_list(tmp_path: Path) -> None:
    path = _write_pptx(tmp_path / "notes.pptx", {"readme.txt": b"hello"})

    assert extract_pptx_images(path) == []


def test_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert extract_pptx_images(tmp_path / "absent.pptx") == []


def test_directory_path_returns_empty_list(tmp_path: Path) -> None:
    assert extract_pptx_images(tmp_path) == []


def test_unparsable_slide_does_not_lose_the_deck(tmp_path: Path) -> None:
    payload = _png_bytes(360, 360, "teal")
    path = _deck(
        tmp_path / "partial.pptx",
        [
            (b"<p:sld><not-closed>", {"rId1": ("image", "../media/pic.png")}),
            (
                _slide_xml(_title_shape("Survivor"), _picture("rId1")),
                {"rId1": ("image", "../media/pic.png")},
            ),
        ],
        {"pic.png": payload},
    )

    images = extract_pptx_images(path)

    assert [(image.source_slide_index, image.source_heading) for image in images] == [(1, "Survivor")]


def test_external_and_dangling_relationships_are_ignored(tmp_path: Path) -> None:
    parts: dict[str, bytes] = {
        "ppt/presentation.xml": _presentation_xml(["rIdSlide1"]),
        "ppt/_rels/presentation.xml.rels": _rels_xml({"rIdSlide1": ("slide", "slides/slide1.xml")}),
        "ppt/slides/slide1.xml": _slide_xml(_title_shape("External"), _picture("rId1"), _picture("rId2")),
        "ppt/slides/_rels/slide1.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{RELATIONSHIP_NS}">'
            f'<Relationship Id="rId1" Type="{OFFICE_RELATIONSHIP_NS}/image"'
            ' Target="https://example.invalid/photo.png" TargetMode="External"/>'
            f'<Relationship Id="rId2" Type="{OFFICE_RELATIONSHIP_NS}/image" Target="../media/gone.png"/>'
            "</Relationships>"
        ).encode("utf-8"),
    }

    assert extract_pptx_images(_write_pptx(tmp_path / "external.pptx", parts)) == []


def test_shape_fill_pictures_are_extracted(tmp_path: Path) -> None:
    payload = _png_bytes(700, 350, "black")
    filled_shape = (
        "<p:sp>"
        '<p:nvSpPr><p:cNvPr id="5" name="Filled"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:blipFill><a:blip r:embed="rId1"/></a:blipFill></p:spPr>'
        "</p:sp>"
    )
    path = _deck(
        tmp_path / "fill.pptx",
        [
            (
                _slide_xml(_title_shape("Filled"), filled_shape),
                {"rId1": ("image", "../media/fill.png")},
            )
        ],
        {"fill.png": payload},
    )

    images = extract_pptx_images(path)

    assert [(image.width, image.height) for image in images] == [(700, 350)]


def test_absolute_relationship_targets_resolve(tmp_path: Path) -> None:
    payload = _png_bytes(512, 288, "yellow")
    path = _deck(
        tmp_path / "absolute.pptx",
        [
            (
                _slide_xml(_title_shape("Absolute"), _picture("rId1")),
                {"rId1": ("image", "/ppt/media/pic.png")},
            )
        ],
        {"pic.png": payload},
    )

    images = extract_pptx_images(path)

    assert [image.digest for image in images] == [hashlib.sha256(payload).hexdigest()]


# The deck fixture declares a 12192000 x 6858000 EMU slide.
_SLIDE_EMU = (12192000, 6858000)


def test_a_full_slide_picture_reports_full_coverage(tmp_path: Path) -> None:
    """A backplate must be recognisable, not just measurable.

    Decks this tool exports put one full-slide raster behind every page, so a
    deck rebuilt from such an export would otherwise offer a photograph of an
    entire old slide as content for a new one.
    """
    payload = _png_bytes(1280, 720)
    deck = _deck(
        tmp_path / "backplate.pptx",
        [(_slide_xml(_picture("rId1", _SLIDE_EMU)), {"rId1": ("image", "../media/a.png")})],
        {"a.png": payload},
    )

    images = extract_pptx_images(deck)

    assert len(images) == 1
    assert images[0].slide_coverage == pytest.approx(1.0)


def test_an_inset_picture_reports_its_share_of_the_slide(tmp_path: Path) -> None:
    payload = _png_bytes(1280, 720)
    half = (_SLIDE_EMU[0] // 2, _SLIDE_EMU[1] // 2)
    deck = _deck(
        tmp_path / "inset.pptx",
        [(_slide_xml(_picture("rId1", half)), {"rId1": ("image", "../media/a.png")})],
        {"a.png": payload},
    )

    images = extract_pptx_images(deck)

    assert images[0].slide_coverage == pytest.approx(0.25, abs=0.01)


def test_a_picture_without_a_stated_extent_reports_no_coverage(tmp_path: Path) -> None:
    """Silence is reported as unknown rather than guessed at."""
    payload = _png_bytes(1280, 720)
    deck = _deck(
        tmp_path / "unstated.pptx",
        [(_slide_xml(_picture("rId1")), {"rId1": ("image", "../media/a.png")})],
        {"a.png": payload},
    )

    images = extract_pptx_images(deck)

    assert images[0].slide_coverage is None
