"""Validated slide-chrome colour grammar a style definition may declare.

A style YAML's ``composer_profile`` mapping (the key and its
``composer-style/v1`` version predate the withdrawn restyle composer's removal
and are kept so existing style files and stored style snapshots stay valid)
names header, table, footer and cover colours.  The SVG style adapter hands it
to the model as the ``chrome`` token set.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_HEX = r"^#[0-9A-Fa-f]{6}$"


class StyleChromeProfile(BaseModel):
    """Explicit reusable chrome grammar supplied by a selected style definition."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    version: Literal["composer-style/v1"] = "composer-style/v1"
    header_style: Literal["tab-band", "rule"] = "rule"
    header_fill: str = Field(pattern=_HEX)
    header_tab_fill: str = Field(pattern=_HEX)
    heading_color: str = Field(pattern=_HEX)
    surface_color: str = Field(pattern=_HEX)
    table_header_fill: str = Field(pattern=_HEX)
    table_header_text: str = Field(pattern=_HEX)
    footer_fill: str = Field(pattern=_HEX)
    footer_text: str = Field(pattern=_HEX)
    cover_alignment: Literal["left", "center"] = "left"
    border_radius: float = Field(default=0.0, ge=0.0, le=32.0)
    cover_fill: str | None = Field(default=None, pattern=_HEX)
    cover_text: str | None = Field(default=None, pattern=_HEX)
    cover_accent_fill: str | None = Field(default=None, pattern=_HEX)
    emphasis_color: str | None = Field(default=None, pattern=_HEX)


__all__ = ["StyleChromeProfile"]
