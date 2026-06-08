"""Unit tests for the authored generation pipeline (no real LLM / no real Chrome).

Covers the resilience guarantees that keep authored generation from aborting on one
bad slide: HTML validity gating, branded fallback, per-slide author retry/fallback,
per-slide render placeholder, image-PPTX assembly, the authored-deck predicate, and
brand resolution."""

import asyncio
import io

from PIL import Image

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.sql.presentation import PresentationModel
from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls import author_slide as author_slide_mod
from utils.llm_calls.author_deck import author_deck, build_image_pptx, plan_deck_roles
from utils.llm_calls.author_slide import (
    Brand,
    fallback_slide_html,
    is_valid_slide_html,
)
from utils import slide_capture
from services.authored_presentation_service import resolve_brand


def _run(coro):
    return asyncio.run(coro)


_VALID = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1>제목</h1></body></html>"
)


def _outline(n):
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"슬라이드 {i} 내용") for i in range(n)]
    )


def _brand():
    return Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR")


# --- HTML validity + fallback ------------------------------------------------


def test_is_valid_slide_html_accepts_full_document():
    assert is_valid_slide_html(_VALID) is True


def test_is_valid_slide_html_rejects_garbage():
    assert is_valid_slide_html("") is False
    assert is_valid_slide_html("   ") is False
    assert is_valid_slide_html("not html") is False
    assert is_valid_slide_html("<html><head></head></html>") is False  # no body


def test_fallback_slide_html_is_valid_and_contains_content():
    html = fallback_slide_html("핵심 메시지 텍스트", _brand(), "COVER", 0, 5)
    assert is_valid_slide_html(html)
    assert "핵심 메시지 텍스트" in html
    assert "COVER" in html
    assert "#2563EB" in html


def test_fallback_slide_html_escapes_angle_brackets():
    html = fallback_slide_html("<script>x</script>", _brand(), "PROBLEM", 1, 3)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- author_deck resilience (mocked LLM) -------------------------------------


def test_author_deck_uses_authored_html_when_valid(monkeypatch):
    async def fake(content, ds, brand, role, index, n):
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", fake)
    htmls = _run(author_deck(_outline(4), _brand()))
    assert len(htmls) == 4
    assert all(h == _VALID for h in htmls)


def test_author_deck_falls_back_when_authoring_raises(monkeypatch):
    async def boom(content, ds, brand, role, index, n):
        raise RuntimeError("provider down")

    monkeypatch.setattr(author_deck_mod, "author_slide_html", boom)
    htmls = _run(author_deck(_outline(3), _brand()))
    assert len(htmls) == 3
    # Every slide degraded to a valid branded fallback (deck still completes).
    assert all(is_valid_slide_html(h) for h in htmls)
    assert any("슬라이드 0 내용" in h for h in htmls)


def test_author_deck_falls_back_when_authoring_returns_invalid(monkeypatch):
    async def empty(content, ds, brand, role, index, n):
        return ""

    monkeypatch.setattr(author_deck_mod, "author_slide_html", empty)
    htmls = _run(author_deck(_outline(2), _brand()))
    assert all(is_valid_slide_html(h) for h in htmls)


def test_author_deck_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def flaky(content, ds, brand, role, index, n):
        calls["n"] += 1
        if calls["n"] == 1:
            return ""  # first attempt invalid -> retry
        return _VALID

    monkeypatch.setattr(author_deck_mod, "author_slide_html", flaky)
    htmls = _run(author_deck(_outline(1), _brand()))
    assert htmls == [_VALID]
    assert calls["n"] == 2


# --- render resilience (mocked Chrome) ---------------------------------------


def test_render_list_uses_placeholder_on_failure(monkeypatch):
    async def boom(html, timeout=60):
        raise RuntimeError("no chrome")

    monkeypatch.setattr(slide_capture, "render_html_to_png", boom)
    pngs = _run(slide_capture.render_html_list_to_pngs([_VALID, _VALID]))
    assert len(pngs) == 2
    for png in pngs:
        img = Image.open(io.BytesIO(png))
        assert img.size == (slide_capture.SLIDE_W, slide_capture.SLIDE_H)


# --- image PPTX assembly -----------------------------------------------------


def _png_1280x720(color):
    img = Image.new("RGB", (1280, 720), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_build_image_pptx_one_fullbleed_picture_per_slide(tmp_path):
    from pptx import Presentation

    pngs = [_png_1280x720((i * 10, 0, 0)) for i in range(3)]
    out = str(tmp_path / "deck.pptx")
    build_image_pptx(pngs, out)
    prs = Presentation(out)
    assert len(prs.slides._sldIdLst) == 3
    assert prs.slide_width == 12191695 and prs.slide_height == 6858000  # 13.333x7.5"
    for s in prs.slides:
        pics = [sh for sh in s.shapes if sh.shape_type == 13]
        assert len(pics) == 1


# --- authored predicate + roles + brand --------------------------------------


def test_is_authored_predicate():
    by_theme = PresentationModel(
        content="x", n_slides=1, language="ko", theme={"mode": "authored"},
        layout={"name": "ignored"},
    )
    by_null_layout = PresentationModel(content="x", n_slides=1, language="ko")
    templated = PresentationModel(
        content="x", n_slides=1, language="ko", layout={"name": "adaptive"},
    )
    assert by_theme.is_authored() is True
    assert by_null_layout.is_authored() is True
    assert templated.is_authored() is False


def test_plan_deck_roles_cover_and_closing():
    roles = plan_deck_roles(_outline(5))
    assert roles[0] == "COVER"
    assert roles[-1] == "CLOSING"


def test_resolve_brand_language_fonts():
    req = GeneratePresentationRequest(content="t", language="Korean (한국어)", template="authored")
    ko = resolve_brand(req, _outline(2), "Korean (한국어)")
    assert ko.fonts == "Noto Sans KR"
    assert ko.primary == "#2563EB"

    req_en = GeneratePresentationRequest(content="t", language="English", template="authored")
    en = resolve_brand(req_en, _outline(2), "English")
    assert en.fonts == "Inter"
