"""Paragraph and run payloads for PPTX text bodies, with per-run typography.

The run texts and the run styles come from one walk over the same children,
so ``payload.paragraphs[i].runStyles[j]`` describes
``payload.paragraphs[i].runs[j]`` and the flat ``payload.runStyles`` describes
the flat ``payload.runs`` index for index. The synthetic ``"\n"`` between
paragraphs carries the preceding paragraph's mark style (``a:endParaRPr``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from lxml import etree

from services.source_content.pptx_typography import (
    A_NS,
    NS,
    RunStyle,
    ShapeTextContext,
    placeholder_of,
)

_RUN_TAGS = frozenset({f"{{{A_NS}}}r", f"{{{A_NS}}}fld"})
_BREAK_TAG = f"{{{A_NS}}}br"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
BulletImageResolver = Callable[[str], dict[str, str]]
#: Resolves explicit ``a:rPr`` / ``a:pPr`` / ``a:endParaRPr`` only (table cells).
_BARE_CONTEXT = ShapeTextContext()


@dataclass(frozen=True, slots=True)
class TextBody:
    paragraphs: list[dict[str, Any]]
    text: str
    runs: list[str]
    run_styles: list[RunStyle]
    dominant_size_pt: float | None
    text_body: dict[str, Any]
    picture_bullet_count: int


def _bullet(
    properties_chain: Iterable[etree._Element | None],
    *,
    image_resolver: BulletImageResolver | None,
) -> dict[str, Any]:
    chain = tuple(properties_chain)
    local = chain[0] if chain else None
    try:
        level = max(0, int(local.get("lvl", "0"))) if local is not None else 0
    except ValueError:
        level = 0
    bullet_font_family: str | None = None
    bullet_font_follows_text = False
    for properties in chain:
        if properties is None:
            continue
        bullet_font = properties.find("a:buFont", namespaces=NS)
        if bullet_font is not None:
            bullet_font_family = (bullet_font.get("typeface") or "").strip() or None
            break
        if properties.find("a:buFontTx", namespaces=NS) is not None:
            bullet_font_follows_text = True
            break

    result: dict[str, Any] = {
        "bullet": False,
        "bulletKind": None,
        "bulletChar": None,
        "bulletAutoNumType": None,
        "bulletStartAt": None,
        "bulletFontFamily": bullet_font_family,
        "bulletFontFollowsText": bullet_font_follows_text,
        "bulletImageDataUri": None,
        "bulletImageDigest": None,
        "bulletImageRelationshipId": None,
        "level": level,
    }
    for properties in chain:
        if properties is None:
            continue
        if properties.find("a:buNone", namespaces=NS) is not None:
            return result
        character = properties.find("a:buChar", namespaces=NS)
        if character is not None:
            result.update(
                bullet=True,
                bulletKind="character",
                bulletChar=character.get("char"),
            )
            return result
        automatic = properties.find("a:buAutoNum", namespaces=NS)
        if automatic is not None:
            start_at = automatic.get("startAt")
            result.update(
                bullet=True,
                bulletKind="auto-number",
                bulletAutoNumType=automatic.get("type"),
                bulletStartAt=int(start_at) if start_at is not None else None,
            )
            return result
        picture = properties.find("a:buBlip/a:blip", namespaces=NS)
        if picture is not None:
            rel_id = picture.get(f"{{{R_NS}}}embed") or picture.get(f"{{{R_NS}}}link")
            resource = image_resolver(rel_id) if image_resolver is not None and rel_id else {}
            result.update(
                bullet=True,
                bulletKind="picture",
                bulletImageRelationshipId=rel_id,
                **resource,
            )
            return result
    return result


def flat_runs(paragraphs: list[dict[str, Any]]) -> list[str]:
    runs: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        runs.extend(paragraph["runs"])
        if index + 1 < len(paragraphs):
            runs.append("\n")
    return runs


def _flat_run_styles(
    paragraphs: list[dict[str, Any]], mark_styles: list[RunStyle]
) -> list[RunStyle]:
    styles: list[RunStyle] = []
    for index, paragraph in enumerate(paragraphs):
        styles.extend(paragraph["runStyles"])
        if index + 1 < len(paragraphs):
            styles.append(mark_styles[index])
    return styles


def _dominant_size_pt(weights: dict[float, int]) -> float | None:
    """Character-weighted mode of the run sizes; ties go to the larger size."""

    if not weights:
        return None
    return max(weights.items(), key=lambda item: (item[1], item[0]))[0]


def extract_text_body(
    element: etree._Element,
    *,
    context: ShapeTextContext | None = None,
    bullet_image_resolver: BulletImageResolver | None = None,
) -> TextBody:
    """Paragraph payloads of every ``a:p`` under ``element`` plus run styles."""

    context = context or _BARE_CONTEXT
    paragraphs: list[dict[str, Any]] = []
    lines: list[str] = []
    mark_styles: list[RunStyle] = []
    weights: dict[float, int] = {}
    text_cursor = 0
    flat_run_index = 0
    picture_bullet_count = 0
    paragraph_elements = element.findall(".//a:p", namespaces=NS)
    for paragraph_index, paragraph in enumerate(paragraph_elements):
        bullet = _bullet(
            context.paragraph_properties(paragraph),
            image_resolver=bullet_image_resolver,
        )
        runs: list[str] = []
        styles: list[RunStyle] = []
        for run_index, child in enumerate(
            child for child in paragraph if child.tag in _RUN_TAGS or child.tag == _BREAK_TAG
        ):
            if child.tag in _RUN_TAGS:
                text_node = child.find("a:t", namespaces=NS)
                text = text_node.text if text_node is not None and text_node.text is not None else ""
                style = context.run_style(child, paragraph)
                if text and style["sizePt"] is not None:
                    weights[style["sizePt"]] = weights.get(style["sizePt"], 0) + len(text)
            elif child.tag == _BREAK_TAG:
                text = "\n"
                style = context.run_style(child, paragraph)
            else:
                continue
            run_start = text_cursor + sum(len(value) for value in runs)
            style.update(
                {
                    "sourceParagraphIndex": paragraph_index,
                    "sourceRunIndex": run_index,
                    "sourceFlatRunIndex": flat_run_index,
                    "textStart": run_start,
                    "textEnd": run_start + len(text),
                }
            )
            runs.append(text)
            styles.append(style)
            flat_run_index += 1
        paragraph_text = "".join(runs)
        if bullet["bulletFontFollowsText"] and bullet["bulletFontFamily"] is None:
            bullet["bulletFontFamily"] = next(
                (style.get("fontFamily") for style in styles if style.get("fontFamily")),
                None,
            )
        if bullet["bulletKind"] == "picture":
            picture_bullet_count += 1
        paragraph_layout = context.paragraph_layout(paragraph)
        paragraphs.append(
            {
                "runs": runs,
                "text": paragraph_text,
                **bullet,
                "runStyles": styles,
                "sourceParagraphIndex": paragraph_index,
                "textStart": text_cursor,
                "textEnd": text_cursor + len(paragraph_text),
                **paragraph_layout,
            }
        )
        lines.append(paragraph_text)
        mark_style = context.mark_style(paragraph)
        mark_style.update(
            {
                "sourceParagraphIndex": paragraph_index,
                "sourceRunIndex": None,
                "sourceFlatRunIndex": flat_run_index,
                "textStart": text_cursor + len(paragraph_text),
                "textEnd": text_cursor + len(paragraph_text) + 1,
            }
        )
        mark_styles.append(mark_style)
        if paragraph_index + 1 < len(paragraph_elements):
            flat_run_index += 1
            text_cursor += len(paragraph_text) + 1
        else:
            text_cursor += len(paragraph_text)
    return TextBody(
        paragraphs=paragraphs,
        text="\n".join(lines),
        runs=flat_runs(paragraphs),
        run_styles=_flat_run_styles(paragraphs, mark_styles),
        dominant_size_pt=_dominant_size_pt(weights),
        text_body=context.text_body_layout(element),
        picture_bullet_count=picture_bullet_count,
    )


def placeholder_payload(shape: etree._Element) -> dict[str, Any]:
    """``placeholderType`` / ``placeholderIdx`` keys; both ``None`` off placeholders."""

    placeholder = placeholder_of(shape)
    if placeholder is None:
        return {"placeholderType": None, "placeholderIdx": None}
    return {"placeholderType": placeholder.type, "placeholderIdx": placeholder.idx}


__all__ = [
    "BulletImageResolver",
    "TextBody",
    "extract_text_body",
    "flat_runs",
    "placeholder_payload",
]
