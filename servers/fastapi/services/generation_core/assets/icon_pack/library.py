"""Reusable monochrome SVGs for the existing authored icon resolver."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Sequence
from services.generation_core.assets.icon_pack.concepts import match_concept
from utils.hashing import sha256_hex

_LIBRARY = Path(__file__).resolve().parents[6] / "assets" / "infographics" / "monochrome-v1"


def _normalize(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Path], dict[str, Path]]:
    """Exact aliases by normalized key, and the same icons by library id."""
    manifest = _LIBRARY / "manifest.json"
    if not manifest.is_file():
        return {}, {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    result: dict[str, Path] = {}
    by_id: dict[str, Path] = {}
    for asset in data.get("assets", []):
        if asset.get("format") != "SVG" or not asset.get("themeable"):
            continue
        source = (_LIBRARY / asset["path"]).resolve()
        if not source.is_relative_to(_LIBRARY.resolve()) or not source.is_file():
            continue
        by_id[asset["id"]] = source
        english = asset.get("alt", {}).get("en", "").removesuffix(" monochrome vector infographic")
        korean = asset.get("tags", {}).get("ko", [])[1:]
        slug = asset["id"].split("-", 2)[-1]
        for alias in [english, slug, *korean]:
            key = _normalize(alias)
            if key:
                result.setdefault(key, source)
    # Spacing is not meaning in Korean ("데이터 분석" / "데이터분석"): also key the
    # compact form, after every exact key so it can never shadow one.
    for key, source in list(result.items()):
        result.setdefault(key.replace(" ", ""), source)
    return result, by_id


def find_icon(concept: str) -> Path | None:
    """The library icon for one label: an exact alias, else a curated concept match."""
    aliases, by_id = _catalog()
    key = _normalize(concept)
    source = aliases.get(key) or aliases.get(key.replace(" ", ""))
    if source is not None:
        return source
    icon_id = match_concept(concept) if by_id else None
    return by_id.get(icon_id) if icon_id else None


def lookup_reusable_icons(
    concepts: Sequence[str], *, cache_directory: Path, color: str
) -> dict[str, Path]:
    """Match explicit bilingual concepts and tint copies, preserving shared masters."""
    result: dict[str, Path] = {}
    ink = color if re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", color or "") else "#000000"
    for concept in concepts:
        source = find_icon(concept)
        if source is None:
            continue
        svg = source.read_text(encoding="utf-8").replace("#000000", ink).replace("currentColor", ink)
        digest = sha256_hex(svg.encode("utf-8"))
        target = Path(cache_directory) / "reusable-svg" / f"{digest}.svg"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(svg, encoding="utf-8")
        result[concept] = target
    return result

