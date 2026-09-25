"""Stamp token-derived owners through the existing source-stamping primitive."""

from __future__ import annotations

from html import escape
from types import SimpleNamespace
from xml.etree.ElementTree import Element

from services.source_content.source_stamp_dom import _StampNode, deterministic_source_qid
from services.source_content.text_stamping import _stamp_node
from utils.authored_html_tree.parser_adapter import parse_authored_html


def stamp_owner(owner: Element, binding_id: str, block_id: str) -> None:
    """Adapt a tree element to _stamp_node without running its legacy parser."""
    attributes = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in owner.attrib.items())
    tag = owner.tag.rsplit("}", 1)[-1]
    adapter = SimpleNamespace(tokens=[f"<{tag}{attributes}>"])
    node = _StampNode(0, False)
    node.has_qid = "data-qid" in owner.attrib
    node.has_semantic_role = any(owner.get(key) for key in ("data-semantic-role", "data-role", "role"))
    # Model-provided source attributes never choose identity.
    owner.attrib.pop("data-source-block-id", None)
    attributes = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in owner.attrib.items())
    adapter.tokens[0] = f"<{tag}{attributes}>"
    _stamp_node(adapter, node, binding_id, block_id)
    # Use a div fragment solely to read the primitive's attribute output;
    # table/SVG owners cannot be parsed alone under the pinned div context.
    attrs = adapter.tokens[0][len(tag) + 1:]
    parsed = parse_authored_html("<div" + attrs + "</div>", profile="fragment")
    owner.attrib.clear()
    owner.attrib.update(parsed.root[0].attrib)
    assert owner.get("data-qid") == deterministic_source_qid(binding_id)
