"""K1 ceiling: cap each outline slide at a per-verbosity character ceiling.

Companion to ``outline_density.py``'s floor/expansion half. Measured
2026-09-08: the slide-authoring model reproduces its outline input almost
verbatim (1.0x-1.4x usable characters), so the amount of text a slide ends up
saying is decided at the outline stage. The floor already guards against too
little; this guards against too much on a single slide -- a page that has more
to say than the deck's box can hold overflows no matter how the author
compresses it.

It is a soft, per-slide ceiling: nothing here fails a deck,
``OUTLINE_DENSITY_CEILING_ENABLED=false`` (the default) removes the whole
step including the prompt-side budget sentence, and a condense call that
cannot honestly shorten a slide returns it unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.outline_density import (
    _log_receipt,
    _parse_outline_text,
    _replace_slide_contents,
    outline_density_receipt,
    usable_chars,
)

LOGGER = logging.getLogger(__name__)

#: Slides condensed in one call. Beyond this the outline is not "a few dense
#: slides", it is a different deck, and one bounded call cannot honestly fix
#: it. Mirrors ``outline_density.MAX_EXPANDED_SLIDES``.
MAX_CONDENSED_SLIDES = 24
#: Semantic-slot prefix for the condensation call, for the same durable
#: admission reason as ``outline_density.EXPANSION_SEMANTIC_SLOT_PREFIX``: a
#: distinct prefix keeps this second call in the ``outline`` stage from being
#: admitted as a replay of the outline stream or of the expansion call.
CONDENSE_SEMANTIC_SLOT_PREFIX = "outline-condensation"

_SYSTEM = (
    "You condense over-long presentation-outline slides that exceed this "
    "deck's per-slide character budget. Rules, in order of precedence:\n"
    "1. Keep the load-bearing facts: figures, dates, named entities and the "
    "central claim of each bullet. Cut restated context, filler adjectives "
    "and redundant bullets, not the concrete detail.\n"
    "2. Never invent a fact, figure, date, name or quotation the slide did "
    "not already contain. Never add material.\n"
    "3. Keep every figure, date and name you keep worded exactly as given.\n"
    "4. When you cannot cut this slide without losing meaning, return it "
    "unchanged. A slide that stays over budget is safer than one gutted into "
    "a heading.\n"
    "5. Write in the same language and Markdown shape you were given: a "
    "'## ' title line, then bullet lines. No bold, no italics."
)


def above_ceiling_indices(outline: Any, *, ceiling: int) -> list[int]:
    """Slide indices whose usable characters exceed the ceiling."""

    if ceiling <= 0:
        return []
    return [
        index
        for index, slide in enumerate(getattr(outline, "slides", None) or ())
        if usable_chars(getattr(slide, "content", "")) > ceiling
    ]


def _condense_schema(count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["slides"],
        "properties": {
            "slides": {
                "type": "array",
                "maxItems": count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", "content"],
                    "properties": {
                        "index": {"type": "integer"},
                        "content": {"type": "string"},
                    },
                },
            }
        },
    }


def _user_prompt(
    outline: Any, indices: list[int], *, ceiling: int, verbosity: str | None
) -> str:
    slides = list(outline.slides)
    blocks = "\n\n".join(
        f"### index {index} ({usable_chars(slides[index].content)} characters, "
        f"ceiling {ceiling})\n{slides[index].content}"
        for index in indices
    )
    return (
        f"These {len(indices)} slides of a {len(slides)}-slide outline "
        f"(verbosity: {verbosity or 'standard'}) each exceed the {ceiling}-"
        "character ceiling for this deck. Cut each one to its load-bearing "
        "bullets so it fits, keeping every figure, date and name you keep "
        "verbatim. Return the same index for each slide; return a slide "
        "unchanged when you cannot cut it without losing meaning.\n\n"
        f"SLIDES TO CONDENSE:\n{blocks}\n"
    )


def merge_condense(
    outline: Any, payload: object, *, indices: list[int]
) -> PresentationOutlineModel | None:
    """Splice a condensation response into the outline, or ``None`` if unusable.

    Only the slides that were asked for may change, and only downward: a
    rewrite that grew, or matched, the slide it replaced kept nothing that the
    ceiling asked cut, so it is dropped and the original kept. The floor (if
    any is in force) is enforced separately, by ``enforce_floor_after_condense``.
    """

    if not isinstance(payload, dict):
        return None
    entries = payload.get("slides")
    if not isinstance(entries, list):
        return None
    allowed = set(indices)
    contents = [slide.content for slide in outline.slides]
    changed = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index not in allowed:
            continue
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if usable_chars(content) >= usable_chars(contents[index]):
            continue
        contents[index] = content
        changed += 1
    if not changed:
        return None
    return PresentationOutlineModel(slides=[{"content": text} for text in contents])


def enforce_floor_after_condense(original: Any, candidate: Any, *, floor: int) -> Any:
    """Revert any condensed slide that fell below the floor in force.

    The ceiling and the floor can disagree on a slide whose honest content
    sits between them only after aggressive cutting; the floor wins, and that
    one slide keeps its pre-condense content. Returns ``original`` (by
    identity) when every change is reverted, and ``candidate`` (by identity)
    when none is, so a caller comparing ``is`` sees "nothing happened" exactly
    when nothing did.
    """

    if candidate is original or floor <= 0:
        return candidate
    original_contents = [slide.content for slide in original.slides]
    candidate_contents = [slide.content for slide in candidate.slides]
    if len(original_contents) != len(candidate_contents):
        return candidate
    contents = [
        before if (after != before and usable_chars(after) < floor) else after
        for before, after in zip(original_contents, candidate_contents, strict=True)
    ]
    if contents == original_contents:
        return original
    if contents == candidate_contents:
        return candidate
    return PresentationOutlineModel(slides=[{"content": text} for text in contents])


async def condense_dense_outline(
    outline: Any, *, verbosity: str | None, ceiling: int
) -> Any:
    """One bounded call that rewrites only the slides over the ceiling.

    Returns the condensed outline, or the original on any error, any unusable
    response, and whenever no slide is over the ceiling -- cutting content the
    ceiling does not flag is not this call's job. Does not itself enforce the
    floor; see ``enforce_floor_after_condense``.
    """

    try:
        if ceiling <= 0:
            return outline
        indices = above_ceiling_indices(outline, ceiling=ceiling)[:MAX_CONDENSED_SLIDES]
        if not indices:
            return outline

        from llmai import get_client
        from llmai.shared import JSONSchemaResponse, SystemMessage, UserMessage

        from utils.llm_config import enforce_llm_egress_policy, get_llm_config
        from utils.llm_provider import get_model
        from utils.llm_utils import generate_structured_with_schema_retries

        schema = _condense_schema(len(indices))
        enforce_llm_egress_policy()
        payload = await generate_structured_with_schema_retries(
            get_client(config=get_llm_config()),
            get_model(),
            messages=[
                SystemMessage(content=_SYSTEM),
                UserMessage(
                    content=_user_prompt(
                        outline, indices, ceiling=ceiling, verbosity=verbosity
                    )
                ),
            ],
            response_format=JSONSchemaResponse(
                name="outline_condensation", json_schema=schema, strict=False
            ),
            json_schema=schema,
            strict=False,
            validate_schema=False,
            validate_schema_max_loop_count=1,
            semantic_slot_prefix=CONDENSE_SEMANTIC_SLOT_PREFIX,
        )
        merged = merge_condense(outline, payload, indices=indices)
        if merged is None:
            LOGGER.info("[outline] density condensation returned nothing usable")
            return outline
        return merged
    except Exception:
        LOGGER.warning("[outline] density condensation failed", exc_info=True)
        return outline


async def apply_density_ceiling_to_text(
    outline_text: str, *, verbosity: str | None, ceiling: int, floor: int = 0
) -> str:
    """Measure one streamed outline and, when a slide is over ceiling, condense it once.

    Mirrors ``outline_density.apply_density_floor_to_text``: returns the
    original bytes whenever the outline is unparseable, no slide exceeds the
    ceiling, or the condensation (once the floor is enforced) changed nothing.
    The receipt is logged either way.
    """

    parsed = _parse_outline_text(outline_text)
    if parsed is None:
        return outline_text
    raw, outline = parsed
    receipt = outline_density_receipt(outline, floor=floor, ceiling=ceiling)
    condensed_text = outline_text
    if receipt["aboveCeiling"]:
        condensed = await condense_dense_outline(
            outline, verbosity=verbosity, ceiling=ceiling
        )
        condensed = enforce_floor_after_condense(outline, condensed, floor=floor)
        if condensed is not outline:
            try:
                import json as _json

                contents = [slide.content for slide in condensed.slides]
                condensed_text = _json.dumps(
                    _replace_slide_contents(raw, contents), ensure_ascii=False
                )
                receipt = outline_density_receipt(condensed, floor=floor, ceiling=ceiling)
                receipt["condensed"] = True
            except Exception:
                LOGGER.warning(
                    "[outline] density re-serialization failed", exc_info=True
                )
                condensed_text = outline_text
    _log_receipt(receipt)
    return condensed_text


__all__ = [
    "CONDENSE_SEMANTIC_SLOT_PREFIX",
    "MAX_CONDENSED_SLIDES",
    "above_ceiling_indices",
    "apply_density_ceiling_to_text",
    "condense_dense_outline",
    "enforce_floor_after_condense",
    "merge_condense",
]
