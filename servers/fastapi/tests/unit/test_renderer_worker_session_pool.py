"""Process reuse across a deck: lease pool, framed sessions, and the call site.

R2-B, authored-quality-speed programme design section 10 phase 3 "production
renderer batch pipe". Covers three seams:

* a lease in session mode reuses one worker session for N renders and
  round-robins over its pool instead of serializing the whole deck;
* ``utils.slide_capture`` reaches the lease only in external-worker mode;
* an unset deck scope means no reuse at all -- exactly the behaviour that
  predates the lease.

Imports ``utils.slide_capture`` before its worker-client siblings, matching
``tests/unit/test_render_with_lease.py``.
"""

# ruff: noqa: ASYNC109 - stubs mirror the renderer API, which takes a timeout

from __future__ import annotations

import asyncio

import pytest

from utils import slide_capture
from utils import slide_capture_worker_client as client
from utils import slide_capture_worker_lease as lease_module
from utils.slide_capture_deck_scope import (
    current_render_deck_scope,
    resolve_render_deck_scope,
    set_render_deck_scope,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"body"


class _FakeSession:
    """One framed worker process: reused until it is closed."""

    instances: list["_FakeSession"] = []

    def __init__(self, *, max_requests: int = 1000) -> None:
        self.renders: list[str] = []
        self.is_closed = False
        self.max_requests = max_requests
        _FakeSession.instances.append(self)

    async def render(self, html, timeout):
        if self.is_closed:
            raise AssertionError("render on a closed session")
        self.renders.append(html)
        if len(self.renders) >= self.max_requests:
            self.is_closed = True
        return PNG, {"slide": html}

    async def close(self) -> None:
        self.is_closed = True


@pytest.fixture(autouse=True)
def _reset():
    _FakeSession.instances = []
    lease_module._leases.clear()
    yield
    lease_module._leases.clear()


async def _unused_render_fn(_html, _timeout):  # pragma: no cover - must never run
    raise AssertionError("session mode must not fall back to the one-shot exchange")


def test_one_session_serves_every_render_of_a_deck():
    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-reuse",
            _unused_render_fn,
            session_factory=_FakeSession,
            pool_size=1,
            idle_seconds=30,
            max_renders=100,
        )
        for index in range(4):
            await lease.render(f"<div>{index}</div>", timeout=10)
        return lease

    lease = asyncio.run(scenario())

    assert len(_FakeSession.instances) == 1, "four renders, one worker process"
    assert _FakeSession.instances[0].renders == [f"<div>{index}</div>" for index in range(4)]
    assert lease.render_count == 4
    assert lease.open_session_count == 1


def test_the_pool_round_robins_admission_across_its_workers():
    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-pool",
            _unused_render_fn,
            session_factory=_FakeSession,
            pool_size=2,
            idle_seconds=30,
            max_renders=100,
        )
        assert lease.pool_size == 2
        await asyncio.gather(*(lease.render(f"<div>{index}</div>", 10) for index in range(4)))

    asyncio.run(scenario())

    assert len(_FakeSession.instances) == 2
    assert [len(session.renders) for session in _FakeSession.instances] == [2, 2]


def test_pool_size_is_ignored_without_a_session_factory():
    async def render_fn(_html, _timeout):
        return PNG, None

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-direct", render_fn, pool_size=4, idle_seconds=30
        )
        await lease.render("<div/>", 10)
        return lease

    assert asyncio.run(scenario()).pool_size == 1


def test_a_session_that_reaches_its_own_ceiling_is_replaced():
    def factory():
        return _FakeSession(max_requests=2)

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-ceiling",
            _unused_render_fn,
            session_factory=factory,
            pool_size=1,
            idle_seconds=30,
            max_renders=100,
        )
        for index in range(3):
            await lease.render(f"<div>{index}</div>", 10)

    asyncio.run(scenario())

    assert len(_FakeSession.instances) == 2
    assert [len(session.renders) for session in _FakeSession.instances] == [2, 1]


def test_closing_a_lease_closes_every_worker_session():
    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-close",
            _unused_render_fn,
            session_factory=_FakeSession,
            pool_size=2,
            idle_seconds=30,
            max_renders=100,
        )
        await asyncio.gather(lease.render("<div>a</div>", 10), lease.render("<div>b</div>", 10))
        await lease_module.close_all_leases()
        return lease

    lease = asyncio.run(scenario())

    assert lease.is_closed
    assert lease_module._leases == {}
    assert all(session.is_closed for session in _FakeSession.instances)
    assert len(_FakeSession.instances) == 2


def test_the_max_render_ceiling_closes_the_lease_and_its_sessions():
    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-max",
            _unused_render_fn,
            session_factory=_FakeSession,
            pool_size=1,
            idle_seconds=30,
            max_renders=2,
        )
        await lease.render("<div>a</div>", 10)
        await lease.render("<div>b</div>", 10)
        assert lease.is_closed
        # Sessions are closed off the idle/ceiling path by a background task.
        await asyncio.sleep(0)
        return lease

    asyncio.run(scenario())

    assert _FakeSession.instances[0].is_closed


