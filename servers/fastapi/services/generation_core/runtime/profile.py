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
"""

from __future__ import annotations

from dataclasses import dataclass

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
DEFAULT_AUTHOR_CONCURRENCY_SOURCE_REFLOW = 2
DEFAULT_RENDER_CONCURRENCY = 3
DEFAULT_RENDER_CONCURRENCY_SOURCE_REFLOW = 1
DEFAULT_ILLUSTRATION_BUDGET = 4


@dataclass(frozen=True, slots=True)
class GenerationRuntimeProfile:
    """One resolved set of concurrency/budget knobs for a generation attempt."""

    author_concurrency: int
    render_concurrency: int
    illustration_budget: int
    illustration_timeout_seconds: int


def resolve_svg_runtime_profile(*, source_reflow: bool) -> GenerationRuntimeProfile:
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
            # The authored image knob (IMAGE_GENERATION_TIMEOUT_SECONDS) is the
            # per-call default, so one picture is bounded identically in both
            # drivers unless an operator narrows it for svg.
            or int(get_image_generation_timeout_seconds())
        ),
    )


__all__ = ["GenerationRuntimeProfile", "resolve_svg_runtime_profile"]
