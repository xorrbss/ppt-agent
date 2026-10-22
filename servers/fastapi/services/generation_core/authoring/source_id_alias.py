"""Page-local short aliases for whole-page source provenance ids.

The SVG author copies the ``element_id`` values of ``source_facts`` (20-hex fact
and cell ids, table and chart group ids) onto its output. Aliases shorten what
the model reads and writes without touching the stored contract: the map is a
pure function of the canonical fact order, so every producer and replay derives
the same one, and the model's response is restored to canonical ids before
anything else parses it.

Alias grammar ``[fktc][1-9][0-9]*``: ``f`` for text, shape, connector and image
facts, ``t`` for table groups, ``c`` for table cells (one counter per page, in
``_cells`` anchor order) and ``k`` for chart groups. Server-stamped chart part
ids ``<chart id>-part-<n>`` become ``k<m>-part-<n>``.

Outbound helpers (``alias_facts``, ``alias_svg``, ``alias_text``) only rewrite
the id values the model is shown. ``canonicalize_ids`` is the inbound half and
never raises or guesses: unknown alias-shaped, canonical and decorative ids
pass through, so coverage, duplicates and placement stay the binder's and the
validator's job.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET

from services.svg_contract import SVG_NS

SOURCE_ID_ALIAS_VERSION = "svg-source-id-alias/v1"

ALIAS_RULE_SENTENCE = (
    "element_id values are short page-local identities (f, t, c or k followed by a number). "
    "Copy each exactly onto its element; never derive, renumber, combine or reuse them, "
    "and never use that form for any other id."
)

# Same registration as svg_contract: alias_svg must serialize like the stored SVG.
ET.register_namespace("", SVG_NS)

# Reserved alias grammar announced to the model. Canonical ids never match it.
ALIAS_RE = re.compile(r"[fktc][1-9][0-9]*")
_ALIAS_PART_RE = re.compile(r"(k[1-9][0-9]*)-part-([0-9]+)")
_CANONICAL_PART_RE = re.compile(r"(native-chart-source-fact-[0-9a-f]{20})-part-([0-9]+)")
# Canonical ids inside free text (correction hints). The boundaries keep derived
# decorative ids such as ``source-fact-<hex>-evaluation`` out of the match.
_CANONICAL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(native-chart-source-fact-[0-9a-f]{20}(?:-part-[0-9]+)?"
    r"|source-native-table-[0-9a-f]{16}|source-fact-[0-9a-f]{20})(?![A-Za-z0-9_.-])")


def _identities(facts):
    for fact in facts:
        kind = fact.get("kind")
        if kind == "table":
            yield "t", fact["element_id"]
            for cell in fact.get("cells", ()):
                yield "c", cell["element_id"]
        elif kind == "chart":
            yield "k", fact["element_id"]
        else:
            yield "f", fact["element_id"]


def alias_map(facts) -> dict[str, str] | None:
    """Canonical element_id -> alias in fact order; None when the ids are not a clean bijection."""
    counters = dict.fromkeys("fktc", 0)
    result: dict[str, str] = {}
    try:
        for prefix, canonical in _identities(facts):
            if not isinstance(canonical, str) or canonical in result or ALIAS_RE.fullmatch(canonical):
                return None
            counters[prefix] += 1
            result[canonical] = f"{prefix}{counters[prefix]}"
    except (KeyError, TypeError, AttributeError):
        return None
    return result


def map_digest(amap: dict[str, str]) -> str:
    pairs = json.dumps(sorted(amap.items()), separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(pairs.encode("utf-8")).hexdigest()


def alias_facts(facts, amap: dict[str, str]) -> list:
    """Model view of ``source_facts``: element_id values, including connection targets, aliased."""
    view = copy.deepcopy(list(facts))
    for fact in view:
        fact["element_id"] = amap[fact["element_id"]]
        if fact.get("kind") == "table":
            for cell in fact.get("cells", ()):
                cell["element_id"] = amap[cell["element_id"]]
        for connection in fact.get("connections", {}).values():
            if isinstance(connection, dict) and connection.get("element_id") in amap:
                connection["element_id"] = amap[connection["element_id"]]
    return view


def _alias_id(value: str, amap: dict[str, str]) -> str:
    if value in amap:
        return amap[value]
    match = _CANONICAL_PART_RE.fullmatch(value)
    if match and match[1] in amap:
        return f"{amap[match[1]]}-part-{match[2]}"
    return value


def alias_svg(svg: str, amap: dict[str, str]) -> str:
    """``previous_svg`` view: id attribute values only; unparsable input is returned unchanged."""
    # ElementTree would drop comments and processing instructions, which is more than an id rewrite.
    if re.search(r"<!|<\?", svg):
        return svg
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    renamed = 0
    for node in root.iter():
        value = node.get("id")
        if value is not None:
            alias = _alias_id(value, amap)
            if alias != value:
                node.set("id", alias)
                renamed += 1
    return ET.tostring(root, encoding="unicode") if renamed else svg


def alias_text(text: str, amap: dict[str, str]) -> str:
    """Correction-hint view: exact canonical id tokens replaced, everything else untouched."""
    return _CANONICAL_TOKEN_RE.sub(lambda match: _alias_id(match[1], amap), text)


def canonicalize_ids(root, amap: dict[str, str]) -> dict[str, int]:
    """Restore canonical ids on a parsed model response, in place; returns attempt-receipt counters."""
    reverse = {alias: canonical for canonical, alias in amap.items()}
    stats = {"aliased": 0, "canonical": 0, "unknown_alias": 0, "part": 0}
    for node in root.iter():
        value = node.get("id")
        if value is None:
            continue
        if value in reverse:
            node.set("id", reverse[value])
            stats["aliased"] += 1
            continue
        match = _ALIAS_PART_RE.fullmatch(value)
        if match and match[1] in reverse:
            node.set("id", f"{reverse[match[1]]}-part-{match[2]}")
            stats["part"] += 1
        elif value in amap:
            stats["canonical"] += 1
        elif ALIAS_RE.fullmatch(value):
            stats["unknown_alias"] += 1
    return stats
