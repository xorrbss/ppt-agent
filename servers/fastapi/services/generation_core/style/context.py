"""Format-neutral style data shared by both drivers.

``StyleContext`` is built once per deck by :mod:`generation_core.style.build`
from the request, the resolved style preset and an optional master/design-reference
resolution. It carries no HTML or SVG markup -- only the tokens a driver's own
``project_style`` turns into its format's inputs (see
docs/generation-core-architecture-20260925.md section 2.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class BriefLine:
    """One line of style-preset guidance, tagged with the formats it is safe for.

    A line naming HTML/CSS/DOM mechanics (``position:absolute``, ``flexbox``,
    an inline exemplar) does not apply to a non-HTML driver; ``applies_to``
    records that instead of silently dropping the line, so a future consumer
    can decide what to do with format-specific guidance it does not need.
    """

    section: str
    text: str
    applies_to: frozenset[str]


@dataclass(frozen=True)
class Palette:
    primary: str
    background: str
    swatches: tuple[str, ...] = ()


@dataclass(frozen=True)
class FontChoice:
    heading: str
    body: str
    mono: str


@dataclass(frozen=True)
class StyleContext:
    """Preset + request brand + master/design-reference rules for one deck.

    ``master_rules`` / ``design_reference_rules`` are already-projected, format-
    neutral rule sets (see ``master_projection.py`` and the uploaded design reference):
    tokens, fonts and layout geometry, never markup. A driver's
    ``project_style`` turns them into its own author inputs.
    """

    preset_id: str
    palette: Palette
    fonts: FontChoice
    wordmark: str
    brief_lines: tuple[BriefLine, ...] = ()
    mapped_guidance: str = ""
    reference_images: Mapping[str, tuple[Mapping[str, str], ...]] = field(default_factory=dict)
    master_rules: Optional[Mapping[str, object]] = None
    master_pin: Optional[Mapping[str, object]] = None
    design_reference_rules: Optional[Mapping[str, object]] = None
