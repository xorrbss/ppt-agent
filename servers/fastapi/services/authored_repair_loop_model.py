"""The single repair loop's model step: one authoring call, token-bound.

There is exactly one authoring prompt in this system, and the repair loop uses
it. The call carries the gate's bounded evidence as the correction hint; the
placeholder block table and the token rules are appended to that same prompt by
the gate's own prompt path, so the repair is bound by the identical source
contract as authoring. It may move, restyle and restructure; it cannot emit
source text, and a candidate that retypes source text instead of placing its
tokens is rejected by the gate's static checks inside ``author_slide_html``.

Nothing here repairs a failure. A refused, invalid or unavailable candidate
returns ``None`` and the loop keeps the frozen previous admission; only a
deck-level provider quota pause propagates.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Awaitable, Callable

from services.authored_repair_loop_evidence import repair_correction_hint
from services.authored_terminal_slide_fallback import replayed_plan_kwargs
from services.provider_usage_instrumentation import provider_usage_scope
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_semantic_role_repair import canonicalize_authored_roles
from utils.llm_calls.author_slide import STYLE_REFERENCE, author_slide_html
from utils.provider_overload import ProviderQuotaPause

LOGGER = logging.getLogger(__name__)

#: The repair call is a recovery ordinal, never a fresh content attempt: the
#: attempt ledger's three content ordinals belong to the author stage (T4,
#: ``authored.provider-attempt-budget-per-operation-attempt@2``).
REPAIR_RETRY_CLASS = "recovery"
REPAIR_ATTEMPT_ORDINAL = 2

#: Its own receipt stage, so a repair call is never counted as authoring.
REPAIR_USAGE_STAGE = "repair_loop"


def _slide_plan_kwargs(
    index: int,
    *,
    chapter_plan: Any,
    illustration_plan: Any,
    infographic_plan: Any,
) -> dict[str, Any]:
    """This slide's own deck-wide decisions, replayed as scalar kwargs."""

    replayed = replayed_plan_kwargs(
        [index],
        chapter_plan=chapter_plan,
        illustration_plan=illustration_plan,
        infographic_plan=infographic_plan,
    )
    kwargs: dict[str, Any] = {}
    prefixes = replayed.get("chapter_prefixes")
    if prefixes:
        kwargs["chapter_breadcrumb"] = True
        kwargs["chapter_prefix"] = prefixes[0]
    slots = replayed.get("illustration_slots")
    if slots is not None:
        kwargs["illustration_allowed"] = slots[0]
    briefs = replayed.get("infographic_briefs")
    if briefs is not None:
        kwargs["infographic"] = briefs[0]
    return kwargs


def build_repair_author(
    *,
    contents: Sequence[str],
    roles: Sequence[str],
    brand: Any,
    design_system: str,
    capacity_briefs: Sequence[str],
    page_number_mode: str,
    instructions: Any,
    source_images_by_slide: Sequence[Mapping[str, Any]] | None,
    reference_mode: str = STYLE_REFERENCE,
    chapter_plan: Any = None,
    illustration_plan: Any = None,
    infographic_plan: Any = None,
    job_id: Any = None,
    attempt_number: int = 0,
    author: Callable[..., Awaitable[str]] = author_slide_html,
) -> Callable[[int, tuple[str, ...]], Awaitable[str | None]]:
    """Bind the deck's authoring inputs once; the loop supplies the evidence."""

    deck_size = len(contents)

    async def propose(index: int, evidence: tuple[str, ...]) -> str | None:
        role = str(roles[index]) if index < len(roles) else ""
        offered = (
            source_images_by_slide[index]
            if source_images_by_slide is not None and index < len(source_images_by_slide)
            else None
        )
        kwargs: dict[str, Any] = {
            "reasoning_effort": None,
            "reference_mode": reference_mode,
            "receipt_retry_class": REPAIR_RETRY_CLASS,
            "receipt_attempt_ordinal": REPAIR_ATTEMPT_ORDINAL,
            "allow_provider_fallback": False,
            "correction_hint": repair_correction_hint(evidence, role=role),
            "page_number_mode": page_number_mode,
            "instructions": instructions,
            **_slide_plan_kwargs(
                index,
                chapter_plan=chapter_plan,
                illustration_plan=illustration_plan,
                infographic_plan=infographic_plan,
            ),
        }
        if capacity_briefs is not None and index < len(capacity_briefs):
            kwargs["capacity_brief"] = capacity_briefs[index]
        if offered:
            # The repaired slide owes the deck the same pictures its first
            # draft was offered; without them the candidate loses source images
            # the verbatim contract requires and the gate rejects it anyway.
            kwargs["source_images"] = offered
            kwargs["require_all_source_images"] = True
        try:
            with provider_usage_scope(
                job_id=job_id,
                operation="authored_generation",
                stage=REPAIR_USAGE_STAGE,
                attempt_number=attempt_number,
                slide_index=index,
            ):
                candidate = await author(
                    contents[index], design_system, brand, role, index, deck_size, **kwargs
                )
        except ProviderQuotaPause:
            # A whole-account pause is a deck decision (T4), never a slide one.
            raise
        except Exception as error:
            LOGGER.warning(
                "[authored-repair-loop] slide %d model candidate unavailable (%s)",
                index + 1,
                getattr(error, "code", None) or type(error).__name__,
            )
            return None
        if not candidate:
            return None
        return ensure_authored_qids(canonicalize_authored_roles(candidate, role=role))

    return propose


__all__ = [
    "REPAIR_ATTEMPT_ORDINAL",
    "REPAIR_RETRY_CLASS",
    "REPAIR_USAGE_STAGE",
    "build_repair_author",
]
