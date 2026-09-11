"""One frozen choice of authoring-prompt text per operation, as a profile.

The authoring prompt has been rewritten many times since 2026-08-19 -- the
semantic-role vocabulary, the shared universals, the compressed SVG contract,
the content-box arithmetic, the connector clearance, the deck-token block, the
style exemplar, the type ramp and the reordering that puts the design system
first.  Measured on the same fixture, the deck-invariant brief grew from 8 235
to 11 569 characters for the default style and from 9 350 to 13 171 for a
preset.  The owner's judgement is that the decks got worse, and the composition
knobs (``docs/authored-composition-envelope-profiles-design-20260912.md``) are
already off by default, which leaves the prompt itself as the live suspect.

``AUTHORED_AUTHOR_PROMPT_PROFILE`` makes that testable as an A/B rather than an
argument: ``current`` (default) is today's prompt byte for byte, ``august-0819``
is the 2026-08-19 text with every contract the gates now require kept in place
(see ``author_slide_brief_august``).  One knob, one table, no per-paragraph
flags -- a reader should not have to assemble the August prompt correctly out of
six switches.

``AUTHORED_AUTHOR_PROMPT_MODEL`` pins the authoring model for the run.  Empty,
the default, changes nothing.  It lives in the same profile because composition
follows the model at least as much as the prompt, so a prompt A/B is only a
clean comparison at a fixed model, and the run has to be able to say which one
it fixed.

Resolution happens once: :func:`current_author_prompt_profile` is a pure read of
the request/task-scoped, immutable ``RuntimeSettings`` snapshot, so two reads
inside one operation cannot disagree, and :func:`author_prompt_profile_scope`
pins an explicit profile for callers that resolved it themselves.  Design:
``docs/authored-author-prompt-profiles-design-20260912.md``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Mapping

from utils.get_env_programme import (
    get_authored_author_prompt_model,
    get_authored_author_prompt_profile,
)
from utils.llm_calls import author_slide_rules
from utils.llm_calls.author_slide_brief import PRE_BUNDLE_TYPE_SCALES
from utils.llm_calls.author_slide_brief_august import (
    AUGUST_DEFAULT_STYLE_BRIEF,
    AUGUST_DESIGN_SYSTEM_RULES,
)

CURRENT = "current"
AUGUST = "august-0819"
PROFILE_NAMES = (CURRENT, AUGUST)

#: The order the per-slide blocks are stated in.  August put the slide's own
#: line first and the design system last, so its block order is the one that
#: existed then; the blocks invented since (the style anchor, the visual story,
#: the figure marker, the capacity brief) keep their current relative position
#: because August has no opinion about where a block it never had should sit.
_CURRENT_BLOCK_ORDER = (
    "anchor",
    "direction",
    "structure",
    "chapter",
    "requirement",
    "illustration",
    "visual_story",
    "figure",
    "capacity",
)
_AUGUST_BLOCK_ORDER = (
    "structure",
    "chapter",
    "requirement",
    "illustration",
    "direction",
    "anchor",
    "visual_story",
    "figure",
    "capacity",
)


class AuthorPromptProfileError(ValueError):
    """A coded, non-retryable authoring-prompt-profile failure."""

    def __init__(self, detail: str = "") -> None:
        self.code = "authored_author_prompt_profile_invalid"
        self.detail = detail[:240]
        self.retryable = False
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class PromptSections:
    """The prompt text and shape one profile hands to the builders.

    ``type_scales`` of ``None`` means "state nothing here": the knob that
    already decided the type scale (``AUTHORED_PROMPT_BUNDLE_ENABLED``) keeps
    deciding it, which is how ``current`` stays byte-identical to the behaviour
    it replaces.
    """

    design_system_rules: str
    default_style_brief: str
    type_scales: Mapping[str, str] | None
    #: Append the capability clause, the deck-token block and the style
    #: exemplar after the design-system rules.  August had none of the three.
    deck_invariant_tail: bool
    #: Name the role's archetype on the slide's own line.
    archetype_clause: bool
    #: State the design system after the slide's blocks instead of before them.
    design_system_last: bool
    block_order: tuple[str, ...]


#: The profile table.  Every row is a whole prompt, never a paragraph.
PROFILES: Mapping[str, PromptSections] = {
    CURRENT: PromptSections(
        design_system_rules=author_slide_rules.DESIGN_SYSTEM_RULES,
        default_style_brief=author_slide_rules.DEFAULT_STYLE_BRIEF,
        type_scales=None,
        deck_invariant_tail=True,
        archetype_clause=True,
        design_system_last=False,
        block_order=_CURRENT_BLOCK_ORDER,
    ),
    AUGUST: PromptSections(
        design_system_rules=AUGUST_DESIGN_SYSTEM_RULES,
        default_style_brief=AUGUST_DEFAULT_STYLE_BRIEF,
        type_scales=PRE_BUNDLE_TYPE_SCALES,
        deck_invariant_tail=False,
        archetype_clause=False,
        design_system_last=True,
        block_order=_AUGUST_BLOCK_ORDER,
    ),
}


@dataclass(frozen=True, slots=True)
class AuthorPromptProfile:
    """Which authoring prompt this operation writes with, as one value."""

    name: str = CURRENT
    #: Authoring model for the run, or ``None`` to leave the selection alone.
    model: str | None = None

    def __post_init__(self) -> None:
        if self.name not in PROFILES:
            raise AuthorPromptProfileError(f"name={self.name!r}")
        if self.model is not None and not (
            isinstance(self.model, str) and self.model.strip()
        ):
            raise AuthorPromptProfileError("model")

    @property
    def sections(self) -> PromptSections:
        return PROFILES[self.name]

    def type_scale(self, verbosity: str | None) -> str:
        """This profile's TYPE SCALE paragraph for a deck's verbosity."""
        scales = self.sections.type_scales
        if scales is None:
            return author_slide_rules.type_scale_rules(verbosity)
        return scales.get(verbosity or "standard", scales["standard"])


def resolve_author_prompt_profile() -> AuthorPromptProfile:
    """Read the two knobs once. The only live settings read there is."""
    return AuthorPromptProfile(
        name=get_authored_author_prompt_profile(),
        model=get_authored_author_prompt_model(),
    )


_PROFILE: ContextVar[AuthorPromptProfile | None] = ContextVar(
    "authored_author_prompt_profile", default=None
)


def pinned_author_prompt_profile() -> AuthorPromptProfile | None:
    """The operation's pinned profile, or ``None`` outside a pinned scope."""
    return _PROFILE.get()


