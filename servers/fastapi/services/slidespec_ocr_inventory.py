"""Canonical OCR truth derived only from immutable SlideSpec content."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class SlideSpecInput:
    """Minimal canonical content view; browser text is intentionally absent."""

    revision_id: str
    spec_hash: str
    semantic_text: Mapping[str, str]
    important_tokens: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class CanonicalOcrInventory:
    text_by_semantic_id: Mapping[str, str]
    important_tokens: tuple[str, ...]


def canonical_ocr_inventory(spec: SlideSpecInput) -> CanonicalOcrInventory:
    """Produce OCR truth solely from the immutable SlideSpec content plane."""

    return CanonicalOcrInventory(
        text_by_semantic_id=dict(sorted(spec.semantic_text.items())),
        important_tokens=tuple(sorted(set(spec.important_tokens))),
    )
