from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


MAX_PRESENTATION_THEME_BYTES = 256 * 1024


class PresentationCreateTheme(BaseModel):
    """Allowlisted presentation theme persisted by chat-first admission."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=2048)
    user: str = Field(min_length=1, max_length=256)
    logo: Optional[str] = Field(default=None, max_length=512)
    logo_url: Optional[str] = Field(default=None, max_length=4096)
    company_name: Optional[str] = Field(default=None, max_length=256)
    data: dict[str, Any]

    @field_validator("id", "name", "user")
    @classmethod
    def reject_blank_identifiers(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def normalize_presentation_create_theme(theme: Optional[dict]) -> Optional[dict]:
    if theme is None:
        return None
    try:
        normalized = PresentationCreateTheme.model_validate(theme).model_dump()
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, ValidationError) as error:
        raise HTTPException(
            status_code=422,
            detail="invalid_presentation_theme",
        ) from error
    if len(encoded) > MAX_PRESENTATION_THEME_BYTES:
        raise HTTPException(status_code=422, detail="presentation_theme_too_large")
    return normalized


def attachment_file_name(file_path: str) -> str:
    """Return only the final path component, regardless of source OS."""

    return PurePosixPath(file_path.replace("\\", "/")).name
