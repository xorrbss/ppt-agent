"""Stage signatures and the attribute contract every Composer stage honors.

These are the seams between the parallel workstreams. Implementations live in
``classify``, ``render``, ``fit``, ``measure`` and ``pipeline``; tests may pass
any callable that satisfies a Protocol here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from services.composer.plan import (
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerSlideResult,
    ComposerTokens,
    FitParameters,
)

# --- Attribute contract -----------------------------------------------------
#
# The renderer writes these; the readback gates and the authored-hybrid
# exporter read them. Nothing else may write them.

#: Every visible text, image and furniture element carries a unique QID.
#: For a source-bound element it is ``deterministic_source_qid(binding_id)``
#: from ``services.source_content.source_stamp_dom``.
QID_ATTRIBUTE = "data-qid"

#: ``title`` | ``subtitle`` | ``body`` | ``supporting`` | ``caption`` |
#: ``image`` | ``decoration`` | ``furniture``.
SEMANTIC_ROLE_ATTRIBUTE = "data-semantic-role"

#: The source binding id (block, ``block:run:i`` or ``block:run:i-j``), see
#: ``services.source_content.source_runs.source_run_binding_id``.
SOURCE_BLOCK_ATTRIBUTE = "data-source-block-id"

#: A rendered table opts into native export with this attribute on the
#: ``<table>`` element only; rows and cells carry no QID.
NATIVE_TABLE_ATTRIBUTE = 'data-native="table"'

#: Source images are placed by asset id; the renderer never invents a ``src``.
ASSET_ATTRIBUTE = "data-asset-id"

#: Text rules the renderer must keep so the readback gates pass by construction:
#: - the element bound to a run carries that run's characters exactly, whole
#:   (nested inline spans and ``<br>`` between whole words are fine);
#: - a marker glyph (bullet, circled number, leading arrow) may be omitted and
#:   drawn by CSS, nothing else may be dropped or added;
#: - runs of one block appear in source order within the block's container;
#: - no source run is duplicated except furniture the source itself repeats;
#: - ``<body>`` is exactly 1280x720, margin 0, overflow hidden; no scripts,
#:   no external resources, no CSS transforms on text owners, and the whole
#:   document passes ``utils.renderer_isolation.admit_authored_html``.
TEXT_RULES = (
    "exact run characters per bound element",
    "markers may be drawn by CSS",
    "source order within a block",
    "no duplicated runs",
    "1280x720 body, admitted HTML only",
)


# --- Measurement --------------------------------------------------------------


@dataclass(frozen=True)
class Measurement:
    """One Chromium observation of one rendered slide."""

    metrics: Mapping[str, Any]
    png: bytes | None
    #: Blocking defect summaries from ``blocking_safety_issues`` on
    #: ``inspect_authored_dom``; empty means the slide is accepted.
    blocking_issues: tuple[str, ...] = ()
    #: Source readback verdict for this slide: ``None`` when not evaluated,
    #: else the list of missing binding ids (empty means every run was found).
    missing_bindings: tuple[str, ...] | None = None
    telemetry_available: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


class Measurer(Protocol):
    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement: ...


class Estimator(Protocol):
    """Font-metric estimate: does the plan fit under these parameters?

    Runs before any Chromium call so most slides render once. Must be
    conservative (a ``True`` that the browser then rejects only costs one
    extra render; a ``False`` that would have fit costs one rung).
    """

    def __call__(
        self, plan: ComposerSlidePlan, tokens: ComposerTokens, params: FitParameters,
        deck: ComposerDeckPlan,
    ) -> bool: ...


class Renderer(Protocol):
    def __call__(
        self, plan: ComposerSlidePlan, tokens: ComposerTokens, params: FitParameters,
        deck: ComposerDeckPlan,
    ) -> str: ...


class Classifier(Protocol):
    """Manifest unit and outline slide -> plan. Deterministic; no model call."""

    def __call__(
        self,
        *,
        unit: Mapping[str, Any],
        slide_index: int,
        deck_context: Mapping[str, Any],
    ) -> ComposerSlidePlan: ...


FitSlide = Callable[
    [ComposerSlidePlan, ComposerTokens, ComposerDeckPlan],
    Awaitable[ComposerSlideResult],
]


@dataclass(frozen=True)
class DeckContext:
    """What the classifier may know about the whole deck while planning one page.

    ``repeated_texts`` are normalized texts that occur on at least 60% of the
    pages (running headers, footers, confidentiality marks). ``page_count`` is
    the source page count, which is also the slide count.
    """

    page_count: int
    repeated_texts: frozenset[str]
    source_page_emu: tuple[int, int] | None
    deck_title: str = ""

    def as_mapping(self) -> dict[str, Any]:
        return {
            "page_count": self.page_count,
            "repeated_texts": sorted(self.repeated_texts),
            "source_page_emu": self.source_page_emu,
            "deck_title": self.deck_title,
        }


def source_px_scale(source_page_emu: tuple[int, int] | None, canvas=None) -> float:
    """Uniform factor that maps the source page onto the 1280x720 canvas.

    16:9 at 13.333 in is exactly 1.0; a 4:3 page fits by height and leaves
    side bands; anything larger scales down so the whole page is visible.
    """

    if not source_page_emu:
        return 1.0
    cx, cy = source_page_emu
    if cx <= 0 or cy <= 0:
        return 1.0
    width_px = cx / 914400.0 * 96.0
    height_px = cy / 914400.0 * 96.0
    return min((canvas.width_px if canvas else 1280.0) / width_px, (canvas.height_px if canvas else 720.0) / height_px)


def repeated_texts(units: Sequence[Mapping[str, Any]], *, share: float = 0.6) -> frozenset[str]:
    """Normalized texts present on at least ``share`` of the pages."""

    from services.source_content.source_runs import normalize_source_text

    counts: dict[str, int] = {}
    for unit in units:
        seen: set[str] = set()
        for block in unit.get("blocks") or ():
            if str(block.get("kind") or "") != "text":
                continue
            payload = block.get("payload") or {}
            text = normalize_source_text(str(payload.get("text") or ""))
            if text and text not in seen:
                seen.add(text)
                counts[text] = counts.get(text, 0) + 1
    threshold = max(2, int(len(units) * share + 0.999))
    return frozenset(text for text, n in counts.items() if n >= threshold)


__all__ = [
    "ASSET_ATTRIBUTE",
    "Classifier",
    "DeckContext",
    "Estimator",
    "FitSlide",
    "Measurement",
    "Measurer",
    "NATIVE_TABLE_ATTRIBUTE",
    "QID_ATTRIBUTE",
    "Renderer",
    "SEMANTIC_ROLE_ATTRIBUTE",
    "SOURCE_BLOCK_ATTRIBUTE",
    "TEXT_RULES",
    "repeated_texts",
    "source_px_scale",
]
