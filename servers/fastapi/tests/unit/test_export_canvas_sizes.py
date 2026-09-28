"""Render/export QA paths use the deck's own canvas instead of a hardcoded
1280x720. Covers: PowerPoint COM QA's Export() size, ``trusted_powerpoint_qa``
threading ``theme`` through to the worker thread, the direct-attempt export
service passing ``presentation.theme`` in, and the legacy stacked /pdf-maker
batch capture's crop math (utils/slide_capture_batch.py)."""

from __future__ import annotations

import inspect

import pytest

from services.authored_canvas import (
    PORTRAIT_ASPECT_RATIO,
    canvas_metadata_for_aspect_ratio,
    merge_presentation_canvas,
    presentation_canvas_contract,
)


def _portrait_theme() -> dict:
    wire = canvas_metadata_for_aspect_ratio(PORTRAIT_ASPECT_RATIO).model_dump(
        mode="json", by_alias=True
    )
    return merge_presentation_canvas({}, wire)


def test_presentation_canvas_contract_defaults_to_legacy_landscape():
    contract = presentation_canvas_contract(None)
    assert (contract.width_px, contract.height_px) == (1280, 720)
    contract = presentation_canvas_contract({})
    assert (contract.width_px, contract.height_px) == (1280, 720)


def test_presentation_canvas_contract_resolves_non_landscape_theme():
    contract = presentation_canvas_contract(_portrait_theme())
    assert (contract.width_px, contract.height_px) == (720, 1280)


def test_run_powerpoint_com_qa_exports_at_the_resolved_canvas_size():
    from services.presentation_export_powerpoint_qa import _run_powerpoint_com_qa

    source = inspect.getsource(_run_powerpoint_com_qa)
    assert "theme: object | None = None" in source
    assert "canvas_contract = presentation_canvas_contract(theme)" in source
    assert (
        'first.Export(str(first_render), "PNG", canvas_contract.width_px, canvas_contract.height_px)'
        in source
    )
    assert (
        'reopened.Export(str(reopen_render), "PNG", canvas_contract.width_px, canvas_contract.height_px)'
        in source
    )
    # No leftover literal 1280/720 Export() calls.
    assert '"PNG", 1280, 720' not in source


def test_trusted_powerpoint_qa_threads_theme_into_the_worker_call():
    from services.presentation_export_powerpoint_qa import trusted_powerpoint_qa

    signature = inspect.signature(trusted_powerpoint_qa)
    assert "theme" in signature.parameters
    assert signature.parameters["theme"].default is None
    source = inspect.getsource(trusted_powerpoint_qa)
    call = source[source.index("asyncio.to_thread(") : source.index("except Exception")]
    assert "theme," in call or "theme)" in call


def test_direct_attempt_service_passes_presentation_theme_to_powerpoint_qa():
    from services import presentation_export_direct_attempt_service as service

    source = inspect.getsource(service)
    anchor = source.index("await trusted_powerpoint_qa(")
    call = source[anchor : source.index(")", source.index("release_required=", anchor))]
    assert "theme=presentation.theme" in call


class _FakeImage:
    def __init__(self, size, box=None):
        self.size = size
        self._box = box

    def convert(self, _mode):
        return self

    def crop(self, box):
        left, top, right, bottom = box
        return _FakeImage((right - left, bottom - top), box=box)

    def save(self, buf, format=None):  # noqa: A002 - PIL API name
        buf.write(f"PNG;{self.size[0]}x{self.size[1]}".encode("ascii"))


@pytest.mark.parametrize(
    ("width_px", "height_px"),
    [(1280, 720), (720, 1280)],
)
def test_capture_slides_impl_crops_at_the_supplied_canvas_size(
    monkeypatch, width_px, height_px
):
    from utils import slide_capture_batch

    n_slides = 3
    captured_window_size: list[str] = []

    async def wait_for_output(_path):
        return True

    def run_chrome_screenshot(args, _timeout):
        for arg in args:
            if arg.startswith("--window-size="):
                captured_window_size.append(arg.split("=", 1)[1])
        return b""

    fake_deck_size = (width_px, height_px * n_slides)

    def fake_open(_path):
        return _FakeImage(fake_deck_size)

    import PIL.Image as real_image_module

    monkeypatch.setattr(real_image_module, "open", fake_open)

    slides = await_capture(
        slide_capture_batch.capture_slides_impl(
            "deck-id",
            n_slides,
            env_enabled=lambda _name: True,
            find_chrome=lambda: "chrome.exe",
            run_chrome_screenshot=run_chrome_screenshot,
            wait_for_output=wait_for_output,
            width_px=width_px,
            height_px=height_px,
        )
    )

    assert captured_window_size == [f"{width_px},{height_px * n_slides}"]
    assert len(slides) == n_slides
    for index, png_bytes in enumerate(slides):
        marker = png_bytes.decode("ascii")
        assert marker == f"PNG;{width_px}x{height_px}", (index, marker)


def test_capture_slides_impl_defaults_preserve_legacy_1280x720():
    from utils import slide_capture_batch

    signature = inspect.signature(slide_capture_batch.capture_slides_impl)
    assert signature.parameters["width_px"].default == 1280
    assert signature.parameters["height_px"].default == 720


def test_capture_slides_wrapper_forwards_canvas_size_to_impl(monkeypatch):
    from utils import slide_capture

    captured: dict = {}

    async def fake_impl(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "utils.slide_capture_batch.capture_slides_impl", fake_impl
    )

    await_capture(
        slide_capture.capture_slides(
            "deck-id", 2, width_px=720, height_px=1280
        )
    )

    assert captured["width_px"] == 720
    assert captured["height_px"] == 1280


def await_capture(coro):
    import asyncio

    return asyncio.run(coro)
