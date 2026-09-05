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
"""

from __future__ import annotations

from typing import Any, Sequence

from llmai.shared import SystemMessage, TextContentPart, UserMessage

from utils.get_env_programme import is_authored_prompt_system_hoist_enabled

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
    "is_authored_prompt_system_hoist_enabled",
    "split_deck_invariant",
]
