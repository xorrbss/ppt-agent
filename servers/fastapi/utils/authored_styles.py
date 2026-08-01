"""File-backed style presets for the authored presentation mode.

Style briefs are intentionally kept server-side.  The public catalogue only exposes
the display metadata required by a picker, so clients cannot accidentally treat a
brief as an editable prompt contract.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from PIL import Image, UnidentifiedImageError
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
_STYLE_REFERENCE = "style_reference"
_EXACT_RECONSTRUCTION = "exact_reconstruction"
_REFERENCE_MODES = frozenset({_STYLE_REFERENCE, _EXACT_RECONSTRUCTION})
_EXACT_ENABLED_ENV = "AUTHORED_EXACT_RECONSTRUCTION_ENABLED"
_EXACT_STYLE_IDS_ENV = "AUTHORED_EXACT_STYLE_IDS"
_EXACT_SOURCE_SIZE = (1280, 720)
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class AuthoredExactReconstructionUnavailableError(ValueError):
    """An exact preset was requested but is not safe to expose or execute."""


def _configured_exact_style_ids() -> frozenset[str]:
    raw = os.getenv(_EXACT_STYLE_IDS_ENV, "")
    return frozenset(
        item
        for item in (part.strip() for part in raw.split(","))
        if item and _ENGLISH_IDENTIFIER_PATTERN.fullmatch(item)
    )


def exact_reconstruction_is_allowed(style_id: str) -> bool:
    """Return whether a private exact preset passes both rollout controls."""
    enabled = os.getenv(_EXACT_ENABLED_ENV, "").strip().lower()
    return enabled in _TRUTHY_ENV_VALUES and style_id in _configured_exact_style_ids()


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
    # Ordered, private source-slide mapping for exact reconstruction.  Unlike the
    # role references above, these are zipped to deck positions one-to-one and are
    # intentionally omitted from ``public_dict``.
    slide_reference_images: tuple[Path, ...] = ()
    # Private preset opt-in: omitted/legacy YAML remains art-direction-only, and
    # public style-selection response shapes remain unchanged.
    reference_mode: str = _STYLE_REFERENCE
    # Private provenance used to re-check path containment at generation time.
    assets_root: Optional[Path] = None
    # Positional mapping is explicit even when scalar entries use list position.
    slide_reference_indices: tuple[int, ...] = ()

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


def _reference_mode(data: Mapping[str, Any], source: Path) -> str:
    value = data.get("reference_mode", _STYLE_REFERENCE)
    normalized = value.strip().lower() if isinstance(value, str) else value
    if not isinstance(normalized, str) or normalized not in _REFERENCE_MODES:
        supported = ", ".join(sorted(_REFERENCE_MODES))
        raise ValueError(
            f"{source.name}: 'reference_mode' must be one of: {supported}"
        )
    return normalized


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


def _slide_reference_images(
    data: Mapping[str, Any],
    source: Path,
    assets_directory: Path,
    *,
    strict: bool,
) -> tuple[tuple[Path, ...], tuple[int, ...]]:
    raw = data.get("slide_reference_images", [])
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{source.name}: 'slide_reference_images' must be a list")
    resolved: list[Path] = []
    indices: list[int] = []
    for index, value in enumerate(raw):
        if isinstance(value, Mapping):
            unknown = set(value) - {"slide_index", "source"}
            if unknown:
                raise ValueError(
                    f"{source.name}: slide_reference_images[{index}] has unsupported "
                    f"fields: {', '.join(sorted(str(item) for item in unknown))}"
                )
            declared_index = value.get("slide_index")
            if not isinstance(declared_index, int) or isinstance(declared_index, bool):
                raise ValueError(
                    f"{source.name}: slide_reference_images[{index}].slide_index "
                    "must be an integer"
                )
            asset_value = value.get("source")
        else:
            # Scalar entries remain compatible; their stable key is the zero-based
            # list position and is rechecked against the outline before generation.
            declared_index = index
            asset_value = value
        if declared_index != index:
            raise ValueError(
                f"{source.name}: slide_reference_images must use contiguous stable "
                f"slide_index values; expected {index}, got {declared_index}"
            )
        path = _asset_path(asset_value, source, assets_directory)
        if path is None:
            if strict:
                raise ValueError(
                    f"{source.name}: slide_reference_images[{index}] is missing or "
                    "unreadable; exact source positions cannot be compacted"
                )
            continue
        resolved.append(path)
        indices.append(declared_index)
    return tuple(resolved), tuple(indices)


def _image_format_from_magic(payload: bytes) -> Optional[str]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "webp"
    return None


def _validate_exact_source_image(path: Path, source: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{source.name}: exact source image is not readable") from exc
    detected = _image_format_from_magic(payload)
    if detected is None:
        raise ValueError(
            f"{source.name}: exact source must contain PNG, JPEG, or WebP magic bytes"
        )
    suffix_format = (
        "jpeg"
        if path.suffix.lower() in {".jpg", ".jpeg"}
        else path.suffix.lower()[1:]
    )
    if suffix_format != detected:
        raise ValueError(
            f"{source.name}: exact source extension does not match its image bytes"
        )
    try:
        with Image.open(BytesIO(payload)) as image:
            size = image.size
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"{source.name}: exact source image is corrupt") from exc
    if size != _EXACT_SOURCE_SIZE:
        raise ValueError(
            f"{source.name}: exact source must be 1280x720 pixels, got "
            f"{size[0]}x{size[1]}"
        )
    return sha256(payload).hexdigest()


def _validate_exact_sources(
    paths: Sequence[Path], source: Path, assets_directory: Path
) -> tuple[str, ...]:
    root = assets_directory.resolve()
    resolved_paths: list[Path] = []
    digests: list[str] = []
    for path in paths:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"{source.name}: exact source must stay inside "
                f"{assets_directory.name}"
            ) from exc
        resolved_paths.append(resolved)
        digests.append(_validate_exact_source_image(resolved, source))
    if (
        len(set(resolved_paths)) != len(resolved_paths)
        or len(set(digests)) != len(digests)
    ):
        raise ValueError(
            f"{source.name}: exact reconstruction requires a unique source image "
            "per slide, including slides with the same role"
        )
    return tuple(digests)


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


def _read_style_mapping(source: Path) -> Mapping[str, Any]:
    with source.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"{source.name}: style must be a mapping")
    return data


def _declared_exact_style_id(data: Mapping[str, Any]) -> Optional[str]:
    style_id = data.get("id")
    reference_mode = data.get("reference_mode", _STYLE_REFERENCE)
    if not isinstance(style_id, str) or not isinstance(reference_mode, str):
        return None
    if reference_mode.strip().lower() != _EXACT_RECONSTRUCTION:
        return None
    return style_id.strip() or None


def _parse_style(
    source: Path,
    assets_directory: Path,
    data: Optional[Mapping[str, Any]] = None,
) -> AuthoredStyle:
    data = data if data is not None else _read_style_mapping(source)

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

    reference_images = _reference_images(data, source, assets_directory)
    reference_mode = _reference_mode(data, source)
    slide_reference_images, slide_reference_indices = _slide_reference_images(
        data,
        source,
        assets_directory,
        strict=reference_mode == "exact_reconstruction",
    )
    if reference_mode == "exact_reconstruction" and not slide_reference_images:
        raise ValueError(
            f"{source.name}: exact_reconstruction requires readable "
            "slide_reference_images"
        )
    if reference_mode == _EXACT_RECONSTRUCTION:
        _validate_exact_sources(slide_reference_images, source, assets_directory)

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
        reference_images=reference_images,
        slide_reference_images=slide_reference_images,
        reference_mode=reference_mode,
        assets_root=assets_directory.resolve(),
        slide_reference_indices=slide_reference_indices,
    )


def validate_exact_reconstruction_style(
    style: AuthoredStyle,
    *,
    slide_count: int,
    roles: Sequence[str],
) -> None:
    """Fail closed before generation unless an exact preset is fully mapped.

    The authored outline has no persistent slide identifier, so the stable mapping
    contract is the zero-based outline position recorded by
    ``slide_reference_indices``.  Loaded presets retain their private assets root
    so containment and image bytes can be rechecked immediately before authoring.
    """
    if getattr(style, "reference_mode", _STYLE_REFERENCE) != _EXACT_RECONSTRUCTION:
        return
    if not exact_reconstruction_is_allowed(style.id):
        raise AuthoredExactReconstructionUnavailableError(
            f"Exact reconstruction style '{style.id}' is disabled or not allow-listed"
        )
    if slide_count < 1 or len(roles) != slide_count:
        raise ValueError(
            f"Exact reconstruction style '{style.id}' requires one role per slide"
        )

    paths = tuple(getattr(style, "slide_reference_images", ()))
    indices = tuple(getattr(style, "slide_reference_indices", ()))
    expected_indices = tuple(range(slide_count))
    if len(paths) != slide_count or indices != expected_indices:
        raise ValueError(
            f"Exact reconstruction style '{style.id}' requires exactly {slide_count} "
            "source images mapped to contiguous zero-based slide indices"
        )

    assets_root = getattr(style, "assets_root", None)
    if not isinstance(assets_root, Path):
        raise ValueError(
            f"Exact reconstruction style '{style.id}' has no validated assets root"
        )
    source = Path(f"{style.id}.yaml")
    digests = _validate_exact_sources(paths, source, assets_root)

    seen_by_role: dict[str, set[tuple[Path, str]]] = {}
    for role, path, digest in zip(roles, paths, digests):
        normalized_role = (role or "CONTENT").strip().upper()
        identity = (path.resolve(), digest)
        if identity in seen_by_role.setdefault(normalized_role, set()):
            raise ValueError(
                f"Exact reconstruction style '{style.id}' reuses a source for "
                f"multiple {normalized_role} slides"
            )
        seen_by_role[normalized_role].add(identity)


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
            data = _read_style_mapping(source)
            declared_exact_id = _declared_exact_style_id(data)
            if declared_exact_id is not None and not exact_reconstruction_is_allowed(
                declared_exact_id
            ):
                # Exact presets are private rollout artefacts.  Gate-disabled or
                # non-allow-listed entries must not enter the public catalogue.
                continue
            style = _parse_style(source, assets_directory, data)
            if (
                style.reference_mode == _EXACT_RECONSTRUCTION
                and not exact_reconstruction_is_allowed(style.id)
            ):
                continue
            if style.id in known_ids:
                raise ValueError(f"{source.name}: duplicate style id '{style.id}'")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            LOGGER.warning("Skipping invalid authored style %s: %s", source, exc)
            continue
        known_ids.add(style.id)
        styles.append(style)

    return sorted(styles, key=lambda style: style.id)


def _declared_exact_style_ids(directory: Path) -> frozenset[str]:
    result: set[str] = set()
    try:
        sources = sorted(directory.glob("*.yaml"), key=lambda path: path.name)
    except OSError:
        return frozenset()
    for source in sources:
        try:
            style_id = _declared_exact_style_id(_read_style_mapping(source))
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if style_id is not None:
            result.add(style_id)
    return frozenset(result)


def resolve_authored_style(
    style_id: Optional[str],
    styles_directory: Optional[Path] = None,
) -> AuthoredStyle:
    """Resolve a preset, falling back to the default style for unknown IDs."""
    styles = load_authored_styles(styles_directory)
    by_id = {style.id: style for style in styles}
    requested_id = (style_id or "").strip() or DEFAULT_AUTHORED_STYLE_ID
    resolved = by_id.get(requested_id)
    if resolved is not None:
        return resolved
    if requested_id != DEFAULT_AUTHORED_STYLE_ID:
        directory = styles_directory or AUTHORED_STYLES_DIRECTORY
        if requested_id in _declared_exact_style_ids(directory):
            if not exact_reconstruction_is_allowed(requested_id):
                raise AuthoredExactReconstructionUnavailableError(
                    f"Exact reconstruction style '{requested_id}' is disabled or "
                    "not allow-listed"
                )
            raise AuthoredExactReconstructionUnavailableError(
                f"Exact reconstruction style '{requested_id}' failed preset "
                "validation"
            )
    if resolved is None and requested_id != DEFAULT_AUTHORED_STYLE_ID:
        # A specific style was asked for but doesn't exist (e.g. a CLI/API typo);
        # surface it instead of silently shipping a default-styled deck.
        LOGGER.warning(
            "Unknown authored style %r; falling back to the default style",
            requested_id,
        )
    return by_id.get(DEFAULT_AUTHORED_STYLE_ID) or _BUILTIN_DEFAULT_STYLE
