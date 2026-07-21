"""File-backed style presets for the authored presentation mode.

Style briefs are intentionally kept server-side.  The public catalogue only exposes
the display metadata required by a picker, so clients cannot accidentally treat a
brief as an editable prompt contract.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml


LOGGER = logging.getLogger(__name__)
DEFAULT_AUTHORED_STYLE_ID = "default"
AUTHORED_STYLES_DIRECTORY = Path(__file__).resolve().parent.parent / "authored_styles"
AUTHORED_STYLE_ASSETS_DIRECTORY = (
    Path(__file__).resolve().parent.parent / "authored_style_assets"
)
DEFAULT_AUTHORED_STYLE_CATEGORY = "general"
AUTHORED_STYLE_CATEGORIES = frozenset(
    {"general", "business", "technology", "research", "editorial", "creative"}
)
_ENGLISH_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_REFERENCE_ROLES = frozenset(
    {"cover", "content", "data", "timeline", "architecture", "closing"}
)
_REFERENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_MAX_REFERENCES_PER_ROLE = 2


@dataclass(frozen=True)
class AuthoredStyle:
    id: str
    name: str
    description: str
    preview_bg: str
    preview_accent: str
    brief: str
    category: str = DEFAULT_AUTHORED_STYLE_CATEGORY
    tags: list[str] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    preview_palette: Optional[list[str]] = None
    preview_variant: Optional[str] = None
    preview_image: Optional[Path] = None
    primary_color: Optional[str] = None
    background_color: Optional[str] = None
    heading_font: Optional[str] = None
    body_font: Optional[str] = None
    mono_font: Optional[str] = None
    illustration_prompt: Optional[str] = None
    reference_images: Mapping[str, tuple[Path, ...]] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        """Return only the fields safe and useful for style selection clients."""
        preview: dict[str, Any] = {
            "bg": self.preview_bg,
            "accent": self.preview_accent,
        }
        if self.preview_palette is not None:
            preview["palette"] = list(self.preview_palette)
        if self.preview_variant is not None:
            preview["variant"] = self.preview_variant
        if self.preview_image is not None:
            preview["image"] = f"/api/v1/ppt/authored/styles/{self.id}/preview"

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": list(self.tags),
            "use_cases": list(self.use_cases),
            "preview": preview,
        }


_BUILTIN_DEFAULT_STYLE = AuthoredStyle(
    id=DEFAULT_AUTHORED_STYLE_ID,
    name="기본 블루프린트",
    description="깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일",
    preview_bg="#F8FAFC",
    preview_accent="#2563EB",
    brief=(
        "Use a clear consulting-style layout with generous whitespace, restrained blue "
        "accents, strong hierarchy, and simple editorial data treatments."
    ),
)


def _required_string(data: Mapping[str, Any], key: str, source: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source.name}: '{key}' must be a non-empty string")
    return value.strip()


def _color(data: Mapping[str, Any], key: str, source: Path) -> str:
    value = _required_string(data, key, source)
    if not _HEX_COLOR_PATTERN.fullmatch(value):
        raise ValueError(f"{source.name}: '{key}' must be a #RRGGBB color")
    return value


def _string_list(data: Mapping[str, Any], key: str, source: Path) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{source.name}: '{key}' must be a list of strings")

    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"{source.name}: '{key}[{index}]' must be a non-empty string"
            )
        result.append(item.strip())
    return result


def _optional_color_list(
    data: Mapping[str, Any], key: str, source: Path
) -> Optional[list[str]]:
    if key not in data:
        return None
    values = _string_list(data, key, source)
    for index, value in enumerate(values):
        if not _HEX_COLOR_PATTERN.fullmatch(value):
            raise ValueError(
                f"{source.name}: '{key}[{index}]' must be a #RRGGBB color"
            )
    return values


def _optional_identifier(
    data: Mapping[str, Any], key: str, source: Path
) -> Optional[str]:
    if key not in data:
        return None
    value = _required_string(data, key, source)
    if not _ENGLISH_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{source.name}: '{key}' must be an English identifier")
    return value


def _optional_string(data: Mapping[str, Any], key: str, source: Path) -> Optional[str]:
    if key not in data:
        return None
    return _required_string(data, key, source)


def _optional_color(data: Mapping[str, Any], key: str, source: Path) -> Optional[str]:
    if key not in data:
        return None
    return _color(data, key, source)


def _asset_path(value: Any, source: Path, assets_directory: Path) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source.name}: reference asset path must be a non-empty string")
    candidate = (assets_directory / value.strip()).resolve()
    root = assets_directory.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"{source.name}: reference asset must stay inside {assets_directory.name}"
        ) from exc
    if candidate.suffix.lower() not in _REFERENCE_SUFFIXES:
        raise ValueError(
            f"{source.name}: unsupported reference asset type '{candidate.suffix}'"
        )
    if not candidate.is_file():
        LOGGER.warning("Missing authored style reference asset %s", candidate)
        return None
    return candidate


def _reference_images(
    data: Mapping[str, Any], source: Path, assets_directory: Path
) -> Mapping[str, tuple[Path, ...]]:
    raw = data.get("reference_images", {})
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source.name}: 'reference_images' must be a mapping")
    result: dict[str, tuple[Path, ...]] = {}
    for role, values in raw.items():
        if role not in _REFERENCE_ROLES:
            raise ValueError(
                f"{source.name}: unsupported reference role '{role}'"
            )
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError(
                f"{source.name}: reference_images.{role} must be a list"
            )
        if len(values) > _MAX_REFERENCES_PER_ROLE:
            raise ValueError(
                f"{source.name}: reference_images.{role} accepts at most "
                f"{_MAX_REFERENCES_PER_ROLE} images"
            )
        resolved = tuple(
            path
            for value in values
            if (path := _asset_path(value, source, assets_directory)) is not None
        )
        if resolved:
            result[str(role)] = resolved
    return result


def reference_images_for_role(style: Optional[AuthoredStyle], role: str) -> tuple[Path, ...]:
    """Return the small, role-appropriate reference set for one authored slide."""
    if style is None:
        return ()
    normalized = (role or "").upper()
    if normalized == "COVER":
        key = "cover"
    elif normalized == "CLOSING":
        key = "closing"
    elif normalized in {"ROADMAP", "TIMELINE"}:
        key = "timeline"
    elif normalized in {"OUTCOMES", "METRICS", "DATA"}:
        key = "data"
    elif normalized in {"ARCHITECTURE", "SYSTEM", "SOLUTION"}:
        key = "architecture"
    else:
        key = "content"
    references = getattr(style, "reference_images", {})
    return tuple(references.get(key, ()))


def _parse_style(source: Path, assets_directory: Path) -> AuthoredStyle:
    with source.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"{source.name}: style must be a mapping")

    preview = data.get("preview")
    if not isinstance(preview, Mapping):
        raise ValueError(f"{source.name}: 'preview' must be a mapping")

    category = data.get("category", DEFAULT_AUTHORED_STYLE_CATEGORY)
    if not isinstance(category, str) or category not in AUTHORED_STYLE_CATEGORIES:
        supported = ", ".join(sorted(AUTHORED_STYLE_CATEGORIES))
        raise ValueError(
            f"{source.name}: 'category' must be one of: {supported}"
        )

    tokens = data.get("design_tokens", {})
    if not isinstance(tokens, Mapping):
        raise ValueError(f"{source.name}: 'design_tokens' must be a mapping")

    illustration = data.get("illustration", {})
    if not isinstance(illustration, Mapping):
        raise ValueError(f"{source.name}: 'illustration' must be a mapping")

    preview_image = None
    if "image" in preview:
        preview_image = _asset_path(preview["image"], source, assets_directory)

    return AuthoredStyle(
        id=_required_string(data, "id", source),
        name=_required_string(data, "name", source),
        description=_required_string(data, "description", source),
        preview_bg=_color(preview, "bg", source),
        preview_accent=_color(preview, "accent", source),
        brief=_required_string(data, "brief", source),
        category=category,
        tags=_string_list(data, "tags", source),
        use_cases=_string_list(data, "use_cases", source),
        preview_palette=_optional_color_list(preview, "palette", source),
        preview_variant=_optional_identifier(preview, "variant", source),
        preview_image=preview_image,
        primary_color=_optional_color(tokens, "primary", source),
        background_color=_optional_color(tokens, "background", source),
        heading_font=_optional_string(tokens, "heading_font", source),
        body_font=_optional_string(tokens, "body_font", source),
        mono_font=_optional_string(tokens, "mono_font", source),
        illustration_prompt=_optional_string(illustration, "style_prompt", source),
        reference_images=_reference_images(data, source, assets_directory),
    )


def load_authored_styles(
    styles_directory: Optional[Path] = None,
) -> list[AuthoredStyle]:
    """Load valid YAML style files in a deterministic order.

    A hand-edited preset must never make the authored API unavailable: unreadable,
    malformed, and duplicate-id files are warned about and skipped.
    """
    directory = styles_directory or AUTHORED_STYLES_DIRECTORY
    assets_directory = (
        AUTHORED_STYLE_ASSETS_DIRECTORY
        if styles_directory is None
        else directory.parent / "authored_style_assets"
    )
    styles: list[AuthoredStyle] = []
    known_ids: set[str] = set()

    try:
        sources = sorted(directory.glob("*.yaml"), key=lambda path: path.name)
    except OSError as exc:
        LOGGER.warning("Unable to enumerate authored styles in %s: %s", directory, exc)
        return styles

    for source in sources:
        try:
            style = _parse_style(source, assets_directory)
            if style.id in known_ids:
                raise ValueError(f"{source.name}: duplicate style id '{style.id}'")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            LOGGER.warning("Skipping invalid authored style %s: %s", source, exc)
            continue
        known_ids.add(style.id)
        styles.append(style)

    return sorted(styles, key=lambda style: style.id)


def resolve_authored_style(
    style_id: Optional[str],
    styles_directory: Optional[Path] = None,
) -> AuthoredStyle:
    """Resolve a preset, falling back to the default style for unknown IDs."""
    styles = load_authored_styles(styles_directory)
    by_id = {style.id: style for style in styles}
    requested_id = (style_id or "").strip() or DEFAULT_AUTHORED_STYLE_ID
    resolved = by_id.get(requested_id)
    if resolved is None and requested_id != DEFAULT_AUTHORED_STYLE_ID:
        # A specific style was asked for but doesn't exist (e.g. a CLI/API typo);
        # surface it instead of silently shipping a default-styled deck.
        LOGGER.warning(
            "Unknown authored style %r; falling back to the default style",
            requested_id,
        )
    return (
        resolved
        or by_id.get(DEFAULT_AUTHORED_STYLE_ID)
        or _BUILTIN_DEFAULT_STYLE
    )
