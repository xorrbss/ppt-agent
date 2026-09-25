"""Slide-count scaled export deadline for public operation admission.

The public control plane pins one absolute deadline per operation at admission
time and never recomputes it while the operation runs.  For export that single
budget has to cover every stage the operation is allowed to run: the
network-none render batches, the export bundle build and persist, the isolated
export worker (which for an editable-hybrid deck measures every slide in its
own browser before assembling OOXML), verification and publication.

Before this module every policy row carried a flat 600 s export budget while
the hybrid worker was granted a separate 1740 s constant, so a 35-slide hybrid
export -- measured at 894 s, i.e. ~25.5 s per slide (run-11 hybrid export
series, W22) -- outlived its own admission.  The budget is therefore::

    deadline = clamp(base + per_slide * slide_count, minimum, maximum)

Every term but the maximum is a registered runtime knob
(``PUBLIC_EXPORT_DEADLINE_*`` in ``constants/settings_registry.json``).  The
result is pinned into the admission row and is never recomputed, so a later
knob change cannot move a running deadline.  The isolated worker's grant is
then *derived* from that pinned row (``services/public_export_deadline.py``)
rather than from a second constant, which is what removes the mismatch.
"""

from __future__ import annotations

from utils.public_export_deadline_env import (
    get_public_export_deadline_base_seconds_env,
    get_public_export_deadline_min_seconds_env,
    get_public_export_deadline_per_slide_seconds_env,
)


# Measured 25.5 s per slide for a 35-slide editable-hybrid export (894 s wall,
# W22 report).  Rounded up.
PUBLIC_EXPORT_DEADLINE_PER_SLIDE_SECONDS = 26

# Fixed part not proportional to the deck: bundle build and persist, browser
# launch inside the worker, assembler verification, package store and
# publication.  A single-slide export measures 22-28 s end to end, so the
# fixed part is small; it is given headroom for a cold Chromium launch.
PUBLIC_EXPORT_DEADLINE_BASE_SECONDS = 120

# Floor: the historical flat budget.  No deck receives less than it used to,
# and an admission that could not learn the deck size (``slide_count = 0``)
# still gets a workable budget instead of the fixed part alone.
PUBLIC_EXPORT_DEADLINE_MIN_SECONDS = 600

# Ceiling: the trusted executor's export role budget
# (``services/trusted_executor/worker_dispatcher.py``, the outer ``min()``).
# It is not a runtime knob because the executor cap it mirrors is not one, and
# Alembic revision ``b4e6f8a0c2d5`` mirrors it into the ``public-default/v5``
# policy row; ``tests/unit/test_public_export_deadline.py`` fails when the
# two diverge.  About 65 slides reach it under the shipped terms.
PUBLIC_EXPORT_DEADLINE_MAX_SECONDS = 1800


def _slide_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("public_export_slide_count_must_be_nonnegative")
    return value


def _override(explicit: int | None, default: int) -> int:
    return explicit if explicit is not None else default


def public_export_per_slide_seconds() -> int:
    """Per-slide allowance, honouring the explicit runtime override."""

    return _override(
        get_public_export_deadline_per_slide_seconds_env(),
        PUBLIC_EXPORT_DEADLINE_PER_SLIDE_SECONDS,
    )


def public_export_base_seconds() -> int:
    """Fixed allowance, honouring the explicit runtime override."""

    return _override(
        get_public_export_deadline_base_seconds_env(),
        PUBLIC_EXPORT_DEADLINE_BASE_SECONDS,
    )


def public_export_min_seconds() -> int:
    """Floor, honouring the explicit runtime override."""

    return _override(
        get_public_export_deadline_min_seconds_env(),
        PUBLIC_EXPORT_DEADLINE_MIN_SECONDS,
    )


def public_export_deadline_seconds(slide_count: int) -> int:
    """Return the export budget for ``slide_count`` slides, in whole seconds.

    Pure in the deck size and the knobs resolved at call time.  Admission takes
    ``min(policy row ceiling, this)``, so a row that still carries the legacy
    flat 600 s (``public-default/v1``..``/v4``) is unchanged while
    ``public-default/v5`` raises the ceiling and lets the formula decide.
    """

    scaled = public_export_base_seconds() + public_export_per_slide_seconds() * (
        _slide_count(slide_count)
    )
    return min(
        PUBLIC_EXPORT_DEADLINE_MAX_SECONDS,
        max(public_export_min_seconds(), scaled),
    )


__all__ = [
    "PUBLIC_EXPORT_DEADLINE_BASE_SECONDS",
    "PUBLIC_EXPORT_DEADLINE_MAX_SECONDS",
    "PUBLIC_EXPORT_DEADLINE_MIN_SECONDS",
    "PUBLIC_EXPORT_DEADLINE_PER_SLIDE_SECONDS",
    "public_export_base_seconds",
    "public_export_deadline_seconds",
    "public_export_min_seconds",
    "public_export_per_slide_seconds",
]
