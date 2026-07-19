"""File-backed style presets for the authored presentation mode.

Style briefs are intentionally kept server-side.  The public catalogue only exposes
the display metadata required by a picker, so clients cannot accidentally treat a
brief as an editable prompt contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


LOGGER = logging.getLogger(__name__)
DEFAULT_AUTHORED_STYLE_ID = "default"
AUTHORED_STYLES_DIRECTORY = Path(__file__).resolve().parent.parent / "authored_styles"


@dataclass(frozen=True)
class AuthoredStyle:
    id: str
    name: str
    description: str
    preview_bg: str
    preview_accent: str
    brief: str

    def public_dict(self) -> dict[str, Any]:
        """Return only the fields safe and useful for style selection clients."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "preview": {"bg": self.preview_bg, "accent": self.preview_accent},
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


def _parse_style(source: Path) -> AuthoredStyle:
    with source.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"{source.name}: style must be a mapping")

    preview = data.get("preview")
    if not isinstance(preview, Mapping):
        raise ValueError(f"{source.name}: 'preview' must be a mapping")

    return AuthoredStyle(
        id=_required_string(data, "id", source),
        name=_required_string(data, "name", source),
        description=_required_string(data, "description", source),
        preview_bg=_required_string(preview, "bg", source),
        preview_accent=_required_string(preview, "accent", source),
        brief=_required_string(data, "brief", source),
    )


def load_authored_styles(
    styles_directory: Optional[Path] = None,
) -> list[AuthoredStyle]:
    """Load valid YAML style files in a deterministic order.

    A hand-edited preset must never make the authored API unavailable: unreadable,
    malformed, and duplicate-id files are warned about and skipped.
    """
    directory = styles_directory or AUTHORED_STYLES_DIRECTORY
    styles: list[AuthoredStyle] = []
    known_ids: set[str] = set()

    try:
        sources = sorted(directory.glob("*.yaml"), key=lambda path: path.name)
    except OSError as exc:
        LOGGER.warning("Unable to enumerate authored styles in %s: %s", directory, exc)
        return styles

    for source in sources:
        try:
            style = _parse_style(source)
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
    requested_id = (style_id or DEFAULT_AUTHORED_STYLE_ID).strip()
    return (
        by_id.get(requested_id)
        or by_id.get(DEFAULT_AUTHORED_STYLE_ID)
        or _BUILTIN_DEFAULT_STYLE
    )
