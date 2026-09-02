"""Sprite-sheet geometry, acceptance checks and inlining for icon packs.

Everything here runs on synthetic Pillow images: no provider, no OCR binary.
The OCR runtime is replaced by a stub so the decision rules are tested on
their own; the rules themselves were calibrated on the live sheets in
``artifacts/icon-pack-verification-20260903``.
"""

from __future__ import annotations

import asyncio
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from services.authored_icon_pack import checks as checks_mod
from services.authored_icon_pack.checks import (
    check_no_text,
    check_transparency,
    find_drawn_text,
    longest_latin_run,
)
from services.authored_icon_pack.inline import (
    INLINE_MAX_BYTES,
    icon_data_uri,
    shrink_png_for_inline,
)
from services.authored_icon_pack.sheet import (
    analyze_sheet,
    build_sheet_prompt,
    encode_png,
    layout_for,
    open_rgba,
    split_cells,
    trim_cell,
)


def _run(coro):
    return asyncio.run(coro)


def synthetic_sheet(
    count: int,
    layout,
    *,
    radius: int = 100,
    sizes: dict[int, int] | None = None,
    bleed: set[int] = frozenset(),
    empty: set[int] = frozenset(),
    opaque_background: bool = False,
) -> bytes:
    width, height = (int(v) for v in layout.size.split("x"))
    background = (255, 255, 255, 255) if opaque_background else (0, 0, 0, 0)
    image = Image.new("RGBA", (width, height), background)
    draw = ImageDraw.Draw(image)
    cell_w, cell_h = width // layout.columns, height // layout.rows
    for index in range(count):
        if index in empty:
            continue
        row, column = divmod(index, layout.columns)
        cx, cy = column * cell_w + cell_w // 2, row * cell_h + cell_h // 2
        r = (sizes or {}).get(index, radius)
        if index in bleed:
            cx = column * cell_w + r  # circle touches the left cell edge
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(30, 94, 255, 255))
    return encode_png(image)


@pytest.mark.parametrize(
    ("count", "columns", "rows", "size"),
    [(1, 3, 1, "1536x1024"), (3, 3, 1, "1536x1024"), (6, 3, 2, "1536x1024"), (9, 3, 3, "1024x1024")],
)
def test_layout_for_picks_square_cells(count, columns, rows, size):
    layout = layout_for(count)
    assert (layout.columns, layout.rows, layout.size) == (columns, rows, size)


def test_layout_for_rejects_out_of_range():
    with pytest.raises(ValueError):
        layout_for(0)
    with pytest.raises(ValueError):
        layout_for(10)


def test_sheet_prompt_quotes_concepts_by_row_and_forbids_text():
    concepts = ["감지", "예측", "조치", "학습"]
    prompt = build_sheet_prompt(concepts, layout_for(4), style_prompt="thin line")
    assert "3x2 grid" in prompt
    assert "Row 1 (left to right): '감지', '예측', '조치'." in prompt
    assert "Row 2 (left to right): '학습'." in prompt
    assert "TRANSPARENT" in prompt and "NO TEXT" in prompt
    assert "Visual style: thin line." in prompt


def test_uniform_sheet_splits_into_clean_cells():
    layout = layout_for(9)
    payload = synthetic_sheet(9, layout)
    analysis = analyze_sheet(payload, layout, 9)
    assert analysis.has_alpha
    assert analysis.empty_cells == ()
    assert analysis.bleeding_cells == ()
    assert analysis.extent_cv == 0.0
    assert 0.5 < analysis.transparent_fraction < 0.95
    cells = split_cells(open_rgba(payload), layout, 9)
    assert len(cells) == 9
    icon = trim_cell(cells[0], analysis.cells[0])
    assert icon.size[0] == icon.size[1] >= 200
    assert icon.getchannel("A").getpixel((0, 0)) == 0


def test_sheet_analysis_reports_empty_bleeding_and_uneven_cells():
    layout = layout_for(6)
    payload = synthetic_sheet(6, layout, sizes={2: 20}, bleed={4}, empty={5})
    analysis = analyze_sheet(payload, layout, 6)
    assert analysis.empty_cells == (5,)
    assert analysis.bleeding_cells == (4,)
    assert analysis.extent_cv > 0.30
    with pytest.raises(ValueError):
        trim_cell(split_cells(open_rgba(payload), layout, 6)[5], analysis.cells[5])


def test_trim_cell_fixed_canvas_gives_every_icon_the_same_edge():
    layout = layout_for(3)
    payload = synthetic_sheet(3, layout, sizes={1: 60})
    analysis = analyze_sheet(payload, layout, 3)
    cells = split_cells(open_rgba(payload), layout, 3)
    icons = [trim_cell(c, m, canvas=260) for c, m in zip(cells, analysis.cells, strict=True)]
    assert {icon.size for icon in icons} == {(260, 260)}


