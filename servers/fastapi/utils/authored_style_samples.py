"""Sample decks that preview an authored style before it is selected.

Samples live in a root separate from ``authored_style_assets/`` (D1). The
request path (``load_style_samples`` / ``sample_file_path``) never computes
digests or touches the style catalogue; ``style_render_digest`` is for the
check and generator scripts only (D5, D10).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence

import yaml

LOGGER = logging.getLogger(__name__)

AUTHORED_STYLE_SAMPLES_DIRECTORY = Path(__file__).resolve().parent.parent / "authored_style_samples"

_STYLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SCHEMA = "authored-style-samples/v1"
_GENERATOR_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_MAX_SLIDES = 24
_MAX_TITLE_LENGTH = 80
_MIN_CANVAS = 320
_MAX_CANVAS = 1280

#: Keys the digest ignores because they do not affect rendering (D6).
DISPLAY_ONLY_STYLE_KEYS = frozenset({"name", "description", "category", "tags", "use_cases", "preview"})


@dataclass(frozen=True)
class StyleSampleSlide:
    index: int
    title: str


@dataclass(frozen=True)
class StyleSampleCanvas:
    width: int
    height: int


@dataclass(frozen=True)
class StyleSamples:
    style_id: str
    canvas: StyleSampleCanvas
    slides: tuple[StyleSampleSlide, ...]
    style_digest: str
    demo_topic_id: str
    generated_at: str
    generator_commit: str


def _warn(style_id: str, rule: str) -> None:
    # Never include an absolute path: style id + rule is enough to locate it.
    LOGGER.warning("Invalid authored style sample manifest for %s: %s", style_id, rule)


def _samples_directory(samples_root: Optional[Path]) -> Path:
    return samples_root if samples_root is not None else AUTHORED_STYLE_SAMPLES_DIRECTORY


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_canvas(value: Any, style_id: str) -> Optional[StyleSampleCanvas]:
    if not isinstance(value, Mapping):
        _warn(style_id, "canvas must be an object")
        return None
    width, height = value.get("width"), value.get("height")
    valid_ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (width, height))
    if not valid_ints:
        _warn(style_id, "canvas width/height must be integers")
        return None
    if not (_MIN_CANVAS <= width <= _MAX_CANVAS and _MIN_CANVAS <= height <= _MAX_CANVAS):
        _warn(style_id, f"canvas width/height must be within {_MIN_CANVAS}-{_MAX_CANVAS}")
        return None
    if max(width, height) != _MAX_CANVAS:
        _warn(style_id, f"canvas long edge must equal {_MAX_CANVAS}")
        return None
    return StyleSampleCanvas(width=width, height=height)


def _parse_slides(value: Any, style_id: str) -> Optional[tuple[StyleSampleSlide, ...]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _warn(style_id, "slides must be a list")
        return None
    if not (1 <= len(value) <= _MAX_SLIDES):
        _warn(style_id, f"slides must have between 1 and {_MAX_SLIDES} entries")
        return None
    slides: list[StyleSampleSlide] = []
    for position, entry in enumerate(value, start=1):
        title = entry.get("title") if isinstance(entry, Mapping) else None
        if not isinstance(title, str) or not title.strip() or len(title) > _MAX_TITLE_LENGTH:
            _warn(style_id, f"slides[{position}].title must be 1-{_MAX_TITLE_LENGTH} characters")
            return None
        slides.append(StyleSampleSlide(index=position, title=title))
    return tuple(slides)


def load_style_samples(style_id: str, samples_root: Optional[Path] = None) -> Optional[StyleSamples]:
    """Validate and load a sample manifest per §4; ``None`` if absent or invalid."""
    if not _STYLE_ID_PATTERN.fullmatch(style_id or ""):
        return None
    manifest_path = _samples_directory(samples_root) / style_id / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        _warn(style_id, "manifest is not readable JSON")
        return None
    if not isinstance(data, Mapping):
        _warn(style_id, "manifest must be a JSON object")
        return None
    if data.get("schema") != _SCHEMA:
        _warn(style_id, "schema mismatch")
        return None
    if data.get("styleId") != style_id:
        _warn(style_id, "styleId must equal the directory name")
        return None
    canvas = _parse_canvas(data.get("canvas"), style_id)
    slides = _parse_slides(data.get("slides"), style_id) if canvas is not None else None
    if canvas is None or slides is None:
        return None
    generator_commit = data.get("generatorCommit")
    if not isinstance(generator_commit, str) or not _GENERATOR_COMMIT_PATTERN.fullmatch(generator_commit):
        _warn(style_id, "generatorCommit must be 40 lowercase hex characters")
        return None
    style_digest, demo_topic_id, generated_at = (
        data.get("styleDigest"), data.get("demoTopicId"), data.get("generatedAt"),
    )
    if not all(isinstance(v, str) and v for v in (style_digest, demo_topic_id, generated_at)):
        _warn(style_id, "styleDigest/demoTopicId/generatedAt must be non-empty strings")
        return None
    return StyleSamples(
        style_id=style_id,
        canvas=canvas,
        slides=slides,
        style_digest=style_digest,
        demo_topic_id=demo_topic_id,
        generated_at=generated_at,
        generator_commit=generator_commit,
    )


def sample_file_path(
    style_id: str,
    index: int,
    size: Literal["thumb", "full"],
    samples_root: Optional[Path] = None,
) -> Optional[Path]:
    """Return the fixed-name image path if it is a regular file inside the style dir."""
    if not _STYLE_ID_PATTERN.fullmatch(style_id or ""):
        return None
    root = _samples_directory(samples_root).resolve()
    directory = (root / style_id).resolve()
    if not _contains(root, directory):
        return None
    suffix = "thumb.webp" if size == "thumb" else "webp"
    candidate = (directory / f"{index:02d}.{suffix}").resolve()
    if not _contains(directory, candidate):
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate


def _raw_reference_paths(data: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    reference_images = data.get("reference_images", {})
    if isinstance(reference_images, Mapping):
        for values in reference_images.values():
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                paths.extend(value for value in values if isinstance(value, str))
    slide_reference_images = data.get("slide_reference_images", [])
    if isinstance(slide_reference_images, Sequence) and not isinstance(slide_reference_images, (str, bytes)):
        for entry in slide_reference_images:
            source = entry.get("source") if isinstance(entry, Mapping) else entry
            if isinstance(source, str):
                paths.append(source)
    return paths


def _resolve_reference_asset(value: str, style_yaml: Path, assets_root: Path) -> Path:
    root = assets_root.resolve()
    candidate = (root / value).resolve()
    if not _contains(root, candidate):
        raise ValueError(f"{style_yaml.name}: reference asset must stay inside {assets_root.name}")
    if not candidate.is_file():
        raise ValueError(f"{style_yaml.name}: reference asset '{value}' is not readable")
    return candidate


def style_render_digest(style_yaml: Path, assets_root: Path) -> str:
    """Compute the render-affecting digest of a style YAML per §5."""
    with style_yaml.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"{style_yaml.name}: style must be a mapping")
    filtered = {key: value for key, value in data.items() if key not in DISPLAY_ONLY_STYLE_KEYS}
    canonical = json.dumps(filtered, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    tail = ""
    for raw_path in sorted(_raw_reference_paths(data)):
        resolved = _resolve_reference_asset(raw_path, style_yaml, assets_root)
        tail += f"\n{raw_path}\n{sha256(resolved.read_bytes()).hexdigest()}"
    return f"sha256:{sha256((canonical + tail).encode('utf-8')).hexdigest()}"
