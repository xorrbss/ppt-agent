"""Mode-neutral document geometry; physical EMU are authoritative.

The metadata model is ``models.document_canvas`` (re-exported here); this
module holds the derivations that need the rest of the service layer.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from models.document_canvas import (  # noqa: F401 - re-exported for callers
    DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED,
    DOCUMENT_CANVAS_A4_RELEASE_VERIFIED,
    DOCUMENT_CANVAS_CONTRACT_VERSION,
    DocumentCanvasMetadata,
    LogicalSize,
    PhysicalSize,
    SafeArea,
)


def document_canvas_for_orientation(orientation: str) -> DocumentCanvasMetadata:
    if orientation not in {"landscape", "portrait"}:
        raise ValueError("document_canvas_orientation_invalid")
    width, height = (1188, 840) if orientation == "landscape" else (840, 1188)
    return DocumentCanvasMetadata(
        contract_version=DOCUMENT_CANVAS_CONTRACT_VERSION,
        logical_size=LogicalSize(width=width, height=height),
        physical_size=PhysicalSize(width_emu=width * 9000, height_emu=height * 9000),
        safe_area=SafeArea(x=60, y=60, width=width - 120, height=height - 120),
    )


def document_canvas_from_source_slide_size(
    *, width_emu: int, height_emu: int
) -> DocumentCanvasMetadata:
    """Project an immutable PPTX slide size without normalising its EMU."""

    if width_emu <= 0 or height_emu <= 0:
        raise ValueError("document_canvas_source_size_invalid")
    if width_emu < height_emu:
        raise ValueError("svg_source_canvas_not_supported")
    # The current SVG source-restyle capability is the user's A4-landscape
    # path.  Classification is deliberately tolerant of the sub-millimetre
    # producer rounding found in valid PPTX files; the stored EMU remain exact.
    width_mm = Decimal(width_emu) / Decimal(36000)
    height_mm = Decimal(height_emu) / Decimal(36000)
    if not (
        Decimal("296") <= width_mm <= Decimal("298")
        and Decimal("209") <= height_mm <= Decimal("211")
    ):
        raise ValueError("svg_source_canvas_not_supported")
    logical_width = 1188
    logical_height = int(
        (Decimal(logical_width) * Decimal(height_emu) / Decimal(width_emu))
        .to_integral_value(rounding="ROUND_HALF_UP")
    )
    margin = 60.0
    return DocumentCanvasMetadata(
        contract_version=DOCUMENT_CANVAS_CONTRACT_VERSION,
        logical_size=LogicalSize(width=logical_width, height=logical_height),
        physical_size=PhysicalSize(width_emu=width_emu, height_emu=height_emu),
        safe_area=SafeArea(
            x=margin,
            y=margin,
            width=logical_width - (margin * 2),
            height=logical_height - (margin * 2),
        ),
    )


def parse_canvas_metadata(value: object):
    from services.authored_canvas import AuthoredCanvasMetadata

    if isinstance(value, (DocumentCanvasMetadata, AuthoredCanvasMetadata)):
        return value
    contract_version = DOCUMENT_CANVAS_CONTRACT_VERSION
    if (isinstance(value, dict) and
            (value.get("contractVersion") or value.get("contract_version")) == contract_version):
        return DocumentCanvasMetadata.model_validate(value)
    return AuthoredCanvasMetadata.model_validate(value)


def canvas_physical_size(value: object) -> tuple[int, int]:
    canvas = parse_canvas_metadata(value)
    if isinstance(canvas, DocumentCanvasMetadata):
        return canvas.physical_size.width_emu, canvas.physical_size.height_emu
    return canvas.width_px * 9525, canvas.height_px * 9525


def canvas_digest(value: object) -> str:
    canvas = DocumentCanvasMetadata.model_validate(value)
    fields = [canvas.contract_version, str(canvas.width_px), str(canvas.height_px),
              str(canvas.physical_size.width_emu), str(canvas.physical_size.height_emu)]
    fields.extend(str(int(Decimal(str(getattr(canvas.safe_area, key))) * 1000000))
                  for key in ("x", "y", "width", "height"))
    canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def require_canvas_capability(theme: object, supported_versions: str | None) -> None:
    """Reject A4 delivery to readers that did not negotiate this contract."""
    if not isinstance(theme, dict) or "documentCanvas" not in theme:
        return
    from fastapi import HTTPException
    from services.authored_canvas import presentation_canvas_metadata

    try:
        presentation_canvas_metadata(theme)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="document_canvas_contract_invalid") from error
    advertised = {item.strip() for item in (supported_versions or "").split(",")}
    if DOCUMENT_CANVAS_CONTRACT_VERSION not in advertised:
        raise HTTPException(status_code=409, detail="document_canvas_client_upgrade_required")


def preserve_document_canvas(current_theme: object, proposed_theme: object) -> object:
    """Keep the server-owned canvas on theme-only updates; never resize implicitly."""
    current = current_theme if isinstance(current_theme, dict) else {}
    proposed = dict(proposed_theme) if isinstance(proposed_theme, dict) else {}
    if "documentCanvas" not in current:
        if "documentCanvas" in proposed:
            raise ValueError("document_canvas_server_owned")
        return proposed_theme
    canvas = DocumentCanvasMetadata.model_validate(current["documentCanvas"])
    if "documentCanvas" in proposed and DocumentCanvasMetadata.model_validate(proposed["documentCanvas"]) != canvas:
        raise ValueError("document_canvas_request_mismatch")
    projection = proposed.get("authored_vnext")
    if isinstance(projection, dict) and "canvas" in projection:
        raise ValueError("document_canvas_legacy_conflict")
    proposed["documentCanvas"] = canvas.model_dump(mode="json", by_alias=True)
    return proposed
