"""Raster region value types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OCRWord:
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float | None
    line_key: tuple[int, int, int]


@dataclass(frozen=True)
class RegionCandidate:
    role: str
    bbox: tuple[int, int, int, int]
    text: str | None = None
    word_indices: tuple[int, ...] = ()
    reason: str = ""
