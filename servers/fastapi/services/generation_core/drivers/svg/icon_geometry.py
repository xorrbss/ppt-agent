"""Geometry for icon flattening: affine transforms, path parsing, curve sampling.

Every sampler returns points already mapped through the current transform
matrix, in the icon's viewBox units, and bounds the chord error by
``tolerance`` (also in viewBox units). See ``icon_flatten.py`` for the element
walk that drives these helpers.
"""
from __future__ import annotations

import math
import re

_MAX_SEGMENTS = 256
_EPS = 1e-9
_NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_SEPARATORS = " \t\r\n,"
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m, n):
    """Compose affine matrices: apply ``n`` first, then ``m``."""
    a, b, c, d, e, f = m
    p, q, r, s, t, u = n
    return (a * p + c * q, b * p + d * q, a * r + c * s, b * r + d * s, a * t + c * u + e, b * t + d * u + f)


def apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def numbers(value: str) -> list[float]:
    rest = _NUMBER_RE.sub(" ", value)
    if rest.strip(_SEPARATORS):
        raise ValueError("icon_number_invalid")
    values = [float(x) for x in _NUMBER_RE.findall(value)]
    if any(not math.isfinite(x) for x in values):
        raise ValueError("icon_number_invalid")
    return values


def parse_transform(value: str):
    matrix = IDENTITY
    consumed = 0
    for match in re.finditer(r"\s*,?\s*([a-zA-Z]+)\s*\(([^()]*)\)", value):
        if match.start() != consumed:
            raise ValueError("icon_transform_invalid")
        consumed = match.end()
        name, args = match[1], numbers(match[2])
        if name == "translate" and len(args) in {1, 2}:
            step = (1, 0, 0, 1, args[0], args[1] if len(args) == 2 else 0)
        elif name == "scale" and len(args) in {1, 2}:
            step = (args[0], 0, 0, args[-1], 0, 0)
        elif name == "matrix" and len(args) == 6:
            step = tuple(args)
        elif name == "rotate" and len(args) in {1, 3}:
            cos, sin = math.cos(math.radians(args[0])), math.sin(math.radians(args[0]))
            step = (cos, sin, -sin, cos, 0, 0)
            if len(args) == 3:
                step = mul(mul((1, 0, 0, 1, args[1], args[2]), step), (1, 0, 0, 1, -args[1], -args[2]))
        elif name in {"skewX", "skewY"} and len(args) == 1:
            k = math.tan(math.radians(args[0]))
            step = (1, 0, k, 1, 0, 0) if name == "skewX" else (1, k, 0, 1, 0, 0)
        else:
            raise ValueError("icon_transform_unsupported")
        matrix = mul(matrix, step)
    if value[consumed:].strip():
        raise ValueError("icon_transform_invalid")
    return matrix


def max_scale(m) -> float:
    a, b, c, d = m[:4]
    total, det = a * a + b * b + c * c + d * d, a * d - b * c
    return math.sqrt(max(0.0, (total + math.sqrt(max(0.0, total * total - 4 * det * det))) / 2))


def is_similarity(m) -> bool:
    a, b, c, d = m[:4]
    return abs(a * a + b * b - c * c - d * d) < 1e-6 * (a * a + b * b + 1) and abs(a * c + b * d) < 1e-6


def is_axis_aligned(m) -> bool:
    return abs(m[1]) < _EPS and abs(m[2]) < _EPS


def _segments(sweep: float, radius: float, tolerance: float) -> int:
    if radius <= tolerance:
        return max(1, math.ceil(abs(sweep) / (math.pi / 2)))
    step = 2 * math.acos(1 - tolerance / radius)
    return max(1, min(_MAX_SEGMENTS, math.ceil(abs(sweep) / step)))


def _bezier(points, tolerance):
    """Sample a quadratic/cubic Bezier (output coords) using Wang's formula."""
    degree = len(points) - 1
    second = max(math.hypot(points[i][0] - 2 * points[i + 1][0] + points[i + 2][0],
                            points[i][1] - 2 * points[i + 1][1] + points[i + 2][1]) for i in range(degree - 1))
    count = max(1, min(_MAX_SEGMENTS, math.ceil(math.sqrt(degree * (degree - 1) / 8 * second / tolerance))))
    result = []
    for i in range(1, count + 1):
        t = i / count
        row = list(points)
        while len(row) > 1:
            row = [((1 - t) * p[0] + t * q[0], (1 - t) * p[1] + t * q[1]) for p, q in zip(row, row[1:], strict=False)]
        result.append(row[0])
    return result


