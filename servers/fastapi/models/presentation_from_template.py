from typing import Annotated, List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReviewScope = Literal["all", "current", "subset"]
SlideIndex = Annotated[int, Field(strict=True, ge=0)]


class AuthoredQualityReviewRequest(BaseModel):
    """Review and automatically repair selected pages of an AI-authored deck."""

    scope: ReviewScope = "all"
    slide_indices: List[SlideIndex] = Field(default_factory=list)
    mode: Literal["analyze_and_fix"] = "analyze_and_fix"
    # Retained for wire compatibility with older clients. ``quality.review`` is
    # maximum-repair-only and ignores false; the server kill switch remains the
    # authoritative operational rollback.
    slidespec_opt_in: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def normalize_selection(self) -> "AuthoredQualityReviewRequest":
        self.slide_indices = sorted(set(self.slide_indices))
        if self.scope == "all":
            if self.slide_indices:
                raise ValueError("All-pages review does not accept slide_indices")
        elif self.scope == "current":
            if len(self.slide_indices) != 1:
                raise ValueError(
                    "Current-page review requires exactly one slide index"
                )
        elif not self.slide_indices:
            raise ValueError("Subset review requires at least one slide index")
        return self


class AuthoredPageRemakePreviewRequest(BaseModel):
    """Create one detached full-page candidate without mutating the live deck."""

    # Reserved for a future user-authored note. Keeping the request explicit
    # prevents clients from smuggling an all/subset scope into this one-page API.
    instruction: str | None = Field(default=None, max_length=500)
    mode: Literal["author", "remeasure_source", "local_svg"] = "author"
    source_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    svg_content: str | None = Field(default=None, min_length=1, max_length=2 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_remeasurement_binding(self):
        if self.mode in {"remeasure_source", "local_svg"} and (not self.source_digest or self.instruction is not None):
            raise ValueError("Local SVG measurement requires its exact source digest and no author instruction")
        if self.mode == "author" and self.source_digest is not None:
            raise ValueError("Author preview does not accept a source remeasurement digest")
        if (self.mode == "local_svg") != (self.svg_content is not None):
            raise ValueError("Only local_svg preview requires candidate SVG content")
        return self

    model_config = ConfigDict(extra="forbid", strict=True)


class AuthoredPageRemakePublishRequest(BaseModel):
    """Publish one completed preview task for the exact path-level slide."""

    preview_task_id: str = Field(min_length=6, max_length=160)

    model_config = ConfigDict(extra="forbid", strict=True)
