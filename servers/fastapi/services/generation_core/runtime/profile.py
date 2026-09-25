"""Runtime profile: concurrency and asset budgets a generation attempt pins.

``services/svg_presentation_prepare.py`` used to hard-code its per-page
provider and render semaphores (``asyncio.Semaphore(2 if source_reflow else
3)`` / ``asyncio.Semaphore(1 if source_reflow else 3)``). Those constants move
here so an operator can override them without a code change, following the
same env-knob pattern ``authored_rollout_profile``'s five switches use for the
authored driver (``utils/get_env_concurrency.py``). The defaults below are
exactly the values the hard-coded semaphores used, so resolving with no
overrides set is behaviour-identical to before this module existed.

``illustration_budget`` / ``illustration_timeout_seconds`` are named for
c3-assets: once the illustration stage moves into ``generation_core/assets/``
it reads the same fields instead of its own constants.

``terminal_policy`` and ``vision_advisory`` are the page-quality decisions a
deck attempt pins (c1-quality): what the collector does with a page that ran
out of attempts (``generation_core/quality/terminal_policy.py``), and whether
the deck gets one contact-sheet vision advisory before publication. Raster
reconstruction is the exact-reconstruction class for both: fail closed, and no
advisory (its pages are pinned to an approved analysis, not composed).
"""

from __future__ import annotations

from dataclasses import dataclass

from services.generation_core.quality.terminal_policy import (
    TerminalPolicy,
    resolve_terminal_policy,
)

from utils.get_env import (
    get_svg_author_concurrency_env,
    get_svg_author_concurrency_source_reflow_env,
    get_svg_illustration_budget_env,
    get_svg_illustration_timeout_seconds_env,
    get_svg_render_concurrency_env,
    get_svg_render_concurrency_source_reflow_env,
)

DEFAULT_AUTHOR_CONCURRENCY = 3
DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW = 2
DEFAULT_RENDER_CONCURRENCY = 3
DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW = 1
DEFAULT_ILLUSTRATION_BUDGET = 4
DEFAULT_ILLUSTRATION_TIMEOUT_SECONDS = 60


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
    return GenerationRuntimeProfile(
        author_concurrency=explicit_author or default_author,
        render_concurrency=explicit_render or default_render,
        illustration_budget=(
            get_svg_illustration_budget_env() or DEFAULT_ILLUSTRATION_BUDGET
        ),
        illustration_timeout_seconds=(
            get_svg_illustration_timeout_seconds_env()
            or DEFAULT_ILLUSTRATION_TIMEOUT_SECONDS
        ),
        terminal_policy=resolve_terminal_policy(
            source_reflow=source_reflow, exact_reconstruction=raster_reconstruction
        ),
        vision_advisory=not raster_reconstruction,
    )


__all__ = ["GenerationRuntimeProfile", "resolve_svg_runtime_profile"]
