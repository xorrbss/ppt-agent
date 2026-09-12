"""Every joiner of a shared render task waits on a bound it chose.

A shared render task is awaited by callers that do not own it.  An unbounded
wait there is not one slow caller: a wedged browser semaphore or a detached
Windows Chrome child strands every joiner at once, and the event loop that
would have timed the renderer out is the loop that is blocked.  The metrics
join has always passed a bound and documented why; the PNG render join did
not, and called the same helper.

These tests pin the property rather than the two call sites, so a third joiner
cannot reintroduce the unbounded wait.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from utils import shared_render_join, slide_capture


#: Both the facade name kept in ``slide_capture`` and the real one.
_HELPERS = {"_await_shared_render_task", "await_shared_render_task"}
_SCANNED = ("utils", "services")


def test_the_shared_join_helper_has_no_unbounded_form():
    """``timeout`` is required, so a caller must name a bound to compile."""

    signature = inspect.signature(shared_render_join.await_shared_render_task)
    assert slide_capture._await_shared_render_task is (
        shared_render_join.await_shared_render_task
    ), "the facade must not diverge from the implementation it re-exports"
    timeout = signature.parameters["timeout"]

    assert timeout.kind is inspect.Parameter.KEYWORD_ONLY
    assert timeout.default is inspect.Parameter.empty, (
        "a default would let a new joiner omit the bound and wait forever"
    )
    assert "None" not in str(timeout.annotation), (
        "an optional bound is the unbounded wait under another name"
    )


def test_every_call_site_passes_a_bound():
    """A guard against a joiner added later, anywhere, with the bound left off."""

    backend = Path(slide_capture.__file__).resolve().parent.parent
    unbounded = []
    for package in _SCANNED:
        for path in (backend / package).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):  # pragma: no cover - unreadable source
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                )
                if name not in _HELPERS:
                    continue
                if not any(keyword.arg == "timeout" for keyword in node.keywords):
                    rel = path.relative_to(backend).as_posix()
                    unbounded.append(f"{rel}:{node.lineno}")

    assert not unbounded, (
        f"a shared render join is awaited without a timeout at {sorted(unbounded)}; "
        "a shared task that never finishes strands every joiner"
    )


def test_a_wedged_shared_task_releases_its_joiner():
    """The joiner gives up on its own bound instead of waiting on the task."""

    async def scenario() -> dict:
        async def never_finishes() -> bytes:
            await asyncio.Event().wait()
            return b""

        inflight: dict = {}
        key = ("render", "wedged", "digest")
        task = asyncio.create_task(never_finishes())
        inflight[key] = task

        try:
            with pytest.raises(asyncio.TimeoutError):
                await slide_capture._await_shared_render_task(
                    task, inflight, key, timeout=0.05
                )
        finally:
            # The last waiter departing cancels the work it was keeping alive.
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return {"inflight": inflight, "key": key, "task": task}

    result = asyncio.run(scenario())

    assert result["inflight"].get(result["key"]) is not result["task"], (
        "the wedged task must not stay registered for the next caller to join"
    )
