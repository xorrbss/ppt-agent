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
    """Small dependency-free visible-text extractor for authored HTML."""

    _HIDDEN = frozenset({"script", "style", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._HIDDEN:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1

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
    return _normalise_text(" ".join(parser.parts))


def _block_text(block: Mapping[str, Any]) -> str:
    payload = _mapping(_value(block, "payload", default={}), label="source block payload")
    text = _value(payload, "text", default=None)
    if isinstance(text, str):
        return _normalise_text(text)
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
        return _normalise_text("".join(values))
    return ""


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
        cursor = 0
        rendered = visible_text(str(html))
        for _, block_id, block in ordered_text_blocks:
            expected = _block_text(block)
            checked_blocks += 1
            position = rendered.find(expected, cursor)
            if position < 0:
                missing.append(block_id)
                failed_slide_indices.add(slide_index)
            else:
                cursor = position + len(expected)

    result = SourceVerbatimReadbackResult(
        checked_slide_count=len(units),
        checked_block_count=checked_blocks,
        missing_block_ids=tuple(missing),
        order_violations=order_violations,
    )
    if not result.passed:
        raise SourceContentReadbackError(
            "source_content_verbatim_readback_failed",
            detail="source text is missing or out of source order",
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
