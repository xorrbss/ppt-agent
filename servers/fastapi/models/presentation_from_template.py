import re
import uuid
from typing import Annotated, List, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReviewScope = Literal["all", "current", "subset"]
SlideIndex = Annotated[int, Field(strict=True, ge=0)]


def parse_review_page_range(value: str, *, slide_count: int) -> list[int]:
    """Parse a 1-based chat page range into sorted, unique 0-based indices.

    The accepted grammar is a comma-separated list of pages or inclusive
    ascending ranges, for example ``3,5-8,12``.  Keeping this parser at the API
    model boundary gives chat and other clients one canonical normalization
    contract while the review pipeline continues to use zero-based indices.
    """

    if slide_count < 1:
        raise ValueError("slide_count must be positive")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Page range is required")

    pages: set[int] = set()
    for raw_token in value.split(","):
        token = raw_token.strip()
        match = re.fullmatch(r"([0-9]+)(?:\s*-\s*([0-9]+))?", token)
        if match is None:
            raise ValueError(f"Invalid page range token: {token or '<empty>'}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1:
            raise ValueError("Page numbers must be positive")
        if end < start:
            raise ValueError("Page ranges must be ascending")
        if end > slide_count:
            raise ValueError("Page number is outside the presentation")
        pages.update(range(start, end + 1))
    return [page - 1 for page in sorted(pages)]

class SlideContentUpdate(BaseModel):
    index: int
    content: dict


class EditPresentationRequest(BaseModel):
    presentation_id: uuid.UUID
    slides: List[SlideContentUpdate]
    export_as: Literal["pptx", "pdf"] = "pptx"


class RetemplatePresentationRequest(BaseModel):
    """Re-author a saved authored deck with another authored style preset."""

    authored_style: str
    vision_qa: bool = False


class AuthoredQualityReviewRequest(BaseModel):
    """Review and automatically repair selected pages of an AI-authored deck."""

    scope: ReviewScope = "all"
    slide_indices: List[SlideIndex] = Field(default_factory=list)
    mode: Literal["analyze_and_fix"] = "analyze_and_fix"
    # SlideSpec is the default repair path. Set false for a per-request legacy
    # comparison; the server kill switch remains the authoritative rollback.
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

    model_config = ConfigDict(extra="forbid", strict=True)


class AuthoredPageRemakePublishRequest(BaseModel):
    """Publish one completed preview task for the exact path-level slide."""

    preview_task_id: str = Field(min_length=6, max_length=160)

    model_config = ConfigDict(extra="forbid", strict=True)
