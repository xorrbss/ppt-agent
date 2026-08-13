"""Deterministic derived web preview and renderer/export plans."""

from __future__ import annotations

from html import escape
import json

from models.authored_vnext.master.contract import DeckMasterContract, FurnitureElement
from models.authored_vnext.master.operations import (
    ExportPlan,
    ExportSlidePlan,
    PngRenderRequest,
    PreviewResponse,
)
from models.authored_vnext.master.slide import SlideSpecV2

from .digests import canonical_digest


def _css_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\003c ")


def _element_html(element: FurnitureElement) -> str:
    bounds = element.bounds
    styles = [
        "position:absolute",
        f"left:{bounds.x:g}px",
        f"top:{bounds.y:g}px",
        f"width:{bounds.width:g}px",
        f"height:{bounds.height:g}px",
        "pointer-events:none",
    ]
    if element.fill:
        styles.append(f"color:{element.fill}")
        if element.kind == "rect":
            styles.append(f"background:{element.fill}")
    if element.stroke:
        styles.append(f"border-color:{element.stroke}")
    if element.kind == "line":
        styles.extend([f"background:{element.stroke or '#000000'}", "height:1px"])
    text = element.text or ("LOGO" if element.kind == "logo" else "")
    return (
        f'<div data-master-furniture="{escape(element.id, quote=True)}" '
        f'data-locked="true" style="{";".join(styles)}">{escape(text)}</div>'
    )


def build_preview(
    contract: DeckMasterContract,
    master_revision: int,
    slide: SlideSpecV2,
) -> PreviewResponse:
    canonical = canonical_digest(
        {
            "masterDigest": contract.digest,
            "masterRevision": master_revision,
            "slide": slide.model_dump(mode="json", by_alias=True),
        }
    )
    layout = (
        contract.layouts.standard
        if slide.layout_id == contract.layouts.standard.id
        else contract.layouts.long_title
    )
    title_slot = next(slot for slot in layout.slots if slot.role == "title")
    body_slot = next(slot for slot in layout.slots if slot.role == "body")
    title_style = contract.semantic_styles.title
    body_style = contract.semantic_styles.body
    furniture = [
        contract.furniture.logo,
        contract.furniture.header,
        contract.furniture.footer,
        contract.furniture.divider,
        contract.furniture.page_number,
    ]
    furniture_html = "".join(_element_html(element) for element in furniture)
    title_lines = "".join(
        f"<span>{escape(line)}</span>" for line in slide.title.lines
    )
    primary = _css_string(contract.font_manifest.primary)
    fallbacks = ",".join(_css_string(name) for name in contract.font_manifest.fallbacks)
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "*{box-sizing:border-box}html,body{margin:0;background:#fff}"
        f".slide{{position:relative;overflow:hidden;width:{contract.canvas.width_px}px;"
        f"height:{contract.canvas.height_px}px;background:{contract.tokens.colors.background};"
        f"font-family:{primary},{fallbacks}}}"
        ".title span{display:block}"
        "</style></head><body>"
        f'<main class="slide" data-canonical-digest="{canonical}">{furniture_html}'
        f'<section class="title" data-body-slot="{escape(title_slot.id, quote=True)}" '
        f'style="position:absolute;left:{title_slot.bounds.x:g}px;top:{title_slot.bounds.y:g}px;'
        f'width:{title_slot.bounds.width:g}px;height:{title_slot.bounds.height:g}px;'
        f'padding:{title_style.margins.top_pt:g}pt {title_style.margins.right_pt:g}pt '
        f'{title_style.margins.bottom_pt:g}pt {title_style.margins.left_pt:g}pt;'
        f'font-size:{slide.title_fit.chosen_font_pt:g}pt;line-height:{title_style.line_height:g};'
        f'font-weight:{title_style.font_weight};color:{contract.tokens.colors.text_primary}">'
        f"{title_lines}</section>"
        f'<section class="body" data-body-slot="{escape(body_slot.id, quote=True)}" '
        f'style="position:absolute;left:{body_slot.bounds.x:g}px;top:{body_slot.bounds.y:g}px;'
        f'width:{body_slot.bounds.width:g}px;height:{body_slot.bounds.height:g}px;'
        f'padding:{body_style.margins.top_pt:g}pt {body_style.margins.right_pt:g}pt '
        f'{body_style.margins.bottom_pt:g}pt {body_style.margins.left_pt:g}pt;'
        f'font-size:{body_style.font_pt:g}pt;line-height:{body_style.line_height:g};'
        f'color:{contract.tokens.colors.text_secondary};white-space:pre-wrap">'
        f"{escape(slide.body)}</section></main></body></html>"
    )
    html_digest = canonical_digest({"html": html})
    export_plan = ExportPlan(
        compiler_port="OoxmlMasterCompilerPort",
        compiler_version="ooxml-master-port/v1",
        master_id=contract.master_id,
        master_revision=master_revision,
        master_digest=contract.digest,
        canonical_digest=canonical,
        source_digest=slide.spec_digest,
        expected_package_hash=None,
        slides=[
            ExportSlidePlan(
                slide_id=slide.slide_id,
                layout_id=slide.layout_id,
                spec_digest=slide.spec_digest,
            )
        ],
    )
    return PreviewResponse(
        canonical_digest=canonical,
        html=html,
        png_render_request=PngRenderRequest(
            renderer="authored-vnext-web-preview/1",
            canonical_digest=canonical,
            html_digest=html_digest,
            viewport_width=contract.canvas.width_px,
            viewport_height=contract.canvas.height_px,
        ),
        export_plan=export_plan,
        slide=slide,
    )
