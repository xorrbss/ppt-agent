"""D7 at the AUTHOR stage: one simplified attempt, then a flagged placeholder.

The render/gate stage already refuses to let one slide kill a deck: a slide
that is still terminal after the bounded repair is remade once and, failing
that, published FLAGGED (``services/authored_terminal_slide_fallback.py``).
The author stage had no such rescue. A slide whose two ordinary author calls
both failed raised ``AuthoredProviderBudgetExhausted``, the pipeline turned it
into ``authored_slide_author_failed``, and the whole deck died -- four times in
production on 2026-09-03 alone, and on 2026-09-05 for a CLOSING page whose
second attempt chose a full-bleed dark composition and dropped the mandatory
header band.

This module extends the same principle one stage earlier, as the same three
rungs:

1. A THIRD author call whose brief is deliberately simpler than the two that
   failed. Same content, same header contract -- but the contract is restated
   first and as non-negotiable, the deck's silhouette/figure decisions are
   withheld so the model composes the role's plain layout, and the frame is
   spelled out in pixels. It reuses ``author_planned_slide`` and the existing
   correction-hint seam rather than a second prompt module, so there is exactly
   one authoring prompt in this system.
2. Failing that, ``services/authored_author_stage_placeholder.py`` renders the
   slide's outline deterministically and the slide is registered flagged with
   reason ``author:attempts_exhausted`` -- the same ``flaggedSlides`` structure
   D7 uses, so the receipt, the workspace projection and the export all treat
   it as one flagged page.
3. All of it behind ``terminal_fallback_active``, D7's one knob, so an
   exact-reconstruction or source-verbatim deck keeps its fail-closed
   behaviour: a deck that may not lose a word may not gain a placeholder.

Only ``AuthoredProviderBudgetExhausted`` is rescued. A transport failure is
retryable and belongs to the operation's own retry; a renderer policy refusal
is a security boundary. The dead end this module knows how to answer is the
one the attempt budget produces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any, Mapping

from services.authored_author_stage_placeholder import author_placeholder_html
from services.authored_checkpoint_service import AuthoredStageError
from services.authored_terminal_slide_flag_only import flagged_entry
from services.authored_terminal_slide_policy import terminal_fallback_active
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    inspect_browser_source_readback,
    log_browser_source_readback_failure,
)
from utils.llm_calls.author_deck import (
    AuthoredProviderBudgetExhausted,
    author_planned_slide,
)
from utils.llm_calls.author_slide import _css_font_stack
from utils.llm_calls.author_slide_stream import (
    OUTPUT_TOKEN_LIMIT_GUIDANCE,
    output_token_limit_in_cause_chain,
)

LOGGER = logging.getLogger(__name__)

#: Recorded on the slide that exhausted every author attempt, in the same
#: ``flaggedSlides`` list D7's gate-stage flags travel in.
AUTHOR_ATTEMPTS_EXHAUSTED_REASON = "author:attempts_exhausted"

#: The same flag when what exhausted the budget was the provider's own output
#: ceiling (``utils/llm_calls/author_slide_stream.py``): the placeholder is
#: the same, but the fix is a setting, not a prompt.
AUTHOR_OUTPUT_TOKEN_LIMIT_REASON = "author:output_token_limit"

#: The provider ordinal the rescue call occupies. The two ordinary calls own 1
#: and 2; the receipt's ``provider_attempt_count`` must show this third one
#: rather than under-reporting what the deck was billed for.
RESCUE_ATTEMPT_ORDINAL = 3

#: The strict third attempt starts after the two ordinary calls and raises the
#: temporary call limit to three, buying exactly one call rather than another
#: two-call budget.
_ONE_MORE_CALL_OFFSET = 2

#: The lowest point on the canvas an absolutely positioned element may claim.
#: Below it a box is either clipped by the 720px frame or collides with the
#: page-number zone, which is the shape most "slide overflows" actually have.
SAFE_BOTTOM_Y_PX = 648


@dataclass(frozen=True, slots=True)
class AuthorStageRescue:
    """What the author-stage rescue produced for one slide."""

    html: str
    provider_attempt_count: int
    #: ``None`` when the third call succeeded; the deck publishes it unflagged.
    flagged_entry: dict[str, object] | None = None
    prior_failure: Mapping[str, object] | None = None


def source_reflow_rescue_readback(
    *, index: int, html: str, metrics: Mapping[str, Any],
    bindings: Sequence[tuple[str, str]], profile: Any, reference_mode: str,
    prior_failure: Mapping[str, object] | None = None,
    provider_attempt_count: int = RESCUE_ATTEMPT_ORDINAL,
) -> dict[str, object] | None:
    """Route a rescued source slide through the canonical browser predicate."""

    unit_id = f"author-rescue:{int(index)}"
    block_ids = [str(block_id) for block_id, _text in bindings]
    result = inspect_browser_source_readback(
        manifest={"units": [{
            "sourceUnitId": unit_id, "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": block_id, "kind": "text", "order": order,
                 "payload": {"text": text}}
                for order, (block_id, text) in enumerate(bindings)
            ],
        }]},
        outline={"slides": [{
            "sourceUnitId": unit_id, "sourceIndex": 0, "readingOrder": block_ids,
        }]},
        browser_metrics=[metrics], htmls=[html],
    )
    hard_failure = bool(
        result.missing_block_ids or result.order_violations
        or result.integrity_failures or result.telemetry_failures
        or result.diagnostic_error
    )
    fallback = terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=True
    )
    if hard_failure or (result.visibility_failures and not fallback):
        summary = log_browser_source_readback_failure(result)
        error = BrowserSourceReadbackError(
            "source_content_browser_readback_failed",
            detail=f"author rescue source readback failed; {summary}",
            block_ids=tuple(dict.fromkeys((
                *result.missing_block_ids, *result.integrity_failures,
                *result.telemetry_failures, *result.visibility_failures,
            ))),
            slide_indices=(int(index),), order_violations=result.order_violations,
        )
        raise AuthoredStageError(
            slide_index=index, stage="author", code=error.code, cause=error,
            retryable=False, message=error.detail,
            provider_attempt_count=provider_attempt_count,
            prior_failure=prior_failure,
        ) from error
    if not result.visibility_failures:
        return None
    entry = flagged_entry(
        index=index, reason="source_readback:visibility", attempted=False,
        outcome="verbatim_flag_only",
    )
    prior_code = str((prior_failure or {}).get("code") or "").strip()
    entry["reasons"] = [
        "source_readback:visibility",
        *([f"author_prior:{prior_code}"[:160]] if prior_code else []),
    ]
    return entry


class _SimplifiedPlan:
    """The deck's plan with its per-slide visual ambitions withheld.

    The silhouette, the planned figure and the illustration slot are the deck's
    style talking, and two calls have now demonstrated that this slide cannot
    satisfy that brief and the header contract at once. Withholding them leaves
    every other decision -- content, role, header contract, capacity envelope,
    chapter prefix, page numbering -- exactly as the deck decided it, so the
    third call is the same slide asked more plainly, not a different slide.

    A proxy rather than ``dataclasses.replace`` because the remake, checkpoint
    and test paths all build stand-in plan objects that are not the dataclass.
    """

    _WITHHELD: Mapping[str, Any] = {
        "visual_plan": None,
        "infographic_briefs": (),
        "illustration_slots": (),
    }

    def __init__(self, plan: Any) -> None:
        self._plan = plan

    def __getattr__(self, name: str) -> Any:
        if name in _SimplifiedPlan._WITHHELD:
            return _SimplifiedPlan._WITHHELD[name]
        return getattr(self._plan, name)


def _required_header_parts(contract: Any) -> str:
    """The contract's own required part list, named the way the DOM names them."""

    parts = ["band", "title", "body"]
    if getattr(contract, "eyebrow", None) is not None and contract.eyebrow.enabled:
        parts.insert(1, "eyebrow")
        if contract.eyebrow.tick.enabled:
            parts.insert(2, "tick")
    if getattr(contract, "divider", None) is not None and contract.divider.enabled:
        parts.insert(-1, "divider")
    return ", ".join(f'data-header-part="{part}"' for part in parts)


