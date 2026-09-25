"""analyze_redesign_pptx records per-page stage timings of a real local-OCR run."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from services.source_content.raster_contracts import seal_analysis
from services.source_content.raster_llm_client import VisionJsonClient


def _font(size: int):
    for candidate in (Path(r"C:\Windows\Fonts\malgun.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return None


def _image_only_pptx(path: Path, image_paths: list[Path]) -> None:
    from pptx import Presentation
    from pptx.util import Emu

    deck = Presentation()
    deck.slide_width, deck.slide_height = Emu(14_630_400), Emu(8_229_600)
    for image_path in image_paths:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_picture(str(image_path), 0, 0, deck.slide_width, deck.slide_height)
    deck.save(path)


class _StubVision(VisionJsonClient):
    """The real client's gate and timing; a canned structure instead of a provider."""

    def __init__(self):
        super().__init__(settings=None, provider="stub", model="stub-vision")
        self.names: list[str] = []

    def _generate(self, system, parts, schema, name):
        self.names.append(name)
        time.sleep(0.01)
        if name == "slide_structure":
            content = {"title": ["L1"], "groups": [{"heading": [], "body": ["L2", "L3"]}], "screenshots": []}
        elif name == "line_transcripts":
            content = {"lines": []}
        else:
            content = {}
        return SimpleNamespace(content=content, usage=None)


def test_redesign_analysis_carries_stage_timings_outside_its_digest(tmp_path: Path):
    from services.source_content.raster_analysis import DEFAULT_TESSDATA, DEFAULT_TESSERACT, analyze_redesign_pptx

    tesseract = (
        DEFAULT_TESSERACT
        if DEFAULT_TESSERACT.is_file()
        else (Path(shutil.which("tesseract")) if shutil.which("tesseract") else None)
    )
    title_font, body_font = _font(64), _font(32)
    if tesseract is None or title_font is None or not DEFAULT_TESSDATA.is_dir():
        pytest.skip("local tesseract or Korean font unavailable")
    images = []
    for number in (1, 2):
        image = Image.new("RGB", (1672, 941), "white")
        draw = ImageDraw.Draw(image)
        draw.text((80, 40), f"분기 실적 {number}", font=title_font, fill=(20, 20, 40))
        draw.text((80, 300), "매출이 40% 증가했습니다", font=body_font, fill=(40, 40, 40))
        draw.text((80, 380), "영업이익은 2024년 120억 원", font=body_font, fill=(40, 40, 40))
        path = tmp_path / f"page{number}.png"
        image.save(path)
        images.append(path)
    source = tmp_path / "deck.pptx"
    _image_only_pptx(source, images)
    vision = _StubVision()

    draft = analyze_redesign_pptx(source, tesseract_path=tesseract, structurer=vision)

    timings = draft.analysis.stage_timings
    assert timings is not None and timings.clock == "monotonic"
    assert [page.page_number for page in timings.pages] == [1, 2]
    ran = {"ocr", "rereads", "number_units", "plan", "other", "structure_calls"}
    if "line_transcripts" in vision.names:
        ran.add("transcription")
    if "table_structure" in vision.names:
        ran.add("tables")
    for page in timings.pages:
        assert ran <= set(page.stages), page.stages
        assert all(seconds >= 0 for seconds in page.stages.values())
        assert sum(page.stages.values()) == pytest.approx(page.wall_seconds, abs=1e-6)
        assert page.vision_calls and all(call.latency_seconds >= 0.009 for call in page.vision_calls)
        assert page.stages["ocr"] > 0 and page.stages["structure_calls"] > 0
    assert timings.vision_call_count == len(vision.names)
    for name in ran:
        assert timings.stage_totals[name] == pytest.approx(sum(p.stages[name] for p in timings.pages))
        assert timings.stage_page_max[name] == max(p.stages[name] for p in timings.pages)
    assert timings.wall_seconds >= max(page.wall_seconds for page in timings.pages)
    # The receipt is measurement, not content: the digest is that of the analysis without it.
    assert seal_analysis(draft.analysis.model_copy(update={"stage_timings": None})).digest == draft.analysis.digest
    assert seal_analysis(draft.analysis).stage_timings == timings
