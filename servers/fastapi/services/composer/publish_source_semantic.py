"""Read source-fit/v2 semantic content from the final, immutable Composer DOM."""

from __future__ import annotations

import base64
import hashlib
import io
import re
from html.parser import HTMLParser
from typing import Any

from PIL import Image

from services.composer.publish_semantic import (
    _VOID_TAGS, _CJK, _declared_color, _declared_weight, _round6,
)

POLICY = "composer-source-fit/v2"


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"tag": "root", "attrs": {}, "children": [], "parent": None}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": [], "parent": self.stack[-1]}
        self.stack[-1]["children"].append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def _nodes(node):
    for child in node["children"]:
        if isinstance(child, dict):
            yield child
            yield from _nodes(child)


def _style(node, tokens, font_family):
    chain = []
    while node is not None:
        chain.append(node)
        node = node["parent"]
    # The publication font is the rendered primary face; source CSS classes
    # retain their original families only later in the fallback stack.
    style = {"color": tokens.text_primary, "font-size": "16px", "font-weight": "400", "font-family": font_family}
    for item in reversed(chain):
        attrs = item["attrs"]
        classes = (attrs.get("class") or "").split()
        if classes:
            style["color"] = _declared_color(classes, tokens)
            if _declared_weight(classes, "") == 700:
                style["font-weight"] = "700"
        if item["tag"] == "th":
            style.update({"font-weight": "700", "background": tokens.accent_soft})
        for declaration in (attrs.get("style") or "").split(";"):
            key, sep, value = declaration.partition(":")
            if sep:
                style[key.strip().lower()] = value.strip()
    return style


def _size(style):
    match = re.fullmatch(r"([0-9.]+)(px|pt)", style.get("font-size", "24px"))
    if not match:
        raise ValueError("composer_source_semantic_font_size_invalid")
    size = _round6(float(match[1]) * (0.75 if match[2] == "px" else 1))
    if size < 5.25:
        raise ValueError("composer_source_semantic_font_below_floor")
    return size


def _paragraphs(node, *, tokens, language, font_family, font_face, owned=True):
    paragraphs = []
    runs = []
    last_owner = None

    def make_run(text, item):
        style = _style(item, tokens, font_family)
        family = style.get("font-family", font_family).split(",")[0].strip(" \"'")
        return {"text": text, "language": language or "ko", "script": "cjk" if _CJK.search(text) else "latin", "fontFamily": family, "fontFace": font_face, "fontSizePt": _size(style), "weight": int(style.get("font-weight", "400")), "italic": style.get("font-style") == "italic", "underline": "underline" in style.get("text-decoration", ""), "color": style["color"]}

    def flush(force=False, context=None):
        nonlocal runs, last_owner
        context = context or node
        if force and not runs:
            first = next((item for item in _nodes(context) if item["tag"] == "span"), context)
            runs = [make_run("", first)]
        if runs:
            alignment = _style(context, tokens, font_family).get("text-align", "left")
            paragraphs.append({"runs": runs, "alignment": alignment if alignment in {"left", "right", "center", "justify"} else "left"})
            runs = []
            last_owner = None

    def walk(item):
        nonlocal last_owner
        for child in item["children"]:
            if isinstance(child, str):
                if not child or (not child.strip() and "\n" in child):
                    continue
                if runs and last_owner is item:
                    runs[-1]["text"] += child
                else:
                    runs.append(make_run(child, item))
                last_owner = item
            elif child["tag"] not in {"script", "style", "template"}:
                if owned and child["attrs"].get("data-qid"):
                    continue
                if child["tag"] == "br":
                    if not runs:
                        runs.append(make_run("", item))
                        last_owner = item
                    runs[-1]["text"] += "\n"
                else:
                    if child["tag"] == "p":
                        flush()
                    walk(child)
                    if child["tag"] == "p":
                        flush(force=True, context=child)
    walk(node)
    flush()
    return paragraphs


def source_semantic_draft(html, *, tokens, language, font_family, font_face):
    parser = _Tree()
    parser.feed(html)
    nodes = list(_nodes(parser.root))
    qids = [node for node in nodes if node["attrs"].get("data-qid")]
    options = dict(tokens=tokens, language=language, font_family=font_family, font_face=font_face)
    elements = []
    for index, node in enumerate(qids):
        parent = node["parent"]
        while parent is not None and not parent["attrs"].get("data-qid"):
            parent = parent["parent"]
        item = {"elementId": node["attrs"]["data-qid"], "semanticRole": node["attrs"].get("data-semantic-role") or "body", "intendedOrder": index, "parentId": parent["attrs"]["data-qid"] if parent else None, "childIds": []}
        if node["tag"] == "img":
            image, _asset, _bytes = _image(node)
            image_style = _style(node, tokens, font_family)
            classes = (node["attrs"].get("class") or "").split()
            image["objectFit"] = image_style.get("object-fit", "contain" if {"cx-image", "cx-image-flex"}.intersection(classes) else "fill")
            image["objectPosition"] = image_style.get("object-position", "50% 50%")
            item.update(kind="image", payload=image)
        elif node["tag"] == "table":
            item.update(kind="table", payload=_table(node, options))
        else:
            paragraphs = _paragraphs(node, **options)
            item.update(kind="text", payload={"schemaVersion": "authored-source-text/v2", "sourceFitPolicy": POLICY, "paragraphs": paragraphs, "whitespace": "pre-wrap"}) if any(run["text"].strip() for paragraph in paragraphs for run in paragraph["runs"]) else item.update(kind="shape", payload={"preset": "rectangle"})
        elements.append(item)
    by_id = {item["elementId"]: item for item in elements}
    for item in elements:
        if item["parentId"]:
            by_id[item["parentId"]]["childIds"].append(item["elementId"])
    return {"semanticSource": {"elements": elements, "identityRegistry": {"activeQids": sorted(by_id), "retiredQids": []}}}