def simplified_author_brief(*, role: str, contract: Any) -> str:
    """The third attempt's brief: the contract first, the layout plain, the frame stated.

    Written as a correction hint because that is the channel the authoring
    prompt already treats as authoritative and appends last. Its first rule is
    the header contract, spelled out as the exact markers the deterministic
    validator counts -- the failure that produced this call was a slide that
    redesigned its own top area, and prose about uniformity did not stop it.
    """

    rules: list[str] = []
    if contract is not None:
        rules.append(
            "1. NON-NEGOTIABLE, BEFORE ANY DESIGN DECISION: reproduce the deck's "
            "header contract exactly. The slide MUST carry every one of these "
            f"markers, each exactly once: {_required_header_parts(contract)}. The "
            "band element must also declare its own derived height in "
            'data-header-band="<px>" and set that same height in its style. No '
            "full-bleed, edge-to-edge or alternative top treatment may replace "
            "the band, on this slide or any other, whatever the slide's role."
        )
    else:
        rules.append(
            "1. NON-NEGOTIABLE: this slide owns its whole frame and must carry no "
            "header band, eyebrow, requirement badge or page number."
        )
    rules.append(
        f"2. Compose the PLAIN layout for a {role or 'content'} slide: a title, "
        "then the content as simple bullets or a plain equal-width card row. "
        "Ignore every silhouette, figure, motif or scene direction given "
        "earlier -- this attempt is not judged on visual ambition, and a plain "
        "slide that fits beats an ambitious one that does not."
    )
    rules.append(
        "3. Keep EVERY element inside the 1280x720 frame. No element may extend "
        "past x=1280 or y=720, and no absolutely positioned element may start "
        f"below y={SAFE_BOTTOM_Y_PX}. Carry fewer, shorter lines than you would "
        "normally: leaving the lower part of the slide empty is correct here."
    )
    return (
        "The two previous attempts at this slide were both rejected. This is the "
        "final attempt, and it is deliberately a SIMPLER brief. Obey these three "
        "rules in order; where anything earlier in this prompt disagrees with "
        "them, these win.\n" + "\n".join(rules)
    )


