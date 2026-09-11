"""Text-slot enumeration on T3's tree; syntax contexts are never token sources."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree.ElementTree import Element

from services.source_content.placeholder.tokens import PlaceholderToken, parse_text_tokens, reject
from services.source_content.text_stamping import _RepairVisibility

_NONCONTENT = frozenset({"head", "script", "style", "noscript", "template", "title"})


def local_name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""


def hidden_element(element: Element) -> bool:
    # Reuse the binder's explicit-hidden predicate directly on parsed attrs;
    # no legacy HTMLParser feed and no second visibility policy.
    visibility = _RepairVisibility()
    visibility.handle_starttag(local_name(element), list(element.attrib.items()))
    return not visibility.visible


@dataclass(frozen=True, slots=True)
class TextSlot:
    node: Element
    attribute: str
    parent: Element
    text: str
    tokens: tuple[PlaceholderToken, ...]


def original_text_slots(root: Element) -> tuple[TextSlot, ...]:
    slots: list[TextSlot] = []

    def add(node: Element, attribute: str, parent: Element, forbidden: bool) -> None:
        value = getattr(node, attribute) or ""
        if "{{q:" not in value:
            return
        if forbidden:
            reject("context", "Placeholder appears in hidden or non-content text")
        slots.append(TextSlot(node, attribute, parent, value, parse_text_tokens(value)))

    def visit(element: Element, forbidden: bool) -> None:
        for name, value in element.attrib.items():
            if "{{q:" in name or "{{q:" in value:
                reject("context", "Placeholder appears in an attribute")
        blocked = forbidden or not isinstance(element.tag, str)
        blocked = blocked or local_name(element) in _NONCONTENT or hidden_element(element)
        add(element, "text", element, blocked)
        for child in list(element):
            visit(child, blocked)
            # A comment/style/hidden child's tail belongs to its visible parent.
            add(child, "tail", element, blocked)

    visit(root, False)
    return tuple(slots)