def _image(node):
    """Bind the final inline pixels, including any pre-capture crop derivative."""
    src = node["attrs"].get("src") or ""
    match = re.fullmatch(r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)", src)
    if not match:
        raise ValueError("composer_source_semantic_inline_image_required")
    content = base64.b64decode(re.sub(r"\s+", "", match[2]), validate=True)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    with Image.open(io.BytesIO(content)) as image:
        width, height = image.size
    asset_id = "asset:composer." + digest.removeprefix("sha256:")
    payload = {"assetId": asset_id, "assetDigest": digest, "altText": node["attrs"].get("alt") or "", "intrinsicWidthPx": width, "intrinsicHeightPx": height}
    asset = {"assetId": asset_id, "artifact": {"artifactRef": "artifact://sha256/" + digest.removeprefix("sha256:"), "digest": digest, "byteLength": len(content), "mediaType": match[1]}, "locatorDigest": "sha256:" + hashlib.sha256(src.encode()).hexdigest(), "resolverVersion": POLICY, "widthPx": width, "heightPx": height, "provenance": "composition", "retentionClass": "active"}
    return payload, asset, content


def source_publication_assets(html):
    """Build the existing immutable asset-manifest contract from the same DOM."""
    from models.authored_vnext.semantic.contracts import AssetManifestV1

    parser = _Tree()
    parser.feed(html)
    assets = {}
    byte_map = {}
    for node in _nodes(parser.root):
        if node["tag"] == "img" and node["attrs"].get("data-qid"):
            payload, asset, content = _image(node)
            assets[payload["assetId"]] = asset
            byte_map[payload["assetDigest"]] = content
    return AssetManifestV1.model_validate({"assets": [assets[key] for key in sorted(assets)]}), byte_map


def _table(node, options):
    row_nodes = [item for item in _nodes(node) if item["tag"] == "tr"]
    grid = [{} for _ in row_nodes]
    merges = []
    for r, row in enumerate(row_nodes):
        c = 0
        for cell in [item for item in row["children"] if isinstance(item, dict) and item["tag"] in {"td", "th"}]:
            while c in grid[r]:
                c += 1
            attrs = cell["attrs"]
            rowspan, colspan = int(attrs.get("rowspan") or 1), int(attrs.get("colspan") or 1)
            paragraphs = _paragraphs(cell, owned=False, **options)
            style = _style(cell, options["tokens"], options["font_family"])
            value = {"text": "\n".join("".join(run["text"] for run in paragraph["runs"]) for paragraph in paragraphs), "textColor": style["color"], "bold": int(style.get("font-weight", "400")) >= 600, "fontFamily": style.get("font-family", options["font_family"]).split(",")[0].strip(" \"'"), "fontSizePt": _size(style)}
            if paragraphs:
                value["paragraphs"] = paragraphs
            fill = style.get("background-color", style.get("background", ""))
            if re.fullmatch(r"#[0-9a-fA-F]{6}", fill):
                value["fill"] = fill
            if rowspan > 1 or colspan > 1:
                merges.append({"row": r, "column": c, "rowSpan": rowspan, "columnSpan": colspan})
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    if rr >= len(grid) or cc in grid[rr]:
                        raise ValueError("composer_source_semantic_table_merge_invalid")
                    grid[rr][cc] = value if (rr, cc) == (r, c) else {**{k: v for k, v in value.items() if k != "paragraphs"}, "text": ""}
            c += colspan
    width = max((max(row, default=-1) + 1 for row in grid), default=0)
    if not width or any(set(row) != set(range(width)) for row in grid):
        raise ValueError("composer_source_semantic_table_grid_invalid")
    payload = {"schemaVersion": "authored-native-table/v2", "sourceFitPolicy": POLICY, "rows": [[row[c] for c in range(width)] for row in grid], "mergedCells": merges}
    columns = [item for item in _nodes(node) if item["tag"] == "col"]
    widths = [float(re.search(r"width\s*:\s*([0-9.]+)", col["attrs"].get("style", ""))[1]) for col in columns]
    if len(widths) == width:
        payload["columnWidths"] = widths
    heights = [re.search(r"height\s*:\s*([0-9.]+)px", row["attrs"].get("style", "")) for row in row_nodes]
    if all(heights):
        payload["rowHeightsPt"] = [_round6(float(height[1]) * .75) for height in heights]
    return payload
