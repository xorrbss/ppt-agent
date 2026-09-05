"""G0: author one anchor slide first and hand its stylesheet to the deck.

``docs/authored-quality-speed-programme-design-20260905.md`` section 5 (G0,
decision D23). Every slide is otherwise authored blind to its siblings, so the
deck reads as unrelated pages. One content slide is authored first, rendered,
checked for blocking DOM defects and -- if it is sound -- offered verbatim to
every later authoring call as a ``StyleAnchor``.

Everything here is best effort. A failed, unrenderable or defective anchor is
logged and dropped; the deck then authors exactly as it does today. Nothing in
this module may fail a deck.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Collection, Sequence

from utils.get_env_programme import is_authored_style_anchor_enabled
from utils.llm_calls.author_style_anchor import StyleAnchor, extract_style_anchor

logger = logging.getLogger(__name__)

# Roles whose page is not the deck's ordinary content design: a cover has no
# header band and a section divider has no cards, so neither can speak for the
# deck's visual language.
NON_ANCHOR_ROLES = frozenset({"COVER", "SECTION", "AGENDA", "CLOSING"})

# The minimum deck size worth spending an anchor on. Below it the anchor call
# would be a large share of the deck's whole authoring budget.
MIN_ANCHOR_DECK_SLIDES = 3


def anchor_slide_index(plan: Any, eligible: Collection[int] | None = None) -> int | None:
    """The slide that speaks for this deck, or ``None`` when none does.

    The lowest index whose role is ordinary content and whose header the deck's
    contract governs. Falls back to index 1 -- the first page after the cover --
    for a deck whose roles say nothing useful.

    ``eligible`` restricts the choice to slides that still need authoring; a
    resumed job must never re-author a slide it already has, so a deck whose
    natural anchor is already complete simply gets no anchor.
    """

    contents = tuple(getattr(plan, "contents", ()) or ())
    if len(contents) < MIN_ANCHOR_DECK_SLIDES:
        return None
    roles = tuple(getattr(plan, "roles", ()) or ())
    exempt = frozenset(getattr(plan, "header_exempt_slides", ()) or ())
    allowed = None if eligible is None else frozenset(eligible)

    def usable(index: int) -> bool:
        return index not in exempt and (allowed is None or index in allowed)

    for index in range(len(contents)):
        if not usable(index):
            continue
        role = str(roles[index]).strip().upper() if index < len(roles) else ""
        if role and role not in NON_ANCHOR_ROLES:
            return index
    fallback = 1
    return fallback if fallback < len(contents) and usable(fallback) else None


def accepts_style_anchor(author: Callable[..., Any]) -> bool:
    """Whether an authoring callable takes the ``style_anchor`` keyword.

    The pipeline's authoring seam is monkeypatched by fixtures with two-argument
    stand-ins; offering them a keyword they do not declare would break the deck
    for a purely additive feature.
    """

    try:
        parameters = inspect.signature(author).parameters
    except (TypeError, ValueError):
        return False
    return "style_anchor" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def anchor_correction_hint(defects: Sequence[Any]) -> str:
    """One correction naming the anchor's blocking defects, for its remake."""

    named = ", ".join(
        f"{getattr(issue, 'category', 'defect')}: "
        f"{getattr(issue, 'message', None) or getattr(issue, 'code', '') or 'unspecified'}"
        for issue in defects[:6]
    )
    return (
        "This slide is the deck's style anchor and every other slide will "
        "inherit its visual language, so it must be free of layout defects. "
        "Re-author it keeping the same design intent while fixing: "
        + (named or "the reported layout defects")
        + ". Every element must sit fully inside the 1280x720 canvas without "
        "clipping, overflow or unintended overlap."
    )


async def resolve_style_anchor(
    plan: Any,
    *,
    author: Callable[..., Awaitable[str]],
    render: Callable[[str], Awaitable[Any]],
    inspect_blocking: Callable[[str], Sequence[Any]],
    author_kwargs: dict[str, Any] | None = None,
    remaining_calls: int = 2,
    eligible: Collection[int] | None = None,
    enabled: bool | None = None,
) -> tuple[StyleAnchor | None, str | None, int]:
    """Author, render and gate the deck's anchor slide.

    Returns ``(anchor, html, index)``: the extracted anchor (or ``None``), the
    authored HTML the caller may reuse as that slide's already-completed
    authoring result, and the anchor's slide index (``-1`` when no anchor slide
    was chosen). Every failure path returns ``(None, None, index)``.

    ``remaining_calls`` is the anchor slide's own provider budget. A blocking
    defect spends the second call on one remake; with no call left the anchor is
    used only if it is clean, and dropped otherwise (``anchor_unavailable``).
    """

    if enabled is None:
        enabled = is_authored_style_anchor_enabled()
    if not enabled:
        return None, None, -1
    index = anchor_slide_index(plan, eligible)
    if index is None:
        return None, None, -1
    kwargs = dict(author_kwargs or {})
    try:
        html = await author(plan, index, **kwargs)
    except Exception:  # noqa: BLE001 -- the anchor may never fail the deck
        logger.warning("anchor_unavailable slide=%d author failed", index, exc_info=True)
        return None, None, index
    remaining_calls -= 1
    defects = await _blocking_defects(html, render=render, inspect_blocking=inspect_blocking)
    if defects and remaining_calls > 0:
        logger.info(
            "anchor_remake slide=%d defects=%d",
            index,
            len(defects),
        )
        try:
            remade = await author(
                plan,
                index,
                **{**kwargs, "prior_failure_hint": anchor_correction_hint(defects)},
            )
        except Exception:  # noqa: BLE001 -- the remake is best effort too
            logger.warning("anchor_remake_failed slide=%d", index, exc_info=True)
            remade = None
        if remade:
            html = remade
            defects = await _blocking_defects(
                html, render=render, inspect_blocking=inspect_blocking
            )
    if defects:
        logger.warning(
            "anchor_unavailable slide=%d retained %d blocking defect(s)",
            index,
            len(defects),
        )
        return None, html, index
    try:
        anchor = extract_style_anchor(html, index)
    except Exception:  # noqa: BLE001 -- extraction may never fail the deck
        logger.warning("anchor_unavailable slide=%d extraction failed", index, exc_info=True)
        return None, html, index
    if anchor is None:
        logger.info("anchor_unavailable slide=%d has no stylesheet to inherit", index)
    return anchor, html, index


