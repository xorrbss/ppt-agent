"""Read back a deck's persisted illustration plan.

The plan itself is no longer resolved here: it is derived from the deck's
infographic plan (``services/authored_deck_infographic_plan.py``), where a
slide carries a generated picture exactly when its figure is a ``scene``. What
remains is the read side, which a single-page remake and the quality review
use to replay the deck's original decision rather than re-deciding it.
"""

from __future__ import annotations

import logging
from typing import Optional

from models.authored_illustration_plan import AuthoredIllustrationPlan

LOGGER = logging.getLogger(__name__)


def load_illustration_plan(theme: object) -> Optional[AuthoredIllustrationPlan]:
    """Rebuild a persisted plan, treating an unreadable one as absent."""
    if not isinstance(theme, dict):
        return None
    payload = theme.get("illustrationPlan")
    if not isinstance(payload, dict):
        return None
    try:
        return AuthoredIllustrationPlan.model_validate(payload)
    except Exception as error:  # noqa: BLE001 — persisted data is untrusted here
        LOGGER.warning("Persisted illustration plan is unusable: %s", error)
        return None


__all__ = [
    "load_illustration_plan",
]
