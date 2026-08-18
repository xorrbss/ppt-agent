"""Request shape for the multi-page authored remake preview fan-out."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuthoredBatchRemakePreviewRequest(BaseModel):
    """Preview several pages at once; publication stays a separate approval.

    The page set is explicit and bounded.  Keeping the scope out of the request
    body - no "all", no range - means a batch can never grow beyond the pages a
    user actually ticked.
    """

    slide_indexes: list[int] = Field(min_length=1, max_length=20)
    instruction: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _validate_slide_indexes(self) -> "AuthoredBatchRemakePreviewRequest":
        if any(index < 0 for index in self.slide_indexes):
            raise ValueError("slide_indexes must be non-negative")
        if len(set(self.slide_indexes)) != len(self.slide_indexes):
            raise ValueError("slide_indexes must be unique")
        return self
