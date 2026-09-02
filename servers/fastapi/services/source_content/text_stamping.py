"""Bind immutable source text blocks to DOM elements before Chromium capture.

Binding is deterministic and as coarse as the DOM allows: a block is bound to
one element whose complete rendered text equals the block when one exists;
otherwise its projected runs (lines, bullet items, arrow/pipe steps, tab-gapped
cells) are covered by the longest contiguous spans that each equal one
element, in source order.  Every bound span gets ``data-source-block-id`` with
the run identity from :mod:`services.source_content.source_runs`.  "Equals"
is ``SourceRunProjection.accepts``: source characters verbatim, markers and
edge separators presentational.

Exclusive ownership and source order are how a block is *measured*, not what
the source contract promises.  When no element owns a span exclusively, or the
owners appear in another DOM order, the span is still bound -- to the innermost
element carrying its characters -- and the compromise is reported as a
:class:`SourceBindingDeviation` instead of failing the deck.  Binding fails
closed only when the characters are absent from the slide, which is the content
contract and is verified independently by the flat readback.

Repeats are tolerated only when bounded: identical text that matches several
elements binds the first in document order, and the remaining exact
duplicates (running headers, page numbers) stay decorative.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from services.source_content.source_runs import (
    SourceRunProjection,
    normalize_source_text,
    project_source_runs,
    source_run_binding_id,
)
from services.source_content.source_stamp_dom import (
    BrowserSourceReadbackError,
    _StampNode,
    _StampParser,
    deterministic_source_qid,
    insert_start_tag_attributes,
    parse_stamp_dom,
    replace_source_qid,
    semantic_role_for_start_tag,
    source_block_data_attribute,
)

_TERMINAL_MARKS = ".!?。！？"
_APPENDED_MARKS = ".。"


@dataclass(slots=True)
class _SpanBinding:
    start: int
    end: int
    node: _StampNode
    binding_id: str
    surplus: str | None
    # An element owned this span exclusively, and did so in source order.
    # Otherwise it merely carries the characters and measurement is coarser.
    exact: bool = True
    ordered: bool = True


@dataclass(frozen=True, slots=True)
class SourceBindingDeviation:
    """One compromise made to bind a span no element owns exclusively."""

    block_id: str
    binding_id: str
    kind: str

    def as_dict(self) -> dict[str, str]:
        return {
            "sourceBlockId": self.block_id,
            "bindingId": self.binding_id,
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class StampedSource:
    """Stamped HTML plus the compromises the binder had to make."""

    html: str
    deviations: tuple[SourceBindingDeviation, ...]


def terminal_punctuation_surplus(actual: object, expected: object) -> str | None:
    """Return the surplus terminal punctuation a bounded repair may strip.

    Verbatim authoring occasionally duplicates the final mark already in the
    source (``.`` -> ``..``) or appends one full stop to a source run that has
    no terminal punctuation.  Neither is accepted as equivalent readback; the
    caller repairs the leaf back to the exact source bytes before capture.
    Any other wording or punctuation drift returns ``None``.
    """

    actual_value = normalize_source_text(actual)
    expected_value = normalize_source_text(expected)
    if not expected_value or len(actual_value) <= len(expected_value):
        return None
    if not actual_value.startswith(expected_value):
        return None
    surplus = actual_value[len(expected_value):]
    last = expected_value[-1]
    if last in _TERMINAL_MARKS:
        return surplus if surplus == last * len(surplus) else None
    return surplus if surplus in _APPENDED_MARKS else None


def stamp_source_block_ids(html_source: str, block_texts: Sequence[tuple[str, str]]) -> str:
    """Stamp deterministic source IDs on the DOM elements owning each block.

    Matching is against an element's complete rendered text (nested text is
    included and hidden script/style text is ignored).  Blocks that render as
    several elements are bound per projected line or run; a span with no exact
    match raises instead of guessing, keeping the browser binding fail closed.
    """

    return stamp_source_blocks(html_source, block_texts).html


def stamp_source_blocks(
    html_source: str, block_texts: Sequence[tuple[str, str]]
) -> StampedSource:
    """Stamp deterministic source IDs and report the binder's compromises."""

    parser = parse_stamp_dom(html_source)
    used: set[str] = set()
    deviations: list[SourceBindingDeviation] = []
    for raw_block_id, raw_expected in block_texts:
        block_id = str(raw_block_id).strip()
        if not block_id or not normalize_source_text(raw_expected) or block_id in used:
            raise BrowserSourceReadbackError(
                "source_content_browser_readback_invalid",
                detail="source block IDs/text must be unique and non-empty",
            )
        projection = project_source_runs(raw_expected)
        for binding in _bind_block(parser, projection, block_id):
            node = binding.node
            if node.qid_count > 1:
                raise BrowserSourceReadbackError(
                    "source_content_browser_readback_failed",
                    detail="source block has duplicate authored QID attributes",
                    block_ids=(block_id,),
                )
            if binding.surplus:
                _repair_terminal_punctuation(parser, node, binding.surplus, block_id)
            _stamp_node(parser, node, binding.binding_id, block_id)
            if not binding.exact:
                deviations.append(
                    SourceBindingDeviation(block_id, binding.binding_id, "shared_owner")
                )
            elif not binding.ordered:
                deviations.append(
                    SourceBindingDeviation(block_id, binding.binding_id, "out_of_order")
                )
        used.add(block_id)
    return StampedSource("".join(parser.tokens), tuple(deviations))


