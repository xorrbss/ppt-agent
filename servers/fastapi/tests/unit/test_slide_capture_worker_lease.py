"""Lifecycle of the per-deck renderer worker lease (A8, authored-quality-speed
programme design section 10 phase 3 "production renderer batch pipe").

These exercise ``utils.slide_capture_worker_lease`` against a fake worker
transport -- no real Chromium or subprocess involved -- so they run fast and
deterministically. See ``test_render_with_lease.py`` for the wiring that
picks between the lease and a direct isolated-worker call.
"""

from __future__ import annotations

import asyncio

import pytest

from utils import slide_capture_worker_lease as lease_module


@pytest.fixture(autouse=True)
def _reset_lease_registry():
    lease_module._leases.clear()
    yield
    lease_module._leases.clear()


def test_lease_requires_a_nonempty_deck_scope():
    async def fake_render(_html, _timeout):
        return (b"png", None)

    with pytest.raises(ValueError):
        lease_module.RendererWorkerLease("", fake_render)


def test_lease_reuses_render_fn_across_calls_and_preserves_isolation_fields():
    calls: list[tuple[str, int]] = []

    async def fake_render(html, render_timeout):
        calls.append((html, render_timeout))
        return (
            b"\x89PNG\r\n\x1a\n" + html.encode("utf-8"),
            {"source_digest": "sha256:" + "0" * 64, "asset_digests": []},
        )

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-1", fake_render, idle_seconds=5, max_renders=10
        )
        # A second acquire for the same deck_scope returns the same open lease.
        assert await lease_module.acquire_lease("deck-1", fake_render) is lease

        png1, metrics1 = await lease.render("<div>1</div>", timeout=10)
        png2, metrics2 = await lease.render("<div>2</div>", timeout=10)

        assert calls == [("<div>1</div>", 10), ("<div>2</div>", 10)]
        assert lease.render_count == 2
        assert not lease.is_closed
        assert png1.endswith(b"<div>1</div>")
        assert png2.endswith(b"<div>2</div>")
        # Isolation attestation fields ride through unchanged, per render.
        assert metrics1["source_digest"] == "sha256:" + "0" * 64
        assert metrics1["asset_digests"] == []
        assert metrics2["source_digest"] == "sha256:" + "0" * 64

    asyncio.run(scenario())


def test_lease_closes_after_idle_timeout():
    async def fake_render(_html, _timeout):
        return (b"png", None)

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-idle", fake_render, idle_seconds=0.05, max_renders=100
        )
        await lease.render("<div/>", timeout=5)
        assert not lease.is_closed

        await asyncio.sleep(0.2)
        assert lease.is_closed
        assert "deck-idle" not in lease_module._leases

        with pytest.raises(lease_module.RendererLeaseClosedError):
            await lease.render("<div/>", timeout=5)

        # A closed lease's deck_scope is available again for a fresh lease.
        fresh = await lease_module.acquire_lease(
            "deck-idle", fake_render, idle_seconds=5
        )
        assert fresh is not lease

    asyncio.run(scenario())


def test_lease_closes_once_the_render_ceiling_is_reached():
    async def fake_render(_html, _timeout):
        return (b"png", None)

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-max", fake_render, idle_seconds=5, max_renders=2
        )
        await lease.render("<div/>", timeout=5)
        assert not lease.is_closed

        await lease.render("<div/>", timeout=5)
        assert lease.is_closed
        assert lease.render_count == 2
        assert "deck-max" not in lease_module._leases

        with pytest.raises(lease_module.RendererLeaseClosedError):
            await lease.render("<div/>", timeout=5)

    asyncio.run(scenario())


def test_lease_retires_itself_when_a_render_fails():
    attempts = {"n": 0}

    async def flaky_render(_html, _timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return (b"png", None)
        raise RuntimeError("cdp pipe exploded")

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-fail", flaky_render, idle_seconds=5, max_renders=100
        )
        await lease.render("<div/>", timeout=5)
        assert not lease.is_closed

        with pytest.raises(RuntimeError, match="cdp pipe exploded"):
            await lease.render("<div/>", timeout=5)

        assert lease.is_closed
        assert "deck-fail" not in lease_module._leases
        with pytest.raises(lease_module.RendererLeaseClosedError):
            await lease.render("<div/>", timeout=5)

    asyncio.run(scenario())


def test_lease_serializes_renders_for_one_deck():
    """One worker session means one render in flight per deck_scope at a time."""

    active = 0
    max_active = 0

    async def fake_render(_html, _timeout):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return (b"png", None)

    async def scenario():
        lease = await lease_module.acquire_lease(
            "deck-serial", fake_render, idle_seconds=5, max_renders=100
        )
        await asyncio.gather(
            lease.render("<div/>", timeout=5),
            lease.render("<div/>", timeout=5),
            lease.render("<div/>", timeout=5),
        )
        assert lease.render_count == 3

    asyncio.run(scenario())
    assert max_active == 1


def test_acquire_lease_is_independent_per_deck_scope_and_close_all_closes_every_lease():
    async def fake_render(_html, _timeout):
        return (b"png", None)

    async def scenario():
        lease_a = await lease_module.acquire_lease(
            "deck-a", fake_render, idle_seconds=5, max_renders=100
        )
        lease_b = await lease_module.acquire_lease(
            "deck-b", fake_render, idle_seconds=5, max_renders=100
        )
        assert lease_a is not lease_b

        await lease_module.close_all_leases()

        assert lease_a.is_closed and lease_b.is_closed
        assert lease_module._leases == {}

    asyncio.run(scenario())
