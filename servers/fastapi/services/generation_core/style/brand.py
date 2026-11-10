"""The shared design tokens injected into every page of one deck."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Brand:
    """The shared design tokens injected into every slide of one deck."""

    topic: str
    language: str = "Korean"
    primary: str = "#2563EB"
    fonts: str = "Noto Sans KR"
    wordmark: str = ""
    primary_is_explicit: bool = False
    fonts_are_explicit: bool = False
    # Decides the slide type scale: less text per slide means larger type.
    verbosity: str = "standard"
