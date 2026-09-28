"""Attempt-timing observations for the SVG driver.

The workspace generation handler (``services/presentation_generation_operation_
handler.py``) installs one :class:`GenerationTimingRecorder` on the request as
``_authored_generation_timing_recorder`` for every strategy, and seals it into
the ``generation-attempt-timing-receipt/v2`` after the attempt. A succeeded
attempt must carry an observation for ``outline``, ``authoring`` (recorder
stage ``author``), ``chromiumReview`` (``render`` + ``quality``) and
``publication``, or the receipt is ``rc_invalid``
(``services/presentation_generation_attempt_timing.py:_public_executed_stages``).
The authored driver records those stages from its own pipeline; this module is
the SVG driver's equivalent, written into the same recorder so the receipt
format stays single.

Stage mapping, each observed where the SVG driver runs it (``collect.py``):

* ``outline`` -- from the operation boundary (``_authored_outline_started_at``,
  pinned before the upstream outline LLM call, the same clock the authored
  driver uses) to the end of the per-deck setup (``_prepare_generation``).
* ``author`` -- every page's author attempt (provider SVG authoring or the
  raster reconstruction), summed across concurrent pages.
* ``render`` -- every page's Chromium render/measure attempt, and a terminal
  fallback page's render.
* ``quality`` -- the deck quality pass: page classification and the vision
  advisory.
* ``publication`` -- the ordered publication handoff.

Outside a workspace operation the request carries no recorder and every helper
here is a no-op.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import time

_RECORDER_ATTRIBUTE = "_authored_generation_timing_recorder"
#: Recorder stages the page loop owns. A page resumed from a verified
#: checkpoint neither authors nor renders in this attempt.
_PAGE_LOOP_STAGES = ("author", "render")


def _recorder(request: object):
    return getattr(request, _RECORDER_ATTRIBUTE, None)


@contextmanager
def observe_stage(request: object, name: str) -> Iterator[None]:
    """Accumulate one executed stage window into the attempt's recorder."""

    recorder = _recorder(request)
    if recorder is None:
        yield
        return
    with recorder.stage(name):
        yield


def enter_deck_stage(request: object, name: str) -> None:
    """Move the active-stage marker the handler's failure path attributes to.

    ``_persist_failed_attempt_timing`` charges the time since the marker to the
    marked stage when an attempt fails, exactly as the authored driver's
    ``mark_stage`` does.
    """

    if _recorder(request) is None:
        return
    object.__setattr__(request, "_authored_timing_stage", name)
    object.__setattr__(request, "_authored_timing_stage_started_at", time.monotonic())


def record_outline(request: object) -> None:
    """Close the outline stage: operation boundary -> per-deck setup done."""

    recorder = _recorder(request)
    if recorder is None:
        return
    started_at = getattr(request, "_authored_outline_started_at", None)
    if not isinstance(started_at, (int, float)) or isinstance(started_at, bool):
        # No upstream outline clock was pinned: the attempt boundary is the
        # earliest instant this attempt's outline work can have started.
        started_at = recorder.started_at
    recorder.record_stage_ms("outline", max(0.0, time.monotonic() - started_at) * 1000)


def settle_page_loop(request: object) -> None:
    """Record the page loop's author/render work once every page has an outcome.

    Every page that authored or rendered in this attempt has already added its
    measured window through :func:`observe_stage`. A stage no page executed --
    every page resumed from a verified checkpoint -- did zero work in this
    attempt, and is recorded as exactly that; the authored driver records its
    per-slide work sums the same way, zero included.
    """

    recorder = _recorder(request)
    if recorder is None:
        return
    observed = recorder.stage_observations()
    for name in _PAGE_LOOP_STAGES:
        if name not in observed:
            recorder.record_stage_ms(name, 0)


__all__ = ["enter_deck_stage", "observe_stage", "record_outline", "settle_page_loop"]
