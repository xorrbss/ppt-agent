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

from services.authored_measured_gates import FRAGMENTATION_REMAKE_HINT
from utils.authored_fragmentation_gate import (
    TEXT_OWNERS_VIOLATION,
    FragmentationReport,
    fragmentation_repair_instruction,
    inspect_fragmentation,
)
from utils.get_env_programme import (
    get_authored_fragmentation_max_text_owners,
    get_authored_type_floor_px,
    is_authored_fragmentation_gate_enabled,
    is_authored_style_anchor_enabled,
)
from utils.llm_calls.author_slide import canonicalize_authored_slide_html
from utils.llm_calls.author_style_anchor import StyleAnchor, extract_style_anchor
from utils.runtime_settings import RuntimeSettings

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


def anchor_correction_hint(
    defects: Sequence[Any], *, fragmentation_hint: str | None = None
) -> str:
    """One correction naming the anchor's blocking defects and/or fragmentation.

    Blocking DOM defects take precedence, matching the pipeline's ordinary
    remake hint (``remake_density_hint``); when both are present the anchor's
    one remake is told about both in a single instruction.
    """

    parts: list[str] = []
    if defects:
        named = ", ".join(
            f"{getattr(issue, 'category', 'defect')}: "
            f"{getattr(issue, 'message', None) or getattr(issue, 'code', '') or 'unspecified'}"
            for issue in defects[:6]
        )
        parts.append(
            "This slide is the deck's style anchor and every other slide will "
            "inherit its visual language, so it must be free of layout defects. "
            "Re-author it keeping the same design intent while fixing: "
            + (named or "the reported layout defects")
            + ". Every element must sit fully inside the 1280x720 canvas without "
            "clipping, overflow or unintended overlap."
        )
    if fragmentation_hint:
        parts.append(fragmentation_hint)
    return " ".join(parts)


def _anchor_role(plan: Any, index: int) -> str:
    """The anchor slide's role, for the fragmentation hint's slide label."""

    roles = tuple(getattr(plan, "roles", ()) or ())
    if 0 <= index < len(roles):
        role = str(roles[index] or "").strip().upper()
        if role:
            return role
    return "SLIDE"


def anchor_fragmentation_report(
    html: str, *, settings: RuntimeSettings | None = None
) -> FragmentationReport | None:
    """The anchor's fragmentation measurement, or ``None`` when it cannot be made.

    ``None`` while the gate knob is off, or when no browser metrics were
    cached for this exact render. Mirrors :func:`anchor_blocking_defects`: the
    metrics come from the render cache keyed on the HTML the caller already
    rendered, so the anchor pays for no render or measurement pass of its own.
    """

    if not is_authored_fragmentation_gate_enabled(settings):
        return None
    from utils.slide_capture import get_cached_browser_metrics

    metrics = get_cached_browser_metrics(html)
    if metrics is None:
        return None
    return inspect_fragmentation(
        metrics,
        max_text_owners=get_authored_fragmentation_max_text_owners(settings),
        floor_px=get_authored_type_floor_px(settings),
    )


def _has_text_owner_violation(report: FragmentationReport | None) -> bool:
    """Whether ``report`` carries the one fragmentation violation a remake can
    fix by merging boxes.

    Type size is already clamped at author time (Q1), so ``type_floor`` should
    not appear on a fresh anchor render; ``fit_floor`` needs a fit report the
    anchor never has. Only ``text_owners`` is checked here.
    """

    if report is None:
        return False
    return any(
        violation.startswith(f"{TEXT_OWNERS_VIOLATION}:") for violation in report.violations
    )


def anchor_fragmentation_hint(report: FragmentationReport, role: str) -> str:
    """The merge-boxes hint for an anchor whose new defect is fragmentation."""

    for violation in report.violations:
        if violation.startswith(f"{TEXT_OWNERS_VIOLATION}:"):
            return fragmentation_repair_instruction(role, violation) or FRAGMENTATION_REMAKE_HINT
    return FRAGMENTATION_REMAKE_HINT


