"""Hoist the deck-invariant prompt prefix into the system message.

``docs/authored-quality-speed-programme-design-20260905.md`` section 5 (G8).
G8 already put the deck-invariant ``design_system`` block first in
``author_prompt``, which is what a provider needs in order to reuse a cached
prompt prefix across the slides of one deck. In the batch path -- which the
one-slide-per-call default also uses -- that ordering is not enough: the user
message opens with ``BEGIN REQUEST FOR SLIDE INDEX n``, so the very first
characters differ per slide and no prefix is shared at all.

Hoisting the invariant block into the *system* message fixes both paths: the
system message is byte-identical for every slide of the deck, so it is the
cacheable prefix, and a batch of three slides also stops carrying three copies
of the same ~20k-character block.

The assembled text is unchanged modulo the split: ``system_extra`` and
``user_text`` rejoin with ``"\\n\\n"`` into exactly the original prompt, and
the blocks keep their order. ``AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED`` off
sends exactly today's two messages.

C2 investigation (``docs/authored-sol-quality-first-design-20260908.md``,
amendment C2): bench run 48 sent ~12.7k input tokens per authoring call, of
which ~9.2k were meant to be the deck-invariant prefix, but only 5 of 25 calls
reported a nonzero ``cached_tokens``. Everything found reading the two live
call sites (this module's ``author_messages``, used by
``services.generation_core.authoring.author_slide``, and the independent hoist in
``services.generation_core.authoring.author_batch.author_slides_html`` -- which is the one the
pipeline's wave scheduler actually drives, since both shipped rate profiles
set ``slides_per_call = 1``) that can make one call's prefix differ
byte-for-byte from another's, or make an identical prefix still miss:

Prefix divergence:

- **The anchor is absent on its own authoring call.** ``resolve_style_anchor``
  authors the anchor slide before any other call of the deck, so that one
  call's prompt has no ``DECK STYLE ANCHOR`` block; every later call's does.
  This is unavoidable -- something has to author the anchor first -- but it
  means the anchor call can never itself hit the prefix the rest of the deck
  shares.
- **Splice-order problem (the one this module fixes under the warm-up
  knob).** ``author_slide_prompt.author_prompt`` places the anchor block
  immediately after ``design_system`` once the anchor exists, but
  ``split_deck_invariant`` only ever recognises ``design_system`` itself as
  the invariant head. The anchor block -- up to ``MAX_ANCHOR_CSS_CHARS``
  (6,000 characters, ~1.5k tokens) of CSS, identical across the other ~23
  calls of the deck -- therefore rides inside each call's still-varying user
  message instead of the hoisted, cacheable system message, shrinking the
  prefix a provider can actually reuse from ~9.2k tokens down to whatever
  ``design_system`` alone is. ``invariant_prefix_source`` below closes this
  by extending the invariant head with the anchor's own
  ``prompt_block()`` text, byte-for-byte identical to what ``author_prompt``
  splices in, whenever the warm-up knob is on and an anchor is already
  resolved.
- **A prompt whose shape changes silently loses the hoist, never errors.**
  ``split_deck_invariant`` is a plain ``str.startswith`` check; a future
  per-slide value spliced ahead of ``design_system``, a role-conditioned
  exemplar, or the design system being re-serialized differently between the
  deck plan and a particular call would all quietly return ``("", prompt)``
  instead of raising, so the hoist can regress without a test noticing unless
  it asserts the split actually fires.
- **Two independent call sites, one hoist each.** ``author_slide.py`` and
  ``author_batch.py`` each build and split their own prompt; a fix applied to
  one and not the other reproduces the defect on whichever is actually live
  for the configured rate profile.
- **The hoist itself is what makes a prefix exist at all in the batch path.**
  With ``AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED`` off, a batch call's user
  message opens with a per-slide ``"BEGIN REQUEST FOR SLIDE INDEX n"`` line,
  so no shared prefix survives regardless of the anchor.

Reasons an identical prefix can still miss:

- **No cache exists yet.** The deck's very first call (the anchor's own) can
  never hit; this is not a defect.
- **A concurrent burst races the cache write.** A provider's cache entry is
  written after a call finishes, on whichever backend served it. The batch
  policy widens from one call to its steady width (``2`` on the token
  profile, ``author_concurrency`` on the api profile) as soon as the first
  post-anchor call succeeds, which can admit several calls at close to the
  same instant the warm entry is written -- before it has necessarily
  propagated to whichever replica a concurrent request lands on.
- **``prompt_cache_key``-based routing is a hint, not a guarantee.** Under
  load a request can land on a colder replica than the one holding the warm
  entry; that replica then starts its own independent copy, so a burst of
  concurrent calls can fragment the "warm" set across several machines
  instead of converging on one.
- **Idle time can outlive the cache TTL.** A deck stalled behind a rate-limit
  cooldown (``AUTHORED_AUTHOR_RATE_LIMIT_COOLDOWN_SECONDS``, or the batch
  policy's overload cooldown, up to five minutes) can resume authoring after
  the provider has already evicted the entry.

``AUTHORED_PROMPT_CACHE_WARMUP_ENABLED`` (default off) addresses the first
category directly (a real code defect) and the concurrency-race half of the
second by making ``services.authored_wave_scheduler`` run exactly one call
with the final, anchor-inclusive prefix to completion before admitting any
other call of the initial-authoring wave. The remaining reasons for a miss
are provider-side behaviour this module cannot fix or verify offline; they
are recorded here so a future bench run's hit-rate log line
(``services.authored_prompt_cache_report``) is read against a known list of
causes rather than treated as unexplained noise.
"""

