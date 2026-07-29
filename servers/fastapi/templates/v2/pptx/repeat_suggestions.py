from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from .models import PresentationCandidates, ShapeCandidate


def _rounded(value: float) -> float:
    return round(value, 3)


def _regular_offsets(shapes: list[ShapeCandidate], axis: str) -> bool:
    offsets = sorted(
        _rounded(shape.x if axis == "horizontal" else shape.y)
        for shape in shapes
    )
    if len(set(offsets)) != len(offsets):
        return False
    if len(offsets) == 2:
        return True
    gaps = [_rounded(right - left) for left, right in zip(offsets, offsets[1:])]
    return max(gaps) - min(gaps) <= 0.01


def _proposal_id(
    source_sha256: str,
    source_part: str,
    axis: str,
    source_ids: list[str],
) -> str:
    canonical = json.dumps(
        [source_sha256, source_part, axis, source_ids],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"repeat_{digest}"


def build_repeat_block_suggestions(
    candidates: PresentationCandidates | Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return deterministic review suggestions without mutating candidates."""
    strict = (
        candidates
        if isinstance(candidates, PresentationCandidates)
        else PresentationCandidates.model_validate(candidates)
    )
    proposals: list[dict[str, Any]] = []
    for slide_index, slide in enumerate(strict.slides, start=1):
        supported = [
            shape
            for shape in slide.shapes
            if shape.kind != "unsupported"
            and shape.width > 0
            and shape.height > 0
        ]
        for axis in ("horizontal", "vertical"):
            groups: dict[tuple[Any, ...], list[ShapeCandidate]] = defaultdict(list)
            for shape in supported:
                alignment = shape.y if axis == "horizontal" else shape.x
                visual_key = (
                    (shape.fill_color or "").casefold()
                    if shape.kind == "container"
                    else ""
                )
                groups[
                    (
                        shape.kind,
                        _rounded(shape.width),
                        _rounded(shape.height),
                        _rounded(alignment),
                        visual_key,
                    )
                ].append(shape)
            for shapes in groups.values():
                if len(shapes) < 2 or not _regular_offsets(shapes, axis):
                    continue
                ordered = sorted(
                    shapes,
                    key=lambda shape: (
                        shape.x if axis == "horizontal" else shape.y,
                        shape.source_id,
                    ),
                )
                source_ids = [shape.source_id for shape in ordered]
                proposals.append(
                    {
                        "id": _proposal_id(
                            strict.source_sha256,
                            slide.source_part,
                            axis,
                            source_ids,
                        ),
                        "kind": "repeat_block_merge",
                        "status": "suggested",
                        "slide": slide_index,
                        "source_part": slide.source_part,
                        "axis": axis,
                        "source_ids": source_ids,
                        "confidence": min(
                            _rounded(shape.confidence) for shape in ordered
                        ),
                    }
                )

    proposals.sort(
        key=lambda proposal: (
            proposal["slide"],
            -len(proposal["source_ids"]),
            0 if proposal["axis"] == "horizontal" else 1,
            proposal["source_ids"],
        )
    )
    accepted: list[dict[str, Any]] = []
    used_by_slide: dict[int, set[str]] = defaultdict(set)
    for proposal in proposals:
        slide = int(proposal["slide"])
        source_ids = set(proposal["source_ids"])
        if source_ids & used_by_slide[slide]:
            continue
        accepted.append(proposal)
        used_by_slide[slide].update(source_ids)
    return accepted