def strict_source_reflow_author_brief(*, role: str, contract: Any) -> str:
    """Final correction for a source-locked slide without relaxing fidelity."""

    header = (
        "Reproduce the header contract exactly with only these required parts: "
        f"{_required_header_parts(contract)}. Remove any data-header-part=\"right\" "
        "element; move its source text into an ordinary body owner unchanged."
        if contract is not None
        else "Do not add a header band or header-part elements."
    )
    return (
        "The two previous candidates were rejected. This is the one final, "
        "source-locked correction. Preserve every source text block verbatim, "
        "including punctuation and ordering; do not summarize, shorten, merge, "
        "or invent text. Give every source block exactly one visible owner. "
        f"{header} Use a plain {role or 'content'} layout inside 1280x720 and "
        "change geometry only as needed to make all source text visible."
    )


def _rescue_attempt_kwargs(author_kwargs: Mapping[str, Any], hint: str) -> dict[str, Any]:
    """The third call's kwargs: the caller's, with the attempt plumbing rebound.

    The inner call believes it is making attempt 2 of 2 (that is how it is
    bounded to one call), so its observer and reserver ordinals are remapped to
    the true third ordinal before they reach the caller's accounting.
    """

    kwargs = dict(author_kwargs)
    kwargs["prior_failure_hint"] = hint
    kwargs["provider_attempt_offset"] = _ONE_MORE_CALL_OFFSET
    kwargs["provider_attempt_limit"] = RESCUE_ATTEMPT_ORDINAL
    return kwargs


def _flagged_entry(
    index: int, *, outcome: str, reason: str = AUTHOR_ATTEMPTS_EXHAUSTED_REASON
) -> dict[str, object]:
    return {
        "index": int(index),
        "reason": reason,
        "remakeAttempted": True,
        "remakeOutcome": outcome[:64],
    }


