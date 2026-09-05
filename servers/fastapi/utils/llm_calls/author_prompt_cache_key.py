"""One prompt-cache key per deck, carried to every authoring call.

``docs/authored-quality-speed-programme-design-20260905.md`` section 5 (G8) and
section 9 step 5. Every slide of a deck is authored with the same
deck-invariant prefix -- the system prompt, the design system, the header
contract -- and only the slide's own content differs. Providers reuse that
prefix only when the request tells them which decks' calls belong together:
the OpenAI SDK in this venv accepts ``prompt_cache_key`` on both
``chat.completions.create`` and ``responses.create``, and ``llmai`` forwards
``extra_body`` verbatim into both (its chatgpt/codex client pops only
``reasoning``).

The key is deck-scoped, never user-scoped or content-derived beyond the
prompt's own invariant part: it is a digest of the deck's design system, so two
decks sharing a design system share a cache lane and no key ever carries deck
content. It travels in a context variable because the authoring call sites are
several frames below the stage that knows the deck, and because the value must
follow each slide's own task rather than a mutable global.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from utils.get_env_programme import is_authored_prompt_cache_key_enabled

# Providers cap the key length; a 32-character digest prefix is well inside it
# and still collision-free for any realistic number of live decks.
CACHE_KEY_CHARS = 32

_PROMPT_CACHE_KEY: ContextVar[str | None] = ContextVar(
    "authored_prompt_cache_key", default=None
)


def deck_prompt_cache_key(design_system: Any) -> str | None:
    """The cache lane for a deck with this design system, or ``None``."""

    if not isinstance(design_system, str) or not design_system.strip():
        return None
    digest = hashlib.sha256(design_system.encode("utf-8")).hexdigest()
    return f"authored-{digest[:CACHE_KEY_CHARS]}"


@contextmanager
def set_prompt_cache_key(key: str | None) -> Iterator[None]:
    """Bind ``key`` for every authoring call made inside this block."""

    token = _PROMPT_CACHE_KEY.set(key or None)
    try:
        yield
    finally:
        _PROMPT_CACHE_KEY.reset(token)


def current_prompt_cache_key() -> str | None:
    """The bound key, or ``None`` when none is bound or the knob is off."""

    key = _PROMPT_CACHE_KEY.get()
    if not key or not is_authored_prompt_cache_key_enabled():
        return None
    return key


def apply_prompt_cache_key(generate_kwargs: dict) -> dict:
    """Merge the bound key into one call's ``extra_body``, in place.

    A call made with no key bound, or with the knob off, is left exactly as it
    was -- including keeping no ``extra_body`` at all -- and any reasoning dict
    already assembled there is preserved.
    """

    key = current_prompt_cache_key()
    if key is None:
        return generate_kwargs
    extra_body = generate_kwargs.get("extra_body")
    generate_kwargs["extra_body"] = {
        **(extra_body if isinstance(extra_body, dict) else {}),
        "prompt_cache_key": key,
    }
    return generate_kwargs


__all__ = [
    "CACHE_KEY_CHARS",
    "apply_prompt_cache_key",
    "current_prompt_cache_key",
    "deck_prompt_cache_key",
    "set_prompt_cache_key",
]