def _bind_block(
    parser: _StampParser, projection: SourceRunProjection, block_id: str
) -> list[_SpanBinding]:
    """Cover every projected run with the coarsest DOM spans available.

    Each span is attempted three ways, best measurement first: an exclusive
    owner in source order, an exclusive owner anywhere in the document, and
    finally the innermost element that merely carries the span's characters.
    Only a span whose characters are absent from the slide fails closed; the
    weaker bindings are reported by the caller instead.
    """

    bound: list[_SpanBinding] = []
    cursor = 0
    last_start = -1
    count = projection.run_count
    while cursor < count:
        # A span that starts after a pipe may be rendered anywhere: the pipe
        # joins independent labels and the new template decides which slot each
        # lands in, regularly emitting the second before the first.
        after = -1 if projection.runs[cursor].order_free_before else last_start
        binding = _bind_span(parser, projection, block_id, cursor, after=after)
        if binding is None:
            if _owned_elsewhere(parser, projection, cursor):
                raise BrowserSourceReadbackError(
                    "source_content_browser_readback_failed",
                    detail="source block text is already bound to a conflicting ID",
                    block_ids=(block_id,),
                )
            raise _no_match(block_id, projection, cursor, cursor)
        bound.append(binding)
        last_start = max(last_start, binding.node.start)
        cursor = binding.end + 1
    return bound


def _bind_span(
    parser: _StampParser,
    projection: SourceRunProjection,
    block_id: str,
    cursor: int,
    *,
    after: int,
) -> _SpanBinding | None:
    """Return the best available binding for the span starting at ``cursor``."""

    count = projection.run_count
    for ordered, exact in ((True, True), (False, True), (False, False)):
        for end in range(count - 1, cursor - 1, -1):
            binding = _find_span(
                parser,
                projection,
                block_id,
                cursor,
                end,
                after=after if ordered else -1,
                exact=exact,
            )
            if binding is not None:
                binding.ordered = ordered
                binding.exact = exact
                return binding
    return None


def _owned_elsewhere(
    parser: _StampParser, projection: SourceRunProjection, cursor: int
) -> bool:
    """Whether the span's characters are on the slide but owned by another ID.

    Distinguishing this from absent text keeps the fail-closed message honest:
    the deck did render the source, and the binder refuses to take an element
    a different source block already claims.
    """

    for node in parser.nodes:
        if node.hidden or not node.stamped:
            continue
        text = normalize_source_text(parser.node_text(node))
        if projection.contains(cursor, cursor, text):
            return True
    return False


def _no_match(
    block_id: str, projection: SourceRunProjection, start: int, end: int
) -> BrowserSourceReadbackError:
    detail = "source block text has no unique DOM element"
    if projection.run_count > 1:
        detail += f" (run {start}: {projection.span_text(start, end)[:60]!r})"
    return BrowserSourceReadbackError(
        "source_content_browser_readback_failed", detail=detail, block_ids=(block_id,)
    )


