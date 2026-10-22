"""Deterministic all-slide montage manifest for published authored vNext decks."""

from __future__ import annotations

from collections.abc import Mapping


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


__all__ = ["build_generation_montage_manifest"]
