"""Offline test-only conversion of historical stamped owners into token text.

This is intentionally not a runtime fallback. It cannot turn historical absent
HTML or a missing owner into success; synthetic negative cases are separate.
"""

from difflib import SequenceMatcher

from services.source_content.source_runs import parse_source_binding_id
from services.source_content.text_stamping import stamp_source_blocks
from services.source_content.placeholder.substitute import _text
from utils.authored_html_tree.parser_adapter import parse_authored_html
from utils.authored_html_tree.serializer import serialize_authored_html


def _slots(node):
    if (node.text or "").strip():
        yield node, "text", node.text
    for child in node:
        if isinstance(child.tag, str):
            yield from _slots(child)
        if (child.tail or "").strip():
            yield child, "tail", child.tail


def token_version(html, table):
    parsed = parse_authored_html(html, profile="document")
    blocks = {block.id: block for block in table}
    owners = [node for node in parsed.root.iter() if node.get("data-source-block-id")]
    for owner in owners:
        binding = owner.get("data-source-block-id")
        block_id, first, last = parse_source_binding_id(binding)
        if block_id not in blocks:  # Source images are independent occurrences.
            continue
        block = blocks[block_id]
        start = 0 if first in (None, 0) else block.run_spans[first - 1][1]
        end = len(block.text) if last is None else block.run_spans[last][1]
        target = block.text[start:end]
        slots = list(_slots(owner))
        actual = "".join(char for _, _, value in slots for char in value if not char.isspace())
        positions = [index for index, char in enumerate(target) if not char.isspace()]
        compact = "".join(target[index] for index in positions)
        matcher = SequenceMatcher(a=actual, b=compact, autojunk=False)
        mapping = {0: 0, len(actual): len(compact)}
        for _tag, a0, a1, b0, b1 in matcher.get_opcodes():
            for offset in range(a0, a1 + 1):
                mapping[offset] = b0 + round((offset - a0) * (b1 - b0) / max(1, a1 - a0))
        consumed = 0
        cursor = start
        for index, (node, attribute, old) in enumerate(slots):
            consumed += sum(not char.isspace() for char in old)
            compact_end = mapping.get(consumed, len(compact))
            boundary = start + (positions[compact_end - 1] + 1 if compact_end else 0)
            boundary = next(value for value in block.boundaries if value >= boundary)
            if index == len(slots) - 1:
                boundary = end
            if boundary <= cursor:
                setattr(node, attribute, "")
                continue
            token = (
                f"{{{{q:{block_id}}}}}" if cursor == 0 and boundary == len(block.text)
                else f"{{{{q:{block_id}@{cursor}-{boundary}}}}}"
            )
            prefix = old[:len(old) - len(old.lstrip())]
            suffix = old[len(old.rstrip()):]
            setattr(node, attribute, prefix + token + suffix)
            cursor = boundary
        del owner.attrib["data-source-block-id"]
    return serialize_authored_html(parsed)


def binding_shapes(html):
    root = parse_authored_html(html, profile="document").root
    parents = {child: parent for parent in root.iter() for child in parent}

    def shape(node):
        attrs = tuple(sorted((key, value) for key, value in node.attrib.items()
                             if key not in {"data-source-block-id", "data-qid"}))
        return node.tag, attrs

    result = {}
    for node in root.iter():
        binding = node.get("data-source-block-id")
        if not binding:
            continue
        chain = [shape(node)]
        ancestor = node
        while ancestor in parents:
            ancestor = parents[ancestor]
            chain.append(shape(ancestor))
        result[binding] = (node.get("data-qid"), chain, [shape(child) for child in node.iter()])
    return result


def legacy_incompatibilities(html, table):
    """Existing binder deviations, not guessed token-mode exceptions."""
    result = []
    stamped = stamp_source_blocks(html, [(block.id, block.full_text) for block in table])
    result.extend(deviation.as_dict() for deviation in stamped.deviations)
    root = parse_authored_html(html, profile="document").root
    blocks = {block.id: block for block in table}
    for node in root.iter():
        binding = node.get("data-source-block-id")
        if not binding:
            continue
        block_id, first, last = parse_source_binding_id(binding)
        if block_id not in blocks:
            continue
        projection = blocks[block_id].projection
        span = projection.whole if first is None else (first, last)
        # A nested source owner is historical aliasing, even if normalization
        # happens to make one of the parent strings an exact match today.
        nested = any(child is not node and child.get("data-source-block-id") for child in node.iter())
        if nested or not projection.accepts(*span, _text(node)):
            item = {"sourceBlockId": block_id, "bindingId": binding, "kind": "shared_owner"}
            if item not in result:
                result.append(item)
    return result
