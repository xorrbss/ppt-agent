"""Resolve concept names to pack icons, generating only what is missing.

The slide plan names concepts (its node labels); the deck's template and
style name a pack. This module answers "which file is the icon for this
concept" by reading the pack first and spending a provider call only for
concepts the pack has never seen - and those are batched onto sprite sheets
so one call yields up to nine icons. Every generated cell is vetted before
it enters the pack: a cell that is empty, bleeds into its neighbour, is not
transparent or carries OCR-detectable text is reported as missing rather
than cached, so the pack never accumulates a broken icon.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from services.generation_core.assets.icon_pack.library import lookup_reusable_icons
from services.generation_core.assets.icon_pack.checks import check_no_text, check_transparency
from services.generation_core.assets.icon_pack.provider import TransparentPngProvider
from services.generation_core.assets.icon_pack.sheet import (
    MAX_CONCEPTS_PER_SHEET,
    analyze_sheet,
    build_sheet_prompt,
    encode_png,
    layout_for,
    open_rgba,
    split_cells,
    trim_cell,
)
from services.generation_core.assets.icon_pack.store import (
    IconEntry,
    IconPack,
    concept_key,
    pack_lock,
)
from utils.runtime_settings import RuntimeSettings
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)

#: Sheets one resolution generates at the same time; a deck asking for forty
#: new concepts fans out to five sheets, and the provider rate limit is the
#: real ceiling anyway.
SHEET_CONCURRENCY = 2
#: A sheet whose icon extents vary more than this is not a uniform set; its
#: cells are rejected as a group rather than mixed into the pack.
MAX_EXTENT_CV = 0.30


@dataclass(frozen=True)
class ResolvedIcon:
    concept: str
    path: str
    width: int
    height: int
    source: str  # "cached" | "generated"


@dataclass
class IconResolution:
    icons: dict[str, ResolvedIcon] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    sheets_generated: int = 0
    sheets_failed: int = 0
    sheets_attempted: int = 0
    sheet_failures: list[dict] = field(default_factory=list)

    def path_for(self, concept: str) -> Optional[str]:
        icon = self.icons.get(concept_key(concept))
        return icon.path if icon else None


def _dedupe(concepts: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for concept in concepts:
        key = concept_key(concept)
        if key and key not in seen:
            seen.add(key)
            out.append(" ".join(concept.split()))
    return out


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _take_file(path: Path) -> bytes:
    """Read the provider's temp file and remove it; the pack keeps its own copy."""
    payload = path.read_bytes()
    path.unlink(missing_ok=True)
    return payload


async def _generate_sheet(
    pack: IconPack,
    provider: TransparentPngProvider,
    concepts: list[str],
    *,
    style_prompt: Optional[str],
    color: str,
) -> tuple[dict[str, IconEntry], list[str]]:
    """One provider call; returns (accepted entries, rejected concepts)."""
    layout = layout_for(len(concepts))
    prompt = build_sheet_prompt(concepts, layout, style_prompt=style_prompt, color=color)
    # Content, rather than the missing-list position, survives cache hits and
    # completion order changes on resume. Style, color and layout are in prompt.
    digest = sha256_hex(f"{pack.pack_key}\0{layout.size}\0{prompt}".encode("utf-8"))
    slot = f"image.icon_sheet.{digest}"
    path = await provider.generate(
        prompt, str(pack.directory / "sheets"), size=layout.size, semantic_slot=slot
    )
    payload = await asyncio.to_thread(_take_file, Path(path))
    analysis = analyze_sheet(payload, layout, len(concepts))
    if not analysis.has_alpha or analysis.extent_cv > MAX_EXTENT_CV:
        LOGGER.warning(
            "[authored] icon sheet rejected (alpha=%s, extent_cv=%.3f)",
            analysis.has_alpha,
            analysis.extent_cv,
        )
        return {}, list(concepts)
    sheet_name = pack.write_sheet(payload)
    cells = split_cells(open_rgba(payload), layout, len(concepts))
    canvas = max((m.extent_px for m in analysis.cells if not m.empty), default=0)
    canvas = int(round(canvas * 1.16)) if canvas else None
    accepted: dict[str, IconEntry] = {}
    rejected: list[str] = []
    for concept, cell, metrics in zip(concepts, cells, analysis.cells, strict=True):
        if metrics.empty or metrics.bleeds:
            rejected.append(concept)
            continue
        icon = trim_cell(cell, metrics, canvas=canvas)
        icon_bytes = encode_png(icon)
        transparency = check_transparency(icon_bytes)
        text = await check_no_text(icon_bytes, forbidden=concepts)
        if not (transparency.passed and text.passed):
            rejected.append(concept)
            continue
        key = concept_key(concept)
        file_name = pack.write_icon(key, icon_bytes)
        accepted[key] = IconEntry(
            concept=concept,
            file=file_name,
            width=icon.size[0],
            height=icon.size[1],
            sheet=sheet_name,
            cell=metrics.index,
            checks={
                "transparency": transparency.to_dict(),
                "text": text.to_dict(),
                "extent_cv": round(analysis.extent_cv, 4),
            },
            created_at=time.time(),
        )
    return accepted, rejected


