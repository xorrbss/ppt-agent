"""Canonical html5lib serializer for authored HTML trees."""

from __future__ import annotations

from html5lib.serializer import serialize

from utils.authored_html_tree.parser_adapter import ParsedAuthoredHtml

_DOCTYPE = "<!DOCTYPE html>"


def serialize_authored_html(parsed: ParsedAuthoredHtml) -> str:
    """Serialize deterministically without sanitizing or dropping optional tags."""

    body = serialize(
        parsed.root,
        tree="etree",
        encoding=None,
        inject_meta_charset=False,
        quote_attr_values="always",
        quote_char='"',
        escape_lt_in_attrs=True,
        escape_rcdata=False,
        resolve_entities=False,
        strip_whitespace=False,
        minimize_boolean_attributes=True,
        use_trailing_solidus=False,
        sanitize=False,
        omit_optional_tags=False,
        alphabetical_attributes=True,
    )
    return _DOCTYPE + body if parsed.profile == "document" else body


__all__ = ["serialize_authored_html"]
