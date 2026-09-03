"""Paragraph and run payloads for PPTX text bodies, with per-run typography.

The run texts and the run styles come from one walk over the same children,
so ``payload.paragraphs[i].runStyles[j]`` describes
``payload.paragraphs[i].runs[j]`` and the flat ``payload.runStyles`` describes
the flat ``payload.runs`` index for index. The synthetic ``"\n"`` between
paragraphs carries the preceding paragraph's mark style (``a:endParaRPr``).
"""

from __future__ import annotations

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
_BULLET_KINDS = (
    ("a:buChar", "character"),
    ("a:buAutoNum", "auto-number"),
    ("a:buBlip", "picture"),
)
#: Resolves explicit ``a:rPr`` / ``a:pPr`` / ``a:endParaRPr`` only (table cells).
_BARE_CONTEXT = ShapeTextContext()


@dataclass(frozen=True, slots=True)
class TextBody:
    paragraphs: list[dict[str, Any]]
    text: str
    runs: list[str]
    run_styles: list[RunStyle]
    dominant_size_pt: float | None


def _bullet(properties: etree._Element | None) -> tuple[str | None, int]:
    if properties is None:
        return None, 0
    try:
        level = max(0, int(properties.get("lvl", "0")))
    except ValueError:
        level = 0
    if properties.find("a:buNone", namespaces=NS) is not None:
        return None, level
    for element_name, kind in _BULLET_KINDS:
        if properties.find(element_name, namespaces=NS) is not None:
            return kind, level
    return None, level


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
    element: etree._Element, *, context: ShapeTextContext | None = None
) -> TextBody:
    """Paragraph payloads of every ``a:p`` under ``element`` plus run styles."""

    context = context or _BARE_CONTEXT
    paragraphs: list[dict[str, Any]] = []
    lines: list[str] = []
    mark_styles: list[RunStyle] = []
    weights: dict[float, int] = {}
    for paragraph in element.findall(".//a:p", namespaces=NS):
        bullet_kind, level = _bullet(paragraph.find("a:pPr", namespaces=NS))
        runs: list[str] = []
        styles: list[RunStyle] = []
        for child in paragraph:
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
            runs.append(text)
            styles.append(style)
        paragraph_text = "".join(runs)
        paragraphs.append(
            {
                "runs": runs,
                "text": paragraph_text,
                "bullet": bullet_kind is not None,
                "bulletKind": bullet_kind,
                "level": level,
                "runStyles": styles,
            }
        )
        lines.append(paragraph_text)
        mark_styles.append(context.mark_style(paragraph))
    return TextBody(
        paragraphs=paragraphs,
        text="\n".join(lines),
        runs=flat_runs(paragraphs),
        run_styles=_flat_run_styles(paragraphs, mark_styles),
        dominant_size_pt=_dominant_size_pt(weights),
    )


def placeholder_payload(shape: etree._Element) -> dict[str, Any]:
    """``placeholderType`` / ``placeholderIdx`` keys; both ``None`` off placeholders."""

    placeholder = placeholder_of(shape)
    if placeholder is None:
        return {"placeholderType": None, "placeholderIdx": None}
    return {"placeholderType": placeholder.type, "placeholderIdx": placeholder.idx}


__all__ = ["TextBody", "extract_text_body", "flat_runs", "placeholder_payload"]
