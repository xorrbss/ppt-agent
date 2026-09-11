"""Static occurrence/partition validation before source text is inserted."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from services.source_content.placeholder.block_table import SourceBlock
from services.source_content.placeholder.tokens import PlaceholderToken, reject


def check_coverage(tokens: Sequence[PlaceholderToken], table: Sequence[SourceBlock]) -> tuple[dict, ...]:
    blocks = {block.id: block for block in table}
    occurrences: dict[str, list[PlaceholderToken]] = defaultdict(list)
    for token in tokens:
        if token.block_id not in blocks:
            reject("unknown", "Token id was not offered by the server", (token.block_id,))
        occurrences[token.block_id].append(token)
    missing = [block.id for block in table if block.id not in occurrences]
    if missing:
        reject("missing", "Expected source occurrences are absent", missing)
    for block in table:
        group = occurrences[block.id]
        whole = [token for token in group if token.start is None]
        if whole:
            if len(group) != 1:
                reject("duplicate", "Whole-block occurrence is repeated or mixed with ranges", (block.id,))
            continue
        spans = [(token.start, token.end) for token in group]
        if len(set(spans)) != len(spans):
            reject("duplicate", "An identical character range occurs twice", (block.id,))
        cursor = 0
        boundaries = frozenset(block.boundaries)
        for start, end in sorted(spans):
            if start != cursor or end <= start or start not in boundaries or end not in boundaries:
                reject("cover", "Ranges must be disjoint, contiguous and grapheme-safe", (block.id,))
            cursor = end
        if cursor != len(block.text):
            reject("cover", "Ranges do not cover the complete normalized block", (block.id,))
    observed = list(dict.fromkeys(token.block_id for token in tokens))
    expected = [block.id for block in table]
    deviations = []
    if observed != expected:
        deviations.append({"kind": "within_slide_dom_order", "count": len(observed)})
    for block_id, group in occurrences.items():
        starts = [token.start for token in group if token.start is not None]
        if starts != sorted(starts):
            deviations.append({"kind": "within_block_dom_order", "blockId": block_id})
    return tuple(deviations[:32])
