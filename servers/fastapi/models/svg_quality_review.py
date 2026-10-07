"""Advisory review of saved SVG pages without authoring replacements."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SlideIndex = Annotated[int, Field(strict=True, ge=0)]


class SvgQualityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["all", "current", "subset"] = "all"
    slide_indices: list[SlideIndex] = Field(default_factory=list)
    mode: Literal["analyze_only"] = "analyze_only"

    @model_validator(mode="after")
    def normalize_selection(self):
        self.slide_indices = sorted(set(self.slide_indices))
        if self.scope == "all" and self.slide_indices:
            raise ValueError("All-pages review does not accept slide_indices")
        if self.scope == "current" and len(self.slide_indices) != 1:
            raise ValueError("Current-page review requires exactly one slide index")
        if self.scope == "subset" and not self.slide_indices:
            raise ValueError("Subset review requires at least one slide index")
        return self
