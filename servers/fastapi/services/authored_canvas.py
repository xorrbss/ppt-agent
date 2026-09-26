"""Canonical canvas contract for the existing Authored product path."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from models.authored_vnext.master.base import StrictCamelModel
from models.authored_vnext.master.contract import CanvasContract, Rect


AUTHORED_CANVAS_CONTRACT_VERSION = "authored-canvas/v1"
LANDSCAPE_ASPECT_RATIO = "16:9"
PORTRAIT_ASPECT_RATIO = "9:16"
PROPOSAL_ASPECT_RATIO = "9:13"
AuthoredAspectRatio = Literal["16:9", "9:16", "9:13"]

# Product exposure is enabled only after the lead completes provider and file
# readback acceptance. Backend admission remains available for that acceptance run.
PORTRAIT_RELEASE_VERIFIED = True
_CANVAS_SIZE_BY_ASPECT_RATIO: dict[AuthoredAspectRatio, tuple[int, int]] = {
    LANDSCAPE_ASPECT_RATIO: (1280, 720),
    PORTRAIT_ASPECT_RATIO: (720, 1280),
    PROPOSAL_ASPECT_RATIO: (720, 1040),
}
_SAFE_MARGIN_PX = 64.0


class AuthoredCanvasMetadata(StrictCamelModel):
    contract_version: Literal["authored-canvas/v1"]
    # The display ratio never selects geometry for a stored presentation.
    aspect_ratio: str = Field(min_length=1, max_length=32)
    width_px: int = Field(ge=320, le=7680)
    height_px: int = Field(ge=180, le=4320)
    locale: str = Field(min_length=2, max_length=32)
    safe_area: Rect

    @model_validator(mode="after")
    def validate_canvas_geometry(self) -> "AuthoredCanvasMetadata":
        if self.safe_area.right > self.width_px or self.safe_area.bottom > self.height_px:
            raise ValueError("authored_canvas_safe_area_out_of_bounds")
        return self

    def canvas_contract(self) -> CanvasContract:
        return CanvasContract(
            width_px=self.width_px,
            height_px=self.height_px,
            locale=self.locale,
            safe_area=self.safe_area,
        )


def _safe_area(width_px: int, height_px: int) -> Rect:
    return Rect(
        x=_SAFE_MARGIN_PX,
        y=_SAFE_MARGIN_PX,
        width=float(width_px) - (_SAFE_MARGIN_PX * 2),
        height=float(height_px) - (_SAFE_MARGIN_PX * 2),
    )


def canvas_metadata_for_aspect_ratio(
    aspect_ratio: AuthoredAspectRatio,
) -> AuthoredCanvasMetadata:
    width_px, height_px = _CANVAS_SIZE_BY_ASPECT_RATIO[aspect_ratio]
    return AuthoredCanvasMetadata(
        contract_version=AUTHORED_CANVAS_CONTRACT_VERSION,
        aspect_ratio=aspect_ratio,
        width_px=width_px,
        height_px=height_px,
        locale="ko-KR",
        safe_area=_safe_area(width_px, height_px),
    )


def _metadata_wire(metadata: AuthoredCanvasMetadata) -> dict[str, object]:
    return metadata.model_dump(mode="json", by_alias=True)


def _canvas_metadata_from_contract(aspect_ratio: AuthoredAspectRatio, canvas: object) -> AuthoredCanvasMetadata:
    try:
        contract = CanvasContract.model_validate(canvas)
    except (TypeError, ValueError) as error:
        raise ValueError("authored_canvas_contract_invalid") from error
    try:
        expected_width, expected_height = _CANVAS_SIZE_BY_ASPECT_RATIO[aspect_ratio]
        if (contract.width_px, contract.height_px) != (expected_width, expected_height):
            raise ValueError("authored_canvas_dimensions_mismatch")
        return AuthoredCanvasMetadata(
            contract_version=AUTHORED_CANVAS_CONTRACT_VERSION,
            aspect_ratio=aspect_ratio,
            width_px=contract.width_px,
            height_px=contract.height_px,
            locale=contract.locale,
            safe_area=contract.safe_area,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("authored_canvas_contract_invalid") from error


def request_canvas_contract(request: Any) -> CanvasContract:
    source_document_canvas = getattr(request, "_document_canvas", None)
    if (
        source_document_canvas is not None
        and getattr(request, "strategy", None) == "svg"
        and getattr(request, "source_mode", None) == "source_content_restyle"
    ):
        from services.document_canvas import DocumentCanvasMetadata

        metadata = DocumentCanvasMetadata.model_validate(source_document_canvas)
        request._document_canvas = metadata
        request._authored_canvas = metadata.canvas_contract()
        return request._authored_canvas
    if getattr(request, "page_size", None) == "A4":
        from services.document_canvas import document_canvas_for_orientation

        metadata = document_canvas_for_orientation(request.orientation)
        frozen = getattr(request, "_document_canvas", None)
        if frozen is not None and frozen != metadata:
            raise ValueError("document_canvas_request_mismatch")
        request._document_canvas = metadata
        request._authored_canvas = metadata.canvas_contract()
        return request._authored_canvas
    generic_metadata = canvas_metadata_for_aspect_ratio(request.aspect_ratio)
    master_contract = getattr(request, "_authored_master_contract", None)
    master_canvas = getattr(master_contract, "canvas", None)
    expected_metadata = (
        _canvas_metadata_from_contract(request.aspect_ratio, master_canvas)
        if master_canvas is not None
        else generic_metadata
    )
    pinned = getattr(request, "_authored_canvas", None)
    if pinned is not None:
        pinned_metadata = _canvas_metadata_from_contract(request.aspect_ratio, pinned)
        master_identity = (
            getattr(request, "master_pin", None) is not None
            or getattr(request, "_authored_master_pin", None) is not None
        )
        if (master_canvas is not None or not master_identity) and (
            pinned_metadata.canvas_contract() != expected_metadata.canvas_contract()
        ):
            raise ValueError("authored_canvas_request_mismatch")
        request._authored_canvas = pinned_metadata.canvas_contract()
        return request._authored_canvas
    request._authored_canvas = expected_metadata.canvas_contract()
    return request._authored_canvas


def request_canvas_metadata(request: Any) -> dict[str, object]:
    contract = request_canvas_contract(request)
    if getattr(request, "_document_canvas", None) is not None:
        return request._document_canvas.model_dump(mode="json", by_alias=True)
    return _metadata_wire(_canvas_metadata_from_contract(request.aspect_ratio, contract))


def restore_request_canvas_metadata(request: Any, payload: object, *, present: bool = True) -> None:
    if not present:
        if getattr(request, "page_size", None) is not None:
            raise ValueError("document_canvas_contract_missing")
        if request.aspect_ratio != LANDSCAPE_ASPECT_RATIO:
            raise ValueError("authored_canvas_contract_missing")
        return
    try:
        from services.document_canvas import (
            DocumentCanvasMetadata,
            parse_canvas_metadata,
            document_canvas_for_orientation,
        )

        metadata = parse_canvas_metadata(payload)
        if isinstance(metadata, DocumentCanvasMetadata):
            source_manifest_canvas = (
                getattr(request, "strategy", None) == "svg"
                and getattr(request, "source_mode", None) == "source_content_restyle"
                and getattr(request, "page_size", None) is None
            )
            if source_manifest_canvas:
                if getattr(request, "orientation", None) is not None:
                    raise ValueError("document_canvas_request_mismatch")
            elif getattr(request, "page_size", None) != "A4" or metadata != document_canvas_for_orientation(
                request.orientation
            ):
                raise ValueError("document_canvas_request_mismatch")
            request._document_canvas = metadata
            request._authored_canvas = metadata.canvas_contract()
            return
        if getattr(request, "page_size", None) is not None:
            raise ValueError("document_canvas_request_mismatch")
    except (TypeError, ValueError) as error:
        raise ValueError("authored_canvas_contract_invalid") from error
    if metadata.aspect_ratio != request.aspect_ratio:
        raise ValueError("authored_canvas_request_mismatch")
    request._authored_canvas = metadata.canvas_contract()


def presentation_canvas_metadata(theme: object) -> dict[str, object]:
    if isinstance(theme, Mapping) and "documentCanvas" in theme:
        from services.document_canvas import DocumentCanvasMetadata

        metadata = DocumentCanvasMetadata.model_validate(theme["documentCanvas"])
        projection = theme.get("authored_vnext")
        if isinstance(projection, Mapping) and "canvas" in projection:
            raise ValueError("document_canvas_legacy_conflict")
        return metadata.model_dump(mode="json", by_alias=True)
    authored_projection: object = None
    if isinstance(theme, Mapping):
        authored_projection = theme.get("authored_vnext")
    if not isinstance(authored_projection, Mapping) or "canvas" not in authored_projection:
        return _metadata_wire(canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO))
    try:
        metadata = AuthoredCanvasMetadata.model_validate(authored_projection["canvas"])
    except (TypeError, ValueError) as error:
        raise ValueError("authored_canvas_contract_invalid") from error
    return _metadata_wire(metadata)


def presentation_canvas_contract(theme: object) -> CanvasContract:
    from services.document_canvas import parse_canvas_metadata

    metadata = parse_canvas_metadata(presentation_canvas_metadata(theme))
    return metadata.canvas_contract()


def merge_presentation_canvas(theme: object, canvas_metadata: Mapping[str, object]) -> dict[str, object]:
    merged_theme = dict(theme) if isinstance(theme, Mapping) else {}
    from services.document_canvas import DocumentCanvasMetadata, parse_canvas_metadata

    metadata = parse_canvas_metadata(dict(canvas_metadata))
    if isinstance(metadata, DocumentCanvasMetadata):
        current = merged_theme.get("documentCanvas")
        if current is not None and DocumentCanvasMetadata.model_validate(current) != metadata:
            raise ValueError("document_canvas_request_mismatch")
        projection = merged_theme.get("authored_vnext")
        if isinstance(projection, Mapping) and "canvas" in projection:
            raise ValueError("document_canvas_legacy_conflict")
        merged_theme["documentCanvas"] = metadata.model_dump(mode="json", by_alias=True)
        return merged_theme
    if "documentCanvas" in merged_theme:
        raise ValueError("document_canvas_request_mismatch")
    projection = merged_theme.get("authored_vnext")
    merged_projection = dict(projection) if isinstance(projection, Mapping) else {}
    merged_projection["canvas"] = dict(canvas_metadata)
    merged_theme["authored_vnext"] = merged_projection
    return merged_theme


def merge_authored_vnext_projection(theme: object, projection_updates: Mapping[str, object]) -> dict[str, object]:
    """Overlay a scene projection without discarding frozen canvas metadata."""

    merged_theme = dict(theme) if isinstance(theme, Mapping) else {}
    current_projection = merged_theme.get("authored_vnext")
    merged_projection = dict(current_projection) if isinstance(current_projection, Mapping) else {}
    merged_projection.update(dict(projection_updates))
    if "documentCanvas" in merged_theme and "canvas" in merged_projection:
        from services.document_canvas import DocumentCanvasMetadata

        if DocumentCanvasMetadata.model_validate(merged_projection["canvas"]) != DocumentCanvasMetadata.model_validate(
            merged_theme["documentCanvas"]
        ):
            raise ValueError("document_canvas_request_mismatch")
        merged_projection.pop("canvas")
    merged_theme["authored_vnext"] = merged_projection
    return merged_theme


def supported_aspect_ratios_for_style(style_id: str) -> list[AuthoredAspectRatio]:
    if style_id == "default" and PORTRAIT_RELEASE_VERIFIED:
        return [LANDSCAPE_ASPECT_RATIO, PORTRAIT_ASPECT_RATIO, PROPOSAL_ASPECT_RATIO]
    if style_id == "kbs-company-proposal":
        return [LANDSCAPE_ASPECT_RATIO, PROPOSAL_ASPECT_RATIO]
    return [LANDSCAPE_ASPECT_RATIO]


def validate_authored_canvas_admission(request: Any, *, style_id: str) -> None:
    if getattr(request, "page_size", None) is not None:
        from services.document_canvas_capabilities import evaluate_a4_request
        from utils.authored_styles import resolve_authored_style

        style = getattr(request, "_authored_style_snapshot", None) or resolve_authored_style(style_id)
        _policy, enabled, reason = evaluate_a4_request(request, reference_mode=getattr(style, "reference_mode", None))
        if not enabled:
            raise ValueError(reason or "document_canvas_a4_not_available")
        request_canvas_contract(request)
        return
    if request.aspect_ratio == LANDSCAPE_ASPECT_RATIO:
        return
    # The authored source reflow restyles on the requested canvas. Retained
    # masters/design packs still have their own fixed geometry contracts.
    if (
        request.source_mode == "source_content_restyle"
        and str(request.template or "").strip().lower() == "authored"
        and request.master_pin is None
        and getattr(request, "_authored_master_pin", None) is None
        and request.design_pack_pin is None
        and request.generation_mode in {"auto", "free-design"}
        and request.generation_engine in {None, "authored"}
    ):
        request_canvas_contract(request)
        return
    if (
        str(request.template or "").strip().lower() != "authored"
        or request.aspect_ratio not in supported_aspect_ratios_for_style(style_id)
        or request.master_pin is not None
        or getattr(request, "_authored_master_pin", None) is not None
        or request.design_pack_pin is not None
        or request.source_mode is not None
        or request.generation_mode not in {"auto", "free-design"}
        or request.generation_engine not in {None, "authored"}
    ):
        raise ValueError("authored_portrait_combination_not_supported")
    request_canvas_contract(request)


__all__ = [
    "AUTHORED_CANVAS_CONTRACT_VERSION",
    "AuthoredAspectRatio",
    "AuthoredCanvasMetadata",
    "LANDSCAPE_ASPECT_RATIO",
    "PORTRAIT_ASPECT_RATIO",
    "PROPOSAL_ASPECT_RATIO",
    "PORTRAIT_RELEASE_VERIFIED",
    "canvas_metadata_for_aspect_ratio",
    "merge_authored_vnext_projection",
    "presentation_canvas_contract",
    "presentation_canvas_metadata",
    "merge_presentation_canvas",
    "request_canvas_contract",
    "request_canvas_metadata",
    "restore_request_canvas_metadata",
    "supported_aspect_ratios_for_style",
    "validate_authored_canvas_admission",
]
