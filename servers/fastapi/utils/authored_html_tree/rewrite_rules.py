"""Enumerated semantic rewrite pass for parsed authored HTML."""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element

from utils.authored_css_strip import (
    strip_break_control_declarations,
    strip_disallowed_declaration_list,
    strip_disallowed_stylesheet,
)
from utils.authored_html_normalize_rules_name import NAME_ATTRIBUTE_RULE_ID
from utils.authored_html_normalize_rules_title import (
    TITLE_LINE_BREAK_RULE_ID,
    is_title_role,
)
from utils.authored_html_tree.receipts import ReceiptCollector

_BOUND_TEXT_ATTRIBUTES = frozenset({"data-source-block-id", "data-qid"})
_WHITESPACE_END = re.compile(r"\s*$")
_WHITESPACE_START = re.compile(r"^\s*")


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _html_elements(root: Element) -> list[Element]:
    return [element for element in root.iter() if isinstance(element.tag, str)]


def _rewrite_break_control_css(root: Element, receipts: ReceiptCollector) -> None:
    break_count = 0
    break_properties: set[str] = set()
    for element in _html_elements(root):
        if "style" in element.attrib:
            value, count, properties = strip_break_control_declarations(element.attrib["style"])
            element.attrib["style"] = value
            break_count += count
            break_properties.update(properties)
        if _local_name(element.tag) == "style":
            value = element.text or ""
            value, count, properties = strip_break_control_declarations(value)
            element.text = value
            break_count += count
            break_properties.update(properties)
    if break_count:
        receipts.add(
            "css_break_control_property_stripped",
            break_count,
            ", ".join(sorted(break_properties)),
        )


def _rewrite_disallowed_css(root: Element, receipts: ReceiptCollector) -> None:
    records: list[dict] = []
    for element in _html_elements(root):
        if "style" in element.attrib:
            value, found = strip_disallowed_declaration_list(element.attrib["style"])
            element.attrib["style"] = value
            records.extend(found)
        if _local_name(element.tag) == "style":
            value, found = strip_disallowed_stylesheet(element.text or "")
            element.text = value
            records.extend(found)
    receipts.extend_legacy(records)


def _has_bound_text(element: Element) -> bool:
    return any(
        any(name in descendant.attrib for name in _BOUND_TEXT_ATTRIBUTES)
        for descendant in element.iter()
        if isinstance(descendant.tag, str)
    )


def _remove_preserving_tail(parent: Element, element: Element) -> None:
    children = list(parent)
    index = children.index(element)
    tail = element.tail or ""
    if index:
        previous = children[index - 1]
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(element)


def _rewrite_hidden(root: Element, receipts: ReceiptCollector) -> None:
    elements = _html_elements(root)
    parents = {child: parent for parent in elements for child in list(parent)}
    removed: set[Element] = set()
    removed_tags: set[str] = set()
    stripped_tags: set[str] = set()
    removed_count = 0
    stripped_count = 0
    for element in elements:
        if "hidden" not in element.attrib:
            continue
        ancestor = parents.get(element)
        if any(parent in removed for parent in _ancestor_chain(ancestor, parents)):
            continue
        tag = _local_name(element.tag)
        if _has_bound_text(element):
            del element.attrib["hidden"]
            stripped_count += 1
            stripped_tags.add(tag)
            continue
        parent = parents.get(element)
        if parent is None:
            element.clear()
        else:
            _remove_preserving_tail(parent, element)
        removed.add(element)
        removed_count += 1
        removed_tags.add(tag)
    if removed_count:
        receipts.add(
            "hidden_element_without_bound_text_removed",
            removed_count,
            ", ".join(sorted(removed_tags)),
        )
    if stripped_count:
        receipts.add(
            "hidden_attribute_stripped_bound_text",
            stripped_count,
            ", ".join(sorted(stripped_tags)),
        )


def _ancestor_chain(element: Element | None, parents: dict[Element, Element]):
    while element is not None:
        yield element
        element = parents.get(element)


def _replace_br(parent: Element, br: Element) -> None:
    children = list(parent)
    index = children.index(br)
    following = _WHITESPACE_START.sub("", br.tail or "")
    if index:
        previous = children[index - 1]
        before = _WHITESPACE_END.sub("", previous.tail or "")
        previous.tail = before + " " + following
    else:
        before = _WHITESPACE_END.sub("", parent.text or "")
        parent.text = before + " " + following
    parent.remove(br)


def _rewrite_title_breaks(root: Element, receipts: ReceiptCollector) -> None:
    elements = _html_elements(root)
    parents = {child: parent for parent in elements for child in list(parent)}
    title_roots: list[Element] = []
    for element in elements:
        tag = _local_name(element.tag)
        attrs = {str(name).lower(): value for name, value in element.attrib.items()}
        if not is_title_role(tag, attrs):
            continue
        if any(ancestor in title_roots for ancestor in _ancestor_chain(parents.get(element), parents)):
            continue
        title_roots.append(element)
    count = 0
    tags: set[str] = set()
    for title in title_roots:
        for parent in list(title.iter()):
            for child in list(parent):
                if _local_name(child.tag) != "br":
                    continue
                _replace_br(parent, child)
                count += 1
                tags.add(_local_name(title.tag))
    if count:
        receipts.add(TITLE_LINE_BREAK_RULE_ID, count, ", ".join(sorted(tags)))


def _rewrite_name_attributes(root: Element, receipts: ReceiptCollector) -> None:
    count = 0
    for element in _html_elements(root):
        if "name" not in element.attrib:
            continue
        del element.attrib["name"]
        count += 1
    if count:
        receipts.add(NAME_ATTRIBUTE_RULE_ID, count, "name")


def apply_rewrite_rules(root: Element, receipts: ReceiptCollector) -> None:
    """Run the fixed semantic rule order used by the legacy normalizer."""

    _rewrite_break_control_css(root, receipts)
    _rewrite_hidden(root, receipts)
    _rewrite_title_breaks(root, receipts)
    _rewrite_name_attributes(root, receipts)
    _rewrite_disallowed_css(root, receipts)


__all__ = ["apply_rewrite_rules"]
