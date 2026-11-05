"""One render computes each cache identity once; isolated-worker parents embed no faces."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services import svg_default_fonts
from services.svg_renderer import svg_preview_html
from utils import slide_capture

SMOKE = Path(__file__).resolve().parents[1] / "fixtures" / "svg_smoke"


@pytest.fixture
def wrapper() -> str:
    return svg_preview_html((SMOKE / "slide-2.svg").read_text(encoding="utf-8"))


@pytest.fixture
def computed(monkeypatch):
    calls: list[str] = []
    original = slide_capture._compute_render_cache_identity

    def counting(html, **kwargs):
        calls.append(html)
        return original(html, **kwargs)

    monkeypatch.setattr(slide_capture, "_compute_render_cache_identity", counting)
    return calls


def test_identity_is_computed_once_per_render_scope(computed):
    html = "<!doctype html><html><body>identity</body></html>"
    with slide_capture._render_identity_scope():
        first = slide_capture._render_cache_identity(html)
        for kind in ("render", "metrics", "capture"):
            assert slide_capture._cache_key_for(kind, html)[1] == first.digest
        assert slide_capture._render_cache_identity(html, width_px=640, height_px=360).digest != first.digest
    assert len(computed) == 2
    # Outside a render (and in the next one) the inputs are read again.
    assert slide_capture._render_cache_identity(html) == first
    with slide_capture._render_identity_scope():
        slide_capture._render_cache_identity(html)
    assert len(computed) == 4


def test_render_entry_points_open_the_scope():
    for entry in (slide_capture.render_html_to_png_with_metrics, slide_capture.render_html_to_png,
                  slide_capture.ensure_browser_metrics):
        assert entry.__wrapped__.__name__ == entry.__name__


def test_validation_only_admission_keeps_identity_and_errors(wrapper):
    validated = slide_capture._admit_render_source(wrapper, materialize_fonts=False)
    materialized = slide_capture._admit_render_source(wrapper)
    assert "data:font/" not in validated.html and "data:font/" in materialized.html
    assert validated.source_digest == materialized.source_digest
    unused = wrapper.replace('font-family="Noto Sans KR"', 'font-family="Pretendard"')
    for materialize_fonts in (True, False):
        with pytest.raises(ValueError, match="svg_default_font_profile_unused"):
            slide_capture._admit_render_source(unused, materialize_fonts=materialize_fonts)


def test_isolated_worker_parent_does_not_build_default_faces(monkeypatch, wrapper, computed):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the parent materialized the worker's faces")

    async def lease(scope, html, _timeout, *, render_fn, width_px, height_px):
        assert html == wrapper
        return b"png", None

    monkeypatch.setattr(svg_default_fonts, "materialize_default_fonts", refuse)
    monkeypatch.setattr(slide_capture, "renderer_requires_external_worker", lambda: True)
    monkeypatch.setattr(slide_capture, "render_with_lease", lease)
    slide_capture.reset_render_runtime(clear_caches=True, retire_session=False)
    assert asyncio.run(slide_capture.render_html_to_png(wrapper)) == b"png"
    assert computed == [wrapper]