async def resolve_icons(
    concepts: Sequence[str],
    *,
    pack_key: str,
    style_prompt: Optional[str] = None,
    color: str = "#1E5EFF",
    settings: RuntimeSettings | None = None,
    provider: Optional[TransparentPngProvider] = None,
    app_data: Optional[str] = None,
    generate: bool = True,
    result: IconResolution | None = None,
) -> IconResolution:
    """Icons for ``concepts`` from the pack, generating the missing ones.

    ``pack_key`` identifies the template/style pair (for example
    ``"korean-biz/iso-blueprint"``); ``color`` and ``style_prompt`` are the
    template's icon look and only affect newly generated sheets. With
    ``generate=False`` nothing is spent and unknown concepts are reported
    missing, which is what a resumed or budget-exhausted deck wants. Never
    raises for a provider failure: the concepts it could not resolve are in
    ``missing`` and the caller draws them as HTML.
    """
    wanted = _dedupe(concepts)
    pack = IconPack(pack_key, app_data=app_data)
    result = result if result is not None else IconResolution()
    if not wanted:
        return result

    async with pack_lock(pack.pack_key):
        found, missing = pack.lookup(wanted)
        reusable = lookup_reusable_icons(missing, cache_directory=pack.directory, color=color)
        for concept, path in reusable.items():
            result.icons[concept_key(concept)] = ResolvedIcon(concept, str(path), 128, 128, "library")
        missing = [concept for concept in missing if concept not in reusable]
        for key, entry in found.items():
            result.icons[key] = ResolvedIcon(
                entry.concept, str(pack.path_for(entry)), entry.width, entry.height, "cached"
            )
        if not missing or not generate:
            result.missing = missing
            return result
        provider = provider or TransparentPngProvider(settings)
        if not provider.available:
            result.missing = missing
            return result

        semaphore = asyncio.Semaphore(SHEET_CONCURRENCY)

        async def one(index: int, chunk: list[str]) -> tuple[dict[str, IconEntry], list[str]]:
            async with semaphore:
                result.sheets_attempted += 1
                try:
                    entries, rejected = await _generate_sheet(
                        pack, provider, chunk, style_prompt=style_prompt, color=color
                    )
                    if entries:
                        result.sheets_generated += 1
                    else:
                        result.sheets_failed += 1
                        result.sheet_failures.append({"sheet": index, "reason": "sheet_acceptance_failed"})
                    return entries, rejected
                except asyncio.CancelledError:
                    result.sheets_failed += 1
                    result.sheet_failures.append({"sheet": index, "reason": "cancelled"})
                    raise
                except Exception as error:  # noqa: BLE001 - degrade per sheet
                    reason = getattr(error, "code", None) or type(error).__name__
                    LOGGER.warning("[authored] icon sheet generation failed: %s", reason)
                    result.sheets_failed += 1
                    result.sheet_failures.append({"sheet": index, "reason": reason})
                    return {}, list(chunk)

        outcomes = await asyncio.gather(
            *(one(index, chunk) for index, chunk in enumerate(_chunks(missing, MAX_CONCEPTS_PER_SHEET)))
        )
        accepted: dict[str, IconEntry] = {}
        for entries, rejected in outcomes:
            accepted.update(entries)
            result.missing.extend(rejected)
        if accepted:
            merged = pack.append(accepted)
            for key in accepted:
                entry = merged[key]
                result.icons[key] = ResolvedIcon(
                    entry.concept,
                    str(pack.path_for(entry)),
                    entry.width,
                    entry.height,
                    "generated",
                )
    return result


__all__ = [
    "MAX_EXTENT_CV",
    "SHEET_CONCURRENCY",
    "IconResolution",
    "ResolvedIcon",
    "resolve_icons",
]
