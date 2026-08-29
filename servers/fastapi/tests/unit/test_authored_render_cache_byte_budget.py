import asyncio

from utils import slide_capture


def test_render_cache_byte_budget_evicts_without_changing_result(monkeypatch):
    slide_capture.reset_render_runtime(clear_caches=True, retire_session=False)

    async def fake_render(html: str, timeout: int = 60) -> bytes:
        del timeout
        return html.encode("utf-8") * 8

    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_render)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 8)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_BYTE_BUDGET", 12)

    first = asyncio.run(slide_capture.render_html_to_png("aa"))
    second = asyncio.run(slide_capture.render_html_to_png("bb"))

    assert first == b"aa" * 8
    assert second == b"bb" * 8
    assert slide_capture._render_cache_resident_bytes() <= 12
    assert not slide_capture._render_cache
