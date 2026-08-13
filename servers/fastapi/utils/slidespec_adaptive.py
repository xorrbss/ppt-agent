"""Adaptive authoring conversion into sealed SlideSpec documents."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from models.slidespec_v1 import (
    Bounds,
    ContentNode,
    LayoutNode,
    Provenance,
    SlideDigests,
    SlideSpec,
    SLIDESPEC_SCHEMA,
)
from utils.slidespec_core import (
    ADAPTER_VERSION,
    ZERO_DIGEST,
    SlideSpecError,
    seal_slidespec,
    validate_slidespec,
)

def _opaque_id(prefix: str) -> str:
    return prefix + uuid4().hex

def _adaptive_blocks(source: Any) -> list[dict[str, Any]]:
    if isinstance(source, BaseModel):
        from models.slide_spec_model import spec_to_blocks

        return [dict(block) for block in spec_to_blocks(source)["blocks"]]
    data = dict(source)
    if "blocks" in data:
        return [
            block.model_dump(mode="json") if isinstance(block, BaseModel) else dict(block)
            for block in data["blocks"]
        ]
    from models.slide_spec_model import SlideSpecUnion, spec_to_blocks

    parsed = TypeAdapter(SlideSpecUnion).validate_python(data)
    return [dict(block) for block in spec_to_blocks(parsed)["blocks"]]

def adapt_adaptive_slide(
    source: Any,
    *,
    slide_id: str,
    identity_map: Mapping[str, str] | None = None,
) -> tuple[SlideSpec, dict[str, str]]:
    """Minimally adapt legacy blocks; persist the returned map for stable identities."""

    blocks = _adaptive_blocks(source)
    if not blocks:
        raise SlideSpecError("adapter.blocks.empty")
    identities = dict(identity_map or {})
    root_key = "layout:$root"
    identities.setdefault(root_key, _opaque_id("lay:"))
    contents: list[ContentNode] = []
    layouts: list[LayoutNode] = []
    kind_map = {
        "title": "title",
        "text": "text",
        "bullets": "list",
        "list": "list",
        "stat": "metric",
        "metric": "metric",
        "table": "table",
        "chart": "chart",
        "image": "image",
        "connector": "connector",
    }
    seen_locators: set[str] = set()
    height = 1 / len(blocks)
    for index, block in enumerate(blocks):
        source_id = str(block.get("id") or block.get("blockId") or f"slot-{index}")
        if source_id in seen_locators:
            raise SlideSpecError("adapter.block_id.duplicate", source_id)
        seen_locators.add(source_id)
        semantic_key = f"semantic:{source_id}"
        layout_key = f"layout:{source_id}"
        identities.setdefault(semantic_key, _opaque_id("sem:"))
        identities.setdefault(layout_key, _opaque_id("lay:"))
        block_type = str(block.get("type") or block.get("kind") or "text")
        text = next(
            (
                str(block[key])
                for key in ("text", "title", "value", "label")
                if block.get(key) is not None
            ),
            None,
        )
        payload = {key: value for key, value in block.items() if key not in {"id", "blockId"}}
        contents.append(
            ContentNode(
                semanticId=identities[semantic_key],
                kind=kind_map.get(block_type, "shape"),
                text=text,
                payload=payload,
                readingOrder=index,
            )
        )
        layouts.append(
            LayoutNode(
                layoutId=identities[layout_key],
                kind="content",
                contentSemanticId=identities[semantic_key],
                bounds=Bounds(x=0, y=index * height, width=1, height=height),
            )
        )
    draft = SlideSpec(
        schemaUri=SLIDESPEC_SCHEMA,
        specRevision=1,
        slideId=slide_id,
        content=tuple(contents),
        layout=LayoutNode(
            layoutId=identities[root_key], kind="canvas", children=tuple(layouts)
        ),
        tokens=(),
        provenance=Provenance(
            source="adaptive-adapter", adapterVersion=ADAPTER_VERSION
        ),
        digests=SlideDigests(
            content=ZERO_DIGEST,
            hierarchy=ZERO_DIGEST,
            importantTokens=ZERO_DIGEST,
            spec=ZERO_DIGEST,
        ),
    )
    sealed = seal_slidespec(draft)
    validate_slidespec(sealed)
    return sealed, identities
