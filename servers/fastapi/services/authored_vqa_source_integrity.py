"""Source-verbatim admission for post-VQA slide candidates."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping

from services.source_content.browser_readback import BrowserSourceReadbackError
from services.authored_admission_session import producer_scope


LOGGER = logging.getLogger(__name__)


async def source_vqa_candidate_integrity_passes(
    slide_index: int,
    candidate_html: str,
    candidate_metrics: Mapping[str, object],
    validator: Callable[[int, str, Mapping[str, object]], Awaitable[None]],
) -> bool:
    """Reject a VQA rewrite locally when it damages immutable source bindings."""

    try:
        with producer_scope("vqa_repair"):
            await validator(slide_index, candidate_html, candidate_metrics)
    except BrowserSourceReadbackError:
        LOGGER.warning(
            "[authored] rejected post-VQA source-integrity candidate slide=%d",
            slide_index + 1,
        )
        return False
    return True


__all__ = ["source_vqa_candidate_integrity_passes"]
