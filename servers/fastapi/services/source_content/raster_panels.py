"""Bordered panel rectangles in a raster slide.

A panel is a pair of horizontal edges with matching extents whose ends are
joined by vertical edges. Captures of application screens are drawn as such
panels; the caller decides which panels are captures from their content.
"""

from __future__ import annotations

import numpy as np

EDGE_LEVEL = 8


def _row_runs(edges: np.ndarray, min_length: int) -> list[tuple[int, int, int]]:
    rules: list[tuple[int, int, int]] = []
    for row in np.flatnonzero(edges.sum(axis=1) >= min_length):
        padded = np.concatenate(([0], edges[row].astype(np.int8), [0]))
        changes = np.flatnonzero(np.diff(padded))
        runs: list[tuple[int, int]] = []
        for start, end in zip(changes[::2], changes[1::2], strict=False):
            if runs and start - runs[-1][1] <= 6:
                runs[-1] = (runs[-1][0], int(end))
            else:
                runs.append((int(start), int(end)))
        rules.extend((int(row), start, end) for start, end in runs if end - start >= min_length)
    collapsed: list[tuple[int, int, int]] = []
    for rule in sorted(rules):
        if (
            collapsed
            and rule[0] - collapsed[-1][0] <= 3
            and abs(rule[1] - collapsed[-1][1]) <= 8
            and abs(rule[2] - collapsed[-1][2]) <= 8
        ):
            continue
        collapsed.append(rule)
    return collapsed


def _vertical_support(vertical: np.ndarray, x: int, top: int, bottom: int) -> float:
    width = vertical.shape[1]
    best = 0.0
    for candidate in range(max(0, x - 4), min(width, x + 5)):
        column = vertical[top:bottom, candidate]
        if column.size:
            best = max(best, float(column.mean()))
    return best


def panel_rectangles(
    gray: np.ndarray, *, min_width: int = 120, min_height: int = 80
) -> list[tuple[int, int, int, int]]:
    """Return (x, y, w, h) rectangles whose four sides are visible edges."""

    horizontal = np.zeros(gray.shape, dtype=bool)
    horizontal[1:-1, :] = np.abs(gray[2:, :] - gray[:-2, :]) >= EDGE_LEVEL
    vertical = np.zeros(gray.shape, dtype=bool)
    vertical[:, 1:-1] = np.abs(gray[:, 2:] - gray[:, :-2]) >= EDGE_LEVEL
    rules = _row_runs(horizontal, min_width)
    found: list[tuple[int, int, int, int]] = []
    for i, (top, left, right) in enumerate(rules):
        for bottom, lower_left, lower_right in rules[i + 1 :]:
            if bottom - top < min_height:
                continue
            if abs(left - lower_left) > 8 or abs(right - lower_right) > 8:
                continue
            x0, x1 = min(left, lower_left), max(right, lower_right)
            if (
                _vertical_support(vertical, x0, top, bottom) < 0.7
                or _vertical_support(vertical, x1 - 1, top, bottom) < 0.7
            ):
                continue
            found.append((x0, top, x1 - x0, bottom - top))
    return found