def test_transparency_check_rejects_opaque_and_haloed_pictures():
    layout = layout_for(3)
    assert check_transparency(synthetic_sheet(3, layout)).passed
    opaque = check_transparency(synthetic_sheet(3, layout, opaque_background=True))
    assert not opaque.passed and not opaque.corners_transparent
    rgb = Image.new("RGB", (64, 64), (255, 255, 255))
    buffer = io.BytesIO()
    rgb.save(buffer, "PNG")
    no_alpha = check_transparency(buffer.getvalue())
    assert not no_alpha.passed and not no_alpha.has_alpha
    halo = Image.new("RGBA", (200, 200), (30, 94, 255, 128))
    ImageDraw.Draw(halo).rectangle((0, 0, 199, 5), fill=(0, 0, 0, 0))
    ImageDraw.Draw(halo).rectangle((0, 194, 199, 199), fill=(0, 0, 0, 0))
    haloed = check_transparency(encode_png(halo))
    assert not haloed.passed and haloed.partial_alpha_fraction > 0.5


@pytest.mark.parametrize(
    ("ocr_text", "forbidden", "passed", "detail"),
    [
        ("", ["센서"], True, "no_text_found"),
        # Calibrated noise from real pictograms: stray glyphs, short runs.
        ("py PP\n| XX)  J\nanal ata S", ["게이트웨이"], True, "no_text_found"),
        ("El\nVil Wl\n5236", ["재고"], True, "no_text_found"),
        ("숭조\nil\n[ 4", ["대시보드"], True, "no_text_found"),
        # The concept name captioned under its icon, even split by whitespace.
        ("= A.\n납기 순수", ["납기"], False, "concept_name_drawn"),
        ("센 서", ["센서"], False, "concept_name_drawn"),
        ("~\n\nGateway", ["게이트웨이"], False, "latin_word_drawn"),
        # One-syllable concepts are never matched: too close to noise.
        ("이", ["이"], True, "no_text_found"),
    ],
)
def test_find_drawn_text_rules(ocr_text, forbidden, passed, detail):
    result = find_drawn_text(ocr_text, forbidden)
    assert result.passed is passed
    assert result.detail == detail
    assert result.method == "ocr"


def test_longest_latin_run_ignores_hangul_and_digits():
    assert longest_latin_run("abc 한글 12345 defgh") == 5
    assert longest_latin_run("") == 0


def _stub_ocr(monkeypatch, *, text, available=True, status="success"):
    async def fake(_payload, **_kwargs):
        return SimpleNamespace(available=available, status=status, text=text)

    import utils.authored_ocr_quality as ocr_mod

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", fake)


def test_check_no_text_uses_ocr_when_present(monkeypatch):
    _stub_ocr(monkeypatch, text="Gateway")
    payload = synthetic_sheet(1, layout_for(1))
    result = _run(check_no_text(payload, forbidden=["게이트웨이"]))
    assert not result.passed and result.method == "ocr"


def test_check_no_text_falls_back_to_heuristic_without_ocr(monkeypatch):
    _stub_ocr(monkeypatch, text=None, available=False, status="unavailable")
    payload = synthetic_sheet(1, layout_for(1))
    result = _run(check_no_text(payload, forbidden=["센서"]))
    assert result.passed and result.method == "heuristic"
    assert result.detail == "ocr_unavailable"

    async def boom(_payload, **_kwargs):
        raise RuntimeError("no tesseract")

    import utils.authored_ocr_quality as ocr_mod

    monkeypatch.setattr(ocr_mod, "inspect_authored_ocr", boom)
    result = _run(check_no_text(payload))
    assert result.passed and result.method == "heuristic"
    assert result.detail.startswith("ocr_unavailable:")


def test_flatten_on_white_makes_transparent_pixels_white():
    payload = synthetic_sheet(1, layout_for(1))
    flat = checks_mod.flatten_on_white(payload)
    with Image.open(io.BytesIO(flat)) as image:
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_shrink_png_for_inline_resizes_and_keeps_alpha():
    big = Image.new("RGBA", (3000, 1500), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse((100, 100, 2900, 1400), fill=(30, 94, 255, 255))
    shrunk = shrink_png_for_inline(encode_png(big), max_edge=1024)
    with Image.open(io.BytesIO(shrunk)) as image:
        assert image.size == (1024, 512)
        assert image.mode in ("RGBA", "P")
    assert len(shrunk) <= INLINE_MAX_BYTES


def test_shrink_png_for_inline_quantizes_when_still_too_large():
    import random

    random.seed(7)
    noisy = Image.new("RGBA", (1024, 1024))
    noisy.putdata(
        [
            (random.randrange(256), random.randrange(256), random.randrange(256), 255)
            for _ in range(1024 * 1024)
        ]
    )
    raw = encode_png(noisy)
    assert len(raw) > INLINE_MAX_BYTES
    shrunk = shrink_png_for_inline(raw, max_edge=1024)
    assert len(shrunk) < len(raw)
    with Image.open(io.BytesIO(shrunk)) as image:
        assert image.mode == "P"


def test_shrink_png_for_inline_returns_unreadable_bytes_unchanged():
    assert shrink_png_for_inline(b"not a png", max_edge=100) == b"not a png"


def test_icon_data_uri_round_trips_a_png(tmp_path):
    path = tmp_path / "icon.png"
    path.write_bytes(synthetic_sheet(1, layout_for(1)))
    uri = icon_data_uri(path)
    assert uri and uri.startswith("data:image/png;base64,")
    payload = base64.b64decode(uri.split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as image:
        assert max(image.size) <= 384
    assert icon_data_uri(tmp_path / "missing.png") is None
