"""Closed identity of the ordered authored HTML rendered by a public export."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExportRenderSceneProvenance(BaseModel):
    """Source identity, not an assertion that rendering or quality passed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["authored-html-render-scene/v1"]
    scene_digest: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    ordered_slide_count: int = Field(ge=1)
