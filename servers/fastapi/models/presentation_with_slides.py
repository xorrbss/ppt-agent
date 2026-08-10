from datetime import datetime
import re
from typing import Any, List, Literal, Optional
import uuid

from pydantic import BaseModel, field_validator, model_validator

from models.sql.slide import SlideModel
from services.presentation_profiles import (
    PresentationCapabilities,
    PresentationProfile,
    resolve_presentation_driver,
)
from templates.v2.constants import LEGACY_PRESENTATION_VERSION


class PresentationWithSlides(BaseModel):
    id: uuid.UUID
    content: str
    n_slides: int
    language: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    tone: Optional[str] = None
    verbosity: Optional[str] = None
    slides: List[SlideModel]
    theme: Optional[dict] = None
    fonts: Optional[Any] = None
    generation_mode: Literal["standard", "smart"] = "standard"
    community_design_ids: Optional[List[int]] = None
    # "template" | "adaptive" | "authored" — lets the editor read the deck's mode
    # explicitly instead of inferring it from theme/layout sentinels.
    mode: Optional[str] = None
    version: str = LEGACY_PRESENTATION_VERSION
    revision: int = 0
    lifecycle_status: str = "published"
    profile: PresentationProfile
    capabilities: PresentationCapabilities

    @model_validator(mode="before")
    @classmethod
    def derive_server_runtime(cls, value: Any) -> Any:
        """Project persisted identity into the canonical runtime contract.

        Callers cannot supply a profile or capability override: both values are
        always replaced with a projection of the server-loaded presentation
        fields.  ``generation_mode`` remains in the wire shape only as the
        documented rolling-compatibility facade.
        """

        if not isinstance(value, dict):
            return value
        projected = dict(value)
        profile, capabilities, _ = resolve_presentation_driver(projected)
        projected["profile"] = profile
        projected["capabilities"] = capabilities
        return projected


_PUBLIC_PRIVATE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "artifact",
    "artifact_id",
    "artifact_pointer",
    "artifacts",
    "attachment",
    "attachment_file_name",
    "attachment_filename",
    "attachment_url",
    "attachments",
    "auth_token",
    "authorization",
    "bearer_token",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "conversation",
    "conversation_id",
    "conversations",
    "error",
    "errors",
    "file_paths",
    "idempotency_key_hash",
    "message",
    "message_body",
    "messages",
    "operation",
    "operation_event",
    "operation_events",
    "operations",
    "original_prompt",
    "original_name",
    "owner_id",
    "password",
    "passwd",
    "presentation",
    "private_key",
    "provider_response",
    "prompt",
    "raw_payload",
    "receipt",
    "receipts",
    "request_id",
    "request_payload",
    "request_sha256",
    "refresh_token",
    "revision",
    "secret",
    "secret_key",
    "share_token",
    "speaker_note",
    "stack_trace",
    "stacktrace",
    "storage_key",
    "storage_path",
    "system_prompt",
    "trace",
    "user_prompt",
    "workspace_conversation",
    "workspace_event",
    "workspace_operation",
}

# Public shares render stored authored HTML directly.  Authored documents carry
# editing/provenance metadata in comments and ``data-*`` attributes (notably
# ``data-illust-prompt``), which can contain the private prompt used to create
# an illustration.  Public rendering has no need for that metadata: safe DOM
# structure, text, styles, and resource attributes such as ``src`` remain.
# Remove every HTML comment and data attribute rather than maintaining a
# growing deny-list of authoring keys.
_PUBLIC_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_PUBLIC_HTML_DATA_ATTRIBUTE_RE = re.compile(
    r"\s+data-[a-z_:][a-z0-9:._-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?",
    re.IGNORECASE,
)


def sanitize_public_html_content(value: Any) -> Any:
    """Remove private authoring metadata while retaining rendered HTML.

    This is deliberately an output-boundary sanitizer, not an editor rewrite:
    the stored authored source must retain its metadata for internal tools.
    Unterminated comments are removed through EOF so a malformed private
    comment cannot leak through a public share.
    """

    if not isinstance(value, str):
        return value
    without_comments = _PUBLIC_HTML_COMMENT_RE.sub("", value)
    return _PUBLIC_HTML_DATA_ATTRIBUTE_RE.sub("", without_comments)


def _normalized_public_key(value: object) -> str:
    """Normalize common JSON naming variants before applying the deny-list."""

    raw = str(value).strip()
    # Normalize acronym boundaries (``APIKey``) as well as ordinary camel
    # case (``accessToken``) before applying the public privacy deny-list.
    snake = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", raw)
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
    return re.sub(r"[^a-zA-Z0-9]+", "_", snake).strip("_").lower()


def project_public_value(value: Any) -> Any:
    """Recursively remove private authoring/workspace fields from public JSON.

    Slide content is intentionally extensible, so a closed nested schema would
    reject valid renderer payloads.  The projection therefore keeps JSON values
    while failing closed for privacy-sensitive keys at every depth.
    """

    if isinstance(value, dict):
        return {
            key: project_public_value(child)
            for key, child in value.items()
            if _normalized_public_key(key) not in _PUBLIC_PRIVATE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [project_public_value(child) for child in value]
    return value


class PublicSlide(BaseModel):
    """Allow-listed rendered slide fields for token-based public viewing."""

    id: uuid.UUID
    layout_group: str
    layout: str
    index: int
    content: dict
    html_content: Optional[str] = None
    ui: Optional[dict] = None
    properties: Optional[dict] = None

    @field_validator("content", "ui", "properties", mode="before")
    @classmethod
    def _project_nested_fields(cls, value: Any) -> Any:
        return project_public_value(value)

    @field_validator("html_content", mode="before")
    @classmethod
    def _sanitize_html_content(cls, value: Any) -> Any:
        return sanitize_public_html_content(value)


class PublicPresentationWithSlides(BaseModel):
    """Public projection that deliberately excludes prompts and author settings."""

    id: uuid.UUID
    n_slides: int
    language: str
    title: Optional[str] = None
    slides: List[PublicSlide]
    theme: Optional[dict] = None
    fonts: Optional[Any] = None
    mode: Optional[str] = None
    version: str = LEGACY_PRESENTATION_VERSION

    @field_validator("theme", "fonts", mode="before")
    @classmethod
    def _project_nested_fields(cls, value: Any) -> Any:
        return project_public_value(value)
