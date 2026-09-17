"""Pure DrawingML text-layout and theme-font resolution helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from lxml import etree

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"a": A_NS}


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _typeface(properties: etree._Element) -> str | None:
    for tag in ("ea", "latin", "cs", "sym"):
        node = properties.find(f"a:{tag}", namespaces=NS)
        value = node.get("typeface", "").strip() if node is not None else ""
        if value:
            return value
    return None


def _language(properties: etree._Element) -> str | None:
    value = (properties.get("lang") or properties.get("altLang") or "").strip()
    return value or None


def _theme_script(language: str | None) -> str | None:
    if not language:
        return None
    lowered = language.lower()
    if lowered.startswith("ko"):
        return "Hang"
    if lowered.startswith("ja"):
        return "Jpan"
    if lowered.startswith(("zh-cn", "zh-sg", "zh-hans")):
        return "Hans"
    if lowered.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
        return "Hant"
    if lowered.startswith("ar"):
        return "Arab"
    if lowered.startswith("he"):
        return "Hebr"
    return None


def resolved_font_family(
    chain: Iterable[etree._Element | None], theme_fonts: dict[str, str]
) -> str | None:
    typeface: str | None = None
    language: str | None = None
    for properties in chain:
        if properties is None:
            continue
        if typeface is None:
            typeface = _typeface(properties)
        if language is None:
            language = _language(properties)
    if not typeface or not typeface.startswith("+"):
        return typeface
    resolved = theme_fonts.get(typeface)
    if resolved:
        return resolved
    script = _theme_script(language)
    return theme_fonts.get(f"{typeface}:{script}", typeface) if script else typeface


def _level_paragraph_properties(
    list_style: etree._Element | None, level: int
) -> list[etree._Element]:
    if list_style is None:
        return []
    result: list[etree._Element] = []
    for path in (f"a:lvl{level + 1}pPr", "a:defPPr"):
        found = list_style.find(path, namespaces=NS)
        if found is not None:
            result.append(found)
    return result


def _first_attribute(
    chain: Iterable[etree._Element | None], name: str
) -> str | None:
    for properties in chain:
        if properties is not None and properties.get(name) is not None:
            return properties.get(name)
    return None


def _spacing(
    chain: Iterable[etree._Element | None], container: str
) -> tuple[float | None, float | None]:
    for properties in chain:
        if properties is None:
            continue
        node = properties.find(f"a:{container}", namespaces=NS)
        if node is None:
            continue
        points = node.find("a:spcPts", namespaces=NS)
        percent = node.find("a:spcPct", namespaces=NS)
        if points is not None:
            value = _optional_int(points.get("val"))
            return (value / 100.0 if value is not None else None), None
        if percent is not None:
            value = _optional_int(percent.get("val"))
            return None, (value / 1000.0 if value is not None else None)
    return None, None


def paragraph_properties(
    paragraph: etree._Element,
    *,
    level: int,
    list_style: etree._Element | None,
    inherited: tuple[etree._Element, ...],
) -> tuple[etree._Element | None, ...]:
    chain: list[etree._Element | None] = [paragraph.find("a:pPr", namespaces=NS)]
    chain.extend(_level_paragraph_properties(list_style, level))
    for inherited_style in inherited:
        chain.extend(_level_paragraph_properties(inherited_style, level))
    return tuple(chain)


def paragraph_layout(
    paragraph: etree._Element,
    *,
    level: int,
    list_style: etree._Element | None,
    inherited: tuple[etree._Element, ...],
) -> dict[str, Any]:
    chain = paragraph_properties(
        paragraph,
        level=level,
        list_style=list_style,
        inherited=inherited,
    )
    before_pt, before_pct = _spacing(chain, "spcBef")
    after_pt, after_pct = _spacing(chain, "spcAft")
    line_pt, line_pct = _spacing(chain, "lnSpc")
    return {
        "alignment": _first_attribute(chain, "algn"),
        "marginLeftEmu": _optional_int(_first_attribute(chain, "marL")),
        "indentEmu": _optional_int(_first_attribute(chain, "indent")),
        "spaceBeforePt": before_pt,
        "spaceBeforePct": before_pct,
        "spaceAfterPt": after_pt,
        "spaceAfterPct": after_pct,
        "lineSpacingPct": line_pct,
        "lineSpacingPt": line_pt,
    }


def text_body_layout(
    element: etree._Element,
    *,
    inherited: tuple[etree._Element, ...],
) -> dict[str, Any]:
    body = element.find(".//a:bodyPr", namespaces=NS)
    table_cell = element.find("a:tcPr", namespaces=NS)
    chain = [body, *inherited]

    def body_attribute(name: str, default: str | None = None) -> str | None:
        return _first_attribute(chain, name) or default

    def inset(body_name: str, cell_name: str, default: int) -> int:
        if table_cell is not None and table_cell.get(cell_name) is not None:
            value = _optional_int(table_cell.get(cell_name))
            return value if value is not None else default
        value = _optional_int(body_attribute(body_name))
        return value if value is not None else default

    auto_fit: str | None = None
    font_scale_pct: float | None = None
    line_space_reduction_pct: float | None = None
    for properties in chain:
        if properties is None:
            continue
        if properties.find("a:noAutofit", namespaces=NS) is not None:
            auto_fit = "none"
            break
        normal = properties.find("a:normAutofit", namespaces=NS)
        if normal is not None:
            auto_fit = "normal"
            font_scale = _optional_int(normal.get("fontScale"))
            line_reduction = _optional_int(normal.get("lnSpcReduction"))
            font_scale_pct = font_scale / 1000.0 if font_scale is not None else None
            line_space_reduction_pct = (
                line_reduction / 1000.0 if line_reduction is not None else None
            )
            break
        if properties.find("a:spAutoFit", namespaces=NS) is not None:
            auto_fit = "shape"
            break
    rotation = _optional_int(body_attribute("rot"))
    upright_value = body_attribute("upright")
    return {
        "marginLeftEmu": inset("lIns", "marL", 91440),
        "marginRightEmu": inset("rIns", "marR", 91440),
        "marginTopEmu": inset("tIns", "marT", 45720),
        "marginBottomEmu": inset("bIns", "marB", 45720),
        "anchor": (
            table_cell.get("anchor")
            if table_cell is not None and table_cell.get("anchor") is not None
            else body_attribute("anchor", "t")
        ),
        "wrap": body_attribute("wrap", "square"),
        "vertical": body_attribute("vert", "horz"),
        "rotationDegrees": rotation / 60000.0 if rotation is not None else None,
        "upright": (
            upright_value.lower() in {"1", "true", "on"}
            if upright_value is not None
            else False
        ),
        "autoFit": auto_fit,
        "fontScalePct": font_scale_pct,
        "lineSpaceReductionPct": line_space_reduction_pct,
    }


def theme_font_map(root: etree._Element | None) -> dict[str, str]:
    if root is None:
        return {}
    result: dict[str, str] = {}
    for family, prefix in (("majorFont", "+mj"), ("minorFont", "+mn")):
        node = root.find(f".//a:fontScheme/a:{family}", namespaces=NS)
        if node is None:
            continue
        for tag, suffix in (("latin", "lt"), ("ea", "ea"), ("cs", "cs")):
            font = node.find(f"a:{tag}", namespaces=NS)
            value = font.get("typeface", "").strip() if font is not None else ""
            if value:
                result[f"{prefix}-{suffix}"] = value
        for font in node.findall("a:font", namespaces=NS):
            script = (font.get("script") or "").strip()
            value = (font.get("typeface") or "").strip()
            if script and value:
                result[f"{prefix}-ea:{script}"] = value
    return result


__all__ = [
    "paragraph_layout",
    "paragraph_properties",
    "resolved_font_family",
    "text_body_layout",
    "theme_font_map",
]
