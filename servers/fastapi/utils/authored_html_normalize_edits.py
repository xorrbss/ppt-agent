"""Byte-span edit application shared by the authored-HTML normalisation rules.

Every rule in ``utils/authored_html_normalize.py`` and its sibling
``*_rules_*`` modules works the same way: locate exact byte spans of real
tags with ``html.parser.HTMLParser``, then splice replacement text into the
*original* source string. Nothing is ever reserialized, because
``admit_authored_html`` validates the original bytes (see the module
docstring of ``utils/authored_html_normalize.py``).

The splice itself is the one piece of that machinery every rule needs and
none of them own, so it lives here rather than being duplicated or imported
across sibling rule modules.
"""

from __future__ import annotations

from collections.abc import Sequence


def apply_edits(source: str, edits: Sequence[tuple[int, int, str]]) -> str:
    """Splice ``(start, end, replacement)`` spans into ``source``.

    Applied back-to-front so an earlier edit never shifts the offsets of a
    later one. Callers are responsible for handing over non-overlapping
    spans; overlapping edits are not detected here.
    """

    result = source
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


__all__ = ["apply_edits"]
