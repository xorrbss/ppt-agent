"""Scan rendered text nodes while keeping source evidence out of receipts."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
from typing import Iterable, Mapping, Sequence
import json
import re
import xml.etree.ElementTree as ET

from services.ppt_writing_policy import (
    ProtectedSpan,
    SymbolCategory,
    WRITING_POLICY_ID,
    scan_visible_text,
    visible_symbol_substitutions,
)


# Arrows and slashes frequently encode a relation or a native label. A prompt
# still asks for ordinary prose, but an unproved rewrite cannot be a hard gate.
_AMBIGUOUS = frozenset({SymbolCategory.TEXT_ARROW, SymbolCategory.LIST_SLASH})
_SVG_EXCLUDED = frozenset({"defs", "metadata", "desc", "title", "script", "style", "clippath", "mask"})
_HTML_EXCLUDED = frozenset({"head", "script", "style", "template", "noscript", "title", "svgdefs"})
_HTML_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_HTML_TEXT_OWNERS = frozenset({"p", "div", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "label", "figcaption", "blockquote", "dt", "dd"})


def _tag(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


#: A template-redesign page carries the user's template furniture (fixed
#: header, footer, logo text) under ``svg-system-template-*`` IDs. That text is
#: the template's own and frozen: the author cannot rephrase it, so it is not
#: writing the policy judges. Only the body group is authored.
_TEMPLATE_FURNITURE_PREFIX = "svg-system-template-"
_TEMPLATE_BODY_ID = "svg-system-template-body"


def _template_furniture(node: ET.Element) -> bool:
    node_id = node.get("id") or ""
    return node_id.startswith(_TEMPLATE_FURNITURE_PREFIX) and node_id != _TEMPLATE_BODY_ID


def _svg_text_records(svg: str, excluded_ids: Sequence[str] = ()) -> tuple[dict, ...]:
    root = ET.fromstring(svg)
    qids = {node: f"svg-{index}" for index, node in enumerate(root.iter())}
    texts: list[dict] = []

    def visit(node: ET.Element, hidden: bool = False) -> None:
        tag = _tag(node.tag)
        style = (node.get("style") or "").replace(" ", "").lower()
        hidden = hidden or tag in _SVG_EXCLUDED or node.get("display") == "none" or "display:none" in style
        if hidden or _template_furniture(node) or (excluded_ids and node.get("id") in excluded_ids):
            return
        if tag == "text":
            value = "".join(node.itertext()).strip()
            if value:
                texts.append({"qid": qids[node], "node_id": node.get("id"), "text": value})
            return
        for child in node:
            visit(child)

    visit(root)
    return tuple(texts)


def svg_text_nodes(svg: str, *, excluded_ids: Sequence[str] = ()) -> tuple[str, ...]:
    """Read only displayed SVG text elements, never attributes or path data.

    Text under an element whose id is in ``excluded_ids`` (server-drawn chart groups) is
    skipped; the default reads everything, as before.
    """
    return tuple(record["text"] for record in _svg_text_records(svg, excluded_ids))


def substitute_svg_visible_symbols(
    svg: str, *, source_values: Sequence[str] = (), required_text: Sequence[str] = (),
    protected_element_ids: Sequence[str] = (),
) -> tuple[str, list[dict]]:
    """Repair policy-approved visible text only, preserving literal provenance.

    Scan each text owner together with its tspans so a literal split across runs
    cannot lose protection. Map offsets back to text/tail slots; never touch XML
    attributes, ids, URLs or source/native values. No-op returns the original bytes.
    """
    root = ET.fromstring(svg)
    counts: Counter[tuple[str, str]] = Counter()
    values = tuple(source_values) + tuple(required_text)

    def blocked(node: ET.Element) -> bool:
        style = re.sub(r"\s+", "", node.get("style", "")).lower()
        return (_tag(node.tag) in _SVG_EXCLUDED or node.get("id") in protected_element_ids
                or node.get("display") == "none" or "display:none" in style
                or node.get("visibility") in {"hidden", "collapse"}
                or "visibility:hidden" in style or "visibility:collapse" in style
                or node.get("opacity") == "0" or "opacity:0;" in style + ";")

    def visit(node: ET.Element) -> None:
        if blocked(node):
            return
        if _tag(node.tag) != "text":
            for child in node:
                visit(child)
            return
        # Mixed hidden/unsupported content is not a provably safe text owner.
        if any(blocked(child) or _tag(child.tag) not in {"tspan", "textpath"}
               for child in node.iter() if child is not node):
            return
        slots = []

        def collect(element: ET.Element) -> None:
            if element.text:
                slots.append((element, "text", element.text))
            for child in element:
                collect(child)
                if child.tail:
                    slots.append((child, "tail", child.tail))

        collect(node)
        text = "".join(value for _, _, value in slots)
        spans = _provenance_spans(text, values)
        scan = (scan_visible_text(text, source_text=text)
                if len(spans) == 1 and spans[0].start == 0 and spans[0].end == len(text)
                else scan_visible_text(text, protected_spans=spans))
        edits = [(start, end, replacement)
                 for start, end, replacement in visible_symbol_substitutions(text, scan)
                 if not any(start < span.end and span.start < end for span in spans)]
        for start, end, replacement in edits:
            counts[(text[start:end].strip(" \t"), replacement)] += 1
        offset = 0
        for element, field, value in slots:
            local = [(max(start - offset, 0), min(end - offset, len(value)),
                      replacement if offset <= start else "")
                     for start, end, replacement in edits
                     if offset < end and start < offset + len(value)]
            for start, end, replacement in reversed(local):
                value = value[:start] + replacement + value[end:]
            offset += len(getattr(element, field))
            if local:
                setattr(element, field, value)

    visit(root)
    receipts = [{"symbol": symbol, "count": count, "replacement": replacement}
                for (symbol, replacement), count in sorted(counts.items())]
    return (ET.tostring(root, encoding="unicode") if receipts else svg), receipts


class _VisibleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.texts: list[str] = []
        self.owners: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self.owners:
            self.owners[-1][1].append("\n")
        values = dict(attrs)
        style = (values.get("style") or "").replace(" ", "").lower()
        parent_hidden = self.stack[-1][1] if self.stack else False
        hidden = (parent_hidden or tag in _HTML_EXCLUDED or "hidden" in values
                  or "display:none" in style or "visibility:hidden" in style)
        if tag not in _HTML_VOID:
            self.stack.append((tag, hidden))
            if tag in _HTML_TEXT_OWNERS and not hidden:
                self.owners.append((tag, []))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _HTML_VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for closed_tag, _ in reversed(self.stack[index:]):
                    if self.owners and self.owners[-1][0] == closed_tag:
                        _, chunks = self.owners.pop()
                        value = "".join(chunks).strip()
                        if value:
                            self.texts.append(value)
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if not (self.stack[-1][1] if self.stack else False) and data.strip():
            if self.owners:
                self.owners[-1][1].append(data)
            else:
                self.texts.append(data.strip())


def html_text_nodes(html: str) -> tuple[str, ...]:
    parser = _VisibleHtmlParser()
    parser.feed(html)
    parser.close()
    return tuple(parser.texts)


def _provenance_spans(text: str, values: Sequence[str]) -> tuple[ProtectedSpan, ...]:
    spans: list[ProtectedSpan] = []
    for value in sorted({item for item in values if item}, key=len, reverse=True):
        cursor = 0
        while True:
            start = text.find(value, cursor)
            if start < 0:
                break
            end = start + len(value)
            if not any(start < span.end and span.start < end for span in spans):
                spans.append(ProtectedSpan(start, end, "source:" + sha256(value.encode()).hexdigest()[:16]))
            cursor = end
    return tuple(sorted(spans, key=lambda span: span.start))


#: A name the source joins with a middle dot (강원·제주권, 금융·보험업): the dot belongs to it.
_MIDDLE_DOT_NAME = re.compile(r"(?<![\w·ㆍ‧∙⋅])[0-9A-Za-z가-힣]+(?:[·ㆍ‧∙⋅][0-9A-Za-z가-힣]+)+(?![\w·ㆍ‧∙⋅])")


def literal_source_values(source_text: str | None = None, native: Mapping | None = None,
                          bindings: Sequence[Mapping] = (),
                          raster_ledger: Mapping | None = None, *,
                          header_contract: Mapping | None = None,
                          header_binding: Mapping | None = None,
                          middle_dot_names: bool = False) -> tuple[str, ...]:
    """Pick explicit literal evidence: quotations, names with symbols, native cells/labels.

    ``middle_dot_names`` also keeps every word the source itself joins with a middle dot
    (the policy otherwise rewrites ``강원·제주권`` to ``강원, 제주권`` and flags it where the
    text is frozen, such as a common header title copied from the outline).
    """
    values: set[str] = set()
    if (header_contract and header_contract.get("version") == "svg-common-header/v2"
            and header_binding and header_binding.get("applied") is True):
        from services.svg_common_header import validate_common_header_binding
        try:
            validate_common_header_binding(header_contract, header_binding)
        except (ValueError, KeyError, TypeError):
            pass  # Invalid bindings never grant writing-policy exemptions.
        else:
            for field in header_binding.get("fields", ()):
                value, source = field.get("value"), field.get("source_ref")
                if (field.get("user_fixed") is True and isinstance(source, str)
                        and source.startswith("request:") and isinstance(value, str) and value.strip()):
                    values.add(value)
                    values.update(line.strip() for line in field.get("lines", ())
                                  if isinstance(line, str) and line.strip())
    if source_text:
        for match in re.finditer(r'[“"]([^“”"\n]{2,120})[”"]', source_text):
            values.add(match.group(1))
        for match in re.finditer(r'(?im)^(?:제품명|브랜드명|공식명칭|모델명|product name|brand name|model name)\s*[:：]\s*([^\n]{2,120})', source_text):
            values.add(match.group(1).strip())
        if middle_dot_names:
            values.update(match.group(0) for match in _MIDDLE_DOT_NAME.finditer(source_text))
    for binding in bindings:
        if binding.get("kind") in {"text", "table_cell", "chart_label", "chart_title"}:
            value = binding.get("text")
            if isinstance(value, str) and value.strip():
                values.add(value)
    if raster_ledger:
        for entry in raster_ledger.get("entries", ()):
            for token in entry.get("tokens", ()):
                if isinstance(token, str) and token.strip():
                    values.add(token)
    if native:
        for table in native.get("tables", ()):
            for row in table.get("rows", ()):
                for value in row:
                    if not isinstance(value, str) or not value.strip():
                        continue
                    values.add(value)
                    # Only server-parsed source-native cells authorize these
                    # complete tokens. Display line wrapping may join adjacent
                    # cell words, while the exact token itself stays intact.
                    for token in value.split():
                        hard = [item for item in scan_visible_text(token).violations
                                if item.category not in _AMBIGUOUS]
                        if hard and all(item.start > 0 and item.end < len(token)
                                        and token[item.start - 1].isalnum() and token[item.end].isalnum()
                                        for item in hard):
                            values.add(token)
        for chart in native.get("charts", ()):
            values.update(value for value in chart.get("categories", ()) if isinstance(value, str) and value.strip())
            for key in ("unit", "name"):
                value = chart.get(key)
                if isinstance(value, str) and value.strip():
                    values.add(value)
            for value in chart.get("units", ()):
                if isinstance(value, str) and value.strip():
                    values.add(value)
            for series in chart.get("series", (chart,)):
                for key in ("name", "unit"):
                    value = series.get(key)
                    if isinstance(value, str) and value.strip():
                        values.add(value)
                for value in series.get("units", ()):
                    if isinstance(value, str) and value.strip():
                        values.add(value)
            for item in chart.get("requiredUnitLabels", ()):
                value = item.get("text")
                if isinstance(value, str) and value.strip():
                    values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def assess_visible_nodes(
    nodes: Iterable[str], *, source_values: Sequence[str] = (),
) -> dict[str, object]:
    """Return bounded counts; only verified literal source values are exempt."""
    hard: Counter[str] = Counter()
    ambiguous: Counter[str] = Counter()
    protected: Counter[str] = Counter()
    style: Counter[str] = Counter()
    count = 0
    for text in nodes:
        if not text.strip():
            continue
        count += 1
        spans = _provenance_spans(text, source_values)
        scan = (
            scan_visible_text(text, source_text=text)
            if len(spans) == 1 and spans[0].start == 0 and spans[0].end == len(text)
            else scan_visible_text(text, protected_spans=spans)
        )
        for item in scan.violations:
            (ambiguous if item.category in _AMBIGUOUS else hard)[item.category.value] += 1
        protected.update(scan.protected_counts)
        style.update(item.signal.value for item in scan.style_signals)
    return {
        "policy_id": WRITING_POLICY_ID,
        "visible_node_count": count,
        "hard_violation_counts": dict(hard),
        "ambiguous_symbol_counts": dict(ambiguous),
        "protected_symbol_counts": dict(protected),
        "style_signal_counts": dict(style),
    }


def svg_writing_repair_witnesses(svg: str, *, source_values: Sequence[str] = (),
                                 limit: int = 12, max_text_chars: int = 180,
                                 total_text_chars: int = 720, excluded_ids: Sequence[str] = ()) -> list[dict]:
    """Bounded transient repair context; never add visible text to quality receipts."""
    if limit <= 0 or max_text_chars <= 0 or total_text_chars <= 0:
        return []
    candidates = []
    for record in _svg_text_records(svg, excluded_ids):
        text = record["text"]
        spans = _provenance_spans(text, source_values)
        scan = (scan_visible_text(text, source_text=text)
                if len(spans) == 1 and spans[0].start == 0 and spans[0].end == len(text)
                else scan_visible_text(text, protected_spans=spans))
        hard = [item for item in scan.violations if item.category not in _AMBIGUOUS]
        if not hard:
            continue
        candidates.append((record, hard))
        if len(candidates) >= min(limit, 12, total_text_chars):
            break
    # Share the previous 4*180-character budget across more failing nodes,
    # rather than spending it before later violations can reach the author.
    excerpt_chars = min(max_text_chars, total_text_chars // max(1, len(candidates)))
    def build_excerpt(chars, symbol_limit, include_ids=True):
        witnesses = []
        for record, hard in candidates:
            text = record["text"]
            start = max(0, min(hard[0].start - chars // 3, len(text) - chars))
            end = start + chars
            witnesses.append({"qid": record["qid"], "node_id": record["node_id"] if include_ids else None,
                "text": text[start:end], "text_start": start,
                "symbols": [{"category": item.category.value, "start": item.start - start, "end": item.end - start}
                            for item in hard if start <= item.start and item.end <= end][:symbol_limit],
                "frozen_header": str(record["node_id"] or "").startswith("svg-system-common-header-")})
        return witnesses

    def context_chars(witnesses):
        return len(json.dumps(witnesses, ensure_ascii=False, separators=(",", ":")))

    witnesses = build_excerpt(excerpt_chars, 4)
    if context_chars(witnesses) > 2200:
        # Reserve room for the counts/instruction inside the existing 3000-char
        # repair hint. Prefer one located symbol on every node to lost nodes.
        witnesses = build_excerpt(excerpt_chars, 1, include_ids=False)
        while context_chars(witnesses) > 2200 and excerpt_chars > 8:
            excerpt_chars = max(8, excerpt_chars - 8)
            witnesses = build_excerpt(excerpt_chars, 1, include_ids=False)
    return witnesses
