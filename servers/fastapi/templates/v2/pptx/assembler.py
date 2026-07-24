from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from templates.v2.models.elements import (
    Container,
    Fill,
    Position,
    Size,
    Text,
    TextRun,
)
from templates.v2.models.layouts import (
    Component,
    RawSlideLayout,
    RawSlideLayouts,
    SlideLayout,
    SlideLayouts,
)

from .models import PresentationCandidates, ShapeCandidate
from .relationship_graph import relationship_graph_manifest_summary


@dataclass(frozen=True)
class AssembledTemplateV2Draft:
    raw_layouts: RawSlideLayouts
    layouts: SlideLayouts
    contents: list[dict[str, Any]]
    manifest: dict[str, Any]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _slot_name(candidate: ShapeCandidate) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", candidate.name).strip("_").lower()
    return (value or f"shape_{candidate.source_id}")[:60]


def _element(candidate: ShapeCandidate, *, relative: bool):
    position = Position(
        x=0 if relative else candidate.x,
        y=0 if relative else candidate.y,
    )
    size = Size(width=max(candidate.width, 1), height=max(candidate.height, 1))
    if candidate.kind == "text":
        text = candidate.text or ""
        return Text(
            type="text",
            position=position,
            size=size,
            rotation=candidate.rotation,
            runs=[TextRun(text=text)],
            decorative=False,
            name=_slot_name(candidate),
            min_length=0,
            max_length=max(1_000, len(text) * 4),
        )
    if candidate.kind == "container":
        return Container(
            type="container",
            position=position,
            size=size,
            rotation=candidate.rotation,
            fill=Fill(color=candidate.fill_color or "#FFFFFF"),
        )
    raise ValueError("unsupported_candidate_cannot_be_assembled")


def assemble_template_v2_draft(
    candidates: PresentationCandidates,
) -> AssembledTemplateV2Draft:
    raw_layouts: list[RawSlideLayout] = []
    layouts: list[SlideLayout] = []
    contents: list[dict[str, Any]] = []
    slide_manifests: list[dict[str, Any]] = []
    for slide_index, slide in enumerate(candidates.slides, start=1):
        layout_id = _stable_id(
            "pptx_layout",
            candidates.source_sha256,
            slide.relationship_id,
        )
        supported = [shape for shape in slide.shapes if shape.kind != "unsupported"]
        unsupported = [shape for shape in slide.shapes if shape.kind == "unsupported"]
        raw_layouts.append(
            RawSlideLayout(
                id=layout_id,
                description=f"Imported PPTX slide {slide_index}",
                elements=[_element(shape, relative=False) for shape in supported],
            )
        )
        components: list[Component] = []
        content: dict[str, Any] = {}
        for shape in supported:
            component_id = _stable_id(
                "component",
                candidates.source_sha256,
                slide.relationship_id,
                shape.source_id,
            )
            components.append(
                Component(
                    id=component_id,
                    description=f"Imported editable PPTX shape {shape.name}"[:300],
                    position=Position(x=shape.x, y=shape.y),
                    elements=[_element(shape, relative=True)],
                )
            )
            if shape.kind == "text":
                content[component_id] = {_slot_name(shape): shape.text or ""}
        layouts.append(
            SlideLayout(
                id=layout_id,
                description=f"Deterministic OOXML import for slide {slide_index}",
                components=components,
            )
        )
        slide_manifests.append(
            {
                "slide": slide_index,
                "source_part": slide.source_part,
                "editable_shape_count": len(supported),
                "confidence": (
                    min((shape.confidence for shape in supported), default=0)
                    if not unsupported
                    else 0.5
                ),
                "unsupported": [
                    {
                        "source_id": shape.source_id,
                        "name": shape.name,
                        "reason": shape.unsupported_reason,
                    }
                    for shape in unsupported
                ],
                "external_relationship_ids_ignored": slide.external_relationships,
                "fallback": {
                    "kind": "manual_review",
                    "reason": "render_and_vision_provider_unavailable",
                },
            }
        )
        contents.append(content)
    strict_raw = RawSlideLayouts(layouts=raw_layouts)
    strict_layouts = SlideLayouts(layouts=layouts)
    needs_review = True
    manifest = {
        "schema_version": 1,
        "source_sha256": candidates.source_sha256,
        "parser": {
            "name": "deterministic-ooxml",
            "network_access": False,
        },
        "render": {
            "available": False,
            "reason": "libreoffice_not_invoked_by_foundation",
        },
        "vision": {
            "available": False,
            "reason": "vision_provider_not_configured",
            "network_access": False,
        },
        "review": {
            "required": needs_review,
            "reason": "visual_fidelity_not_verified",
        },
        "fallback": {
            "kind": "manual_review",
            "reason": "render_and_vision_provider_unavailable",
        },
        "slides": slide_manifests,
    }
    if candidates.relationship_graph is not None:
        manifest["structure_evidence"] = relationship_graph_manifest_summary(
            candidates.relationship_graph
        )
    return AssembledTemplateV2Draft(
        raw_layouts=strict_raw,
        layouts=strict_layouts,
        contents=contents,
        manifest=manifest,
    )