def current_author_prompt_profile() -> AuthorPromptProfile:
    """The profile every prompt builder reads.

    Inside a pinned scope this is the value that scope froze.  Outside one it
    is resolved from the request/task-scoped settings snapshot, which is itself
    immutable for the life of the operation, so a deck cannot author half its
    slides under one prompt and half under another.
    """
    profile = _PROFILE.get()
    return profile if profile is not None else resolve_author_prompt_profile()


@contextmanager
def author_prompt_profile_scope(profile: AuthorPromptProfile):
    token = _PROFILE.set(profile)
    try:
        yield profile
    finally:
        _PROFILE.reset(token)


def current_prompt_sections() -> PromptSections:
    """Shorthand for the text half of the current profile."""
    return current_author_prompt_profile().sections


def authoring_model(default: object) -> object:
    """The profile's pinned authoring model, or the caller's own selection."""
    return current_author_prompt_profile().model or default


def assemble_author_prompt(
    *,
    design_system: str,
    blocks: Mapping[str, str],
    slide_line: str,
    page_marker: str,
    closing: str,
    profile: AuthorPromptProfile | None = None,
) -> str:
    """Order one slide's prompt the way its profile states it.

    ``blocks`` maps the per-slide block names in :data:`PROFILES` to their
    rendered text; an empty or missing value contributes nothing, so a deck
    without a chapter plan keeps exactly the prompt it had before the plan
    existed.
    """
    sections = (profile or current_author_prompt_profile()).sections
    lead = "".join(
        f"{blocks[name]}\n\n" for name in sections.block_order if blocks.get(name)
    )
    if sections.design_system_last:
        return f"{slide_line}\n\n{page_marker}\n\n{lead}{design_system}\n\n{closing}"
    return f"{design_system}\n\n{lead}{slide_line}\n\n{page_marker}\n\n{closing}"


__all__ = [
    "AUGUST",
    "CURRENT",
    "PROFILES",
    "PROFILE_NAMES",
    "AuthorPromptProfile",
    "AuthorPromptProfileError",
    "PromptSections",
    "assemble_author_prompt",
    "author_prompt_profile_scope",
    "authoring_model",
    "current_author_prompt_profile",
    "current_prompt_sections",
    "pinned_author_prompt_profile",
    "resolve_author_prompt_profile",
]
