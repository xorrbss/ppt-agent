"""Wiring for ``render_with_lease`` (A8, authored-quality-speed programme
design section 10 phase 3): use a per-deck lease only when both
``AUTHORED_RENDERER_LEASE_ENABLED`` is on and this process requires the
external isolated renderer worker boundary; otherwise fall back to exactly
``_render_html_in_isolated_worker``, unchanged from before this lease existed.

Imports ``utils.slide_capture`` before ``utils.slide_capture_worker_client``,
matching ``tests/unit/test_renderer_isolation.py``: the two modules import
each other at module scope, so ``slide_capture`` must finish initializing
first in any process that has not already imported it.
"""

from __future__ import annotations

import asyncio

import pytest

from utils import slide_capture  # noqa: F401  (import order, see module docstring)
from utils import slide_capture_worker_client as client
from utils import slide_capture_worker_lease as lease_module


@pytest.fixture(autouse=True)
def _reset_lease_registry():
    lease_module._leases.clear()
    yield
    lease_module._leases.clear()


def _fake_isolated_worker_call(monkeypatch, calls):
    async def fake(html, render_timeout):
        calls.append((html, render_timeout))
        return (b"png", {"source_digest": "sha256:" + "1" * 64, "asset_digests": []})

    monkeypatch.setattr(client, "_render_html_in_isolated_worker", fake)
    return fake


def test_falls_back_directly_when_lease_knob_is_disabled(monkeypatch):
    monkeypatch.setenv("AUTHORED_RENDERER_LEASE_ENABLED", "false")
    monkeypatch.setenv("AUTHORED_RENDERER_PRODUCTION", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_OS_ISOLATION", "nonroot-readonly-tmpfs-network-none")
    calls: list[tuple[str, int]] = []
    _fake_isolated_worker_call(monkeypatch, calls)

    result = asyncio.run(client.render_with_lease("deck-x", "<div/>", 42))

    assert result[0] == b"png"
    assert calls == [("<div/>", 42)]
    assert lease_module._leases == {}


def test_falls_back_directly_when_process_is_not_in_external_worker_mode(monkeypatch):
    monkeypatch.setenv("AUTHORED_RENDERER_LEASE_ENABLED", "true")
    monkeypatch.delenv("AUTHORED_RENDERER_PRODUCTION", raising=False)
    monkeypatch.delenv("AUTHORED_RENDERER_PRODUCTION_STRICT", raising=False)
    monkeypatch.delenv("AUTHORED_RENDERER_REQUIRE_EXTERNAL", raising=False)
    monkeypatch.delenv("TEMPLATE_V2_DEPLOYMENT_TIER", raising=False)
    calls: list[tuple[str, int]] = []
    _fake_isolated_worker_call(monkeypatch, calls)

    result = asyncio.run(client.render_with_lease("deck-x", "<div/>", 30))

    assert result[0] == b"png"
    assert calls == [("<div/>", 30)]
    assert lease_module._leases == {}


def test_uses_a_shared_lease_when_enabled_in_external_worker_mode(monkeypatch):
    monkeypatch.setenv("AUTHORED_RENDERER_LEASE_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_PRODUCTION", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_OS_ISOLATION", "nonroot-readonly-tmpfs-network-none")
    calls: list[tuple[str, int]] = []
    _fake_isolated_worker_call(monkeypatch, calls)

    async def scenario():
        first = await client.render_with_lease("deck-shared", "<div>a</div>", 10)
        second = await client.render_with_lease("deck-shared", "<div>b</div>", 10)
        return first, second

    first, second = asyncio.run(scenario())

    assert calls == [("<div>a</div>", 10), ("<div>b</div>", 10)]
    assert first[0] == b"png" and second[0] == b"png"
    # One lease was opened and reused for both renders of the same deck.
    assert list(lease_module._leases) == ["deck-shared"]
    assert lease_module._leases["deck-shared"].render_count == 2


def test_different_deck_scopes_get_independent_leases(monkeypatch):
    monkeypatch.setenv("AUTHORED_RENDERER_LEASE_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_PRODUCTION", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_OS_ISOLATION", "nonroot-readonly-tmpfs-network-none")
    calls: list[tuple[str, int]] = []
    _fake_isolated_worker_call(monkeypatch, calls)

    async def scenario():
        await client.render_with_lease("deck-1", "<div/>", 10)
        await client.render_with_lease("deck-2", "<div/>", 10)

    asyncio.run(scenario())

    assert set(lease_module._leases) == {"deck-1", "deck-2"}