def _placeholder(plan: Any, index: int) -> str:
    contract = None
    resolver = getattr(plan, "header_contract_at", None)
    if callable(resolver):
        contract = resolver(index)
    brand = getattr(plan, "brand", None)
    return author_placeholder_html(
        content=plan.contents[index],
        brand=brand,
        role=(plan.roles[index] if getattr(plan, "roles", None) else ""),
        contract=contract,
        font_stack=_css_font_stack(str(getattr(brand, "fonts", "") or "Noto Sans KR")),
    )


async def rescue_author_stage(
    plan: Any,
    index: int,
    error: BaseException,
    *,
    author_kwargs: Mapping[str, Any],
    profile: Any,
    reference_mode: str,
    source_reflow: bool,
) -> AuthorStageRescue | None:
    """Rescue one slide whose author budget is spent, or ``None`` to fail closed.

    ``None`` is today's behaviour byte for byte: the caller re-raises and the
    deck fails with ``authored_slide_author_failed``.
    """

    if not isinstance(error, AuthoredProviderBudgetExhausted):
        return None
    fallback_allowed = terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=source_reflow
    )
    if not fallback_allowed and not source_reflow:
        return None

    role = str(plan.roles[index]) if getattr(plan, "roles", None) else ""
    resolver = getattr(plan, "header_contract_at", None)
    contract = resolver(index) if callable(resolver) else None
    hint = (
        strict_source_reflow_author_brief(role=role, contract=contract)
        if source_reflow
        else simplified_author_brief(role=role, contract=contract)
    )
    token_limited = output_token_limit_in_cause_chain(error)
    LOGGER.warning(
        "[authored] slide %d author budget spent (%s); one simplified attempt%s",
        index + 1,
        getattr(error, "code", None) or type(error).__name__,
        f". {OUTPUT_TOKEN_LIMIT_GUIDANCE}" if token_limited else "",
    )
    try:
        html = await author_planned_slide(
            _SimplifiedPlan(plan), index, **_rescue_attempt_kwargs(author_kwargs, hint)
        )
    except AuthoredProviderBudgetExhausted as exhausted:
        if source_reflow:
            raise
        outcome = "author_rescue_budget_exhausted"
        token_limited = token_limited or output_token_limit_in_cause_chain(exhausted)
        LOGGER.warning(
            "[authored] slide %d simplified attempt rejected (%s); publishing flagged%s",
            index + 1,
            getattr(exhausted, "code", None) or "unknown",
            f". {OUTPUT_TOKEN_LIMIT_GUIDANCE}" if token_limited else "",
        )
    except Exception:
        if source_reflow:
            raise
        outcome = "author_rescue_failed"
        LOGGER.warning(
            "[authored] slide %d simplified attempt failed; publishing flagged",
            index + 1,
            exc_info=True,
        )
    else:
        LOGGER.info("[authored] slide %d recovered on the simplified attempt", index + 1)
        return AuthorStageRescue(
            html=html,
            provider_attempt_count=RESCUE_ATTEMPT_ORDINAL,
            prior_failure=getattr(error, "prior_failure", None),
        )
    try:
        placeholder = _placeholder(plan, index)
    except Exception:
        # A defect in the rescue's own rendering must not replace the cause the
        # deck actually failed on. Fail closed on the original error instead.
        LOGGER.error(
            "[authored] slide %d placeholder could not be rendered", index + 1,
            exc_info=True,
        )
        return None
    return AuthorStageRescue(
        html=placeholder,
        provider_attempt_count=RESCUE_ATTEMPT_ORDINAL,
        flagged_entry=_flagged_entry(
            index,
            outcome=outcome,
            reason=(
                AUTHOR_OUTPUT_TOKEN_LIMIT_REASON
                if token_limited
                else AUTHOR_ATTEMPTS_EXHAUSTED_REASON
            ),
        ),
    )


__all__ = [
    "AUTHOR_ATTEMPTS_EXHAUSTED_REASON",
    "AUTHOR_OUTPUT_TOKEN_LIMIT_REASON",
    "RESCUE_ATTEMPT_ORDINAL",
    "SAFE_BOTTOM_Y_PX",
    "AuthorStageRescue",
    "rescue_author_stage",
    "source_reflow_rescue_readback",
    "simplified_author_brief",
    "strict_source_reflow_author_brief",
]