def _find_span(
    parser: _StampParser,
    projection: SourceRunProjection,
    block_id: str,
    start: int,
    end: int,
    *,
    after: int,
    exact: bool = True,
) -> _SpanBinding | None:
    """Return a binding for one run span, or ``None``.

    With ``exact`` the element's complete text must be the span; without it the
    element only has to carry the span's characters, which still binds text the
    model buried inside a larger element so it can be measured.
    """

    binding_id = source_run_binding_id(block_id, start, end, projection.run_count)
    visible = [node for node in parser.nodes if not node.hidden and node.start > after]
    texts = {id(node): normalize_source_text(parser.node_text(node)) for node in visible}
    surplus_by_node: dict[int, str] = {}
    if exact:
        matching = [node for node in visible if projection.accepts(start, end, texts[id(node)])]
        if not matching:
            for node in visible:
                surplus = _span_punctuation_surplus(projection, start, end, texts[id(node)])
                if surplus:
                    surplus_by_node[id(node)] = surplus
                    matching.append(node)
    else:
        matching = [node for node in visible if projection.contains(start, end, texts[id(node)])]
    candidates = [node for node in matching if not node.stamped or node.source_id == binding_id]
    if not candidates:
        return None
    # A wrapper and its leaf can expose the same rendered text (for example
    # ``<p><span>text</span></p>``).  Bind the deepest match; among unrelated
    # exact duplicates the first in document order owns the span and the
    # remaining copies stay decorative.
    candidates = [
        node for node in candidates
        if not any(
            other is not node and other.start > node.start and
            (node.end is None or (other.end is not None and other.end <= node.end))
            for other in candidates
        )
    ]
    node = candidates[0]
    return _SpanBinding(start, end, node, binding_id, surplus_by_node.get(id(node)))


def _span_punctuation_surplus(
    projection: SourceRunProjection, start: int, end: int, actual: str
) -> str | None:
    """Surplus terminal punctuation on a span rendering, if repairing it binds.

    ``terminal_punctuation_surplus`` decides which surplus is repairable; the
    span's tolerant matcher decides whether the text left after the repair is
    the span, so an omitted marker or an attached pipe does not block the same
    one-mark repair a canonical rendering gets.
    """

    if not actual:
        return None
    last = actual[-1]
    if last in _TERMINAL_MARKS:
        repaired = actual.rstrip(last)
        candidates = (repaired + last, repaired)
    else:
        candidates = (actual,)
    for candidate in candidates:
        if candidate == actual or not projection.accepts(start, end, candidate):
            continue
        surplus = terminal_punctuation_surplus(actual, candidate)
        if surplus:
            return surplus
    return None


def _repair_terminal_punctuation(
    parser: _StampParser, node: _StampNode, surplus: str, block_id: str
) -> None:
    """Strip surplus terminal punctuation from the node's last text token."""

    last = node.end if node.end is not None else len(parser.tokens)
    for index in range(last - 1, node.start, -1):
        if index not in parser.text_values:
            continue
        value = parser.text_values[index]
        if not value.strip():
            continue
        stripped = value.rstrip()
        if index not in parser.data_tokens or not stripped.endswith(surplus):
            break
        repaired = stripped[: -len(surplus)] + value[len(stripped):]
        parser.tokens[index] = repaired
        parser.text_values[index] = repaired
        return
    raise BrowserSourceReadbackError(
        "source_content_browser_readback_failed",
        detail="source block punctuation drift is not safely repairable",
        block_ids=(block_id,),
    )


def _stamp_node(
    parser: _StampParser, node: _StampNode, binding_id: str, block_id: str
) -> None:
    raw_tag = parser.tokens[node.start]
    source_qid = deterministic_source_qid(binding_id)
    if node.has_qid:
        raw_tag = replace_source_qid(raw_tag, source_qid)
    attributes = "" if node.stamped else source_block_data_attribute(binding_id)
    if not node.has_qid:
        attributes = (attributes + " " if attributes else "") + f'data-qid="{source_qid}"'
    if not node.has_semantic_role:
        # Keep source text in the same semantic contract as authored text.
        # This marker is observed by Chromium and avoids accepting an
        # HTML-only match.
        role = semantic_role_for_start_tag(raw_tag)
        attributes = (attributes + " " if attributes else "") + f'data-semantic-role="{role}"'
    if attributes:
        # A previously sealed owner (source ID, QID and role all present) is
        # left byte-identical so durable retries can prove the seal existed.
        parser.tokens[node.start] = insert_start_tag_attributes(
            raw_tag, attributes, block_id=block_id, label="DOM"
        )
    node.stamped = True
    node.source_id = binding_id


__all__ = [
    "SourceBindingDeviation",
    "StampedSource",
    "stamp_source_block_ids",
    "stamp_source_blocks",
    "terminal_punctuation_surplus",
]
