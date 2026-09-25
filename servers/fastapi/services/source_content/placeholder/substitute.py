"""Single-pass data insertion with token-derived whole/run ownership."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from services.source_content.placeholder.block_table import SourceBlock
from services.source_content.placeholder.stamping import stamp_owner
from services.source_content.placeholder.static_checks import check_coverage
from services.source_content.placeholder.substitute_legacy import expand_placeholders_legacy
from services.source_content.placeholder.tokens import TOKEN, reject
from services.source_content.placeholder.tree_slots import TextSlot, original_text_slots
from services.source_content.source_runs import source_run_binding_id
from utils.authored_html_tree.parser_adapter import parse_authored_html
from utils.authored_html_tree.serializer import serialize_authored_html
from services.authored_rollout_profile import current_rollout_profile


@dataclass(frozen=True, slots=True)
class ExpandedHtml:
    html: str
    deviations: tuple[dict, ...]
    receipt: dict


@dataclass(frozen=True, slots=True)
class _Placement:
    owner: Element
    block_id: str
    start: int
    end: int


def _insert(slot: TextSlot, blocks: dict[str, SourceBlock], direct: bool) -> list[_Placement]:
    """Assign strings to etree text/tail; serializer alone escapes inserted data."""
    pieces: list[str | Element] = []
    placements: list[_Placement] = []
    cursor = 0
    for token in slot.tokens:
        pieces.append(slot.text[cursor:token.begin])
        block = blocks[token.block_id]
        start = token.start if token.start is not None else 0
        end = token.end if token.end is not None else len(block.text)
        value = block.text[start:end]
        if direct:
            pieces.append(value)
            owner = slot.parent
        else:
            owner = Element("span")
            owner.text = value
            pieces.append(owner)
        placements.append(_Placement(owner, block.id, start, end))
        cursor = token.finish
    pieces.append(slot.text[cursor:])
    setattr(slot.node, slot.attribute, "")
    previous = slot.node if slot.attribute == "tail" else None
    position = list(slot.parent).index(slot.node) + 1 if previous is not None else 0
    for piece in pieces:
        if isinstance(piece, str):
            if previous is None:
                slot.parent.text = (slot.parent.text or "") + piece
            else:
                previous.tail = (previous.tail or "") + piece
        else:
            slot.parent.insert(position, piece)
            position += 1
            previous = piece
    return placements


def _common_owner(placements: Sequence[_Placement], parents: dict[Element, Element]) -> Element:
    chains = []
    for placement in placements:
        chain = [placement.owner]
        while chain[-1] in parents:
            chain.append(parents[chain[-1]])
        chains.append(chain)
    return next(node for node in chains[0] if all(node in chain for chain in chains[1:]))


def _text(element: Element) -> str:
    # itertext omits <br>'s rendered contribution; reuse the binder's convention.
    value = element.text or ""
    for child in element:
        value += "\n" if child.tag == "br" else _text(child)
        value += child.tail or ""
    return value


def _owners(root: Element, table: Sequence[SourceBlock], placements: list[_Placement]) -> int:
    parents = {child: parent for parent in root.iter() for child in parent}
    by_block: dict[str, list[_Placement]] = defaultdict(list)
    for placement in placements:
        by_block[placement.block_id].append(placement)
    claimed: set[Element] = set()
    count = 0
    for block in table:
        group = by_block[block.id]
        whole = _common_owner(group, parents)
        projection = block.projection
        if projection.accepts(*projection.whole, _text(whole)) and whole not in claimed:
            stamp_owner(whole, block.id, block.id)
            claimed.add(whole)
            count += 1
            continue
        # A range may cut inside a run for emphasis, but measured owners still
        # partition existing projected runs. Never mint a character-range QID.
        cursor = 0
        while cursor < projection.run_count:
            found = False
            for last in range(projection.run_count - 1, cursor - 1, -1):
                begin, end = block.run_spans[cursor][0], block.run_spans[last][1]
                selected = [p for p in group if p.end > begin and p.start < end]
                if not selected:
                    continue
                owner = _common_owner(selected, parents)
                if owner in claimed or not projection.accepts(cursor, last, _text(owner)):
                    continue
                binding_id = source_run_binding_id(block.id, cursor, last, projection.run_count)
                stamp_owner(owner, binding_id, block.id)
                claimed.add(owner)
                count += 1
                cursor = last + 1
                found = True
                break
            if not found:
                reject("context", "Token ranges need an exclusive whole-block or projected-run owner", (block.id,))
    return count


def expand_placeholders(html: str, block_table: Sequence[SourceBlock], *, context) -> ExpandedHtml:
    """Expand only original visible text nodes; final policy/readback remain T2's.

    Structural validation (which tokens exist, in which order, covering which
    blocks) always runs on the parsed tree. The *serialization* the caller
    receives back must match the active ``AUTHORED_HTML_NORMALIZATION`` mode
    (B5/R8): ``tree`` mode adopts the canonical HTML5 serializer, exactly as
    admission does elsewhere for that mode; ``legacy`` mode instead splices
    the same substitutions directly into ``html``'s own bytes, so a call with
    nothing to expand -- and every byte outside a token or a stamped owner
    attribute -- stays byte-identical to the input.
    """
    profile = getattr(context, "normalization_profile", "document")
    parsed = parse_authored_html(html, profile=profile)
    slots = original_text_slots(parsed.root)
    tokens = tuple(token for slot in slots for token in slot.tokens)
    deviations = check_coverage(tokens, block_table)
    blocks = {block.id: block for block in block_table}
    # Plan ownership before inserting any source data (which may look like tokens).
    direct = []
    for slot in slots:
        parent_text = "".join(slot.parent.itertext())
        ids = {match[1] for match in TOKEN.finditer(parent_text)}
        direct.append(len(slot.tokens) == 1 and len(ids) == 1 and not TOKEN.sub("", parent_text).strip())

    # The attempt's frozen profile, never a live read: an admission that is
    # already pinned must splice the representation it was pinned to, and a
    # resumed attempt must restore it from the job record rather than from
    # whatever the environment says now.
    if current_rollout_profile().html_normalization == "legacy":
        token_direct = tuple(
            is_direct for slot, is_direct in zip(slots, direct, strict=True) for _ in slot.tokens
        )
        expanded, owners = expand_placeholders_legacy(html, tokens, token_direct, blocks, block_table)
    else:
        placements = []
        for slot, use_parent in zip(slots, direct, strict=True):
            placements.extend(_insert(slot, blocks, use_parent))
        owners = _owners(parsed.root, block_table, placements)
        expanded = serialize_authored_html(parsed)

    return ExpandedHtml(expanded, deviations, {
        "rule": "source_placeholder_expanded", "blocks": len(block_table),
        "tokens": len(tokens), "owners": owners, "deviations": len(deviations),
        "insertedChars": sum(len(block.text) for block in block_table),
    })