def _arc(start, rx, ry, angle, large, sweep, end, matrix, tolerance):
    """SVG elliptical arc (endpoint parameterisation, spec F.6.5) in local coords."""
    (x1, y1), (x2, y2) = start, end
    if math.hypot(x2 - x1, y2 - y1) < _EPS:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < _EPS or ry < _EPS:
        return [apply(matrix, x2, y2)]
    phi = math.radians(angle % 360)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    xp, yp = cos * dx + sin * dy, -sin * dx + cos * dy
    lam = xp * xp / (rx * rx) + yp * yp / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp
    den = rx * rx * yp * yp + ry * ry * xp * xp
    coef = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = coef * rx * yp / ry, -coef * ry * xp / rx
    cx, cy = cos * cxp - sin * cyp + (x1 + x2) / 2, sin * cxp + cos * cyp + (y1 + y2) / 2
    theta = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
    delta = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx) - theta
    if sweep and delta < 0:
        delta += 2 * math.pi
    elif not sweep and delta > 0:
        delta -= 2 * math.pi
    count = _segments(delta, max(rx, ry) * max_scale(matrix), tolerance)
    result = []
    for i in range(1, count + 1):
        t = theta + delta * i / count
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        result.append(apply(matrix, cx + cos * ex - sin * ey, cy + sin * ex + cos * ey))
    result[-1] = apply(matrix, x2, y2)
    return result


def _tokenize_path(d: str):
    pos, size, command, out = 0, len(d), None, []
    while True:
        while pos < size and d[pos] in _SEPARATORS:
            pos += 1
        if pos >= size:
            return out
        if d[pos].isalpha():
            command = d[pos]
            pos += 1
            if command.upper() not in _ARITY:
                raise ValueError("icon_path_command_unsupported")
            if command in "Zz":
                out.append((command, []))
                continue
        elif command is None or command in "Zz":
            raise ValueError("icon_path_invalid")
        args = []
        for index in range(_ARITY[command.upper()]):
            while pos < size and d[pos] in _SEPARATORS:
                pos += 1
            if command in "Aa" and index in {3, 4}:
                if pos >= size or d[pos] not in "01":
                    raise ValueError("icon_path_arc_flag_invalid")
                args.append(float(d[pos]))
                pos += 1
                continue
            match = _NUMBER_RE.match(d, pos)
            if not match:
                raise ValueError("icon_path_arguments_invalid")
            args.append(float(match[0]))
            pos = match.end()
        out.append((command, args))
        if command in "Mm":
            command = "L" if command == "M" else "l"


def path_subpaths(d: str, matrix, tolerance):
    """Return ``[(points, closed)]`` with points already in output coordinates."""
    result, points = [], None
    cur = start = (0.0, 0.0)
    control, previous = None, ""
    for command, a in _tokenize_path(d):
        up = command.upper()
        ox, oy = cur if command != up else (0.0, 0.0)
        if up == "M":
            if points:
                result.append((points, False))
            cur = start = (ox + a[0], oy + a[1])
            points = [apply(matrix, *cur)]
            previous = up
            continue
        if up == "Z":
            if points:
                result.append((points, True))
            points, cur, previous = None, start, up
            continue
        if points is None:
            points = [apply(matrix, *cur)]
        if up in {"L", "T", "H", "V"}:
            if up == "H":
                target = (ox + a[0], cur[1])
            elif up == "V":
                target = (cur[0], oy + a[0])
            else:
                target = (ox + a[0], oy + a[1])
            if up == "T":
                ctrl = (2 * cur[0] - control[0], 2 * cur[1] - control[1]) if previous in {"Q", "T"} else cur
                points += _bezier([apply(matrix, *p) for p in (cur, ctrl, target)], tolerance)
                control = ctrl
            else:
                points.append(apply(matrix, *target))
        elif up in {"C", "S"}:
            if up == "C":
                c1, c2, target = (ox + a[0], oy + a[1]), (ox + a[2], oy + a[3]), (ox + a[4], oy + a[5])
            else:
                c1 = (2 * cur[0] - control[0], 2 * cur[1] - control[1]) if previous in {"C", "S"} else cur
                c2, target = (ox + a[0], oy + a[1]), (ox + a[2], oy + a[3])
            points += _bezier([apply(matrix, *p) for p in (cur, c1, c2, target)], tolerance)
            control = c2
        elif up == "Q":
            ctrl, target = (ox + a[0], oy + a[1]), (ox + a[2], oy + a[3])
            points += _bezier([apply(matrix, *p) for p in (cur, ctrl, target)], tolerance)
            control = ctrl
        else:
            target = (ox + a[5], oy + a[6])
            points += _arc(cur, a[0], a[1], a[2], a[3], a[4], target, matrix, tolerance)
        cur, previous = target, up
    if points:
        result.append((points, False))
    return result


def ellipse_points(cx, cy, rx, ry, matrix, tolerance):
    count = max(8, _segments(2 * math.pi, max(rx, ry) * max_scale(matrix), tolerance))
    return [apply(matrix, cx + rx * math.cos(2 * math.pi * i / count), cy + ry * math.sin(2 * math.pi * i / count))
            for i in range(count)]


def rounded_rect_points(x, y, w, h, rx, ry, matrix, tolerance):
    corners = ((x + w - rx, y + ry, -math.pi / 2), (x + w - rx, y + h - ry, 0.0),
               (x + rx, y + h - ry, math.pi / 2), (x + rx, y + ry, math.pi))
    count = _segments(math.pi / 2, max(rx, ry) * max_scale(matrix), tolerance)
    angles = [(cx, cy, base + math.pi / 2 * i / count) for cx, cy, base in corners for i in range(count + 1)]
    return [apply(matrix, cx + rx * math.cos(t), cy + ry * math.sin(t)) for cx, cy, t in angles]
