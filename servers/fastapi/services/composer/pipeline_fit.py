"""Off-loop slide fitting: each slide's fit ladder runs in its own worker thread.

Split out of ``pipeline.py`` to keep that module under the file-size cap, but
also the direct fix for a measured production defect: a 35-page verbatim
restyle (``COMPOSER_RESTYLE_ENABLED=true``) lost its workspace operation
lease mid-render. ``fit_deck`` used to run every slide's fit ladder as a
plain coroutine on the worker's own event loop, bounded by an
``asyncio.Semaphore``; every Chromium render inside that ladder is driven
through a blocking subprocess wait (``utils.slide_capture.
render_html_to_png_with_metrics``, 1-3s per render), and while nothing else
on that loop can run, the operation's lease-heartbeat task -- also scheduled
on that same loop -- starves.

``fit_deck`` now submits each slide to ``asyncio.to_thread`` running
``asyncio.run(fit_slide(...))`` inside the worker thread: render, measure and
estimate are entirely self-contained per slide, so each thread drives its own
event loop from a clean call to a clean return. The main loop only awaits
thread results between submissions, so the heartbeat keeps flowing
regardless of how long any single Chromium render takes. ``concurrency``
(``PUBLIC_RENDER_MAX_CONCURRENCY``) still bounds real parallel work: the
``asyncio.Semaphore`` is acquired on the main loop before a slide is handed
to a thread and released only once that thread returns, exactly as it bounded
the ladder walk itself before this change.

Real OS-thread concurrency introduces two correctness hazards that never
existed when every render ran cooperatively on one loop:

* Progress reporting. ``on_slide`` ultimately writes an optimistic-concurrency
  row keyed off the version it last read (``WorkspaceOperation.version`` via
  ``_ExecutionContext.progress`` in ``services.workspace_operation_runner``);
  two callers racing that read-then-write lose one call's CAS, which
  ``services.composer.pipeline._report`` turns into a spurious
  ``operation_lease_lost`` -- not a real lease loss. This raced even before
  this module existed, since ``fit_deck`` already ran two slides concurrently
  via ``asyncio.gather``; it is confirmed as the cause of one live failure
  (operation 6ca5b1f0, attempt 1: two ``on_slide`` calls landed within the
  same second and the second lost its CAS). ``fit_deck`` below serializes
  every ``on_slide`` call behind one ``asyncio.Lock`` held on the main loop,
  so calls into the progress reporter are never concurrent even though the
  slides that trigger them are fit in parallel.
* Shared Chromium module state. ``utils.slide_capture``'s render/metrics/
  capture caches (``_render_cache``, ``_capture_cache``,
  ``_browser_metrics_cache``) and its in-flight de-dupe tables
  (``_render_inflight``, ``_browser_metrics_inflight``) are plain ``dict``/
  ``OrderedDict`` objects mutated with no lock, and the in-flight tables hold
  ``asyncio.Task`` objects bound to whichever event loop created them. That is
  safe today only because one event loop drives every render cooperatively
  (single-threaded interleaving never mutates two of these structures at
  once); once two worker threads each run their own loop, concurrent
  mutation of those dicts is a genuine data race, and a cross-thread await of
  a cached ``Task`` can raise or hang. (``_reusable_chrome`` and the runtime
  counters are the exception: both are already behind a ``threading.Lock``,
  which is presumably why ``_get_reusable_chrome_sync`` is already invoked
  through ``asyncio.to_thread`` today.) This module does not own
  ``utils.slide_capture`` and this change does not touch it; instead
  ``guard_shared_chromium_state`` below wraps the default Chromium
  ``Measurer`` in a process-wide ``threading.Lock`` so at most one worker
  thread is ever inside a Chromium render at a time. HTML templating
  (``Renderer``) and the estimator touch no shared state and stay fully
  parallel across threads; a caller-injected ``Measurer`` (every existing
  test's fakes) is never wrapped, so those keep their full concurrency and
  the concurrency-bound assertions those tests make (``max_in_flight``) are
  unaffected. The trade-off: in production, the real Chromium call across a
  deck's slides is now serialized rather than two-way parallel. Correctness
  over throughput; the concurrency knob still parallelizes HTML generation,
  estimation, and everything else in the ladder around that one call.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from services.composer.assets import (
    AssetResolver,
    ComposerAssetError,
    in_memory_asset_resolver,
    manifest_asset_resolver,
    preload_deck_assets,
)
from services.composer.contracts import Estimator, Measurement, Measurer, Renderer
from services.composer.plan import ComposerDeckPlan, ComposerSlidePlan, ComposerSlideResult
from services.composer.receipt import ComposerAssertionError, ComposerPipelineError

LOGGER = logging.getLogger(__name__)

#: Serializes every call into the real Chromium measurer across worker
#: threads. See the module docstring: ``utils.slide_capture``'s render/
#: metrics/capture caches are not safe under true multi-thread concurrency.
_CHROMIUM_MEASURE_LOCK = threading.Lock()


class _SerializedMeasurer:
    """Wrap ``inner`` so at most one worker thread is inside it at a time."""

    def __init__(self, inner: Measurer) -> None:
        self.inner = inner

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        with _CHROMIUM_MEASURE_LOCK:
            return await self.inner(html, plan)


def guard_shared_chromium_state(measurer: Measurer) -> Measurer:
    """Serialize cross-thread access to ``utils.slide_capture``'s module caches.

    Apply this only to the real Chromium measurer. A test's fake measurer
    shares no state with ``utils.slide_capture`` and must keep its full
    concurrency -- several existing tests assert real overlap up to
    ``PUBLIC_RENDER_MAX_CONCURRENCY`` (``measurer.max_in_flight``).
    """

    return _SerializedMeasurer(measurer)


def _default_asset_resolver(manifest: Any, sql_session: Any) -> AssetResolver:
    """Resolve against the owner-scoped manifest asset store, lazily.

    Owner id parsing is deferred into the resolver closure so building this
    never fails for a manifest without a usable ``owner_id`` -- it simply
    never resolves anything, which only matters if a marker is found.
    """

    async def resolve(asset_id: str) -> bytes | None:
        try:
            owner_id = uuid.UUID(str(getattr(manifest, "owner_id", "")))
        except (AttributeError, TypeError, ValueError):
            return None
        return await manifest_asset_resolver(manifest, owner_id=owner_id, session=sql_session)(asset_id)

    return resolve


async def preload_assets_for_fit(
    deck: ComposerDeckPlan,
    manifest: Any,
    sql_session: Any,
    asset_resolver: AssetResolver | None,
) -> AssetResolver:
    """The resolver the fit ladder's worker threads may safely call.

    Those threads must never open a database session (see the module
    docstring). An injected ``asset_resolver`` -- every test, and the offline
    harness -- is already database-free (``in_memory_asset_resolver`` or
    equivalent) and is handed back unchanged: it may resolve ids beyond what
    ``deck``'s plans enumerate (a synthetic renderer under test, for
    instance), so it must not be narrowed to a pre-scanned subset. The
    production default has no such guarantee, so it is resolved eagerly here
    through ``preload_deck_assets`` -- walking exactly the ids
    ``deck.slides[*].images`` reference, the only place a real render ever
    draws a ``data-asset-id`` marker from -- and replaced with an
    ``in_memory_asset_resolver`` over the result. This also removes what was
    previously up to one DB round trip per image marker per ladder render
    per slide, since ``inline_result_assets`` used to hit the same DB-backed
    resolver a second time after the fit ladder too.
    """

    if asset_resolver is not None:
        return asset_resolver
    try:
        owner_id = uuid.UUID(str(getattr(manifest, "owner_id", "")))
    except (AttributeError, TypeError, ValueError):
        # No usable owner id: the same lazily-failing closure the
        # pre-threading code used. It never opens a session -- owner id
        # parsing fails before any query -- so it is thread-safe as-is.
        return _default_asset_resolver(manifest, sql_session)
    resolved = await preload_deck_assets(deck, manifest, owner_id, sql_session)
    return in_memory_asset_resolver(resolved)


async def maybe_extend_lease(
    extend_lease: Callable[..., Awaitable[bool]] | None, seconds: int
) -> None:
    """Best-effort widen of the attempt's lease; never raises into the caller.

    Shared by the fit loop below (called per slide, from the same serialized
    path as ``on_slide``) and the publish stage in ``services.composer.
    pipeline`` (called once, before its one long write transaction). Neither
    caller offering ``extend_lease`` (most callers -- see ``run_composer_restyle``'s
    docstring on the parameter) nor a ``False`` return is an error here: the
    fit loop's own readback/binding assertions and publication's fencing
    protect correctness regardless of whether the lease was actually widened.
    """

    if extend_lease is None:
        return
    try:
        await extend_lease(seconds=seconds)
    except Exception:
        LOGGER.warning("composer_restyle lease extension failed", exc_info=True)


def _run_fit_slide_in_thread(
    plan: ComposerSlidePlan,
    deck: ComposerDeckPlan,
    *,
    render: Renderer,
    measure: Measurer,
    estimate: Estimator | None,
) -> ComposerSlideResult:
    """Run one slide's fit ladder to completion in a fresh, private event loop.

    Called from a worker thread only (see ``fit_deck``). ``asyncio.run``
    creates and tears down its own loop for this single call, so nothing in
    the ladder's render/measure/estimate awaits ever touches the loop that
    submitted this work.
    """

    from services.composer.fit import fit_slide

    return asyncio.run(
        fit_slide(plan, deck.tokens, deck, render=render, measure=measure, estimate=estimate)
    )


#: Lease window requested after each slide completes, when a caller offers
#: ``extend_lease``. Matched to ``WORKSPACE_OPERATION_LEASE_SECONDS`` (300s):
#: every slide re-establishes a full lease window, so the 30s heartbeat only
#: has to land once within that window rather than continuously for the
#: whole fit stage (223s measured for the 43-page deck, section 6a of
#: docs/generation-quality-root-cause-and-composer-architecture-20260904.md).
#: A literal, not an import of the workspace constant, for the same reason
#: ``pipeline.py``'s ``EXTEND_LEASE_SECONDS_FOR_PUBLISH`` is a literal: this
#: package stays decoupled from that subsystem's internals.
EXTEND_LEASE_SECONDS_FOR_FIT = 300


async def fit_deck(
    deck: ComposerDeckPlan,
    *,
    renderer: Renderer,
    measurer: Measurer,
    estimator: Estimator | None,
    concurrency: int,
    on_slide: Callable[[ComposerSlideResult, int], Awaitable[None]] | None = None,
    extend_lease: Callable[..., Awaitable[bool]] | None = None,
    extend_lease_seconds: int = EXTEND_LEASE_SECONDS_FOR_FIT,
) -> list[ComposerSlideResult]:
    """Fit every slide with at most ``concurrency`` Chromium sessions at once.

    Each slide's ladder runs off the calling event loop (see the module
    docstring): the real work happens in ``asyncio.to_thread``, referenced
    dynamically here (not bound as a default argument) so a test can patch
    ``asyncio.to_thread`` and observe it being used. ``on_slide`` calls are
    serialized behind one lock local to this call, so two slides finishing at
    nearly the same moment never report progress concurrently. ``extend_lease``
    -- the operation attempt's lease widener, when a durable-operation caller
    offers one -- is called from that same serialized path once per slide, for
    the same reason: it is a second write keyed off the operation's row, and
    keeping it behind the same lock keeps every per-slide side effect
    strictly ordered rather than racing across worker threads.
    """

    from services.composer.fit import ComposerMeasurementError
    from services.composer.render import ComposerRenderError

    semaphore = asyncio.Semaphore(max(1, concurrency))
    report_lock = asyncio.Lock()
    completed = 0

    async def one(plan: ComposerSlidePlan) -> ComposerSlideResult:
        nonlocal completed
        async with semaphore:
            try:
                result = await asyncio.to_thread(
                    _run_fit_slide_in_thread,
                    plan,
                    deck,
                    render=renderer,
                    measure=measurer,
                    estimate=estimator,
                )
            except ComposerMeasurementError as error:
                raise ComposerPipelineError(
                    "composer_measurement_unavailable", detail=str(error), retryable=True
                ) from error
            except ComposerAssetError as error:
                raise ComposerAssertionError(error.slide_index, error.code, error.detail) from error
            except ComposerRenderError as error:
                raise ComposerAssertionError(plan.slide_index, error.code, error.detail) from error
        completed += 1
        if on_slide is not None or extend_lease is not None:
            # Serialize: on_slide ultimately writes an optimistic-concurrency
            # row (see the module docstring); two concurrent callers racing
            # it lose one as a spurious lease loss, not a real one. The lease
            # extension is kept in this same lock for the same reason -- it
            # is another write keyed off the operation's row.
            async with report_lock:
                if on_slide is not None:
                    await on_slide(result, completed)
                await maybe_extend_lease(extend_lease, extend_lease_seconds)
        return result

    # Drain all in-flight render threads before publishing the failure receipt;
    # a failed sibling must not leave background captures/progress writes alive.
    outcomes = await asyncio.gather(*(one(plan) for plan in deck.slides), return_exceptions=True)
    results = []
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
        results.append(outcome)
    return sorted(results, key=lambda item: item.slide_index)


__all__ = [
    "EXTEND_LEASE_SECONDS_FOR_FIT",
    "fit_deck",
    "guard_shared_chromium_state",
    "maybe_extend_lease",
    "preload_assets_for_fit",
]
