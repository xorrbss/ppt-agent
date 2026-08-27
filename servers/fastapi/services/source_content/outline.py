"""Page-locked text/image outline for authored source-content reflow.

The selected authored template owns the new design.  Only source text and
source-image occurrences survive into the outline; source tables, charts,
shapes, and layout geometry are deliberately not authoring inputs.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import Field, model_validator

from services.source_content.allocator import canonical_digest
from services.source_content.contracts import (
    ContractModel,
    SourceContentManifestPin,
)


OUTLINE_VERSION = "source-document-outline/v1"


class SourceDocumentOutlineSlide(ContractModel):
    source_unit_id: str = Field(min_length=1, max_length=300)
    source_index: int = Field(ge=0)
    title_block_ids: tuple[str, ...] = ()
    text_block_ids: tuple[str, ...] = ()
    image_occurrence_ids: tuple[str, ...] = ()
    table_block_ids: tuple[str, ...] = ()
    chart_block_ids: tuple[str, ...] = ()
    reading_order: tuple[str, ...] = Field(default=(), min_length=0)


class SourceDocumentOutline(ContractModel):
    contract_version: str = OUTLINE_VERSION
    manifest_pin: SourceContentManifestPin
    source_slide_count: int = Field(ge=0)
    slides: tuple[SourceDocumentOutlineSlide, ...]
    outline_digest: str

    @model_validator(mode="after")
    def _validate_locked_cardinality(self) -> "SourceDocumentOutline":
        if self.contract_version != OUTLINE_VERSION:
            raise ValueError("source_content_outline_invalid")
        if self.source_slide_count != len(self.slides):
            raise ValueError("source_content_source_unit_cardinality_mismatch")
        unit_ids = [slide.source_unit_id for slide in self.slides]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("source_content_source_unit_duplicate")
        block_ids = [
            block_id
            for slide in self.slides
            for block_id in slide.reading_order
        ]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("source_content_outline_block_duplicate")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"outline_digest"})
        if self.outline_digest != canonical_digest(payload):
            raise ValueError("source_content_outline_digest_mismatch")
        return self


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True)
    raise ValueError("source_content_manifest_invalid")


def _field(value: Mapping[str, Any], camel: str, snake: str | None = None) -> Any:
    if camel in value:
        return value[camel]
    return value.get(snake or camel)


class SourceDocumentOutlineBuilder:
    """Build one outline item for each manifest source unit, in source order."""

    def build(
        self,
        *,
        manifest: Any,
        manifest_pin: SourceContentManifestPin,
    ) -> SourceDocumentOutline:
        value = _mapping(manifest)
        if (
            _field(value, "manifestId", "manifest_id") != manifest_pin.manifest_id
            or _field(value, "manifestDigest", "manifest_digest") != manifest_pin.digest
        ):
            raise ValueError("source_content_manifest_invalid")
        raw_units = _field(value, "units")
        if not isinstance(raw_units, (list, tuple)):
            raise ValueError("source_content_manifest_invalid")
        counts = _field(value, "counts")
        declared_units = _field(_mapping(counts), "sourceUnits", "source_units") if counts is not None else None
        if declared_units is not None and int(declared_units) != len(raw_units):
            raise ValueError("source_content_source_unit_cardinality_mismatch")
        units = sorted(
            (_mapping(unit) for unit in raw_units),
            key=lambda unit: (
                int(_field(unit, "sourceIndex", "source_index") or 0),
                str(_field(unit, "sourceUnitId", "source_unit_id") or ""),
            ),
        )
        slides: list[SourceDocumentOutlineSlide] = []
        all_manifest_blocks: set[str] = set()
        for unit in units:
            unit_id = str(_field(unit, "sourceUnitId", "source_unit_id") or "")
            raw_blocks = _field(unit, "blocks")
            if not unit_id or not isinstance(raw_blocks, (list, tuple)):
                raise ValueError("source_content_manifest_invalid")
            blocks = sorted(
                (_mapping(block) for block in raw_blocks),
                key=lambda block: (
                    int(_field(block, "order") or 0),
                    str(_field(block, "sourceBlockId", "source_block_id") or ""),
                ),
            )
            ids: list[str] = []
            title: list[str] = []
            text: list[str] = []
            images: list[str] = []
            tables: list[str] = []
            charts: list[str] = []
            for block in blocks:
                block_id = str(_field(block, "sourceBlockId", "source_block_id") or "")
                kind = str(_field(block, "kind") or "")
                role = str(_field(block, "semanticRole", "semantic_role") or "body")
                if not block_id or block_id in all_manifest_blocks:
                    raise ValueError("source_content_manifest_invalid")
                all_manifest_blocks.add(block_id)
                if kind not in {"text", "image"}:
                    # The selected authored template owns the new visual
                    # composition. Tables, charts, shapes, and notes from the
                    # source are not part of the re-authoring outline.
                    continue
                ids.append(block_id)
                if role in {"title", "heading", "section"} and kind == "text":
                    title.append(block_id)
                if kind == "text":
                    text.append(block_id)
                elif kind == "image":
                    # Each source image block is an occurrence, even when its
                    # asset bytes are deduplicated in the manifest store.
                    images.append(block_id)
            slides.append(
                SourceDocumentOutlineSlide(
                    source_unit_id=unit_id,
                    source_index=int(_field(unit, "sourceIndex", "source_index") or 0),
                    title_block_ids=tuple(title),
                    text_block_ids=tuple(text),
                    image_occurrence_ids=tuple(images),
                    table_block_ids=tuple(tables),
                    chart_block_ids=tuple(charts),
                    reading_order=tuple(ids),
                )
            )
        payload = {
            "contractVersion": OUTLINE_VERSION,
            "manifestPin": manifest_pin.model_dump(mode="json", by_alias=True),
            "sourceSlideCount": len(slides),
            "slides": [slide.model_dump(mode="json", by_alias=True) for slide in slides],
        }
        return SourceDocumentOutline(
            manifest_pin=manifest_pin,
            source_slide_count=len(slides),
            slides=tuple(slides),
            outline_digest=canonical_digest(payload),
        )


def build_authored_text_image_slides(
    *, manifest: Any, outline: SourceDocumentOutline
) -> list[str]:
    """Project the admitted source into ordinary authored slide markdown.

    Text remains page-locked and verbatim. Image bytes stay in the immutable
    reference snapshot and are offered to the existing authored source-image
    pipeline by their original slide index.
    """

    value = _mapping(manifest)
    if (
        _field(value, "manifestId", "manifest_id")
        != outline.manifest_pin.manifest_id
        or _field(value, "manifestDigest", "manifest_digest")
        != outline.manifest_pin.digest
    ):
        raise ValueError("source_content_manifest_invalid")
    blocks: dict[str, Mapping[str, Any]] = {}
    for raw_unit in _field(value, "units") or ():
        unit = _mapping(raw_unit)
        for raw_block in _field(unit, "blocks") or ():
            block = _mapping(raw_block)
            block_id = str(_field(block, "sourceBlockId", "source_block_id") or "")
            if block_id:
                blocks[block_id] = block

    slides: list[str] = []
    for slide in outline.slides:
        text_ids = set(slide.text_block_ids)
        title_ids = set(slide.title_block_ids)
        parts: list[str] = []
        for block_id in slide.reading_order:
            if block_id not in text_ids:
                continue
            block = blocks.get(block_id)
            if block is None or str(_field(block, "kind") or "") != "text":
                raise ValueError("source_content_outline_invalid")
            payload = _mapping(_field(block, "payload"))
            text = str(_field(payload, "text") or "").strip()
            if not text:
                continue
            parts.append(f"# {text}" if block_id in title_ids else text)
        # Image-only pages still need one non-visible authoring marker so the
        # ordinary outline contract retains the source page cardinality.
        slides.append("\n\n".join(parts) or "<!-- source-image-only slide -->")
    if len(slides) != outline.source_slide_count:
        raise ValueError("source_content_source_unit_cardinality_mismatch")
    return slides


def build_source_document_outline(
    *, manifest: Any, manifest_pin: SourceContentManifestPin
) -> SourceDocumentOutline:
    """Functional façade used by callers that do not need a builder object."""

    return SourceDocumentOutlineBuilder().build(
        manifest=manifest,
        manifest_pin=manifest_pin,
    )


__all__ = [
    "OUTLINE_VERSION",
    "SourceDocumentOutline",
    "SourceDocumentOutlineBuilder",
    "SourceDocumentOutlineSlide",
    "build_authored_text_image_slides",
    "build_source_document_outline",
]
