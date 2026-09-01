"""Deterministic Pareto search over an immutable source-native frame registry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from .contracts import (
    Digest,
    FrameSearchReceiptV1,
    FrameSelectionReceiptV1,
    Key,
    NarrativeIRV1,
    StrictModel,
)
from .validation import canonical_digest, validate_narrative_ir


SEARCH_POLICY = {
    "schemaVersion": "source-native-frame-search-policy/v1",
    "hardConstraints": [
        "source-native",
        "role",
        "data-representation",
        "density",
        "content-capacity",
        "chromium-line-capacity",
        "content-area-capacity",
        "table-shape-capacity",
        "numeric-card-capacity",
        "image-aspect-capacity",
        "required-slot-coverage",
        "frame-semantics",
    ],
    "ranking": "pareto-layers-then-canonical-digest",
    "providerCallsAdded": 0,
}
SEARCH_POLICY_DIGEST = canonical_digest(SEARCH_POLICY)
SELECTION_POLICY_DIGEST = canonical_digest(
    {
        "schemaVersion": "source-native-frame-selection-policy/v1",
        "selection": "first-pareto-candidate-per-slide",
        "wholeOperation": True,
        "providerCallsAdded": 0,
    }
)


class FrameSearchError(ValueError):
    code = "narrative_frame_search_invalid"

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


class SourceNativeFrameV1(StrictModel):
    frame_id: Key
    frame_digest: Digest
    source_part_digest: Digest
    source_native: Literal[True]
    roles: list[Key] = Field(min_length=1, max_length=100)
    data_representations: list[
        Literal[
            "none",
            "metric",
            "table",
            "chart",
            "timeline",
            "process",
            "comparison",
            "image",
            "quote",
        ]
    ] = Field(min_length=1, max_length=10)
    densities: list[Literal["sparse", "medium", "dense"]] = Field(
        min_length=1, max_length=3
    )
    frame_semantics: list[Key] = Field(default_factory=list, max_length=100)
    max_supporting_points: int = Field(ge=0, le=1000)
    supports_source_owned_content: bool = False
    slot_contracts: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    executable: bool = True


class DesignPackSnapshotV1(StrictModel):
    schema_version: Literal["design-pack-snapshot/v1"] = "design-pack-snapshot/v1"
    design_pack_id: Key
    version: int = Field(ge=1)
    manifest_digest: Digest
    frame_registry_digest: Digest
    frames: list[SourceNativeFrameV1] = Field(min_length=1, max_length=1000)

    def unique_frames(self) -> "DesignPackSnapshotV1":
        ids = [item.frame_id for item in self.frames]
        digests = [item.frame_digest for item in self.frames]
        if len(ids) != len(set(ids)) or len(digests) != len(set(digests)):
            raise ValueError("design_pack_frame_identity_duplicate")
        return self


def _pack(value: Mapping[str, Any] | object) -> DesignPackSnapshotV1:
    if not isinstance(value, Mapping):
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            value = dump(mode="json", by_alias=True)
        else:
            fields = (
                "schema_version",
                "design_pack_id",
                "version",
                "manifest_digest",
                "frame_registry_digest",
                "frames",
            )
            value = {
                name: getattr(value, name)
                for name in fields
                if hasattr(value, name)
            }
    try:
        pack = DesignPackSnapshotV1.model_validate(value)
        return pack.unique_frames()
    except (ValidationError, ValueError) as error:
        raise FrameSearchError("design_pack_snapshot_invalid") from error


def _slot_envelope(slot: Mapping[str, Any]) -> Mapping[str, Any]:
    value = slot.get("capacityEnvelope", slot.get("capacity_envelope", {}))
    return value if isinstance(value, Mapping) else {}


def _capacity(slide: object, frame: SourceNativeFrameV1) -> tuple[float, list[str]]:
    text_values = [str(getattr(slide, "key_message"))]
    text_values.extend(str(item) for item in getattr(slide, "supporting_points"))
    characters = sum(len(" ".join(item.split())) for item in text_values)
    data = list(getattr(slide, "data"))
    numeric_items = sum(
        1
        for item in data
        if str(getattr(item, "kind", "")).lower() in {"metric", "number", "numeric"}
    )
    table_rows = 0
    table_columns = 0
    image_aspects: list[float] = []
    for item in data:
        payload = getattr(item, "value", None)
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            table_rows += len(payload)
            table_columns = max(
                table_columns,
                max(
                    (
                        len(row)
                        for row in payload
                        if isinstance(row, Sequence) and not isinstance(row, (str, bytes))
                    ),
                    default=0,
                ),
            )
        if isinstance(payload, Mapping):
            aspect = payload.get("aspectRatio", payload.get("aspect_ratio"))
            try:
                if aspect is not None:
                    image_aspects.append(float(aspect))
            except (TypeError, ValueError):
                pass
    if not frame.slot_contracts:
        point_count = len(getattr(slide, "supporting_points"))
        legacy = 1.0 if point_count == 0 else min(1.0, frame.max_supporting_points / point_count)
        return legacy, []
    envelopes = [_slot_envelope(item) for item in frame.slot_contracts]
    character_limits = [
        item.get("maxCharacters", item.get("max_characters")) for item in envelopes
    ]
    line_limits = [item.get("maxLines", item.get("max_lines")) for item in envelopes]
    line_widths = [
        item.get("charactersPerLine", item.get("characters_per_line"))
        for item in envelopes
    ]
    max_characters = (
        sum(int(item) for item in character_limits)
        if character_limits and all(item is not None for item in character_limits)
        else None
    )
    max_lines = (
        sum(int(item) for item in line_limits)
        if line_limits
        and all(item is not None for item in line_limits)
        and all(item is not None for item in line_widths)
        else None
    )
    content_area = sum(float(item.get("contentArea", item.get("content_area", 0)) or 0) for item in envelopes)
    max_rows = sum(int(item.get("maxTableRows", item.get("max_table_rows", 0)) or 0) for item in envelopes)
    max_columns = max((int(item.get("maxTableColumns", item.get("max_table_columns", 0)) or 0) for item in envelopes), default=0)
    max_numeric = sum(int(item.get("maxNumericItems", item.get("max_numeric_items", 0)) or 0) for item in envelopes)
    estimated_lines = sum(max(1, (len(item) + 39) // 40) for item in text_values if item)
    estimated_area = characters * 150
    reasons: list[str] = []
    if characters and max_characters is not None and max_characters < characters:
        reasons.append("content-character-capacity-exceeded")
    if estimated_lines and max_lines is not None and max_lines < estimated_lines:
        reasons.append("chromium-line-capacity-exceeded")
    if estimated_area and content_area < estimated_area:
        reasons.append("content-area-capacity-exceeded")
    if table_rows > max_rows or table_columns > max_columns:
        reasons.append("table-shape-capacity-exceeded")
    if numeric_items > max_numeric:
        reasons.append("numeric-card-capacity-exceeded")
    for aspect in image_aspects:
        if not any(
            float(item.get("aspectRatioMin", item.get("aspect_ratio_min", 0)) or 0)
            <= aspect
            <= float(item.get("aspectRatioMax", item.get("aspect_ratio_max", 1000)) or 1000)
            for item in envelopes
        ):
            reasons.append("image-aspect-capacity-exceeded")
            break
    required_slots = [item for item in frame.slot_contracts if item.get("required") is True]
    if len(required_slots) > 1 + len(getattr(slide, "supporting_points")) + len(data):
        reasons.append("required-slot-coverage-missing")
    ratios = [(content_area / estimated_area) if estimated_area else 1.0]
    if max_characters is not None:
        ratios.append((max_characters / characters) if characters else 1.0)
    if max_lines is not None:
        ratios.append((max_lines / estimated_lines) if estimated_lines else 1.0)
    return min(1.0, *ratios), reasons


def _quality(slide: object, frame: SourceNativeFrameV1) -> dict[str, float]:
    semantics = set(getattr(slide, "frame_semantics"))
    supported = set(frame.frame_semantics)
    semantic_fit = 1.0 if not semantics else len(semantics & supported) / len(semantics)
    capacity, _ = _capacity(slide, frame)
    owns_source = bool(getattr(slide, "source_ownership"))
    return {
        "roleFitness": 1.0 if getattr(slide, "role") in frame.roles else 0.0,
        "contentCapacity": round(capacity, 6),
        "sourceCompatibility": (
            1.0 if not owns_source or frame.supports_source_owned_content else 0.0
        ),
        "semanticFit": round(semantic_fit, 6),
    }


def _rejections(slide: object, frame: SourceNativeFrameV1) -> list[str]:
    reasons: list[str] = []
    if not frame.executable:
        reasons.append("frame-fidelity-only")
    if getattr(slide, "role") not in frame.roles:
        reasons.append("role-unsupported")
    if getattr(slide, "data_representation") not in frame.data_representations:
        reasons.append("data-representation-unsupported")
    if getattr(slide, "density") not in frame.densities:
        reasons.append("density-unsupported")
    if len(getattr(slide, "supporting_points")) > frame.max_supporting_points:
        reasons.append("content-capacity-exceeded")
    _, capacity_reasons = _capacity(slide, frame)
    reasons.extend(capacity_reasons)
    if getattr(slide, "source_ownership") and not frame.supports_source_owned_content:
        reasons.append("source-ownership-unsupported")
    if not set(getattr(slide, "frame_semantics")).issubset(frame.frame_semantics):
        reasons.append("frame-semantics-unsupported")
    return reasons


def _dominates(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    keys = ("roleFitness", "contentCapacity", "sourceCompatibility", "semanticFit")
    return all(left[key] >= right[key] for key in keys) and any(
        left[key] > right[key] for key in keys
    )


def _pareto_layers(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = list(items)
    ordered: list[dict[str, Any]] = []
    layer = 0
    while remaining:
        frontier = [
            item
            for item in remaining
            if not any(
                _dominates(other["qualityVector"], item["qualityVector"])
                for other in remaining
                if other is not item
            )
        ]
        for item in sorted(frontier, key=lambda entry: entry["candidateDigest"]):
            item["paretoLayer"] = layer
            ordered.append(item)
        remaining = [item for item in remaining if item not in frontier]
        layer += 1
    return ordered


def search_source_native_frames(
    *,
    narrative: NarrativeIRV1,
    design_pack: Mapping[str, Any] | object,
) -> FrameSearchReceiptV1:
    narrative = validate_narrative_ir(narrative)
    pack = _pack(design_pack)
    slide_receipts: list[dict[str, Any]] = []
    any_missing = False
    for slide in narrative.slides:
        passed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for frame in pack.frames:
            reasons = _rejections(slide, frame)
            vector = _quality(slide, frame)
            unsigned = {
                "slideKey": slide.slide_key,
                "frameId": frame.frame_id,
                "frameDigest": frame.frame_digest,
                "sourcePartDigest": frame.source_part_digest,
                "qualityVector": vector,
                "rejectionReasons": reasons,
                "searchPolicyDigest": SEARCH_POLICY_DIGEST,
            }
            item = {
                "candidateId": "candidate-" + canonical_digest(unsigned)[7:19],
                "frameId": frame.frame_id,
                "frameDigest": frame.frame_digest,
                "sourcePartDigest": frame.source_part_digest,
                "hardConstraintStatus": "failed" if reasons else "passed",
                "rejectionReasons": reasons,
                "qualityVector": vector,
                "paretoLayer": 999 if reasons else 0,
                "rank": 1,
                "candidateDigest": canonical_digest(unsigned),
            }
            (failed if reasons else passed).append(item)
        ranked = _pareto_layers(passed)
        ranked.extend(sorted(failed, key=lambda entry: entry["candidateDigest"]))
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        eligible_count = len(passed)
        any_missing = any_missing or eligible_count == 0
        slide_receipts.append(
            {
                "slideKey": slide.slide_key,
                "sequenceIndex": slide.sequence_index,
                "candidates": ranked,
                "eligibleCount": eligible_count,
            }
        )
    unsigned = {
        "schemaVersion": "frame-search-receipt/v1",
        "operationId": str(narrative.operation_id),
        "designPackId": pack.design_pack_id,
        "designPackVersion": pack.version,
        "designPackManifestDigest": pack.manifest_digest,
        "frameRegistryDigest": pack.frame_registry_digest,
        "searchPolicyDigest": SEARCH_POLICY_DIGEST,
        "narrativeDigest": narrative.narrative_digest,
        "slides": slide_receipts,
        "status": "no-compatible-frame" if any_missing else "passed",
    }
    return FrameSearchReceiptV1.model_validate(
        {**unsigned, "receiptDigest": canonical_digest(unsigned)}
    )


def select_source_native_frames(
    search: FrameSearchReceiptV1,
) -> FrameSelectionReceiptV1:
    if search.status != "passed":
        raise FrameSearchError("source_native_frame_selection_unavailable")
    selections: list[dict[str, Any]] = []
    for slide in search.slides:
        eligible = [
            item for item in slide.candidates if item.hard_constraint_status == "passed"
        ]
        if not eligible:
            raise FrameSearchError("source_native_frame_selection_unavailable")
        selected = min(eligible, key=lambda item: (item.pareto_layer, item.rank, item.candidate_digest))
        selections.append(
            {
                "slideKey": slide.slide_key,
                "sequenceIndex": slide.sequence_index,
                "selectedCandidateId": selected.candidate_id,
                "selectedFrameId": selected.frame_id,
                "selectedFrameDigest": selected.frame_digest,
                "selectedSourcePartDigest": selected.source_part_digest,
                "qualityVector": selected.quality_vector.model_dump(mode="json", by_alias=True),
                "tieBreakDigest": canonical_digest(
                    {
                        "candidateDigest": selected.candidate_digest,
                        "paretoLayer": selected.pareto_layer,
                        "rank": selected.rank,
                        "selectionPolicyDigest": SELECTION_POLICY_DIGEST,
                    }
                ),
            }
        )
    unsigned = {
        "schemaVersion": "frame-selection-receipt/v1",
        "operationId": str(search.operation_id),
        "designPackId": search.design_pack_id,
        "designPackVersion": search.design_pack_version,
        "designPackManifestDigest": search.design_pack_manifest_digest,
        "frameRegistryDigest": search.frame_registry_digest,
        "searchReceiptDigest": search.receipt_digest,
        "narrativeDigest": search.narrative_digest,
        "selectionPolicyDigest": SELECTION_POLICY_DIGEST,
        "slides": selections,
    }
    return FrameSelectionReceiptV1.model_validate(
        {**unsigned, "receiptDigest": canonical_digest(unsigned)}
    )


__all__ = [
    "DesignPackSnapshotV1",
    "FrameSearchError",
    "SEARCH_POLICY_DIGEST",
    "SELECTION_POLICY_DIGEST",
    "SourceNativeFrameV1",
    "search_source_native_frames",
    "select_source_native_frames",
]
