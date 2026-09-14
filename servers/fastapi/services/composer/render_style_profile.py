"""Apply an explicit selected-style grammar before admission and capture."""
from __future__ import annotations

import re

from lxml import etree, html as lxml_html

from services.composer.plan import ComposerSlidePlan
from services.composer.render_context import RenderContext
from services.composer.render_css import px


def _nodes(root, css_class: str):
    return root.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " ' + css_class + ' ")]')


def _style(node, **values) -> None:
    current = {}
    for declaration in (node.get("style") or "").split(";"):
        key, separator, value = declaration.partition(":")
        if separator:
            current[key.strip()] = value.strip()
    current.update({key.replace("_", "-"): str(value) for key, value in values.items()})
    node.set("style", ";".join(f"{key}:{value}" for key, value in current.items()))


def _paint_text(node, color: str) -> None:
    _style(node, color=color)
    for child in node.iterdescendants():
        if child.tag in {"p", "span", "h1", "h2", "h3", "h4", "th", "td"}:
            _style(child, color=color)


def _panel(node, ctx: RenderContext, hint: str, fill: str, **values) -> bool:
    if node.get("data-source-block-id"):
        return False
    if not node.get("data-qid"):
        node.set("data-qid", ctx.registry.qids.claim(hint))
        node.set("data-semantic-role", "decoration")
    _style(node, background_color=fill, **values)
    return True


def apply_style_profile(html: str, plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    profile = ctx.deck.style_profile
    if profile is None:
        return html
    document = lxml_html.document_fromstring(html)
    body = document.find("body")
    if body is None:
        raise ValueError("composer_style_document_missing_body")
    source_geometry = ctx.params.source_geometry
    role = "source" if source_geometry else plan.archetype
    body.set("data-composer-style-profile", profile.profile_id)
    body.set("data-composer-style-role", role)
    radius = px(profile.border_radius)

    for node in _nodes(body, "cx-title-band"):
        if profile.header_style == "tab-band":
            _panel(node, ctx, "style-header", profile.header_fill, border_bottom="none")
        else:
            _style(node, border_bottom=f"2px solid {profile.header_fill}")
    for node in _nodes(body, "cx-title-stack"):
        if profile.header_style == "tab-band":
            _panel(node, ctx, "style-title-tab", profile.header_tab_fill, border_radius=radius)
        _paint_text(node, profile.heading_color)
    for node in _nodes(body, "cx-title"):
        _paint_text(node, profile.heading_color)
    if source_geometry and profile.header_style == "tab-band":
        # Style the existing title box; never move or duplicate a source title.
        for title in body.xpath('.//*[@data-semantic-role="title" and @data-source-block-id]'):
            wrapper = next((parent for parent in title.iterancestors()
                            if "cx-geo" in (parent.get("class") or "").split()), None)
            if wrapper is not None:
                _panel(wrapper, ctx, "style-source-title-tab", profile.header_tab_fill,
                       border=f"1px solid {profile.header_fill}", box_sizing="border-box", border_radius=radius)
    for node in _nodes(body, "cx-running-header"):
        _paint_text(node, profile.table_header_text)
    for node in _nodes(body, "cx-footer-band"):
        _panel(node, ctx, "style-footer", profile.footer_fill, border_top="none")
        _paint_text(node, profile.footer_text)
    for node in _nodes(body, "cx-side-band"):
        style = dict(part.split(":", 1) for part in (node.get("style") or "").split(";") if ":" in part)
        top = style.get("top", "").strip()
        height = style.get("height", "").strip()
        fill = profile.surface_color
        if top in {"0", "0px", "0.0px", "0.00px"} and height != px(ctx.deck.canvas.height_px):
            fill = ctx.tokens.background if source_geometry else profile.header_fill
        elif top not in {"0", "0px", "0.0px", "0.00px"}:
            fill = profile.footer_fill
        _panel(node, ctx, "style-source-band", fill)

    for table in _nodes(body, "cx-table"):
        _style(table, border_radius=radius)
        rows = table.xpath('./thead/tr | ./tbody/tr | ./tr')
        for row_index, row in enumerate(rows):
            for cell in row.xpath('./td | ./th'):
                fill = profile.table_header_fill if row_index == 0 else profile.surface_color
                _style(cell, background_color=fill, border_color=profile.header_fill)
                if row_index == 0:
                    _paint_text(cell, profile.table_header_text)
    for css_class in ("cx-step-head", "cx-compare-head"):
        for node in _nodes(body, css_class):
            painted = _panel(node, ctx, "style-section", profile.table_header_fill, border_radius=radius)
            _paint_text(node, profile.table_header_text if painted else profile.heading_color)
    for css_class in ("cx-panel", "cx-card"):
        for node in _nodes(body, css_class):
            _panel(node, ctx, "style-panel", profile.surface_color, border_radius=radius)
    if profile.emphasis_color:
        for node in _nodes(body, "cx-accent"):
            _paint_text(node, profile.emphasis_color)

    if not source_geometry and role == "cover":
        fill = profile.cover_fill or profile.header_fill
        text = profile.cover_text or profile.table_header_text
        _style(body, background_color=fill)
        for css_class in ("cx-title-band", "cx-title-stack", "cx-cover"):
            for node in _nodes(body, css_class):
                _style(node, background_color=fill)
                _paint_text(node, text)
        for node in _nodes(body, "cx-cover-stack"):
            _style(node, text_align=profile.cover_alignment,
                   align_items="flex-start" if profile.cover_alignment == "left" else "center")
        for node in _nodes(body, "cx-cover-rule"):
            _style(node, background_color=profile.cover_accent_fill or profile.table_header_fill)
        angle = etree.Element("div")
        angle.set("data-qid", ctx.registry.qids.claim("style-cover-angle"))
        angle.set("data-semantic-role", "decoration")
        _style(angle, position="absolute", left="0px", top="0px",
               width=px(ctx.deck.canvas.width_px * 0.38), height=px(ctx.deck.canvas.height_px * 0.22),
               background_color=profile.cover_accent_fill or profile.table_header_fill,
               opacity="0.16", clip_path="polygon(0 0,100% 0,0 100%)")
        body.insert(0, angle)
    rendered = lxml_html.tostring(document, encoding="unicode", method="html", doctype="<!DOCTYPE html>")
    # Preserve the existing transient asset metadata quoting contract.
    return re.sub(r"(\sdata-source-image-color-transforms=)'([^']*)'",
                  lambda match: match[1] + '"' + match[2].replace('"', "&quot;") + '"', rendered)