from __future__ import annotations

from typing import Any, Sequence

from llmai.shared import SystemMessage, TextContentPart, UserMessage

from utils.get_env_programme import (
    is_authored_prompt_cache_warmup_enabled,
    is_authored_prompt_system_hoist_enabled,
)

BLOCK_SEPARATOR = "\n\n"


def split_deck_invariant(
    prompt_text: str, design_system: str | None
) -> tuple[str, str]:
    """Split one slide prompt into its deck-invariant head and its own text.

    Returns ``("", prompt_text)`` -- no hoist -- unless the prompt really does
    open with the design-system block, so a prompt whose shape changed can only
    lose the optimisation, never lose text.
    """

    if not prompt_text or not isinstance(design_system, str):
        return "", prompt_text
    head = design_system.strip()
    if not head:
        return "", prompt_text
    opening = head + BLOCK_SEPARATOR
    if not prompt_text.startswith(opening):
        return "", prompt_text
    return head, prompt_text[len(opening) :]


def invariant_prefix_source(
    design_system: str | None,
    style_anchor: object | None,
    *,
    warmup_enabled: bool | None = None,
) -> str | None:
    """Return the text a caller should hoist as this call's cacheable prefix.

    Without the warm-up knob, or before a style anchor exists (the anchor's
    own authoring call), this is exactly ``design_system`` -- today's
    behaviour, unchanged. With the knob on and an anchor already resolved,
    the anchor's own ``prompt_block()`` text joins it: ``author_prompt``
    splices that identical text immediately after the design system for
    every slide authored once the anchor exists (G0), so it belongs in the
    invariant head, not the still-varying per-slide text that follows it.
    This is the splice-order fix described in this module's docstring (C2).

    ``style_anchor`` is duck-typed the same way ``author_prompt`` treats it:
    any object exposing ``prompt_block() -> str``, or ``None``.
    """

    if warmup_enabled is None:
        warmup_enabled = is_authored_prompt_cache_warmup_enabled()
    if not warmup_enabled or style_anchor is None or not isinstance(design_system, str):
        return design_system
    anchor_text = str(style_anchor.prompt_block()).strip()
    if not anchor_text:
        return design_system
    return f"{design_system}{BLOCK_SEPARATOR}{anchor_text}"


def author_messages(
    system_prompt: str,
    prompt_text: str,
    design_system: str | None,
    *,
    image_parts: Sequence[Any] = (),
    enabled: bool | None = None,
) -> list[Any]:
    """The two messages of one authoring call, with the prefix hoisted.

    ``image_parts`` are appended to the user message as they are today; a call
    with none keeps sending a plain string, which is what the provider clients
    and the existing fixtures expect.
    """

    if enabled is None:
        enabled = is_authored_prompt_system_hoist_enabled()
    system_extra, user_text = (
        split_deck_invariant(prompt_text, design_system) if enabled else ("", prompt_text)
    )
    system_content = (
        system_prompt + BLOCK_SEPARATOR + system_extra if system_extra else system_prompt
    )
    user_content: Any = (
        [TextContentPart(text=user_text), *image_parts] if image_parts else user_text
    )
    return [
        SystemMessage(content=system_content),
        UserMessage(content=user_content),
    ]


__all__ = [
    "BLOCK_SEPARATOR",
    "author_messages",
    "invariant_prefix_source",
    "is_authored_prompt_system_hoist_enabled",
    "split_deck_invariant",
]
