"""Post-fit gates for the Composer deck loop: classify, assert, resolve PNGs.

Split out of ``pipeline.py`` to keep that module under the file-size cap
(``docs/file-size-policy.md``), the same way ``pipeline_fit.py`` and
``pipeline_inputs.py`` were already extracted.

Everything here runs around the fit ladder and before publication:
``classify_deck`` wraps the injected ``Classifier`` per source page: one call
per page, with the index-continuity check that lets every later stage assume
``plans[i].slide_index == i``. ``assert_slide_bindings`` and
``assert_verbatim_readback`` are the stamping and source-readback gates run as
assertions -- see docs/generation-quality-root-cause-and-composer-
architecture-20260904.md section 4.7 ("gates are assertions... a measured
defect is a renderer bug to fix in code, never a reason to ask the model
again"). ``resolve_pngs`` recovers each accepted slide's PNG from whatever the
fit ladder's measurer already captured, falling back to one extra render only
when nothing was kept.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from services.composer.contracts import Classifier, DeckContext, Measurement, Measurer
from services.composer.plan import ComposerSlidePlan, ComposerSlideResult
from services.composer.receipt import ComposerAssertionError, is_accepted_residual


class RecordingMeasurer:
    """Wrap a ``Measurer`` and keep the PNG of every render, keyed by its HTML."""

    def __init__(self, inner: Measurer) -> None:
        self.inner = inner
        self.pngs: dict[str, bytes] = {}

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        measurement = await self.inner(html, plan)
        if isinstance(measurement.png, bytes) and measurement.png:
            self.pngs[html] = measurement.png
        return measurement


def classify_deck(
    units: Sequence[Mapping[str, Any]],
    deck_context: DeckContext,
    classifier: Classifier,
) -> list[ComposerSlidePlan]:
    context = deck_context.as_mapping()
    plans: list[ComposerSlidePlan] = []
    for index, unit in enumerate(units):
        try:
            plan = classifier(unit=unit, slide_index=index, deck_context=context)
        except ValueError as error:
            raise ComposerAssertionError(index, "composer_plan_invalid", type(error).__name__) from error
        if plan.slide_index != index:
            raise ComposerAssertionError(index, "composer_plan_index_mismatch", f"plan index {plan.slide_index}")
        plans.append(plan)
    return plans


def assert_slide_bindings(
    results: Sequence[ComposerSlideResult],
    bindings: Sequence[Sequence[tuple[str, str]]],
    stamping_rebound: dict[int, bool] | None = None,
) -> None:
    """Every accepted slide is free of blocking defects and already sealed.

    Rung 5's own residual ``overlap`` issues are accepted (see
    ``services.composer.receipt.is_accepted_residual``); any other residual
    issue, at any rung, still fails closed.

    The stamper is only required not to raise. For a block rendered as
    several bound run elements it may rebind the coarsest exclusive owner
    (typically the parent wrapper) instead of every individual run element,
    which changes the stamped bytes without dropping or misattributing any
    run -- a valid alternative binding, not a defect; the Chromium readback
    that follows is the authoritative binding gate. When ``stamping_rebound``
    is given, each slide's index is recorded there so the receipt can report
    whether that happened, purely for observability.
    """

    from services.source_content.text_stamping import stamp_source_blocks

    if len(bindings) != len(results):
        raise ComposerAssertionError(0, "composer_binding_cardinality_mismatch")
    for result, slide_bindings in zip(results, bindings, strict=True):
        index = result.slide_index
        issues = result.receipt.blocking_issues
        if issues and not is_accepted_residual(
            result.receipt.rung, issues, fit_policy_revision=result.receipt.fit_policy_revision,
        ):
            raise ComposerAssertionError(index, "composer_blocking_issues_remaining", "; ".join(issues))
        try:
            stamped = stamp_source_blocks(result.html, list(slide_bindings))
        except Exception as error:
            code = str(getattr(error, "code", "") or "composer_source_binding_missing")
            raise ComposerAssertionError(index, code, str(getattr(error, "detail", error))) from error
        if stamping_rebound is not None:
            stamping_rebound[index] = stamped.html != result.html


def assert_verbatim_readback(manifest: Any, outline: Any, htmls: Sequence[str]) -> Mapping[str, Any]:
    from services.source_content.readback import SourceContentReadbackError, verify_source_verbatim_readback

    try:
        return verify_source_verbatim_readback(manifest=manifest, outline=outline, htmls=list(htmls)).as_dict()
    except SourceContentReadbackError as error:
        indices = tuple(error.slide_indices or ())
        raise ComposerAssertionError(indices[0] if indices else 0, error.code, error.detail) from error


async def resolve_pngs(
    results: Sequence[ComposerSlideResult],
    plans: Mapping[int, ComposerSlidePlan],
    measurer: RecordingMeasurer,
) -> list[bytes]:
    """The PNG of each accepted render; one extra capture only when none was kept."""

    pngs: list[bytes] = []
    for result in results:
        png = measurer.pngs.get(result.html)
        if png is None and result.png_path:
            try:
                png = await asyncio.to_thread(lambda path=result.png_path: open(path, "rb").read())
            except OSError:
                png = None
        if png is None:
            png = (await measurer(result.html, plans[result.slide_index])).png
        if not isinstance(png, bytes) or not png:
            raise ComposerAssertionError(result.slide_index, "composer_png_unavailable")
        pngs.append(png)
    return pngs


__all__ = [
    "RecordingMeasurer",
    "assert_slide_bindings",
    "assert_verbatim_readback",
    "classify_deck",
    "resolve_pngs",
]
