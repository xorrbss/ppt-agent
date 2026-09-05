"""Clamp the content brief's stage budget to a share of the operation deadline.

The brief's own wall ceiling (``CONTENT_BRIEF_TOTAL_BUDGET_SECONDS``, 1500 s)
was chosen against the brief alone.  Its former value happened to equal the
whole deck's base budget, so on a small deck a slow brief could legally spend
everything the operation had been granted and leave nothing for the outline,
the authoring waves, the repair rounds or the review.

The fix keeps the brief policy ignorant of admission: the *caller* -- which is
already inside the operation and knows its deadline -- passes the seconds it
has left, and this module turns that into a reduced
:class:`ContentBriefTimeoutPolicy`.

The share
---------
``CONTENT_BRIEF_OPERATION_DEADLINE_SHARE`` defaults to **0.25**: at most a
quarter of what is left may go to the brief, so three quarters always survive
for the six stages behind it.  A quarter is deliberately above the brief's
modelled share of a full deck budget (1500 s of a 6,622 s 24-slide budget, i.e.
23%), so on a normal deck the clamp never binds and the brief keeps its full
1500 s; it binds only where it must, on decks small enough that the brief would
otherwise eat the deck.  Where the clamp leaves the retry less than its floor
(``content_brief_policy``), the stage falls back at once rather than spending
it.  Nothing here can *raise* the brief's budget.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from utils.get_env import get_setting_env

if TYPE_CHECKING:  # the brief policy imports this module; keep the edge one-way
    from api.v1.ppt.endpoints.content_brief_policy import ContentBriefTimeoutPolicy

LOGGER = logging.getLogger(__name__)

OPERATION_DEADLINE_SHARE_ENV = "CONTENT_BRIEF_OPERATION_DEADLINE_SHARE"

#: Fraction of the remaining operation deadline the brief stage may spend.
DEFAULT_OPERATION_DEADLINE_SHARE = 0.25


def operation_deadline_share() -> float:
    """Read the share knob; anything outside ``(0, 1]`` falls back to the default."""

    raw = (get_setting_env(OPERATION_DEADLINE_SHARE_ENV) or "").strip()
    if not raw:
        return DEFAULT_OPERATION_DEADLINE_SHARE
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning(
            "Ignoring non-numeric %s=%r; using %.2f",
            OPERATION_DEADLINE_SHARE_ENV,
            raw,
            DEFAULT_OPERATION_DEADLINE_SHARE,
        )
        return DEFAULT_OPERATION_DEADLINE_SHARE
    if not 0.0 < value <= 1.0:
        return DEFAULT_OPERATION_DEADLINE_SHARE
    return value


def clamp_brief_policy_to_operation(
    policy: "ContentBriefTimeoutPolicy",
    remaining_operation_seconds: float | None,
) -> "ContentBriefTimeoutPolicy":
    """Return ``policy`` with its wall ceilings clamped to the operation's share.

    ``remaining_operation_seconds`` is ``None`` outside a public operation (the
    outline SSE stream, a local run), and the policy is then returned
    unchanged.  A non-positive remaining budget clamps the stage to zero, which
    makes the brief fall back to the raw source immediately instead of
    consuming a deadline that has already run out.
    """

    if remaining_operation_seconds is None:
        return policy
    allowance = max(0.0, float(remaining_operation_seconds)) * operation_deadline_share()
    if allowance >= policy.total_budget_seconds and allowance >= policy.max_seconds:
        return policy
    return replace(
        policy,
        max_seconds=min(policy.max_seconds, allowance),
        total_budget_seconds=min(policy.total_budget_seconds, allowance),
    )


__all__ = [
    "DEFAULT_OPERATION_DEADLINE_SHARE",
    "OPERATION_DEADLINE_SHARE_ENV",
    "clamp_brief_policy_to_operation",
    "operation_deadline_share",
]
