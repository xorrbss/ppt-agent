"""Pure parsers for string-valued configuration knobs.

This module is the single place that turns the raw text of an environment
variable (or a managed runtime setting) into an ``int``, ``float`` or ``bool``.
It reads nothing itself: ``utils.get_env.env_int`` / ``env_float`` /
``env_bool`` resolve the text through ``get_setting_env`` and hand it here, so
admin-managed values and process environment values are parsed identically.

Before 2026-09-29 the backend carried eighteen private copies of this parser
with four different contracts for the same malformed value (silently default,
warn and default, raise ``ValueError``, raise ``SourceLockedError``) -- see
``docs/refactoring-audit-20260929.md`` section 4.  Each call site now names
its contract explicitly through ``on_invalid``:

``"default"``
    Unset, blank, malformed or out-of-range text yields ``default`` silently.
``"warn"``
    As ``"default"``, but a malformed or out-of-range value is logged once per
    call at WARNING level so an operator typo is visible.
``"clamp"``
    Malformed text yields ``default``; an out-of-range number is clamped to
    the nearest bound instead of being discarded.
``"raise"``
    Malformed or out-of-range text raises :class:`EnvValueError`, whose
    ``code`` is a stable snake_case token (``NAME_must_be_a_positive_integer``,
    ``NAME_between_1_and_500``) suitable for an error envelope.

Unset or blank text is never an error under any policy: every knob has a
default, and an empty string means "not configured".

Keep this module free of ``services``/``api`` imports; it sits below both.
"""

from __future__ import annotations

import logging
import math
from typing import Literal, TypeVar

logger = logging.getLogger(__name__)

InvalidPolicy = Literal["default", "warn", "raise", "clamp"]

TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
FALSY_VALUES = frozenset({"0", "false", "no", "off"})

_T = TypeVar("_T")
_Number = TypeVar("_Number", int, float)


class EnvValueError(ValueError):
    """A configuration knob holds text that its policy refuses.

    ``str(error)`` equals ``code`` so existing callers that raised
    ``ValueError(f"{name}_must_be_a_positive_integer")`` keep their message.
    """

    def __init__(self, *, name: str, code: str, raw: str) -> None:
        super().__init__(code)
        self.name = name
        self.code = code
        self.raw = raw


def _range_code(name: str, kind: str, minimum: object, maximum: object) -> str:
    if minimum is not None and maximum is not None:
        return f"{name}_between_{minimum}_and_{maximum}"
    if minimum == 1 and kind == "integer":
        return f"{name}_must_be_a_positive_integer"
    if minimum == 0:
        return f"{name}_must_be_a_nonnegative_{kind}"
    if minimum is not None:
        return f"{name}_must_be_at_least_{minimum}"
    if maximum is not None:
        return f"{name}_must_be_at_most_{maximum}"
    return f"{name}_must_be_a_{kind}"


def _invalid(
    *,
    name: str,
    raw: str,
    code: str,
    default: _T,
    on_invalid: InvalidPolicy,
    reason: str,
) -> _T:
    if on_invalid == "raise":
        raise EnvValueError(name=name, code=code, raw=raw)
    if on_invalid == "warn":
        logger.warning("Ignoring %s %s=%r", reason, name, raw)
    return default


def _bounded(
    value: _Number,
    *,
    name: str,
    raw: str,
    kind: str,
    default: _T,
    minimum: _Number | None,
    maximum: _Number | None,
    on_invalid: InvalidPolicy,
) -> _Number | _T:
    below = minimum is not None and value < minimum
    above = maximum is not None and value > maximum
    if not (below or above):
        return value
    if on_invalid == "clamp":
        if below:
            return minimum  # type: ignore[return-value]
        return maximum  # type: ignore[return-value]
    return _invalid(
        name=name,
        raw=raw,
        code=_range_code(name, kind, minimum, maximum),
        default=default,
        on_invalid=on_invalid,
        reason="out-of-range",
    )


def parse_int(
    raw: str | None,
    *,
    name: str,
    default: _T,
    minimum: int | None = None,
    maximum: int | None = None,
    on_invalid: InvalidPolicy = "default",
) -> int | _T:
    """Parse integer knob text under ``on_invalid`` (see module docstring).

    ``default`` may be ``None`` for "explicit override or nothing" knobs.
    """
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return _invalid(
            name=name,
            raw=raw or "",
            code=_range_code(name, "integer", minimum, maximum),
            default=default,
            on_invalid=on_invalid,
            reason="invalid",
        )
    return _bounded(
        value,
        name=name,
        raw=raw or "",
        kind="integer",
        default=default,
        minimum=minimum,
        maximum=maximum,
        on_invalid=on_invalid,
    )


def parse_float(
    raw: str | None,
    *,
    name: str,
    default: _T,
    minimum: float | None = None,
    maximum: float | None = None,
    on_invalid: InvalidPolicy = "default",
) -> float | _T:
    """Parse a finite float knob; ``nan``/``inf`` count as malformed."""
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        return _invalid(
            name=name,
            raw=raw or "",
            code=_range_code(name, "number", minimum, maximum),
            default=default,
            on_invalid=on_invalid,
            reason="invalid",
        )
    return _bounded(
        value,
        name=name,
        raw=raw or "",
        kind="number",
        default=default,
        minimum=minimum,
        maximum=maximum,
        on_invalid=on_invalid,
    )


def parse_bool(raw: str | None, *, default: bool) -> bool:
    """``1/true/yes/on`` -> True, ``0/false/no/off`` -> False, else ``default``.

    Matching is case-insensitive after stripping whitespace.  Unrecognised
    text falls back to ``default`` rather than to False so that a knob that is
    on by default cannot be switched off by a typo.
    """
    text = (raw or "").strip().lower()
    if not text:
        return default
    if text in TRUTHY_VALUES:
        return True
    if text in FALSY_VALUES:
        return False
    return default


def is_truthy(raw: str | None) -> bool:
    """Strict opt-in: only the truthy set enables; anything else is off."""
    return (raw or "").strip().lower() in TRUTHY_VALUES
