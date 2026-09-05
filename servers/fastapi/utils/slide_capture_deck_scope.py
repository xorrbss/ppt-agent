"""Ambient deck scope for authored slide renders.

The renderer worker lease groups renders by an opaque ``deck_scope`` so the
slides of one deck can share a worker process. Threading that key explicitly
through every renderer entry point would touch dozens of call sites that have
nothing to say about batching, so the deck-scoped pipeline sets it once for
the span of a deck and the renderer reads it here.

The default is deliberately "no reuse": when nothing set a scope, every render
gets its own single-use ``adhoc-<uuid>`` scope, which is exactly the
one-process-per-render behaviour that predates the lease. Sharing a worker
process is therefore something a caller opts into for a known deck, never
something an unrelated caller inherits -- which also keeps two tenants from
ever landing in the same renderer process by accident.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_render_deck_scope: ContextVar[str | None] = ContextVar(
    "authored_render_deck_scope", default=None
)

ADHOC_SCOPE_PREFIX = "adhoc-"


def current_render_deck_scope() -> str | None:
    """Return the deck scope set for the current task, or ``None``."""

    return _render_deck_scope.get()


@contextmanager
def set_render_deck_scope(scope: str | None) -> Iterator[None]:
    """Bind ``scope`` for every render started inside this block.

    ``None`` clears the scope, restoring per-render isolation for nested work
    (for example a shared cache-warming path) that must not join a deck.
    """

    token = _render_deck_scope.set(scope or None)
    try:
        yield
    finally:
        _render_deck_scope.reset(token)


def resolve_render_deck_scope(explicit: str | None = None) -> str:
    """Return the deck scope for one render: explicit, ambient, or single-use."""

    scope = explicit or _render_deck_scope.get()
    if scope:
        return scope
    return ADHOC_SCOPE_PREFIX + uuid.uuid4().hex


def is_adhoc_render_deck_scope(scope: str) -> bool:
    return scope.startswith(ADHOC_SCOPE_PREFIX)


__all__ = [
    "ADHOC_SCOPE_PREFIX",
    "current_render_deck_scope",
    "is_adhoc_render_deck_scope",
    "resolve_render_deck_scope",
    "set_render_deck_scope",
]
