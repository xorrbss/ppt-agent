"""Legacy-mode placeholder expansion: text-node replacement on the raw string.

Under ``AUTHORED_HTML_NORMALIZATION=legacy`` the admission gate must not adopt
the canonical HTML5 serializer (B5/R8): every byte outside an inserted
placeholder token or a stamped owner attribute has to survive unchanged, and a
call with nothing to expand must return the exact input bytes. This module
reuses the same owner-determination shape as the tree path
(``substitute._owners``/``_common_owner``) but works on the byte-preserving
token list from :mod:`services.source_content.source_stamp_dom` instead of an
html5lib element tree, and only ever rewrites the specific list entries the
expansion touches -- never the whole document.

Structural validation (which tokens exist, in which order, covering which
blocks) stays the tree path's job in ``substitute.expand_placeholders``; this
module only decides *how to splice* what that validation already accepted.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from html import escape as escape_text

from services.source_content.placeholder.block_table import SourceBlock
from services.source_content.placeholder.tokens import TOKEN, PlaceholderToken, reject
from services.source_content.source_runs import source_run_binding_id
from services.source_content.source_stamp_dom import (
    _StampNode,
    _StampParser,
    deterministic_source_qid,
    parse_stamp_dom,
    source_block_data_attribute,
)
from services.source_content.text_stamping import _stamp_node

_Edit = tuple[int, int, str]


@dataclass(eq=False)
class _SyntheticSpan:
    """A wrapping ``<span>`` that does not exist yet in the raw token list.

    Mirrors the fresh ``Element("span")`` the tree path creates for a
    not-direct placement: it starts bare, and only gains stamp attributes if
    the owner search below picks it (rather than a coarser existing
    ancestor) as the block's final owner.
    """

    text: str
    parent: _StampNode
    decided_binding_id: str | None = field(default=None, init=False)


@dataclass(frozen=True, slots=True)
class _RawPlacement:
    owner: object
    block_id: str
    start: int
    end: int


def _locate(parser: _StampParser, offsets: Sequence[int], start: int, end: int) -> tuple[int, int, int]:
    """Return ``(data_token_index, local_start, local_end)`` for one raw match."""
    index = bisect.bisect_right(offsets, start) - 1
    if index < 0:
        reject("context", "Placeholder token has no owning text node")
    local_start = start - offsets[index]
    local_end = end - offsets[index]
    token = parser.tokens[index]
    if index not in parser.data_tokens or not (0 <= local_start <= local_end <= len(token)):
        reject("context", "Placeholder token crosses a markup boundary")
    return index, local_start, local_end


def _enclosing_node(nodes: Sequence[_StampNode], data_index: int) -> _StampNode | None:
    best: _StampNode | None = None
    for node in nodes:
        if node.start < data_index and (node.end is None or node.end > data_index):
            if best is None or node.start > best.start:
                best = node
    return best


def _parent_map(nodes: Sequence[_StampNode]) -> dict[_StampNode, _StampNode]:
    """Nesting parent of every node, derived from raw token-index intervals."""
    parent_of: dict[_StampNode, _StampNode] = {}
    stack: list[_StampNode] = []
    for node in nodes:
        while stack and stack[-1].end is not None and node.start > stack[-1].end:
            stack.pop()
        if stack:
            parent_of[node] = stack[-1]
        stack.append(node)
    return parent_of


def _owner_parent(owner: object, parent_of: dict) -> object | None:
    if isinstance(owner, _SyntheticSpan):
        return owner.parent
    return parent_of.get(owner)


def _owner_text(owner: object, virtual_text: dict[int, str]) -> str:
    if isinstance(owner, _SyntheticSpan):
        return owner.text
    return "".join(virtual_text[index] for index in owner.text_indices)


def _ancestor_chain(owner: object, parent_of: dict) -> list:
    chain = [owner]
    while True:
        parent = _owner_parent(chain[-1], parent_of)
        if parent is None:
            return chain
        chain.append(parent)


def _common_owner(owners: Sequence, parent_of: dict):
    chains = [_ancestor_chain(owner, parent_of) for owner in owners]
    return next(node for node in chains[0] if all(node in chain for chain in chains[1:]))


def _apply_edits(original: str, edits: Sequence[_Edit]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0]):
        pieces.append(original[cursor:start])
        pieces.append(replacement)
        cursor = end
    pieces.append(original[cursor:])
    return "".join(pieces)


def _apply_stamp(parser: _StampParser, owner: object, binding_id: str, block_id: str) -> None:
    if isinstance(owner, _SyntheticSpan):
        owner.decided_binding_id = binding_id
    else:
        _stamp_node(parser, owner, binding_id, block_id)


def _owners_raw(
    parser: _StampParser,
    block_table: Sequence[SourceBlock],
    placements: Sequence[_RawPlacement],
    parent_of: dict,
    virtual_text: dict[int, str],
) -> int:
    """Port of ``substitute._owners`` onto raw token-list owners."""
    by_block: dict[str, list[_RawPlacement]] = defaultdict(list)
    for placement in placements:
        by_block[placement.block_id].append(placement)
    claimed: set = set()
    count = 0
    for block in block_table:
        group = by_block[block.id]
        whole = _common_owner([placement.owner for placement in group], parent_of)
        projection = block.projection
        if projection.accepts(*projection.whole, _owner_text(whole, virtual_text)) and whole not in claimed:
            _apply_stamp(parser, whole, block.id, block.id)
            claimed.add(whole)
            count += 1
            continue
        cursor = 0
        while cursor < projection.run_count:
            found = False
            for last in range(projection.run_count - 1, cursor - 1, -1):
                begin, end = block.run_spans[cursor][0], block.run_spans[last][1]
                selected = [p for p in group if p.end > begin and p.start < end]
                if not selected:
                    continue
                owner = _common_owner([p.owner for p in selected], parent_of)
                if owner in claimed or not projection.accepts(cursor, last, _owner_text(owner, virtual_text)):
                    continue
                binding_id = source_run_binding_id(block.id, cursor, last, projection.run_count)
                _apply_stamp(parser, owner, binding_id, block.id)
                claimed.add(owner)
                count += 1
                cursor = last + 1
                found = True
                break
            if not found:
                reject("context", "Token ranges need an exclusive whole-block or projected-run owner", (block.id,))
    return count


def _wrapped_markup(span: _SyntheticSpan, escaped: str) -> str:
    if span.decided_binding_id is None:
        return f"<span>{escaped}</span>"
    binding_id = span.decided_binding_id
    attrs = (
        f'{source_block_data_attribute(binding_id)} '
        f'data-qid="{deterministic_source_qid(binding_id)}" data-semantic-role="body"'
    )
    return f"<span {attrs}>{escaped}</span>"


def expand_placeholders_legacy(
    html: str,
    tokens: Sequence[PlaceholderToken],
    token_direct: Sequence[bool],
    blocks: dict[str, SourceBlock],
    block_table: Sequence[SourceBlock],
) -> tuple[str, int]:
    """Splice token substitutions into ``html``'s own bytes; nothing else moves.

    A call with no tokens returns ``html`` byte-identical (no parse artifact
    survives, since nothing in ``parser.tokens`` is ever rewritten).
    """
    parser = parse_stamp_dom(html)
    offsets: list[int] = []
    position = 0
    for piece in parser.tokens:
        offsets.append(position)
        position += len(piece)

    raw_matches = list(TOKEN.finditer(html))
    if len(raw_matches) != len(tokens):
        reject("context", "Placeholder token count changed between structural and raw scans")
    if not tokens:
        return html, 0

    located = [_locate(parser, offsets, match.start(), match.end()) for match in raw_matches]
    values: list[str] = []
    for token in tokens:
        block = blocks[token.block_id]
        start = token.start if token.start is not None else 0
        end = token.end if token.end is not None else len(block.text)
        values.append(block.text[start:end])

    # Plan a decoded (unescaped) preview of every touched text node first, so
    # owner comparisons see the post-substitution characters -- exactly what
    # the tree path compares after `_insert` has already mutated the tree.
    plain_edits: dict[int, list[_Edit]] = defaultdict(list)
    for (data_index, local_start, local_end), value in zip(located, values, strict=True):
        plain_edits[data_index].append((local_start, local_end, value))
    virtual_text = dict(parser.text_values)
    for data_index, edits in plain_edits.items():
        virtual_text[data_index] = _apply_edits(parser.tokens[data_index], edits)

    owner_of: list = []
    for (data_index, _local_start, _local_end), is_direct, value in zip(located, token_direct, values, strict=True):
        enclosing = _enclosing_node(parser.nodes, data_index)
        if enclosing is None:
            reject("context", "Placeholder token has no owning element")
        owner_of.append(enclosing if is_direct else _SyntheticSpan(text=value, parent=enclosing))

    placements = [
        _RawPlacement(
            owner,
            token.block_id,
            token.start if token.start is not None else 0,
            token.end if token.end is not None else len(blocks[token.block_id].text),
        )
        for owner, token in zip(owner_of, tokens, strict=True)
    ]

    parent_of = _parent_map(parser.nodes)
    owners = _owners_raw(parser, block_table, placements, parent_of, virtual_text)

    markup_edits: dict[int, list[_Edit]] = defaultdict(list)
    for (data_index, local_start, local_end), is_direct, owner, value in zip(
        located, token_direct, owner_of, values, strict=True
    ):
        escaped = escape_text(value, quote=False)
        markup = escaped if is_direct else _wrapped_markup(owner, escaped)
        markup_edits[data_index].append((local_start, local_end, markup))

    for data_index, edits in markup_edits.items():
        parser.tokens[data_index] = _apply_edits(parser.tokens[data_index], edits)

    return "".join(parser.tokens), owners


__all__ = ["expand_placeholders_legacy"]
