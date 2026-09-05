"""Startup warm-up for the authored HTML renderer.

Pays the renderer's cold start once at process boot instead of on the first
slide of the first real deck: a persistent Chromium bring-up in development's
reusable-Chrome mode (``AUTHORED_CHROME_REUSE``), or the worker binary spawn
plus DevToolsActivePort poll in production's external-worker mode (see
``utils.renderer_worker_policy.renderer_requires_external_worker``). Either
way this goes through the exact same entry point (``render_html_to_png``)
every authored slide render uses, so a warm-up failure reflects a real
renderer problem rather than a warm-up-specific code path.

Must never fail application startup: every error is caught and logged.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from utils.get_env_programme import is_authored_renderer_warmup_enabled

logger = logging.getLogger(__name__)

# A minimal, deliberately tiny (well under 1 KB) authored fragment. Admission
# only requires a well-formed allowlisted document; the actual pixels are
# discarded, so the fragment need not resemble a real slide.
_WARMUP_HTML = (
    '<div style="width:1280px;height:720px;background:#ffffff;'
    'display:flex;align-items:center;justify-content:center;'
    'font-family:sans-serif;font-size:1px;color:#ffffff;">'
    "renderer warmup"
    "</div>"
)
_WARMUP_TIMEOUT_SECONDS = 30


async def warm_up_authored_renderer(
    render: Callable[[str, int], Awaitable[bytes]] | None = None,
) -> None:
    """Render one throwaway slide through the normal render entry point.

    ``render`` is injectable for tests; production omits it and this resolves
    to ``utils.slide_capture.render_html_to_png`` -- the same function every
    authored slide render calls -- imported lazily so importing this module
    (and therefore ``api.lifespan``) never pays that import's cost unless a
    warm-up actually runs.
    """

    started = time.monotonic()
    if not is_authored_renderer_warmup_enabled():
        logger.info(
            "renderer_warmup skipped reason=disabled duration_seconds=%.3f",
            time.monotonic() - started,
        )
        return
    if render is None:
        from utils.slide_capture import render_html_to_png as render
    try:
        await render(_WARMUP_HTML, _WARMUP_TIMEOUT_SECONDS)
    except Exception:
        logger.warning(
            "renderer_warmup failed duration_seconds=%.3f",
            time.monotonic() - started,
            exc_info=True,
        )
        return
    logger.info(
        "renderer_warmup ok duration_seconds=%.3f", time.monotonic() - started
    )


__all__ = ["warm_up_authored_renderer"]