def anchor_blocking_defects(
    html: str,
    *,
    composition_profile: Any,
    reference_mode: str,
) -> tuple[Any, ...]:
    """The anchor's publication-blocking DOM defects, from the render cache.

    The same inspection the pipeline's ``process_slide`` runs. A deck without
    the strict DOM gate has no blocking notion here, so the gate is skipped
    (``anchor_gate_skipped``) rather than treated as a clean anchor by accident.
    """

    from services.authored_composition_policy import blocking_safety_issues
    from utils.authored_dom_quality import inspect_authored_dom
    from utils.llm_calls.author_slide_effort import EXACT_RECONSTRUCTION
    from utils.slide_capture import get_cached_browser_metrics

    if (
        composition_profile is None
        or not getattr(composition_profile, "strict_dom_gate", False)
        or reference_mode == EXACT_RECONSTRUCTION
    ):
        logger.info("anchor_gate_skipped no strict DOM gate for this deck")
        return ()
    report = inspect_authored_dom(
        html,
        get_cached_browser_metrics(html),
        reference_mode=reference_mode,
        strict_structural_off_canvas=(
            getattr(composition_profile, "profile_id", "ordinary") != "ordinary"
        ),
    )
    if report.telemetry_status != "available":
        logger.info("anchor_gate_skipped telemetry %s", report.telemetry_status)
        return ()
    return blocking_safety_issues(report)


async def resolve_direct_lane_anchor(
    plan: Any,
    *,
    author_planned_slide: Callable[..., Awaitable[str]],
    authored_htmls: dict[int, str],
    attempt_counts: dict[int, int],
) -> StyleAnchor | None:
    """G0 for the lane with no generation job: one awaited anchor call.

    The slide worker pool then runs exactly as before, with every slide's prompt
    carrying the anchor. The anchor slide itself is already authored, so the
    pool finds it in ``authored_htmls`` and spends no second call on it. Nothing
    is rendered here: the pool renders every slide once anyway, and an extra
    Chromium pass would buy this lane nothing.
    """

    async def author(selected_plan: Any, index: int, **_kwargs: Any) -> str:
        html = await author_planned_slide(selected_plan, index)
        authored_htmls[index] = html
        attempt_counts[index] = max(attempt_counts.get(index, 0), 1)
        return html

    async def skip_render(_html: str) -> None:
        return None

    anchor, _html, _index = await resolve_style_anchor(
        plan,
        author=author,
        render=skip_render,
        inspect_blocking=lambda _html: (),
        remaining_calls=1,
    )
    return anchor


async def _blocking_defects(
    html: str,
    *,
    render: Callable[[str], Awaitable[Any]],
    inspect_blocking: Callable[[str], Sequence[Any]],
) -> tuple[Any, ...]:
    """Render the anchor once and return its blocking DOM defects.

    A render or inspection that cannot run is not a defect: the gate is skipped
    (``anchor_gate_skipped``) rather than turned into a reason to drop an
    otherwise usable anchor.
    """

    if not html:
        return ()
    try:
        await render(html)
    except Exception:  # noqa: BLE001
        logger.info("anchor_gate_skipped render unavailable", exc_info=True)
        return ()
    try:
        return tuple(inspect_blocking(html) or ())
    except Exception:  # noqa: BLE001
        logger.info("anchor_gate_skipped inspection unavailable", exc_info=True)
        return ()


__all__ = [
    "MIN_ANCHOR_DECK_SLIDES",
    "NON_ANCHOR_ROLES",
    "accepts_style_anchor",
    "anchor_blocking_defects",
    "anchor_correction_hint",
    "anchor_slide_index",
    "resolve_direct_lane_anchor",
    "resolve_style_anchor",
]