def test_an_idle_lease_closes_its_sessions():
    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-idle-session",
            _unused_render_fn,
            session_factory=_FakeSession,
            pool_size=1,
            idle_seconds=0.05,
            max_renders=100,
        )
        await lease.render("<div/>", 10)
        await asyncio.sleep(0.2)
        assert lease.is_closed

    asyncio.run(scenario())

    assert _FakeSession.instances[0].is_closed


def test_a_failed_render_retires_both_the_session_and_the_lease():
    class _Poisoned(_FakeSession):
        async def render(self, html, timeout):
            raise RuntimeError("worker died")

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-poison",
            _unused_render_fn,
            session_factory=_Poisoned,
            pool_size=2,
            idle_seconds=30,
            max_renders=100,
        )
        with pytest.raises(RuntimeError):
            await lease.render("<div/>", 10)
        return lease

    lease = asyncio.run(scenario())

    assert lease.is_closed
    assert lease_module._leases == {}
    assert _FakeSession.instances[0].is_closed


def test_the_slide_capture_call_site_uses_the_lease_only_in_external_worker_mode(monkeypatch):
    scopes: list[str] = []

    async def fake_render_with_lease(deck_scope, html, timeout, render_fn=None):
        scopes.append(deck_scope)
        return PNG, None

    async def fake_reusable(html, timeout, *, script_nonce=None, cache_html=None):
        scopes.append("reusable")
        return PNG, None

    monkeypatch.setenv("AUTHORED_RENDERER_MODE", "isolated")
    monkeypatch.delenv("AUTHORED_RENDERER_LEGACY_ENABLED", raising=False)
    monkeypatch.setattr(slide_capture, "render_with_lease", fake_render_with_lease)
    monkeypatch.setattr(slide_capture, "_render_html_in_reusable_chrome", fake_reusable)
    monkeypatch.setattr(slide_capture, "CHROME_REUSE_ENABLED", True)

    monkeypatch.setattr(slide_capture, "renderer_requires_external_worker", lambda: False)
    asyncio.run(slide_capture._render_html_to_png_uncached("<main>a</main>", timeout=5))
    assert scopes == ["reusable"]

    monkeypatch.setattr(slide_capture, "renderer_requires_external_worker", lambda: True)
    asyncio.run(slide_capture.render_html_to_png("<main>b</main>", timeout=5, deck_scope="deck-77"))
    assert scopes == ["reusable", "deck-77"]


def test_an_unset_deck_scope_never_reuses_a_worker(monkeypatch):
    scopes: list[str] = []

    async def fake_render_with_lease(deck_scope, html, timeout, render_fn=None):
        scopes.append(deck_scope)
        return PNG, None

    monkeypatch.setenv("AUTHORED_RENDERER_MODE", "isolated")
    monkeypatch.setattr(slide_capture, "render_with_lease", fake_render_with_lease)
    monkeypatch.setattr(slide_capture, "renderer_requires_external_worker", lambda: True)

    assert current_render_deck_scope() is None
    asyncio.run(slide_capture._render_html_to_png_uncached("<main>a</main>", timeout=5))
    asyncio.run(slide_capture._render_html_to_png_uncached("<main>b</main>", timeout=5))

    assert len(set(scopes)) == 2, "every ad-hoc render gets its own single-use scope"
    assert all(scope.startswith("adhoc-") for scope in scopes)

    with set_render_deck_scope("deck-ambient"):
        asyncio.run(slide_capture._render_html_to_png_uncached("<main>c</main>", timeout=5))
    assert scopes[-1] == "deck-ambient"


def test_setting_a_deck_scope_is_scoped_to_its_block():
    with set_render_deck_scope("deck-a"):
        assert current_render_deck_scope() == "deck-a"
        assert resolve_render_deck_scope() == "deck-a"
        assert resolve_render_deck_scope("deck-b") == "deck-b"
        with set_render_deck_scope(None):
            assert current_render_deck_scope() is None
            assert resolve_render_deck_scope().startswith("adhoc-")
        assert current_render_deck_scope() == "deck-a"
    assert current_render_deck_scope() is None


def test_render_with_lease_uses_framed_sessions_only_when_the_knob_is_on(monkeypatch):
    monkeypatch.setenv("AUTHORED_RENDERER_LEASE_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_PRODUCTION", "true")
    monkeypatch.setenv("AUTHORED_RENDERER_OS_ISOLATION", "nonroot-readonly-tmpfs-network-none")
    monkeypatch.setattr(client, "RendererWorkerSession", _FakeSession)
    direct: list[str] = []

    async def one_shot(html, _timeout):
        direct.append(html)
        return PNG, None

    monkeypatch.setenv("AUTHORED_RENDERER_FRAMED_PROTOCOL_ENABLED", "false")
    asyncio.run(client.render_with_lease("deck-knob", "<div>a</div>", 10, render_fn=one_shot))
    assert direct == ["<div>a</div>"] and _FakeSession.instances == []

    lease_module._leases.clear()
    monkeypatch.setenv("AUTHORED_RENDERER_FRAMED_PROTOCOL_ENABLED", "true")
    asyncio.run(client.render_with_lease("deck-knob", "<div>b</div>", 10, render_fn=one_shot))
    assert direct == ["<div>a</div>"]
    assert [session.renders for session in _FakeSession.instances] == [["<div>b</div>"]]
