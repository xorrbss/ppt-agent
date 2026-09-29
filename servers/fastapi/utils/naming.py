"""Identifier case conversion shared by the camelCase alias generators."""

from __future__ import annotations


def snake_to_camel(name: str) -> str:
    """``snake_case`` to ``camelCase``, upper-casing only each tail's first letter.

    The tail's remaining characters keep their case (``foo_BAR`` ->
    ``fooBAR``), so an already-capitalised segment is not flattened.  For
    the lowercase-and-digit field names pydantic models declare this is the
    same string ``str.capitalize`` would give, which is what the previous
    per-module copies used.
    """

    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


__all__ = ["snake_to_camel"]
