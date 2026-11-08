"""Parse the uniform-scale SVG ``transform`` subset shared by native chart readers.

The service-side native geometry reader and the browser-capture chart proof
both need the same root transform interpretation. It lives in ``utils`` so the
capture side does not import ``services`` (see the structural layering gate).
"""

from __future__ import annotations

import math
import re

UNSUPPORTED_TRANSFORM = "native_group_transform_unsupported"

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_FUNCTION = re.compile(r"([A-Za-z]+)\s*\(([^()]*)\)")


class UniformTransformUnsupported(ValueError):
    """The transform is outside the translate/uniform-scale subset."""

    def __init__(self) -> None:
        super().__init__(UNSUPPORTED_TRANSFORM)


def compose_uniform_transform(parent, child):
    dx, dy, scale = parent
    tx, ty, factor = child
    result = (dx + scale * tx, dy + scale * ty, scale * factor)
    if not all(math.isfinite(value) for value in result) or result[2] <= 0:
        raise UniformTransformUnsupported()
    return result


def parse_uniform_transform(value):
    """Return ``(dx, dy, scale)`` for a transform attribute value (``None`` is identity)."""
    if not value:
        return (0.0, 0.0, 1.0)
    result, end = (0.0, 0.0, 1.0), 0
    for match in _FUNCTION.finditer(value):
        if value[end:match.start()].strip():
            raise UniformTransformUnsupported()
        raw = match[2].strip()
        if raw.startswith(",") or raw.endswith(",") or re.search(r",\s*,", raw):
            raise UniformTransformUnsupported()
        tokens = re.split(r"[\s,]+", raw) if raw else []
        if not tokens or any(not _NUMBER.fullmatch(token) for token in tokens):
            raise UniformTransformUnsupported()
        numbers = [float(token) for token in tokens]
        if not all(math.isfinite(number) for number in numbers):
            raise UniformTransformUnsupported()
        name = match[1]
        if name == "translate" and len(numbers) in {1, 2}:
            child = (numbers[0], numbers[1] if len(numbers) == 2 else 0.0, 1.0)
        elif name == "scale" and len(numbers) in {1, 2} and (len(numbers) == 1 or numbers[0] == numbers[1]) and numbers[0] > 0:
            child = (0.0, 0.0, numbers[0])
        elif (name == "matrix" and len(numbers) == 6 and numbers[1] == 0 and numbers[2] == 0
              and numbers[0] == numbers[3] and numbers[0] > 0):
            child = (numbers[4], numbers[5], numbers[0])
        else:
            raise UniformTransformUnsupported()
        result = compose_uniform_transform(result, child)
        end = match.end()
    if not end or value[end:].strip():
        raise UniformTransformUnsupported()
    return result
