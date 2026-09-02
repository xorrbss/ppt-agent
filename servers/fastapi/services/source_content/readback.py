"""Deterministic readback checks for source-verbatim authored slides.

The authored renderer is intentionally free to change markup and styling.  A
verbatim source contract therefore cannot compare HTML bytes; it must compare
the source text projected by the immutable manifest with the visible text of
each final slide, while retaining source-unit and block order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Any

from services.source_content.source_runs import project_source_runs


class SourceContentReadbackError(ValueError):
    """A publication-blocking source-verbatim readback failure."""

    def __init__(
        self,
        code: str,
        *,
        detail: str,
        slide_indices: tuple[int, ...] = (),
        block_ids: tuple[str, ...] = (),
        order_violations: int = 0,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.slide_indices = slide_indices
        self.block_ids = block_ids
        self.order_violations = order_violations


@dataclass(frozen=True, slots=True)
class SourceVerbatimReadbackResult:
    """Stable receipt payload for the pre-publication verifier."""

    checked_slide_count: int
    checked_block_count: int
    missing_block_ids: tuple[str, ...] = ()
    order_violations: int = 0

    @property
    def passed(self) -> bool:
        return not self.missing_block_ids and self.order_violations == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkedSlideCount": self.checked_slide_count,
            "checkedBlockCount": self.checked_block_count,
            "missingBlockIds": list(self.missing_block_ids),
            "orderViolations": self.order_violations,
            "passed": self.passed,
        }


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    if not isinstance(value, Mapping):
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_invalid",
            detail=f"{label} must be an object",
        )
    return value


def _value(value: Mapping[str, Any], camel: str, snake: str | None = None, default: Any = None) -> Any:
    if camel in value:
        return value[camel]
    if snake and snake in value:
        return value[snake]
    return default


def _normalise_text(value: str) -> str:
    # HTML layout whitespace is not source content.  Collapse it while
    # preserving every non-whitespace code point, including CJK punctuation.
    return re.sub(r"\s+", " ", value).strip()


class _VisibleTextParser(HTMLParser):
    """Small dependency-free visible-text extractor for authored HTML.

    Text is concatenated the way a browser lays it out: an inline element does
    not separate the characters around it, while a block-level boundary and an
    explicit ``br`` do.  Separating on *every* tag turned emphasis inside a
    source sentence -- ``<strong>8시간</strong>의 원칙`` -- into ``8시간 의 원칙``
    and made verbatim source text look absent.
    """

    _HIDDEN = frozenset({"script", "style", "noscript", "template"})
    _INLINE = frozenset({
        "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "dfn",
        "em", "font", "i", "kbd", "mark", "nobr", "q", "rp", "rt", "ruby", "s",
        "samp", "small", "span", "strong", "sub", "sup", "time", "tt", "u",
        "var", "wbr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def _boundary(self, tag: str) -> None:
        if tag not in self._INLINE and not self._hidden_depth:
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self._HIDDEN:
            self._hidden_depth += 1
            return
        self._boundary(lowered)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._boundary(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._HIDDEN:
            if self._hidden_depth:
                self._hidden_depth -= 1
            return
        self._boundary(lowered)

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data:
            self.parts.append(data)


def visible_text(html: str) -> str:
    """Return normalized text visible to a slide reader."""

    parser = _VisibleTextParser()
    try:
        parser.feed(str(html))
        parser.close()
    except Exception as error:  # pragma: no cover - HTMLParser is forgiving
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_invalid",
            detail="authored slide HTML could not be parsed",
        ) from error
    return _normalise_text("".join(parser.parts))


def _block_raw_text(block: Mapping[str, Any]) -> str:
    payload = _mapping(_value(block, "payload", default={}), label="source block payload")
    text = _value(payload, "text", default=None)
    if isinstance(text, str):
        return text
    # A few extractors retain only runs in payload.  Joining those runs keeps
    # the check lossless without requiring a particular extractor version.
    runs = _value(payload, "runs", default=())
    if isinstance(runs, Sequence) and not isinstance(runs, (str, bytes)):
        values: list[str] = []
        for run in runs:
            if isinstance(run, Mapping):
                value = run.get("text")
            else:
                value = run
            if isinstance(value, str):
                values.append(value)
        return "".join(values)
    return ""


def _block_text(block: Mapping[str, Any]) -> str:
    return _normalise_text(_block_raw_text(block))


def _layout_free(value: str) -> str:
    """Drop whitespace entirely so layout cannot hide present source text.

    ``_normalise_text`` already collapses whitespace runs, which is not enough:
    a restyle may put a source run's parts in separate boxes (a metric widget
    rendering ``10곳 찾기`` as ``10`` beside ``곳 찾기``) or wrap part of it in
    emphasis.  Whitespace is the template's, the characters are the source's.
    Single-element ownership and uniqueness are proven by the stamping gate, so
    matching here only has to answer whether the characters are present in
    order.
    """

    return re.sub(r"\s+", "", value)


def _find_block_runs(rendered: str, block: Mapping[str, Any], cursor: int) -> int:
    """Return the cursor after the block's projected runs, or ``-1``.

    Authored markup may split one source block into bullet items, flow steps
    or lines.  Every projected run must still occur verbatim and in order;
    only the leading bullet/enumerator/arrow marker of a run is optional.
    """

    try:
        projection = project_source_runs(_block_raw_text(block))
    except ValueError:
        return -1
    for run in projection.runs:
        # A pipe joins independent labels the new template may place in either
        # order, so a run after one is searched from the start of the slide.
        # Bullets, arrows and lines keep the sequence the source author chose.
        if run.order_free_before:
            cursor = 0
        forms = (run.marker + run.body, run.body) if run.marker else (run.body,)
        position = -1
        for raw_form in forms:
            form = _layout_free(raw_form)
            if not form:
                continue
            position = rendered.find(form, cursor)
            if position >= 0:
                cursor = position + len(form)
                break
        if position < 0:
            return -1
    return cursor


def verify_source_verbatim_readback(
    *,
    manifest: Any,
    outline: Any,
    htmls: Sequence[str],
) -> SourceVerbatimReadbackResult:
    """Verify source text presence, slide cardinality, and reading order.

    The function is deliberately fail-closed for malformed contracts.  It is
    intended to run after the final authored/VQA HTML is available and before
    export/publication.  Template furniture and generated visual labels may
    remain in a slide; every admitted source text block must nevertheless occur
    verbatim and in source order.
    """

    manifest_value = _mapping(manifest, label="source manifest")
    outline_value = _mapping(outline, label="source outline")
    raw_units = _value(manifest_value, "units", default=())
    raw_slides = _value(outline_value, "slides", default=())
    if not isinstance(raw_units, Sequence) or isinstance(raw_units, (str, bytes)):
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_invalid", detail="manifest units must be a sequence"
        )
    if not isinstance(raw_slides, Sequence) or isinstance(raw_slides, (str, bytes)):
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_invalid", detail="outline slides must be a sequence"
        )
    units = sorted(
        (_mapping(unit, label="source unit") for unit in raw_units),
        key=lambda item: (
            int(_value(item, "sourceIndex", "source_index", 0)),
            str(_value(item, "sourceUnitId", "source_unit_id", "")),
        ),
    )
    if len(units) != len(raw_slides) or len(htmls) != len(units):
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_failed",
            detail="source and authored slide cardinality differs",
            slide_indices=tuple(range(max(len(units), len(htmls)))),
        )

    missing: list[str] = []
    order_violations = 0
    checked_blocks = 0
    failed_slide_indices: set[int] = set()
    for slide_index, (unit, raw_slide, html) in enumerate(zip(units, raw_slides, htmls, strict=True)):
        slide = _mapping(raw_slide, label="source outline slide")
        expected_unit_id = str(_value(unit, "sourceUnitId", "source_unit_id", ""))
        actual_unit_id = str(_value(slide, "sourceUnitId", "source_unit_id", ""))
        if expected_unit_id != actual_unit_id:
            order_violations += 1
            failed_slide_indices.add(slide_index)
        block_by_id: dict[str, Mapping[str, Any]] = {}
        ordered_text_blocks: list[tuple[int, str, Mapping[str, Any]]] = []
        for raw_block in _value(unit, "blocks", default=()) or ():
            block = _mapping(raw_block, label="source block")
            block_id = str(_value(block, "sourceBlockId", "source_block_id", ""))
            kind = str(_value(block, "kind", default=""))
            if block_id and kind == "text":
                block_by_id[block_id] = block
                if _block_text(block):
                    ordered_text_blocks.append(
                        (int(_value(block, "order", default=0)), block_id, block)
                    )
        ordered_text_blocks.sort(key=lambda item: (item[0], item[1]))
        expected_ids = [item[1] for item in ordered_text_blocks]
        reading_order = _value(slide, "readingOrder", "reading_order", ()) or ()
        actual_ids = [str(block_id) for block_id in reading_order if str(block_id) in block_by_id]
        if actual_ids != expected_ids:
            order_violations += 1
            failed_slide_indices.add(slide_index)
            for block_id in expected_ids:
                if block_id not in actual_ids and block_id not in missing:
                    missing.append(block_id)
        rendered = _layout_free(visible_text(str(html)))
        # Restyling recomposes the page: the new template owns where a source
        # block lands, so one block's position relative to *another* block is
        # not part of the contract.  Source shape order is not even the source's
        # own reading order -- a page number in the top-right corner regularly
        # precedes a bottom running footer in the XML -- so requiring the
        # authored slide to reproduce it rejects correct decks.  Order still
        # binds *within* a block (``_find_block_runs`` walks its own runs in
        # order), which is where the source author's sequence carries meaning.
        # Two blocks that share the same text keep a monotonic cursor so N
        # identical source blocks still require N rendered occurrences.
        text_cursors: dict[str, int] = {}
        for _, block_id, block in ordered_text_blocks:
            checked_blocks += 1
            block_key = _block_text(block)
            next_cursor = _find_block_runs(
                rendered, block, text_cursors.get(block_key, 0)
            )
            if next_cursor < 0:
                missing.append(block_id)
                failed_slide_indices.add(slide_index)
            else:
                text_cursors[block_key] = next_cursor

    result = SourceVerbatimReadbackResult(
        checked_slide_count=len(units),
        checked_block_count=checked_blocks,
        missing_block_ids=tuple(missing),
        order_violations=order_violations,
    )
    if not result.passed:
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_failed",
            detail=(
                "source text is missing from the authored deck"
                if not result.order_violations
                else "source reading order does not match the manifest"
            ),
            slide_indices=tuple(sorted(failed_slide_indices)),
            block_ids=result.missing_block_ids,
            order_violations=result.order_violations,
        )
    return result


__all__ = [
    "SourceContentReadbackError",
    "SourceVerbatimReadbackResult",
    "verify_source_verbatim_readback",
    "visible_text",
]
