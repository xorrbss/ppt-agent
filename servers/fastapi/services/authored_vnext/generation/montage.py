"""Deterministic all-slide montage manifests for authored deck QA."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass


_MISSING = object()


def _plain(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python", by_alias=True)
    return value


def _read(value: object, *names: str, default: object = _MISSING) -> object:
    value = _plain(value)
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return default


def _required_string(value: object, names: tuple[str, ...], *, path: str) -> str:
    result = _read(value, *names, default=None)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{path} is required")
    return result


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class MontageTile:
    slide_number: int
    sheet: int
    row: int
    column: int
    slide_id: str
    revision: int
    layout_id: str
    spec_digest: str
    source_digest: str
    preview_digest: str
    preview_reference: str
    title: str

    def to_dict(self) -> dict[str, object]:
        return {
            "slideNumber": self.slide_number,
            "sheet": self.sheet,
            "row": self.row,
            "column": self.column,
            "slideId": self.slide_id,
            "revision": self.revision,
            "layoutId": self.layout_id,
            "specDigest": self.spec_digest,
            "sourceDigest": self.source_digest,
            "previewDigest": self.preview_digest,
            "previewReference": self.preview_reference,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class MontageManifest:
    schema_version: str
    deck_id: str
    deck_digest: str
    slide_count: int
    columns: int
    rows: int
    sheet_count: int
    selection_policy: str
    tiles: tuple[MontageTile, ...]
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "deckId": self.deck_id,
            "deckDigest": self.deck_digest,
            "slideCount": self.slide_count,
            "columns": self.columns,
            "rows": self.rows,
            "sheetCount": self.sheet_count,
            "selectionPolicy": self.selection_policy,
            "tiles": [tile.to_dict() for tile in self.tiles],
            "digest": self.digest,
        }


def _preview(slide: object, *, index: int) -> tuple[str, str]:
    preview = _read(slide, "previewArtifact", "preview_artifact", "preview", default=None)
    digest = _required_string(
        preview if preview is not None else slide,
        ("artifactDigest", "artifact_digest", "previewDigest", "preview_digest"),
        path=f"slides[{index}].previewArtifact.artifactDigest",
    )
    reference = _required_string(
        preview if preview is not None else slide,
        ("storageReference", "storage_reference", "previewReference", "preview_reference"),
        path=f"slides[{index}].previewArtifact.storageReference",
    )
    return digest, reference


def build_montage_manifest(
    slides: Iterable[object],
    *,
    deck_id: str | None = None,
    deck_digest: str | None = None,
    columns: int = 5,
    rows: int = 2,
) -> MontageManifest:
    """Build an all-slide, deck-order manifest split into deterministic sheets.

    The default 5x2 sheet means a 10-slide deck produces one sheet and a
    90-slide deck produces nine sheets. No representative sampling can hide a
    bad slide from the visual review artifact.
    """

    deck = None
    if deck_id is None and deck_digest is None:
        deck = slides
        slides = _read(deck, "slides", default=())
        deck_id = _read(deck, "deckId", "deck_id", default=None)
        deck_digest = _read(deck, "digest", default=None)
    if (
        not isinstance(deck_id, str)
        or not deck_id.strip()
        or not isinstance(deck_digest, str)
        or not deck_digest.strip()
    ):
        raise ValueError("deck_id and deck_digest are required")
    if columns <= 0 or rows <= 0:
        raise ValueError("montage rows and columns must be positive")
    materialized = tuple(slides)
    if not materialized:
        raise ValueError("montage requires at least one slide")

    if deck is not None:
        from models.authored_vnext.deck.quality import MontageManifest as WireMontageManifest
        from models.authored_vnext.deck.quality import MontageTile as WireMontageTile
        from models.authored_vnext.deck.quality import require_published_deck_size

        require_published_deck_size(len(materialized))
        wire_tiles = []
        for index, item in enumerate(materialized):
            slide = _read(item, "slide", default=None)
            slide_id = _required_string(slide, ("slideId", "slide_id"), path=f"slides[{index}].slideId")
            png_digest = _read(item, "pngDigest", "png_digest", default=None)
            if not isinstance(png_digest, str):
                png_digest = _digest({
                    "deckDigest": deck_digest,
                    "slideId": slide_id,
                    "specDigest": _required_string(
                        slide, ("specDigest", "spec_digest"), path=f"slides[{index}].specDigest"
                    ),
                })
            wire_tiles.append(WireMontageTile(slide_id=slide_id, slide_index=index, png_digest=png_digest))
        unsigned = {
            "schemaVersion": "deck-montage-manifest/v1",
            "deckId": deck_id,
            "deckDigest": deck_digest,
            "deckSize": len(materialized),
            "columns": columns,
            "tiles": [tile.model_dump(mode="json", by_alias=True) for tile in wire_tiles],
        }
        return WireMontageManifest(
            deck_id=deck_id,
            deck_digest=deck_digest,
            deck_size=len(materialized),
            columns=columns,
            tiles=wire_tiles,
            montage_digest=_digest(unsigned),
        )

    capacity = columns * rows
    tiles: list[MontageTile] = []
    for index, slide in enumerate(materialized):
        preview_digest, preview_reference = _preview(slide, index=index)
        revision = _read(slide, "revision", default=None)
        if type(revision) is not int or revision < 1:
            raise ValueError(f"slides[{index}].revision must be a positive integer")
        title = _read(_read(slide, "title", default=None), "text", default="")
        if not isinstance(title, str):
            raise ValueError(f"slides[{index}].title.text must be a string")
        position = index % capacity
        tiles.append(
            MontageTile(
                slide_number=index + 1,
                sheet=(index // capacity) + 1,
                row=(position // columns) + 1,
                column=(position % columns) + 1,
                slide_id=_required_string(slide, ("slideId", "slide_id"), path=f"slides[{index}].slideId"),
                revision=revision,
                layout_id=_required_string(slide, ("layoutId", "layout_id"), path=f"slides[{index}].layoutId"),
                spec_digest=_required_string(
                    slide,
                    ("specDigest", "spec_digest"),
                    path=f"slides[{index}].specDigest",
                ),
                source_digest=_required_string(
                    slide,
                    ("sourceDigest", "source_digest"),
                    path=f"slides[{index}].sourceDigest",
                ),
                preview_digest=preview_digest,
                preview_reference=preview_reference,
                title=title,
            )
        )

    sheet_count = (len(tiles) + capacity - 1) // capacity
    unsigned = {
        "schemaVersion": "authored-vnext-montage/v1",
        "deckId": deck_id,
        "deckDigest": deck_digest,
        "slideCount": len(tiles),
        "columns": columns,
        "rows": rows,
        "sheetCount": sheet_count,
        "selectionPolicy": "all-slides-in-deck-order",
        "tiles": [tile.to_dict() for tile in tiles],
    }
    return MontageManifest(
        schema_version="authored-vnext-montage/v1",
        deck_id=deck_id,
        deck_digest=deck_digest,
        slide_count=len(tiles),
        columns=columns,
        rows=rows,
        sheet_count=sheet_count,
        selection_policy="all-slides-in-deck-order",
        tiles=tuple(tiles),
        digest=_digest(unsigned),
    )


__all__ = ["MontageManifest", "MontageTile", "build_montage_manifest"]


def build_generation_montage_manifest(deck: object):
    """Build the strict published generation manifest without render I/O."""

    from models.authored_vnext.deck.quality import (
        MontageManifest as WireMontageManifest,
        MontageTile as WireMontageTile,
        require_published_deck_size,
    )
    from services.authored_vnext.master.digests import canonical_digest

    slides = tuple(_read(deck, "slides", default=()) or ())
    require_published_deck_size(len(slides))
    tiles = []
    for index, validated in enumerate(slides):
        slide = _read(validated, "slide", default=None)
        tiles.append(
            WireMontageTile(
                slide_id=_required_string(
                    slide, ("slideId", "slide_id"), path=f"slides[{index}].slideId"
                ),
                slide_index=index,
                png_digest=canonical_digest(
                    {
                        "kind": "deterministic-local-preview",
                        "specDigest": _required_string(
                            slide,
                            ("specDigest", "spec_digest"),
                            path=f"slides[{index}].specDigest",
                        ),
                    }
                ),
            )
        )
    unsigned = {
        "schemaVersion": "deck-montage-manifest/v1",
        "deckId": _read(deck, "deckId", "deck_id"),
        "deckDigest": _read(deck, "digest"),
        "deckSize": len(slides),
        "columns": 5 if len(slides) <= 10 else 10,
        "tiles": [tile.model_dump(mode="json", by_alias=True) for tile in tiles],
    }
    return WireMontageManifest(
        **unsigned, montageDigest=canonical_digest(unsigned)
    )


__all__.append("build_generation_montage_manifest")
