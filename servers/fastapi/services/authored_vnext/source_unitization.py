"""Deterministic, structure-preserving source text admission.

This module is intentionally provider-free.  It turns the public v2
``[SOURCE PAGE n]`` brief into digest-bound text units without inventing copy,
flattening paragraph boundaries, or cutting inside a sentence or word.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal

from services.authored_vnext.narrative_ir.content_graph import seal_content_graph
from services.authored_vnext.composition_capacity import (
    MIN_SOURCE_VERBATIM_TEXT_SCALE,
)


class SourceUnitizationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


BoundaryKind = Literal[
    "title-candidate",
    "paragraph",
    "bullet",
    "table-cell",
]


@dataclass(frozen=True)
class SourceTextUnit:
    text: str
    boundary_kind: BoundaryKind
    source_block_index: int
    sentence_start: int
    sentence_end: int


@dataclass(frozen=True)
class SourcePageUnitization:
    units: tuple[SourceTextUnit, ...]
    title_candidate_index: int
    source_text_digest: str
    unitization_digest: str


@dataclass(frozen=True)
class SourceContentAdmission:
    content_graph: object
    frame_indexes: tuple[int, ...]
    page_unitization_digests: tuple[str, ...]


@dataclass(frozen=True)
class _SourceBlock:
    kind: Literal["heading", "paragraph", "bullet", "table-cell"]
    text: str
    source_block_index: int


_SOURCE_PAGE_PATTERN = re.compile(
    r"(?m)^\[SOURCE PAGE (?P<number>[1-9][0-9]{0,2})\]\s*"
)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<text>.+?)\s*#*\s*$")
_BULLET = re.compile(
    r"^\s*(?P<marker>(?:[-*+\u2022\u2023\u25aa\u25e6]|(?:[0-9]+|[A-Za-z])[.)]))\s+(?P<text>\S(?:.*\S)?)\s*$"
)
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_SENTENCE = re.compile(
    r".*?(?:[.!?\u3002\uff01\uff1f]+(?:[\"'\u201d\u2019)\]]+)?(?=\s|$)|$)",
    re.DOTALL,
)
_PAGE_NUMBER = re.compile(r"^\s*\d{1,3}\s*/\s*\d{1,3}\s*$")
_EYEBROW = re.compile(r"^\s*\d{1,2}\s*(?:[·.\-—]|[|:])\s*\S+")
_PROCESS_STEP = re.compile(r"^0*[1-9]\d{0,2}$")
_PROCESS_SECTION = re.compile(r"^\s*\d{1,2}\s*[—-]\s*\d{1,2}(?:\s+\S.*)?$")
_KPI_VALUE = re.compile(
    r"^\s*(?:\d+(?:[.,]\d+)?\s*(?:%|건|개|명|주|일|시간|분|초|원|억|만|배|MD)"
    r"|\d+(?:[.,]\d+)?\s*(?:→|->)\s*\d+(?:[.,]\d+)?(?:\s*[A-Za-z가-힣]+)?)\s*$",
    re.IGNORECASE,
)

_SEMANTIC_FIELDS = (
    ("semantic-role", "semanticRole"),
    ("semantic-group-kind", "semanticGroupKind"),
    ("semantic-group-id", "semanticGroupId"),
    ("semantic-group-ordinal", "semanticGroupOrdinal"),
    ("semantic-table-row", "tableRow"),
    ("semantic-table-column", "tableColumn"),
    ("semantic-table-row-span", "tableRowSpan"),
    ("semantic-table-column-span", "tableColumnSpan"),
    ("semantic-table-header", "tableHeader"),
    ("semantic-image-kind", "imageKind"),
    ("semantic-image-aspect-class", "imageAspectClass"),
    ("semantic-image-aspect-ratio", "imageAspectRatio"),
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalize_inline(value: str) -> str:
    return re.sub(r"[\t\x0b\x0c\r ]+", " ", value).strip()


def _table_cells(line: str) -> list[str] | None:
    if "|" not in line:
        return None
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    cells = [_normalize_inline(item) for item in re.split(r"(?<!\\)\|", raw)]
    if len(cells) < 2:
        return None
    if all(not item or _TABLE_SEPARATOR.fullmatch(item) for item in cells):
        return []
    return [item.replace(r"\|", "|") for item in cells if item]


def _parse_blocks(value: str) -> list[_SourceBlock]:
    blocks: list[_SourceBlock] = []
    paragraph_lines: list[str] = []
    source_block_index = 0

    def append(kind: Literal["heading", "paragraph", "bullet", "table-cell"], text: str) -> None:
        nonlocal source_block_index
        normalized = _normalize_inline(text)
        if not normalized:
            return
        blocks.append(_SourceBlock(kind, normalized, source_block_index))
        source_block_index += 1

    def flush_paragraph() -> None:
        if paragraph_lines:
            append("paragraph", " ".join(paragraph_lines))
            paragraph_lines.clear()

    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            flush_paragraph()
            continue
        heading = _HEADING.match(line)
        if heading:
            flush_paragraph()
            append("heading", heading.group("text"))
            continue
        bullet = _BULLET.match(line)
        if bullet:
            flush_paragraph()
            append("bullet", f"{bullet.group('marker')} {bullet.group('text')}")
            continue
        cells = _table_cells(line)
        if cells is not None:
            flush_paragraph()
            for cell in cells:
                append("table-cell", cell)
            continue
        paragraph_lines.append(_normalize_inline(line))
    flush_paragraph()
    return blocks


def _sentences(text: str) -> list[str]:
    values = [_normalize_inline(item.group(0)) for item in _SENTENCE.finditer(text)]
    values = [item for item in values if item]
    return values or [_normalize_inline(text)]


def _split_complete_sentences(text: str, *, capacity: int) -> list[tuple[str, int, int]]:
    sentences = _sentences(text)
    pieces: list[tuple[str, int, int]] = []
    current: list[str] = []
    start = 0
    for index, sentence in enumerate(sentences):
        if len(sentence) > capacity:
            raise SourceUnitizationError("source_native_sentence_capacity_exceeded")
        candidate = sentence if not current else " ".join([*current, sentence])
        if current and len(candidate) > capacity:
            pieces.append((" ".join(current), start, index))
            current = [sentence]
            start = index
        else:
            current.append(sentence)
    if current:
        pieces.append((" ".join(current), start, len(sentences)))
    return pieces


def _text_slot_capacity(slot: object) -> int | None:
    accepted = {
        str(item)
        for item in (getattr(slot, "accepted_kinds", None) or ("text",))
    }
    handle = getattr(slot, "patch_handle", None)
    if "text" not in accepted or getattr(handle, "kind", None) not in {
        "authored-qid",
        "scene-element",
    }:
        return None
    envelope = getattr(slot, "capacity_envelope", None)
    bounds = getattr(slot, "bounds", None)
    if envelope is None:
        return None
    maximums = [int(getattr(envelope, "max_characters", 0) or 0)]
    width = float(getattr(bounds, "width", 0) or 0)
    characters_per_line = max(
        4,
        int(
            getattr(envelope, "characters_per_line", 0)
            or (width / 11 if width else 40)
        ),
    )
    maximums.append(
        int(getattr(envelope, "max_lines", 0) or 0) * characters_per_line
    )
    content_area = float(getattr(envelope, "content_area", 0) or 0)
    character_area = float(getattr(envelope, "character_area", 150) or 150)
    if content_area > 0:
        maximums.append(int(content_area / character_area))
    positive = [item for item in maximums if item > 0]
    return (
        max(1, int(min(positive) / MIN_SOURCE_VERBATIM_TEXT_SCALE))
        if positive
        else None
    )


def frame_text_capacity_contract(frame: object) -> tuple[tuple[int, ...], int]:
    title_required: list[int] = []
    other_required: list[int] = []
    optional: list[int] = []
    for slot in getattr(frame, "slots", ()) or ():
        capacity = _text_slot_capacity(slot)
        if capacity is None:
            continue
        role = str(getattr(slot, "semantic_role", "body") or "body").lower()
        required = bool(getattr(slot, "required", False))
        if required and any(token in role for token in ("title", "headline", "heading")):
            title_required.append(capacity)
        elif required:
            other_required.append(capacity)
        else:
            optional.append(capacity)
    if not title_required:
        return (), 0
    title_capacity = min(title_required)
    remaining = [*title_required, *other_required]
    remaining.remove(title_capacity)
    limits = (title_capacity, *sorted(remaining + optional, reverse=True))
    return tuple(limits), len(title_required) + len(other_required)


def unitize_source_page(
    value: str,
    *,
    capacity_limits: Sequence[int],
    required_slot_count: int,
    maximum_units: int = 24,
) -> SourcePageUnitization:
    blocks = _parse_blocks(value)
    if not blocks:
        raise SourceUnitizationError("source_native_source_page_empty")
    if not capacity_limits or capacity_limits[0] <= 0:
        raise SourceUnitizationError("source_native_title_capacity_missing")
    first = blocks[0]
    if first.kind not in {"heading", "paragraph"}:
        raise SourceUnitizationError("source_native_title_candidate_unavailable")

    title_capacity = int(capacity_limits[0])
    body_capacity = max((int(item) for item in capacity_limits[1:]), default=title_capacity)
    title_sentences = _sentences(first.text)
    if first.kind == "heading" or len(first.text) <= title_capacity:
        if len(first.text) > title_capacity:
            raise SourceUnitizationError("source_native_title_candidate_capacity_exceeded")
        title_text = first.text
        title_end = len(title_sentences)
        first_remainder: list[tuple[str, int, int]] = []
    else:
        title_text = title_sentences[0]
        if len(title_text) > title_capacity:
            raise SourceUnitizationError("source_native_title_candidate_capacity_exceeded")
        title_end = 1
        remainder = " ".join(title_sentences[1:])
        first_remainder = (
            _split_complete_sentences(remainder, capacity=body_capacity)
            if remainder
            else []
        )

    units: list[SourceTextUnit] = [
        SourceTextUnit(
            text=title_text,
            boundary_kind="title-candidate",
            source_block_index=first.source_block_index,
            sentence_start=0,
            sentence_end=title_end,
        )
    ]
    for text, start, end in first_remainder:
        units.append(
            SourceTextUnit(
                text=text,
                boundary_kind="paragraph",
                source_block_index=first.source_block_index,
                sentence_start=start + 1,
                sentence_end=end + 1,
            )
        )
    for block in blocks[1:]:
        kind: BoundaryKind = (
            "bullet"
            if block.kind == "bullet"
            else "table-cell"
            if block.kind == "table-cell"
            else "paragraph"
        )
        for text, start, end in _split_complete_sentences(
            block.text, capacity=body_capacity
        ):
            units.append(
                SourceTextUnit(
                    text=text,
                    boundary_kind=kind,
                    source_block_index=block.source_block_index,
                    sentence_start=start,
                    sentence_end=end,
                )
            )

    if any(not item.text.strip() for item in units):
        raise SourceUnitizationError("source_native_empty_content_unit")
    if len(units) < required_slot_count:
        raise SourceUnitizationError("source_native_required_slot_content_missing")
    if len(units) > maximum_units:
        raise SourceUnitizationError("source_native_source_unit_count_exceeded")

    reconstructed: list[str] = []
    for block in blocks:
        owned = [item.text for item in units if item.source_block_index == block.source_block_index]
        if not owned or _normalize_inline(" ".join(owned)) != block.text:
            raise SourceUnitizationError("source_native_orphan_sentence_detected")
        reconstructed.append(block.text)
    source_text_digest = _digest(reconstructed)
    unsigned = {
        "schemaVersion": "source-page-unitization/v1",
        "sourceTextDigest": source_text_digest,
        "titleCandidateIndex": 0,
        "units": [
            {
                "text": item.text,
                "boundaryKind": item.boundary_kind,
                "sourceBlockIndex": item.source_block_index,
                "sentenceStart": item.sentence_start,
                "sentenceEnd": item.sentence_end,
            }
            for item in units
        ],
    }
    return SourcePageUnitization(
        units=tuple(units),
        title_candidate_index=0,
        source_text_digest=source_text_digest,
        unitization_digest=_digest(unsigned),
    )


def _page_sections(brief: str, *, slide_count: int) -> list[str] | None:
    matches = list(_SOURCE_PAGE_PATTERN.finditer(brief))
    if len(matches) != slide_count:
        return None
    expected = list(range(1, slide_count + 1))
    actual = [int(item.group("number")) for item in matches]
    if actual != expected:
        raise SourceUnitizationError("source_native_source_page_order_invalid")
    return [
        brief[
            match.end() : matches[index + 1].start()
            if index + 1 < len(matches)
            else len(brief)
        ].strip()
        for index, match in enumerate(matches)
    ]


def build_source_content_admission(
    *, brief: str, slide_count: int, frames: Sequence[object]
) -> SourceContentAdmission | None:
    sections = _page_sections(brief, slide_count=slide_count)
    if sections is None:
        return None
    candidates: list[tuple[int, object, tuple[int, ...], int]] = []
    for frame_index, frame in enumerate(frames):
        limits, required = frame_text_capacity_contract(frame)
        if limits:
            candidates.append((frame_index, frame, limits, required))
    if not candidates:
        raise SourceUnitizationError("source_native_composition_unavailable")

    pages: list[dict[str, object]] = []
    frame_indexes: list[int] = []
    page_digests: list[str] = []
    for page_index, text in enumerate(sections):
        admitted: list[tuple[int, int, SourcePageUnitization]] = []
        for frame_index, _, limits, required in candidates:
            try:
                unitization = unitize_source_page(
                    text,
                    capacity_limits=limits,
                    required_slot_count=required,
                )
            except SourceUnitizationError:
                continue
            admitted.append((sum(limits), frame_index, unitization))
        if not admitted:
            raise SourceUnitizationError("source_native_composition_unavailable")
        _, frame_index, unitization = max(
            admitted,
            key=lambda item: (item[0], -item[1], item[2].unitization_digest),
        )
        page_key = f"source-page-{page_index + 1:03d}"
        units: list[dict[str, object]] = []
        boundary_counts: dict[str, int] = {}
        for unit in unitization.units:
            boundary_counts[unit.boundary_kind] = boundary_counts.get(unit.boundary_kind, 0) + 1
            ordinal = boundary_counts[unit.boundary_kind]
            binding_id = f"{page_key}:{unit.boundary_kind}-{ordinal:03d}"
            source_digest = _digest(unit.text)
            units.append(
                {
                    "contentUnitId": binding_id,
                    "kind": "text",
                    "payload": {"text": unit.text, "data": []},
                    "required": True,
                    "sourceOwnership": [
                        {
                            "sourceBindingId": binding_id,
                            "sourceContentDigest": source_digest,
                            "useMode": "verbatim",
                            "required": True,
                        }
                    ],
                }
            )
        pages.append(
            {
                "pageId": page_key,
                "sourceUnitId": page_key,
                "contentUnits": units,
            }
        )
        frame_indexes.append(frame_index)
        page_digests.append(unitization.unitization_digest)
    return SourceContentAdmission(
        content_graph=seal_content_graph(pages, generation_lane="new-authoring"),
        frame_indexes=tuple(frame_indexes),
        page_unitization_digests=tuple(page_digests),
    )


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True)
    raise SourceUnitizationError("source_content_manifest_invalid")


def _field(value: Mapping[str, object], camel: str, snake: str | None = None) -> object:
    if camel in value:
        return value[camel]
    return value.get(snake or camel)


def _maximum_text_capacities(frames: Sequence[object]) -> tuple[int, int]:
    # Imported design packs do not consistently label a required title slot;
    # some executable frames intentionally expose every editable text target as
    # an optional body role.  Manifest-backed source text still needs one
    # deterministic title candidate, but it must not be rejected merely because
    # the imported semantic label is incomplete.  Admission therefore uses the
    # largest executable text envelope for both title and body unitization.  The
    # later per-frame composition and Chromium readback remain the hard fit gates.
    capacities = [
        capacity
        for frame in frames
        for slot in (getattr(frame, "slots", ()) or ())
        if (capacity := _text_slot_capacity(slot)) is not None
    ]
    if not capacities:
        raise SourceUnitizationError("source_native_composition_unavailable")
    maximum = max(capacities)
    return maximum, maximum


def _manifest_text_units(
    *,
    block: Mapping[str, object],
    boundary_kind: BoundaryKind,
    capacity: int,
) -> list[tuple[str, BoundaryKind]]:
    payload = _mapping(_field(block, "payload"))
    paragraphs = _field(payload, "paragraphs")
    values: list[tuple[str, bool]] = []
    if isinstance(paragraphs, Sequence) and not isinstance(
        paragraphs, (str, bytes, bytearray)
    ):
        for paragraph in paragraphs:
            paragraph_value = _mapping(paragraph)
            text = _normalize_inline(str(_field(paragraph_value, "text") or ""))
            if text:
                values.append((text, bool(_field(paragraph_value, "bullet"))))
    if not values:
        text = str(_field(payload, "text") or "")
        values = [
            (_normalize_inline(item), False)
            for item in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if _normalize_inline(item)
        ]
    units: list[tuple[str, BoundaryKind]] = []
    for value, declared_bullet in values:
        bullet = _BULLET.match(value)
        resolved_kind: BoundaryKind = (
            "bullet" if declared_bullet or bullet else boundary_kind
        )
        resolved = (
            f"{bullet.group('marker')} {bullet.group('text')}" if bullet else value
        )
        units.extend(
            (text, resolved_kind)
            for text, _, _ in _split_complete_sentences(
                resolved, capacity=capacity
            )
        )
    return units


def _manifest_block_texts(block: Mapping[str, object]) -> tuple[str, ...]:
    """Return normalized paragraph values for recurrence classification only."""

    payload = _mapping(_field(block, "payload"))
    paragraphs = _field(payload, "paragraphs")
    values: list[str] = []
    if isinstance(paragraphs, Sequence) and not isinstance(
        paragraphs, (str, bytes, bytearray)
    ):
        values.extend(
            normalized
            for paragraph in paragraphs
            if (normalized := _normalize_inline(
                str(_field(_mapping(paragraph), "text") or "")
            ))
        )
    if values:
        return tuple(values)
    return tuple(
        normalized
        for item in str(_field(payload, "text") or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
        if (normalized := _normalize_inline(item))
    )


def _recurring_text_values(
    raw_units: Sequence[object], *, page_count: int
) -> frozenset[str]:
    counts: Counter[str] = Counter()
    for raw_unit in raw_units:
        unit = _mapping(raw_unit)
        raw_blocks = _field(unit, "blocks")
        if not isinstance(raw_blocks, Sequence) or isinstance(
            raw_blocks, (str, bytes, bytearray)
        ):
            raise SourceUnitizationError("source_content_manifest_invalid")
        page_text_values: list[str] = []
        for raw_block in sorted(
            raw_blocks,
            key=lambda item: (
                int(_field(_mapping(item), "order") or 0),
                str(
                    _field(
                        _mapping(item), "sourceBlockId", "source_block_id"
                    )
                    or ""
                ),
            ),
        ):
            block = _mapping(raw_block)
            if str(_field(block, "kind") or "") == "text":
                page_text_values.extend(_manifest_block_texts(block))
        # The leading title stack is structural even when a section deck repeats
        # its eyebrow or lede. Only later repeated copy can be compact footer.
        counts.update(set(page_text_values[3:]))
    threshold = max(3, (page_count + 1) // 2)
    return frozenset(value for value, count in counts.items() if count >= threshold)


def _image_semantics(
    payload: Mapping[str, object], *, neighborhood: Sequence[str]
) -> tuple[str, str, float]:
    geometry_value = _field(payload, "geometry")
    if not isinstance(geometry_value, Mapping):
        return "media", "unknown", 0.0
    width = float(_field(geometry_value, "cx", "width") or 0)
    height = float(_field(geometry_value, "cy", "height") or 0)
    if width <= 0 or height <= 0:
        return "media", "unknown", 0.0
    ratio = width / height
    if ratio >= 1.35:
        aspect_class = "landscape"
    elif ratio <= 0.74:
        aspect_class = "portrait"
    else:
        aspect_class = "square"

    alt_text = _normalize_inline(str(_field(payload, "altText", "alt_text") or ""))
    context = " ".join((alt_text, *neighborhood)).lower()
    size_bytes = max(0, int(_field(payload, "sizeBytes", "size_bytes") or 0))
    area = width * height
    decorative_signals = (
        "decorative",
        "decoration",
        "background",
        "ornament",
        "divider",
        "장식",
    )
    icon_signals = (
        "icon",
        "logo",
        "badge",
        "symbol",
        "transparent",
        "아이콘",
        "로고",
        "심볼",
        "투명",
    )
    evidence_signals = (
        "screenshot",
        "screen capture",
        "dashboard",
        "console",
        "product screen",
        "스크린샷",
        "화면 캡처",
        "대시보드",
        "제품 화면",
    )
    if any(signal in context for signal in decorative_signals):
        image_kind = "decorative"
    elif any(signal in context for signal in icon_signals) or (
        aspect_class == "square"
        and (
            (width <= 1_500_000 and height <= 1_500_000)
            or 0 < size_bytes <= 64 * 1024
        )
    ):
        image_kind = "icon"
    elif aspect_class == "landscape" and (
        any(signal in context for signal in evidence_signals)
        or (area >= 6_000_000_000_000 and size_bytes >= 80 * 1024)
    ):
        image_kind = "evidence"
    else:
        image_kind = "media"
    return image_kind, aspect_class, round(ratio, 4)


def _image_neighborhood(
    blocks: Sequence[Mapping[str, object]], *, block_index: int
) -> tuple[str, ...]:
    values: list[str] = []
    for index in range(max(0, block_index - 2), min(len(blocks), block_index + 3)):
        if index == block_index:
            continue
        block = blocks[index]
        if str(_field(block, "kind") or "") == "text":
            values.extend(_manifest_block_texts(block))
    return tuple(values)


def _assign_semantic_group(
    prepared: list[dict[str, object]],
    indexes: Sequence[int],
    *,
    page_index: int,
    group_kind: str,
    group_number: int,
    roles: Sequence[str],
) -> None:
    group_id = (
        f"source-page-{page_index + 1:03d}:{group_kind}-{group_number:03d}"
    )
    for ordinal, (item_index, role) in enumerate(zip(indexes, roles, strict=True)):
        item = prepared[item_index]
        item["semanticRole"] = role
        item["semanticGroupKind"] = group_kind
        item["semanticGroupId"] = group_id
        item["semanticGroupOrdinal"] = ordinal


def _semantic_metadata(item: Mapping[str, object]) -> list[dict[str, object]]:
    binding_id = str(item["sourceBindingId"])
    return [
        {
            "datumKey": datum_key,
            "label": datum_key,
            "value": item[item_key],
            "sourceBindingId": binding_id,
        }
        for datum_key, item_key in _SEMANTIC_FIELDS
        if item_key in item
    ]


def _decorate_manifest_semantics(
    prepared: list[dict[str, object]],
    *,
    page_index: int,
) -> None:
    """Add digest-bound semantic grouping without changing source units or order."""

    group_counts: Counter[str] = Counter()

    def assign(indexes: Sequence[int], kind: str, roles: Sequence[str]) -> None:
        group_counts[kind] += 1
        _assign_semantic_group(
            prepared,
            indexes,
            page_index=page_index,
            group_kind=kind,
            group_number=group_counts[kind],
            roles=roles,
        )

    footer_indexes = [
        index for index, item in enumerate(prepared) if item.get("compactFooter")
    ]
    if footer_indexes:
        footer_run = [footer_indexes[0]]
        for footer_index in footer_indexes[1:]:
            if footer_index == footer_run[-1] + 1:
                footer_run.append(footer_index)
                continue
            assign(
                footer_run,
                "compact-footer",
                ["compact-footer"] * len(footer_run),
            )
            footer_run = [footer_index]
        assign(
            footer_run,
            "compact-footer",
            ["compact-footer"] * len(footer_run),
        )

    table_groups: dict[str, list[int]] = {}
    for index, item in enumerate(prepared):
        if "tableRow" in item:
            table_groups.setdefault(str(item["sourceBindingId"]), []).append(index)
    for indexes in table_groups.values():
        roles = [
            "table-header"
            if int(prepared[index]["tableRow"]) == 0
            else "table-row-header"
            if int(prepared[index]["tableColumn"]) == 0
            else "table-cell"
            for index in indexes
        ]
        assign(indexes, "table-matrix", roles)

    title_candidates = [
        index
        for index, item in enumerate(prepared)
        if item.get("kind") == "text"
        and "semanticGroupId" not in item
        and item.get("boundaryKind") != "table-cell"
    ]
    if title_candidates:
        boundary_title = next(
            (
                index
                for index in title_candidates
                if prepared[index].get("boundaryKind") == "title-candidate"
            ),
            title_candidates[0],
        )
        stack: list[int] = []
        roles: list[str] = []
        position = title_candidates.index(boundary_title)
        first = boundary_title
        first_text = str(prepared[first].get("text") or "")
        previous = title_candidates[position - 1] if position > 0 else None
        if previous is not None and previous != boundary_title - 1:
            previous = None
        previous_text = (
            str(prepared[previous].get("text") or "")
            if previous is not None
            else ""
        )
        if previous is not None and _EYEBROW.match(previous_text):
            stack.extend((previous, boundary_title))
            roles.extend(("eyebrow", "title"))
            if (
                position + 1 < len(title_candidates)
                and title_candidates[position + 1] == boundary_title + 1
                and not _PROCESS_STEP.fullmatch(
                    str(prepared[title_candidates[position + 1]].get("text") or "")
                )
            ):
                stack.append(title_candidates[position + 1])
                roles.append("lede")
        elif (
            _EYEBROW.match(first_text)
            and position + 1 < len(title_candidates)
            and title_candidates[position + 1] == first + 1
        ):
            stack.extend((first, title_candidates[position + 1]))
            roles.extend(("eyebrow", "title"))
            if (
                position + 2 < len(title_candidates)
                and title_candidates[position + 2] == first + 2
                and not _PROCESS_STEP.fullmatch(
                    str(prepared[title_candidates[position + 2]].get("text") or "")
                )
            ):
                stack.append(title_candidates[position + 2])
                roles.append("lede")
        else:
            stack.append(boundary_title)
            roles.append("title")
            if (
                position + 1 < len(title_candidates)
                and title_candidates[position + 1] == boundary_title + 1
            ):
                stack.append(title_candidates[position + 1])
                roles.append("lede")
        assign(stack, "title-stack", roles)

    # A process step is a retained number/heading/body triple plus its
    # immediately preceding visual when present. Keeping that visual inside
    # the same semantic group prevents column packing from separating a
    # screenshot/icon from the step it explains.
    for index in range(max(0, len(prepared) - 2)):
        members = (index, index + 1, index + 2)
        if any("semanticGroupId" in prepared[item] for item in members):
            continue
        if not all(prepared[item].get("kind") == "text" for item in members):
            continue
        number, heading, body = (str(prepared[item].get("text") or "") for item in members)
        if (
            _PROCESS_STEP.fullmatch(number)
            and 1 <= len(heading) <= 60
            and body
            and not _PROCESS_STEP.fullmatch(heading)
            and not _PROCESS_STEP.fullmatch(body)
        ):
            process_members = members
            process_roles: tuple[str, ...] = (
                "step-number",
                "step-heading",
                "step-body",
            )
            if (
                index > 0
                and prepared[index - 1].get("kind") == "image"
                and prepared[index - 1].get("imageKind")
                not in {"evidence", "ui", "product-screen", "screenshot"}
                and "semanticGroupId" not in prepared[index - 1]
            ):
                process_members = (index - 1, *members)
                process_roles = (
                    "step-media",
                    "step-number",
                    "step-heading",
                    "step-body",
                )
            assign(
                process_members,
                "process-step",
                process_roles,
            )

    # Preserve image/text interleaving while identifying a generic media card.
    for index in range(max(0, len(prepared) - 2)):
        members = (index, index + 1, index + 2)
        if any("semanticGroupId" in prepared[item] for item in members):
            continue
        if prepared[index].get("kind") != "image" or not all(
            prepared[item].get("kind") == "text" for item in members[1:]
        ):
            continue
        heading = str(prepared[index + 1].get("text") or "")
        body = str(prepared[index + 2].get("text") or "")
        if 1 <= len(heading) <= 60 and len(body) > len(heading):
            assign(members, "media-card", ("media", "heading", "body"))

    for index in range(max(0, len(prepared) - 1)):
        members = (index, index + 1)
        if any("semanticGroupId" in prepared[item] for item in members):
            continue
        if not all(prepared[item].get("kind") == "text" for item in members):
            continue
        value = str(prepared[index].get("text") or "")
        label = str(prepared[index + 1].get("text") or "")
        if _KPI_VALUE.fullmatch(value) and label:
            assign(members, "kpi", ("kpi-value", "kpi-label"))

    for index, item in enumerate(prepared):
        if "semanticGroupId" in item:
            continue
        text = str(item.get("text") or "")
        role = (
            "section-heading"
            if item.get("kind") == "text" and _PROCESS_SECTION.match(text)
            else "media"
            if item.get("kind") == "image"
            else "body"
        )
        assign((index,), "body-flow", (role,))


def build_manifest_source_content_admission(
    *,
    manifest: object,
    outline: object,
    frames: Sequence[object],
    expected_slide_count: int,
) -> SourceContentAdmission:
    """Build page-locked text/image content directly from immutable authority."""

    manifest_value = _mapping(manifest)
    outline_value = _mapping(outline)
    manifest_id = str(_field(manifest_value, "manifestId", "manifest_id") or "")
    manifest_digest = str(
        _field(manifest_value, "manifestDigest", "manifest_digest") or ""
    )
    outline_pin = _mapping(_field(outline_value, "manifestPin", "manifest_pin"))
    if (
        not manifest_id
        or str(_field(outline_pin, "manifestId", "manifest_id") or "") != manifest_id
        or str(_field(outline_pin, "digest") or "") != manifest_digest
    ):
        raise SourceUnitizationError("source_content_manifest_invalid")
    raw_units = _field(manifest_value, "units")
    raw_slides = _field(outline_value, "slides")
    if not isinstance(raw_units, Sequence) or isinstance(raw_units, (str, bytes)):
        raise SourceUnitizationError("source_content_manifest_invalid")
    if not isinstance(raw_slides, Sequence) or isinstance(raw_slides, (str, bytes)):
        raise SourceUnitizationError("source_content_outline_invalid")
    if len(raw_units) != expected_slide_count or len(raw_slides) != expected_slide_count:
        raise SourceUnitizationError("source_content_source_unit_cardinality_mismatch")

    title_capacity, body_capacity = _maximum_text_capacities(frames)
    units_by_id = {
        str(_field(_mapping(item), "sourceUnitId", "source_unit_id") or ""): _mapping(item)
        for item in raw_units
    }
    recurring_texts = _recurring_text_values(
        raw_units, page_count=expected_slide_count
    )
    pages: list[dict[str, object]] = []
    page_digests: list[str] = []
    for page_index, raw_slide in enumerate(raw_slides):
        slide = _mapping(raw_slide)
        source_unit_id = str(
            _field(slide, "sourceUnitId", "source_unit_id") or ""
        )
        source_index = int(_field(slide, "sourceIndex", "source_index") or 0)
        unit = units_by_id.get(source_unit_id)
        if unit is None or source_index != page_index or int(
            _field(unit, "sourceIndex", "source_index") or 0
        ) != page_index:
            raise SourceUnitizationError("source_content_outline_invalid")
        title_ids = {
            str(item)
            for item in (_field(slide, "titleBlockIds", "title_block_ids") or ())
        }
        raw_blocks = _field(unit, "blocks")
        if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
            raise SourceUnitizationError("source_content_manifest_invalid")
        blocks = sorted(
            (_mapping(item) for item in raw_blocks),
            key=lambda item: (
                int(_field(item, "order") or 0),
                str(_field(item, "sourceBlockId", "source_block_id") or ""),
            ),
        )
        prepared: list[dict[str, object]] = []
        title_marked = False
        for block_index, block in enumerate(blocks):
            block_id = str(
                _field(block, "sourceBlockId", "source_block_id") or ""
            )
            kind = str(_field(block, "kind") or "")
            block_digest = str(
                _field(block, "contentDigest", "content_digest") or ""
            )
            if not block_id or not block_digest.startswith("sha256:"):
                raise SourceUnitizationError("source_content_manifest_invalid")
            if kind == "text":
                is_title = block_id in title_ids and not title_marked
                values = _manifest_text_units(
                    block=block,
                    boundary_kind="title-candidate" if is_title else "paragraph",
                    capacity=title_capacity if is_title else body_capacity,
                )
                for text_index, (text, boundary_kind) in enumerate(values):
                    resolved_kind = boundary_kind
                    if is_title and text_index == 0:
                        resolved_kind = "title-candidate"
                        title_marked = True
                    elif boundary_kind == "title-candidate":
                        resolved_kind = "paragraph"
                    prepared.append(
                        {
                            "kind": "text",
                            "text": text,
                            "boundaryKind": resolved_kind,
                            "compactFooter": (
                                text in recurring_texts
                                or bool(_PAGE_NUMBER.fullmatch(text))
                            ),
                            "sourceBindingId": block_id,
                            "sourceContentDigest": block_digest,
                        }
                    )
            elif kind == "table":
                payload = _mapping(_field(block, "payload"))
                rows = _field(payload, "rows")
                if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                    raise SourceUnitizationError("source_content_manifest_invalid")
                for row_index, row in enumerate(rows):
                    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
                        raise SourceUnitizationError("source_content_manifest_invalid")
                    for column_index, cell in enumerate(row):
                        cell_value = _mapping(cell)
                        cell_text = _normalize_inline(str(_field(cell_value, "text") or ""))
                        if not cell_text:
                            continue
                        resolved_row = int(_field(cell_value, "row") or row_index)
                        resolved_column = int(
                            _field(cell_value, "column") or column_index
                        )
                        row_span = max(
                            1, int(_field(cell_value, "rowSpan", "row_span") or 1)
                        )
                        column_span = max(
                            1, int(_field(cell_value, "gridSpan", "grid_span") or 1)
                        )
                        for text, _, _ in _split_complete_sentences(
                            cell_text, capacity=body_capacity
                        ):
                            prepared.append(
                                {
                                    "kind": "text",
                                    "text": text,
                                    "boundaryKind": "table-cell",
                                    "sourceBindingId": block_id,
                                    "sourceContentDigest": block_digest,
                                    "tableRow": resolved_row,
                                    "tableColumn": resolved_column,
                                    "tableRowSpan": row_span,
                                    "tableColumnSpan": column_span,
                                    "tableHeader": resolved_row == 0,
                                }
                            )
            elif kind == "image":
                payload = _mapping(_field(block, "payload"))
                asset_id = str(_field(payload, "assetId", "asset_id") or "")
                asset_digest = str(
                    _field(payload, "contentDigest", "content_digest") or ""
                )
                if not asset_id or not asset_digest.startswith("sha256:"):
                    raise SourceUnitizationError("source_content_image_occurrence_mismatch")
                image_kind, aspect_class, aspect_ratio = _image_semantics(
                    payload,
                    neighborhood=_image_neighborhood(
                        blocks, block_index=block_index
                    ),
                )
                prepared.append(
                    {
                        "kind": "image",
                        "assetOccurrenceId": block_id,
                        "assetDigest": asset_digest,
                        "assetId": asset_id,
                        "imageKind": image_kind,
                        "imageAspectClass": aspect_class,
                        "imageAspectRatio": aspect_ratio,
                        "sourceBindingId": block_id,
                        "sourceContentDigest": block_digest,
                    }
                )
            elif kind in {"chart", "shape", "source_note"}:
                continue

        if not title_marked:
            first_text = next(
                (item for item in prepared if item.get("kind") == "text"), None
            )
            if first_text is None:
                raise SourceUnitizationError("source_native_title_candidate_unavailable")
            if len(str(first_text.get("text") or "")) > title_capacity:
                raise SourceUnitizationError(
                    "source_native_title_candidate_capacity_exceeded"
                )
            first_text["boundaryKind"] = "title-candidate"
        _decorate_manifest_semantics(prepared, page_index=page_index)
        content_units: list[dict[str, object]] = []
        boundary_counts: dict[str, int] = {}
        for item in prepared:
            boundary = str(item.get("boundaryKind") or item.get("kind") or "content")
            boundary_counts[boundary] = boundary_counts.get(boundary, 0) + 1
            content_unit_id = (
                f"source-page-{page_index + 1:03d}:{boundary}-{boundary_counts[boundary]:03d}"
            )
            payload: dict[str, object] = {"data": _semantic_metadata(item)}
            if item["kind"] == "text":
                payload["text"] = item["text"]
            else:
                payload.update(
                    assetOccurrenceId=item["assetOccurrenceId"],
                    assetDigest=item["assetDigest"],
                )
            content_units.append(
                {
                    "contentUnitId": content_unit_id,
                    "kind": item["kind"],
                    "payload": payload,
                    "required": True,
                    "sourceOwnership": [
                        {
                            "sourceBindingId": item["sourceBindingId"],
                            "sourceContentDigest": item["sourceContentDigest"],
                            "useMode": "verbatim",
                            "required": True,
                        }
                    ],
                }
            )
        if not content_units:
            raise SourceUnitizationError("source_native_source_page_empty")
        page_id = f"source-page-{page_index + 1:03d}"
        pages.append(
            {
                "pageId": page_id,
                "sourceUnitId": source_unit_id,
                "contentUnits": content_units,
            }
        )
        page_digests.append(
            _digest(
                {
                    "sourceUnitId": source_unit_id,
                    "sourceIndex": page_index,
                    "contentUnits": content_units,
                }
            )
        )
    return SourceContentAdmission(
        content_graph=seal_content_graph(pages, generation_lane="preserve-source"),
        frame_indexes=(),
        page_unitization_digests=tuple(page_digests),
    )


__all__ = [
    "SourceContentAdmission",
    "SourcePageUnitization",
    "SourceTextUnit",
    "SourceUnitizationError",
    "build_manifest_source_content_admission",
    "build_source_content_admission",
    "frame_text_capacity_contract",
    "unitize_source_page",
]
