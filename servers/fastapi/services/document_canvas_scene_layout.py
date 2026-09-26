"""Revision-local Scene layout; the published master pin stays immutable."""

from __future__ import annotations

from models.authored_vnext.master.contract import DeckMasterContract, Rect
from services.document_canvas import DocumentCanvasMetadata
from services.document_canvas_contain import CanvasRect, build_contain_plan
from services.authored_vnext.master.digests import seal_master_contract


def document_scene_master(contract: DeckMasterContract, canvas: object | None) -> DeckMasterContract:
    if canvas is None:
        return contract
    target = DocumentCanvasMetadata.model_validate(canvas)
    if (contract.canvas.width_px, contract.canvas.height_px) == (target.width_px, target.height_px):
        return contract
    plan = build_contain_plan(
        source_rect=CanvasRect(0, 0, contract.canvas.width_px, contract.canvas.height_px),
        target_canvas=target,
    )

    def transform(value):
        if isinstance(value, list):
            return [transform(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {"x", "y", "width", "height"}:
            x, y = plan.map_point(value["x"], value["y"])
            return {"x": x, "y": y, "width": value["width"] * plan.scale, "height": value["height"] * plan.scale}
        return {
            key: child * 0.75 / target.point_per_unit
            if key in {"fontPt", "minFontPt", "preferredFontPt", "minimumFontPt"} and isinstance(child, (int, float))
            else transform(child)
            for key, child in value.items()
        }

    payload = transform(contract.model_dump(mode="json", by_alias=True))
    payload["canvas"] = target.canvas_contract().model_dump(mode="json", by_alias=True)
    # Font sizes are template intent, not a completed scene to shrink to fit.
    payload["digest"] = None
    return seal_master_contract(DeckMasterContract.model_validate(payload))


def canonical_safe_area(contract: DeckMasterContract) -> Rect:
    if (contract.canvas.width_px, contract.canvas.height_px) == (1280, 720):
        return Rect(x=60.0, y=80.0, width=1160.0, height=560.0)
    return contract.canvas.safe_area


def safe_title_bounds(title_bounds: Rect, contract: DeckMasterContract, *, slide_role: str) -> Rect:
    _source_binding(title_bounds, contract, "title")
    if (contract.canvas.width_px, contract.canvas.height_px) == (1280, 720):
        top = {"cover": 176.0, "section": 146.0}.get(slide_role, 84.0)
        height = 240.0 if slide_role == "longTitle" else 180.0 if slide_role in {"cover", "section"} else 140.0
        return Rect(x=92.0, y=top, width=1096.0, height=height)
    safe = canonical_safe_area(contract)
    top_fraction = 0.16 if slide_role == "cover" else 0.10 if slide_role == "section" else 0.02
    height_fraction = 0.28 if slide_role == "longTitle" else 0.22 if slide_role in {"cover", "section"} else 0.18
    inset = min(32.0, safe.width * 0.04)
    return Rect(
        x=safe.x + inset,
        y=safe.y + safe.height * top_fraction,
        width=safe.width - 2 * inset,
        height=safe.height * height_fraction,
    )


def _source_binding(bounds: Rect, contract: DeckMasterContract, role: str) -> None:
    if min(bounds.right, contract.canvas.width_px) - max(bounds.x, 0) < 1 or (
        min(bounds.bottom, contract.canvas.height_px) - max(bounds.y, 0) < 1
    ):
        raise ValueError(f"authored {role} source binding has no usable area inside canvas")


def safe_body_bounds(body_bounds: Rect, *, title_bounds: Rect, contract: DeckMasterContract) -> Rect:
    _source_binding(body_bounds, contract, "body")
    safe = canonical_safe_area(contract)
    top = max(safe.y, title_bounds.bottom + 24.0)
    if safe.bottom - top < 48:
        raise ValueError("authored body has insufficient safeArea below title; title/body overlap is not publishable")
    return Rect(x=safe.x, y=top, width=safe.width, height=safe.bottom - top)


async def scene_revision_preview(*, master, deck, slide_index, preview_builder, capture):
    """Capture the target-sized preview through the caller's renderer boundary."""
    from services.authored_vnext.generation.effective_style import materialize_authored_composition

    contract = (
        materialize_authored_composition(master.contract, deck.composition)
        if deck.composition is not None
        else master.contract
    )
    contract = document_scene_master(contract, deck.scene.document_canvas if deck.scene is not None else None)
    html = preview_builder(contract, master.revision, deck.slides[slide_index].slide, page_number=slide_index + 1).html
    png = await capture(html, width_px=contract.canvas.width_px, height_px=contract.canvas.height_px)
    return html, png
