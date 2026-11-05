"""svg generation binds a per-deck renderer lease scope when workers can be reused."""

from __future__ import annotations

import asyncio

import pytest

from services.generation_core.drivers.svg import collect
from utils import slide_capture_worker_client as client
from utils import slide_capture_worker_lease as lease_module
from utils.slide_capture_deck_scope import current_render_deck_scope


@pytest.fixture(autouse=True)
def _no_open_leases():
    lease_module._leases.clear()
    yield
    lease_module._leases.clear()


@pytest.mark.parametrize(("framed", "expected"), [(True, "svg:deck-1"), (False, None)])
def test_generation_binds_the_deck_scope_only_for_reusable_workers(monkeypatch, framed, expected):
    seen = []

    async def generate_deck(*_args, **_kwargs):
        seen.append(current_render_deck_scope())
        return "published"

    monkeypatch.setenv("AUTHORED_RENDERER_FRAMED_PROTOCOL_ENABLED", "true" if framed else "false")
    monkeypatch.setattr(collect, "_generate_deck", generate_deck)
    assert asyncio.run(collect._generate(object(), "deck-1", None, "ko", None, None)) == "published"
    assert seen == [expected]
    assert current_render_deck_scope() is None
    assert collect._svg_render_deck_scope(None) is None


def test_a_canvas_size_opens_its_own_lease(monkeypatch):
    calls = []

    async def exchange(_html, _timeout, **kwargs):
        calls.append(kwargs)
        return b"png", None

    monkeypatch.setattr(client, "is_authored_renderer_lease_enabled", lambda: True)
    monkeypatch.setattr(client, "is_authored_renderer_framed_protocol_enabled", lambda: False)
    monkeypatch.setattr(client, "renderer_requires_external_worker", lambda: True)

    async def run():
        await client.render_with_lease("svg:deck", "<html></html>", render_fn=exchange)
        await client.render_with_lease("svg:deck", "<html></html>", render_fn=exchange,
                                       width_px=1080, height_px=1080)
        await client.render_with_lease("svg:deck", "<html></html>", render_fn=exchange)

    asyncio.run(run())
    assert set(lease_module._leases) == {"svg:deck", "svg:deck|1080x1080"}
    assert calls == [{}, {"width_px": 1080, "height_px": 1080}, {}]