async def _evaluate_anchor(
    html: str,
    *,
    render: Callable[[str], Awaitable[Any]],
    inspect_blocking: Callable[[str], Sequence[Any]],
    settings: RuntimeSettings | None,
) -> tuple[tuple[Any, ...], bool, FragmentationReport | None]:
    """Blocking DOM defects and the fragmentation report for one anchor render.

    The fragmentation report is only attempted once the blocking-defect gate
    itself ran (``gated``): without a working render or inspection there is no
    cached metrics to measure fragmentation on either.
    """

    defects, gated = await _blocking_defects(
        html, render=render, inspect_blocking=inspect_blocking
    )
    fragmentation = anchor_fragmentation_report(html, settings=settings) if gated else None
    return defects, gated, fragmentation


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
    settings: RuntimeSettings | None = None,
) -> tuple[StyleAnchor | None, str | None, int]:
    """Author, render and gate the deck's anchor slide.

    Returns ``(anchor, html, index)``: the extracted anchor (or ``None``), the
    authored HTML the caller may reuse as that slide's already-completed
    authoring result, and the anchor's slide index (``-1`` when no anchor slide
    was chosen). Every failure path returns ``(None, None, index)``.

    ``remaining_calls`` is the anchor slide's own provider budget. A blocking
    defect or -- while ``AUTHORED_FRAGMENTATION_GATE_ENABLED`` is on -- a
    ``text_owners`` fragmentation violation spends the second call on one
    remake; with no call left, or if the remake does not clear the violation,
    the anchor is used only if it is clean, and dropped otherwise
    (``anchor_unavailable`` / ``anchor_fragmented``). A fragmented anchor is
    dropped rather than published: it would hand its shape to every other
    slide in the deck, which is strictly worse than authoring without one.
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
    defects, gated, fragmentation = await _evaluate_anchor(
        html, render=render, inspect_blocking=inspect_blocking, settings=settings
    )
    fragmented = _has_text_owner_violation(fragmentation)
    if (defects or fragmented) and remaining_calls > 0:
        if defects:
            logger.info(
                "anchor_remake slide=%d defects=%d",
                index,
                len(defects),
            )
        if fragmented:
            logger.info(
                "anchor_remake_fragmented slide=%d owners=%d",
                index,
                fragmentation.text_owners,
            )
        hint = anchor_correction_hint(
            defects,
            fragmentation_hint=(
                anchor_fragmentation_hint(fragmentation, _anchor_role(plan, index))
                if fragmented
                else None
            ),
        )
        try:
            remade = await author(
                plan,
                index,
                **{**kwargs, "prior_failure_hint": hint},
            )
        except Exception:  # noqa: BLE001 -- the remake is best effort too
            logger.warning("anchor_remake_failed slide=%d", index, exc_info=True)
            remade = None
        if remade:
            html = remade
            defects, gated, fragmentation = await _evaluate_anchor(
                html, render=render, inspect_blocking=inspect_blocking, settings=settings
            )
            fragmented = _has_text_owner_violation(fragmentation)
    if defects:
        logger.warning(
            "anchor_unavailable slide=%d retained %d blocking defect(s)",
            index,
            len(defects),
        )
        return None, html, index
    if fragmented:
        # The design's choice (Q0): a still-fragmented anchor is dropped, not
        # published. The deck then authors without one, exactly as it does
        # today for a defective anchor -- propagating the fragmentation to
        # every later slide is the outcome this gate exists to prevent.
        logger.warning(
            "anchor_fragmented slide=%d owners=%d",
            index,
            fragmentation.text_owners,
        )
        return None, html, index
    try:
        anchor = extract_style_anchor(html, index)
    except Exception:  # noqa: BLE001 -- extraction may never fail the deck
        logger.warning("anchor_unavailable slide=%d extraction failed", index, exc_info=True)
        return None, html, index
    if anchor is None:
        logger.info("anchor_unavailable slide=%d has no stylesheet to inherit", index)
    elif fragmentation is not None:
        # Knob on and metrics available: the bench-reportable text-owner count
        # rides the same line as before, rather than a second log statement.
        logger.info(
            "anchor_ready slide=%d gated=%s owners=%d",
            index,
            gated,
            fragmentation.text_owners,
        )
    else:
        # The only positive evidence a run leaves that the deck inherited one
        # visual language, and whether that anchor was gated or merely trusted.
        logger.info("anchor_ready slide=%d gated=%s", index, gated)
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


def anchor_render_step(
    render_gate: Any,
    *,
    deck_scope: str | None,
    render_html: Callable[[str], Awaitable[Any]],
) -> Callable[[str], Awaitable[None]]:
    """The job lane's anchor render: one render under the deck's gate and scope.

    ``set_render_deck_scope`` is a *synchronous* context manager, so it has to
    be entered with ``with`` inside the ``async with`` that holds the render
    gate. Naming both in a single ``async with`` raises ``TypeError`` before the
    render is even attempted, which silently skipped the anchor gate on every
    deck of the job lane (``anchor_gate_skipped render unavailable``, bench run
    30 on 2026-09-06). Building the step here keeps that one line under test.
    """

    from services.authored_admission_capture import capture_once
    from services.authored_admission_context import digest_html
    from utils.slide_capture import get_cached_browser_metrics
    from utils.slide_capture_deck_scope import set_render_deck_scope
    from utils.slide_capture_result import RenderCapture

    async def render_anchor(html: str) -> None:
        # The anchor is rendered before illustration/substitution has consumed
        # authoring-only image slot metadata.  Admit a renderer-safe projection
        # while retaining the original HTML for the downstream authoring stages.
        renderable_html = canonicalize_authored_slide_html(html, finalized=True)

        async def capture(value: str) -> RenderCapture:
            png = await render_html(value)
            return RenderCapture.from_payload(
                html_digest=digest_html(value),
                png=png,
                browser_metrics=get_cached_browser_metrics(value),
                runtime_counters={},
            )

        async with render_gate:
            with set_render_deck_scope(deck_scope):
                # The anchor candidate may be re-offered; the owner renders the
                # same projection once. It has no deck index of its own.
                captured = await capture_once(
                    renderable_html,
                    capture=capture,
                )
                _ = captured.png

    return render_anchor


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
) -> tuple[tuple[Any, ...], bool]:
    """Render the anchor once and return its blocking DOM defects.

    Returns ``(defects, gated)``; ``gated`` is False when the gate could not
    run at all. A render or inspection that cannot run is not a defect: the
    gate is skipped (``anchor_gate_skipped``) and the anchor is still used,
    rather than dropping an otherwise usable anchor. A bad anchor is a smaller
    risk than no anchor, and the remake round still catches its defects.
    """

    if not html:
        return (), False
    try:
        await render(html)
    except Exception:  # noqa: BLE001
        logger.info("anchor_gate_skipped render unavailable", exc_info=True)
        return (), False
    try:
        return tuple(inspect_blocking(html) or ()), True
    except Exception:  # noqa: BLE001
        logger.info("anchor_gate_skipped inspection unavailable", exc_info=True)
        return (), False


__all__ = [
    "MIN_ANCHOR_DECK_SLIDES",
    "NON_ANCHOR_ROLES",
    "accepts_style_anchor",
    "anchor_blocking_defects",
    "anchor_correction_hint",
    "anchor_fragmentation_hint",
    "anchor_fragmentation_report",
    "anchor_render_step",
    "anchor_slide_index",
    "resolve_direct_lane_anchor",
    "resolve_style_anchor",
]
