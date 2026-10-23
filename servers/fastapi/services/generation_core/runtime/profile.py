"""Runtime profile: concurrency and asset budgets a generation attempt pins.

``services/svg_presentation_prepare.py`` used to hard-code its per-page
provider and render semaphores (``asyncio.Semaphore(2 if source_reflow else
3)`` / ``asyncio.Semaphore(1 if source_reflow else 3)``). Those constants move
here so an operator can override them without a code change, following the
same env-knob pattern ``authored_rollout_profile``'s five switches use for the
authored driver (``utils/get_env_concurrency.py``). The defaults below are
exactly the values the hard-coded semaphores used, so resolving with no
overrides set is behaviour-identical to before this module existed.

``illustration_budget`` caps the image-provider calls one deck's AssetOffer
may spend (a scene illustration or an icon sprite sheet is one call).
``illustration_timeout_seconds`` bounds each such call and defaults to the
authored ``IMAGE_GENERATION_TIMEOUT_SECONDS`` knob.

``terminal_policy`` and ``vision_advisory`` are the page-quality decisions a
deck attempt pins (c1-quality): what the collector does with a page that ran
out of attempts (``generation_core/quality/terminal_policy.py``), and whether
the deck gets one contact-sheet vision advisory before publication. Raster
reconstruction is the exact-reconstruction class for both: fail closed, and no
advisory (its pages are pinned to an approved analysis, not composed).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import heapq

from services.generation_core.quality.terminal_policy import (
    TerminalPolicy,
    resolve_terminal_policy,
)

from utils.get_env import (
    get_image_generation_timeout_seconds,
    get_svg_author_concurrency_env,
    get_svg_author_concurrency_source_reflow_env,
    get_svg_illustration_budget_env,
    get_svg_illustration_timeout_seconds_env,
    get_svg_render_concurrency_env,
    get_svg_render_concurrency_source_reflow_env,
)

DEFAULT_AUTHOR_CONCURRENCY = 3
DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW = 6
DEFAULT_RENDER_CONCURRENCY = 3
DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW = 2
DEFAULT_ILLUSTRATION_BUDGET = 4


@dataclass(frozen=True, slots=True)
class GenerationRuntimeProfile:
    """One resolved set of concurrency/budget knobs for a generation attempt."""

    author_concurrency: int
    render_concurrency: int
    illustration_budget: int
    illustration_timeout_seconds: int
    terminal_policy: TerminalPolicy = TerminalPolicy.FAIL
    vision_advisory: bool = False


def resolve_svg_runtime_profile(
    *, source_reflow: bool, raster_reconstruction: bool = False
) -> GenerationRuntimeProfile:
    """Resolve the profile ``svg_presentation_prepare`` pins per deck job."""

    profile, _ = resolve_svg_runtime_profile_with_provenance(
        source_reflow=source_reflow, raster_reconstruction=raster_reconstruction,
    )
    return profile


def resolve_svg_runtime_profile_with_provenance(
    *, source_reflow: bool, raster_reconstruction: bool = False,
) -> tuple[GenerationRuntimeProfile, dict]:
    """Keep diagnostic provenance outside serialized runtime identity values."""

    explicit_author = (
        get_svg_author_concurrency_source_reflow_env()
        if source_reflow
        else get_svg_author_concurrency_env()
    )
    default_author = (
        DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW
        if source_reflow
        else DEFAULT_AUTHOR_CONCURRENCY
    )
    explicit_render = (
        get_svg_render_concurrency_source_reflow_env()
        if source_reflow
        else get_svg_render_concurrency_env()
    )
    default_render = (
        DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW
        if source_reflow
        else DEFAULT_RENDER_CONCURRENCY
    )
    configured_author = explicit_author or default_author
    executor_capacity = _author_executor_capacity() if source_reflow else None
    effective_author = (
        min(configured_author, executor_capacity)
        if executor_capacity is not None else configured_author
    )
    provenance = {"author_concurrency": {
        "source": "env" if explicit_author is not None else "default",
        "requested": configured_author,
        "executor_capacity": executor_capacity,
        "clamped_to_executor": effective_author < configured_author,
        "resolved": effective_author,
        "effective": effective_author,
    }}
    profile = GenerationRuntimeProfile(
        author_concurrency=effective_author,
        render_concurrency=explicit_render or default_render,
        illustration_budget=(
            get_svg_illustration_budget_env() or DEFAULT_ILLUSTRATION_BUDGET
        ),
        illustration_timeout_seconds=(
            get_svg_illustration_timeout_seconds_env()
            # The authored image knob (IMAGE_GENERATION_TIMEOUT_SECONDS) is the
            # per-call default, so one picture is bounded identically in both
            # drivers unless an operator narrows it for svg.
            or int(get_image_generation_timeout_seconds())
        ),
        terminal_policy=resolve_terminal_policy(
            source_reflow=source_reflow, exact_reconstruction=raster_reconstruction
        ),
        vision_advisory=not raster_reconstruction,
    )
    return profile, provenance


def _author_executor_capacity() -> int:
    """Return the authored slide executor's actual capacity for SVG admission."""
    from services.generation_core.authoring.author_slide import _AUTHOR_EXECUTOR

    return _AUTHOR_EXECUTOR._max_workers


class ProviderPermits:
    """Provider-call permits of one deck attempt; waiting repairs go first.

    A page keeps the deck's in-flight page permit (``ctx.semaphore``) for its
    whole life and holds one of these only around its author attempt, so a
    page renders, measures and persists while another authors, and no more
    author attempts run at once than the configured author concurrency.
    Waiters are admitted repairs first, then in arrival order. A task that
    already holds a permit re-enters without taking another, so a nested
    acquisition can never wait on itself. A permit handed to a waiter that is
    cancelled before it resumes is passed on, never lost.
    """

    def __init__(self, capacity: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("svg_provider_permit_capacity_invalid")
        self.capacity = capacity
        self._in_use = 0
        self._sequence = 0
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._depth: dict[asyncio.Task, int] = {}

    @property
    def in_use(self) -> int:
        return self._in_use

    def _hand_off(self) -> None:
        while self._waiters:
            _, _, waiter = heapq.heappop(self._waiters)
            if not waiter.done():
                waiter.set_result(None)
                return
        self._in_use -= 1

    async def _acquire(self, repair: bool) -> None:
        if self._in_use < self.capacity and not any(not w.done() for _, _, w in self._waiters):
            self._in_use += 1
            return
        waiter = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (0 if repair else 1, self._sequence, waiter))
        self._sequence += 1
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                self._hand_off()
            else:
                waiter.cancel()
            raise

    @asynccontextmanager
    async def slot(self, *, repair: bool):
        task = asyncio.current_task()
        if task is not None and task in self._depth:
            self._depth[task] += 1
            try:
                yield
            finally:
                self._depth[task] -= 1
            return
        await self._acquire(repair)
        if task is not None:
            self._depth[task] = 1
        try:
            yield
        finally:
            if task is not None:
                self._depth.pop(task, None)
            self._hand_off()


__all__ = [
    "GenerationRuntimeProfile", "ProviderPermits", "resolve_svg_runtime_profile",
    "resolve_svg_runtime_profile_with_provenance",
]
