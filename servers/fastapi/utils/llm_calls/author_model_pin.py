"""The authoring-model pin every page author honours.

``AUTHORED_AUTHOR_PROMPT_MODEL`` pins the model a run authors with; an
operation can also pin a whole prompt profile
(``author_prompt_profile.author_prompt_profile_scope``), whose model then wins.
Moved out of ``author_prompt_profile`` so svg authoring reads the pin without
importing the HTML author's prompt text; ``author_prompt_profile`` re-exports
``authoring_model`` and shares this module's context variable.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from utils.get_env_programme import (
    get_authored_author_prompt_model,
    get_authored_author_prompt_profile,
)

#: The operation's pinned ``AuthorPromptProfile``, set only by
#: ``author_prompt_profile.author_prompt_profile_scope``.
_PROFILE: ContextVar[Any] = ContextVar("authored_author_prompt_profile", default=None)


def authoring_model(default: object) -> object:
    """The profile's pinned authoring model, or the caller's own selection."""
    profile = _PROFILE.get()
    if profile is not None:
        return profile.model or default
    # The reads ``resolve_author_prompt_profile`` makes, so an invalid profile
    # name is refused here exactly as it is when the whole profile resolves.
    get_authored_author_prompt_profile()
    return get_authored_author_prompt_model() or default


__all__ = ["authoring_model"]
