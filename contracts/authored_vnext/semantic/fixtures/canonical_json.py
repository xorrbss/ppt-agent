"""Canonical JSON and SHA-256 helpers shared by semantic contract fixtures."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal
from typing import Any


_NUMBER_RE = re.compile(r"^(?P<whole>\d+)(?:\.(?P<fraction>\d+))?(?:e(?P<exponent>[+-]?\d+))?$", re.I)


def canonical_number(value: int | float) -> str:
    """Render a finite JSON number identically to the JS helper.

    Python's ``repr`` and JavaScript's ``String`` both expose the shortest
    round-trippable decimal, but differ in exponent padding and fixed-point
    cutovers. Parsing that decimal and emitting a normalized coefficient
    removes those runtime-specific details.
    """

    if isinstance(value, bool):
        raise TypeError("booleans are not numbers")
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("canonical JSON only accepts finite numbers")
    if value == 0:
        return "0"
    negative = value < 0
    source = repr(abs(value)).lower()
    match = _NUMBER_RE.fullmatch(source)
    if match is None:
        # Decimal is only a guard for unusual integer subclasses; normal JSON
        # values use repr and always match the grammar above.
        source = format(Decimal(abs(value)), "f")
        match = _NUMBER_RE.fullmatch(source)
    if match is None:
        raise TypeError(f"unsupported number: {value!r}")
    whole = match.group("whole")
    fraction = match.group("fraction") or ""
    explicit_exponent = int(match.group("exponent") or "0")
    digits = whole + fraction
    first_non_zero = next((index for index, char in enumerate(digits) if char != "0"), None)
    if first_non_zero is None:
        return "0"
    digits = digits[first_non_zero:].rstrip("0")
    exponent = explicit_exponent + len(whole) - first_non_zero - 1
    coefficient = digits if len(digits) == 1 else f"{digits[0]}.{digits[1:]}"
    body = coefficient if exponent == 0 else f"{coefficient}e{exponent}"
    return f"-{body}" if negative else body


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be", "surrogatepass")


def canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(unicodedata.normalize("NFC", value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float)):
        return canonical_number(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        normalized = sorted(
            ((unicodedata.normalize("NFC", str(key)), item) for key, item in value.items()),
            key=lambda entry: _utf16_key(entry[0]),
        )
        if len({key for key, _ in normalized}) != len(normalized):
            raise TypeError("canonical JSON object has normalization-colliding keys")
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":")) + ":" + canonical_json(item)
            for key, item in normalized
        ) + "}"
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()
