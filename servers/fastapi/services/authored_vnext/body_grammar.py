"""Deterministic authored body grammar shared by preview and native export."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any
import unicodedata

from models.authored_vnext.master.contract import DeckMasterContract, Rect
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.retained_reference_marker import (
    RETAINED_REFERENCE_MARKER_CODEC,
    canonical_reference_digest,
    decode_reference_layout_marker,
)


BODY_GRAMMAR_VALUE_KEY = "__bodyGrammar"
BODY_GRAMMAR_ROLE_PREFIX = "authored-body-grammar:"
BODY_GRAMMAR_SCHEMA_VERSION = "authored-body-grammar/v1"
BODY_GRAMMAR_SCHEMA_VERSION_V2 = "authored-body-grammar/v2"
BODY_GRAMMAR_SCHEMA_VERSION_V3 = "authored-body-grammar/v3"
_REFERENCE_LAYOUT_ROLE_PREFIX = "retained-reference-layout:"


def _authored_body_grammar_identity(
    contract: DeckMasterContract,
) -> tuple[str | None, str]:
    for element in contract.semantic_elements:
        role = element.content.role or ""
        if not role.startswith(BODY_GRAMMAR_ROLE_PREFIX):
            continue
        value = role.removeprefix(BODY_GRAMMAR_ROLE_PREFIX)
        if value.endswith(":v3"):
            return value.removesuffix(":v3") or None, "v3"
        if value.endswith(":v2"):
            return value.removesuffix(":v2") or None, "v2"
        return value or None, "v1"
    return None, "v1"


def authored_body_grammar_style_id(contract: DeckMasterContract) -> str | None:
    """Read the style identity sealed into a v3 effective master contract."""

    return _authored_body_grammar_identity(contract)[0]


def _raw_digest(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("body grammar does not permit non-finite numbers")
        if value.is_integer():
            return int(value)
        return float(format(value, ".15g"))
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_value(item)
            for key, item in value.items()
        }
    return value


def _canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _partition_text(text: str, desired: int = 3) -> list[str]:
    """Partition text at deterministic whitespace boundaries without rewriting it."""

    count = min(desired, len(text))
    if count <= 1:
        return [text]
    boundaries = [match.end() for match in re.finditer(r"\s+", text)]
    cuts: list[int] = []
    floor = 0
    for part in range(1, count):
        remaining = count - part
        lower = floor + 1
        upper = len(text) - remaining
        target = round(len(text) * part / count)
        candidates = [point for point in boundaries if lower <= point <= upper]
        cut = (
            min(candidates, key=lambda point: (abs(point - target), point))
            if candidates
            else max(lower, min(target, upper))
        )
        # Never split before a combining mark or across a ZWJ sequence. Python
        # slices code points rather than UTF-8 bytes, so every result remains
        # valid Unicode even when no whitespace boundary exists.
        while cut < upper and (
            unicodedata.combining(text[cut]) or text[cut - 1] == "\u200d"
        ):
            cut += 1
        cuts.append(cut)
        floor = cut
    points = [0, *cuts, len(text)]
    blocks = [text[left:right] for left, right in zip(points, points[1:])]
    blocks = [block for block in blocks if block]
    if "".join(blocks) != text:
        raise ValueError("authored body grammar partition changed source text")
    return blocks


def _partition_text_by_capacity(text: str, capacities: list[float]) -> list[str]:
    """Partition immutable text in retained reading order by usable text area."""
    if not capacities:
        return []
    if len(capacities) == 1:
        return [text]
    positive = [max(0.0, float(value)) for value in capacities]
    total_capacity = sum(positive)
    if total_capacity <= 0:
        return [text, *([""] * (len(capacities) - 1))]
    boundaries = [match.end() for match in re.finditer(r"\s+", text)]
    glyph_prefix = [0.0]
    for character in text:
        glyph_prefix.append(glyph_prefix[-1] + _glyph_em_width(character))
    total_glyphs = glyph_prefix[-1]
    cuts: list[int] = []
    floor = 0
    cumulative_capacity = 0.0
    for index, capacity in enumerate(positive[:-1]):
        if capacity <= 0:
            cuts.append(floor)
            continue
        cumulative_capacity += capacity
        target_glyphs = total_glyphs * cumulative_capacity / total_capacity
        candidates = [point for point in boundaries if point >= floor]
        cut = (
            min(candidates, key=lambda point: (abs(glyph_prefix[point] - target_glyphs), point))
            if candidates
            else len(text)
        )
        cuts.append(cut)
        floor = cut
    points = [0, *cuts, len(text)]
    parts = [text[left:right] for left, right in zip(points, points[1:])]
    if "".join(parts) != text:
        raise ValueError("authored capacity partition changed source text")
    return parts


def _glyph_em_width(character: str) -> float:
    if character.isspace():
        return 0.35
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 1.0
    if character in "ilI1.,:;|'":
        return 0.32
    if character in "MW@#%&":
        return 0.9
    return 0.58


def _wrapped_line_count(text: str, width_px: float, font_pt: float) -> int:
    capacity = max(1.0, width_px / (font_pt * (4 / 3)))
    return sum(
        max(
            1,
            math.ceil(sum(_glyph_em_width(char) for char in line) / capacity),
        )
        for line in (text.splitlines() or [""])
    )


def _text_insets(kind: str) -> dict[str, float]:
    if kind == "evidence-stack":
        return {"top": 10.0, "right": 14.0, "bottom": 10.0, "left": 36.0}
    if kind == "process-grid":
        return {"top": 30.0, "right": 12.0, "bottom": 10.0, "left": 12.0}
    if kind == "data-metrics":
        return {"top": 32.0, "right": 14.0, "bottom": 12.0, "left": 14.0}
    if kind == "timeline":
        return {"top": 34.0, "right": 12.0, "bottom": 10.0, "left": 12.0}
    if kind == "architecture":
        return {"top": 12.0, "right": 16.0, "bottom": 12.0, "left": 16.0}
    if kind in {"cover-statement", "section-bridge", "closing"}:
        return {"top": 18.0, "right": 20.0, "bottom": 18.0, "left": 20.0}
    return {"top": 12.0, "right": 12.0, "bottom": 12.0, "left": 12.0}


def _fitted_block_font_pt(
    text: str,
    bounds: dict[str, float],
    insets: dict[str, float],
    *,
    preferred_font_pt: float,
    line_height: float,
    minimum_font_pt: float = 1.0,
    safety_factor: float = 1.0,
) -> float:
    width = max(1.0, bounds["width"] - insets["left"] - insets["right"])
    height = max(1.0, bounds["height"] - insets["top"] - insets["bottom"])
    candidate = preferred_font_pt
    minimum = max(1.0, min(float(minimum_font_pt), candidate))
    while candidate > minimum:
        required = (
            _wrapped_line_count(text, width, candidate)
            * candidate
            * (4 / 3)
            * line_height
            * safety_factor
        )
        if required <= height:
            return round(candidate, 2)
        candidate = max(minimum, candidate - 0.5)
    return round(minimum, 2)


def _block_text_capacity(
    bounds: dict[str, float],
    insets: dict[str, float],
    *,
    font_pt: float,
    line_height: float,
    safety_factor: float = 1.0,
) -> float:
    font_px = font_pt * (4 / 3)
    width = max(1.0, bounds["width"] - insets["left"] - insets["right"])
    height = max(1.0, bounds["height"] - insets["top"] - insets["bottom"])
    line_capacity = width / font_px
    line_count = max(
        1,
        math.floor(height / (font_px * line_height * safety_factor)),
    )
    return max(1.0, line_capacity * line_count)


def _compact_v3_insets(
    archetype: str,
    insets: dict[str, float],
    bounds: dict[str, float],
    *,
    registered_text_flow: bool = False,
) -> dict[str, float]:
    """Reserve readable line height in retained data/timeline micro-shapes."""
    compact = dict(insets)
    if archetype == "data":
        compact["top"] = min(compact["top"], 4.0)
        compact["bottom"] = min(compact["bottom"], 4.0)
    elif archetype == "timeline" and bounds["height"] < 48.0:
        compact["top"] = min(compact["top"], 3.0)
        compact["bottom"] = min(compact["bottom"], 3.0)
    elif archetype == "cover" and registered_text_flow:
        # Registered cover summaries sometimes resolve to the minimum 7pt in
        # PowerPoint. Preserve their template-owned envelope while reclaiming
        # vertical padding that would otherwise clip the final native line.
        compact["top"] = min(compact["top"], 4.0)
        compact["bottom"] = min(compact["bottom"], 4.0)
    return compact


def _fitted_text_insets(
    insets: dict[str, Any],
    bounds: dict[str, Any],
) -> dict[str, float]:
    """Keep retained padding proportional while fitting very shallow blocks."""

    def fit_pair(first: str, second: str, extent_key: str) -> tuple[float, float]:
        left = max(0.0, float(insets.get(first, 0.0)))
        right = max(0.0, float(insets.get(second, 0.0)))
        extent = max(0.0, float(bounds[extent_key]))
        total = left + right
        if total > 0 and total >= extent:
            available = max(0.0, extent - 0.02)
            scale = available / total
            left *= scale
            right *= scale
        left = round(left, 4)
        right = round(right, 4)
        if left + right >= extent and extent > 0:
            right = round(max(0.0, extent - left - 0.01), 4)
        return left, right

    horizontal = fit_pair("left", "right", "width")
    vertical = fit_pair("top", "bottom", "height")
    return {
        "top": vertical[0],
        "right": horizontal[1],
        "bottom": vertical[1],
        "left": horizontal[0],
    }


def _rect(value: Rect) -> dict[str, float]:
    return {
        "x": round(float(value.x), 4),
        "y": round(float(value.y), 4),
        "width": round(float(value.width), 4),
        "height": round(float(value.height), 4),
    }


def _block_bounds(
    body: Rect,
    *,
    kind: str,
    index: int,
    count: int,
) -> dict[str, float]:
    inset = min(18.0, body.width * 0.025)
    available_x = body.x + inset
    available_y = body.y + inset
    available_width = max(1.0, body.width - inset * 2)
    available_height = max(1.0, body.height - inset * 2)
    gap = min(16.0, available_width * 0.02)
    if kind in {"process-grid", "data-metrics", "timeline"}:
        width = max(1.0, (available_width - gap * (count - 1)) / count)
        return {
            "x": round(available_x + index * (width + gap), 4),
            "y": round(available_y, 4),
            "width": round(width, 4),
            "height": round(available_height, 4),
        }
    if kind in {"cover-statement", "section-bridge", "closing"}:
        return {
            "x": round(available_x, 4),
            "y": round(available_y, 4),
            "width": round(available_width, 4),
            "height": round(available_height, 4),
        }
    gap = min(12.0, available_height * 0.025)
    height = max(1.0, (available_height - gap * (count - 1)) / count)
    width_ratios = (0.82, 0.72, 0.88)
    width = available_width * width_ratios[index % len(width_ratios)]
    x_offset = (available_width - width) * (0.12 if index % 2 == 0 else 0.88)
    return {
        "x": round(available_x + x_offset, 4),
        "y": round(available_y + index * (height + gap), 4),
        "width": round(width, 4),
        "height": round(height, 4),
    }


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _v2_slide_role(layout_kind: str, title: str, body: str) -> str:
    if layout_kind == "cover":
        return "cover"
    # Closing intent must be explicit.  Generic body copy such as "반복 문의
    # 감소" is ordinary KPI/content language and must not turn the entire
    # slide into a closing layout merely because it contains the noun 문의.
    if _contains_any(title, ("감사", "thank you", "문의처", "contact", "q&a", "마무리")) or _contains_any(
        body,
        ("감사합니다", "thank you", "문의처", "contact us", "q&a", "마무리합니다"),
    ):
        return "closing"
    if layout_kind == "section":
        return "section"
    return "content"


def _v2_archetype(style_id: str, slide_role: str, title: str, body: str) -> str:
    if slide_role == "cover":
        return "cover-statement"
    if slide_role == "section":
        return "section-bridge"
    if slide_role == "closing":
        return "closing"
    if style_id != "osb-smart-banking":
        return "content-cards"
    value = f"{title}\n{body}"
    if _contains_any(
        value,
        ("아키텍처", "architecture", "플랫폼", "계층", "레이어", "시스템 구성"),
    ):
        return "architecture"
    if _contains_any(
        value,
        ("로드맵", "timeline", "일정", "단계별", "분기", "월", "주차"),
    ):
        return "timeline"
    if _contains_any(
        value,
        ("kpi", "지표", "성과", "데이터", "증가", "감소", "%", "억원", "건"),
    ) or sum(character.isdigit() for character in value) >= 4:
        return "data-metrics"
    if _contains_any(
        value,
        ("프로세스", "process", "절차", "흐름", "단계", "실행 순서"),
    ):
        return "process-grid"
    return "content-cards"


def _semantic_role(kind: str) -> str:
    return {
        "cover-statement": "cover-summary",
        "section-bridge": "section-summary",
        "content-cards": "content-card",
        "data-metrics": "metric",
        "timeline": "timeline-step",
        "architecture": "architecture-layer",
        "process-grid": "process-step",
        "closing": "closing-message",
        "evidence-stack": "evidence-card",
    }[kind]


def _deck_metadata(contract: DeckMasterContract) -> dict[str, str] | None:
    for element in contract.semantic_elements:
        role = element.content.role or ""
        if role.startswith("deck-metadata:") and element.content.text:
            return {
                "kind": role.removeprefix("deck-metadata:"),
                "value": element.content.text,
            }
    return None


def _retained_reference_layout(contract: DeckMasterContract) -> dict[str, Any] | None:
    chunks: list[tuple[int, int, str, str, bool]] = []
    for element in contract.semantic_elements:
        role = element.content.role or ""
        if not role.startswith(_REFERENCE_LAYOUT_ROLE_PREFIX):
            continue
        suffix = role.removeprefix(_REFERENCE_LAYOUT_ROLE_PREFIX)
        compressed = suffix.startswith(f"{RETAINED_REFERENCE_MARKER_CODEC}:")
        if compressed:
            suffix = suffix.removeprefix(f"{RETAINED_REFERENCE_MARKER_CODEC}:")
        sequence, separator, remainder = suffix.partition("-of-")
        total, digest_separator, digest_hex = remainder.partition(":")
        if not separator or not digest_separator or element.content.text is None:
            raise ValueError("retained reference layout marker is malformed")
        chunks.append(
            (int(sequence), int(total), digest_hex, element.content.text, compressed)
        )
    if not chunks:
        return None
    chunks.sort(key=lambda item: item[0])
    expected_total = chunks[0][1]
    expected_digest = chunks[0][2]
    if (
        len(chunks) != expected_total
        or [item[0] for item in chunks] != list(range(1, expected_total + 1))
        or any(
            item[1] != expected_total
            or item[2] != expected_digest
            or item[4] != chunks[0][4]
            for item in chunks
        )
    ):
        raise ValueError("retained reference layout marker chunks are inconsistent")
    marker_payload = "".join(item[3] for item in chunks)
    snapshot = (
        decode_reference_layout_marker(marker_payload)
        if chunks[0][4]
        else json.loads(marker_payload)
    )
    digest = snapshot.get("digest")
    payload = {key: value for key, value in snapshot.items() if key != "digest"}
    reference_digest = canonical_reference_digest(payload)
    if digest != reference_digest or digest != f"sha256:{expected_digest}":
        raise ValueError("retained reference layout marker digest mismatch")
    return snapshot


def _retained_archetype(
    slide_role: str,
    slide: SlideSpecV2,
    available_archetypes: set[str],
) -> str:
    if slide_role in {"cover", "section", "closing"}:
        if slide_role not in available_archetypes:
            raise ValueError(f"retained reference has no {slide_role} layout")
        return slide_role
    # A concise authored title is a stronger semantic signal than incidental
    # words buried in long body copy.  This prevents every operational slide
    # containing terms such as 월/일정 from collapsing to the timeline layout.
    title_signals = (
        ("architecture", ("아키텍처", "architecture", "구조", "플랫폼", "시스템")),
        ("timeline", ("추진", "로드맵", "timeline", "일정", "계획", "단계별")),
        ("data", ("데이터", "모델", "kpi", "지표", "성과", "분석")),
        ("process", ("실행", "프로세스", "process", "절차", "운영 흐름")),
    )
    for archetype, markers in title_signals:
        if not _contains_any(slide.title.text, markers):
            continue
        if archetype in available_archetypes:
            return archetype
        # A retained library may deliberately omit a specialized data/timeline
        # silhouette. Preserve that semantic distinction by routing the slide
        # to its authored content geometry instead of letting incidental body
        # words collapse every page into the process layout.
        if "content" in available_archetypes:
            return "content"
    body_signals = (
        ("architecture", ("아키텍처", "architecture", "계층", "레이어", "시스템 구성", "플랫폼")),
        ("timeline", ("로드맵", "timeline", "일정", "분기", "주차", "단계별", "도입계획")),
        ("data", ("kpi", "지표", "데이터", "정량", "측정", "분석", "정확도", "성과")),
        ("process", ("프로세스", "process", "절차", "흐름", "단계", "승인", "검증", "대응")),
    )
    normalized_body = slide.body.casefold()
    scored = [
        (
            sum(normalized_body.count(marker.casefold()) for marker in markers),
            -index,
            archetype,
        )
        for index, (archetype, markers) in enumerate(body_signals)
    ]
    if scored:
        score, _, selected = max(scored)
        if score > 0:
            if selected in available_archetypes:
                return selected
            if "content" in available_archetypes:
                return "content"
    # Semantic content without an explicit signal still varies deterministically
    # across the retained silhouettes instead of collapsing to one card grid.
    choices = tuple(
        archetype
        for archetype in ("content", "data", "timeline", "architecture", "process")
        if archetype in available_archetypes
    )
    if not choices:
        raise ValueError("retained reference has no content layout")
    digest = hashlib.sha256(slide.slide_id.encode("utf-8")).digest()
    return choices[int.from_bytes(digest[:2], "big") % len(choices)]


def _flow_rect(
    x: float,
    y: float,
    width: float,
    height: float,
) -> dict[str, float]:
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        # Native admission uses strict floating-point containment. Keep a tiny
        # deterministic inset so decimal rounding can never put the far edge
        # microscopically outside the retained content rectangle.
        "width": round(max(0.01, width - 0.02), 4),
        "height": round(max(0.01, height - 0.02), 4),
    }


def _retained_text_flow_bounds(
    content_rect: dict[str, float],
    *,
    flow: str,
    count: int,
) -> list[dict[str, float]]:
    """Resolve readable regions from a flow frozen in the retained snapshot."""

    x = float(content_rect["x"])
    y = float(content_rect["y"])
    width = float(content_rect["width"])
    height = float(content_rect["height"])
    gap = min(14.0, max(8.0, min(width, height) * 0.025))
    if count == 1 or flow == "single":
        return [_flow_rect(x, y, width, height)]
    if flow in {"lead-top-two-column", "intro-top-two-column"} and count == 3:
        top_height = (height - gap) * (0.34 if flow.startswith("lead") else 0.28)
        lower_height = height - gap - top_height
        column_width = (width - gap) / 2
        return [
            _flow_rect(x, y, width, top_height),
            _flow_rect(x, y + top_height + gap, column_width, lower_height),
            _flow_rect(
                x + column_width + gap,
                y + top_height + gap,
                column_width,
                lower_height,
            ),
        ]
    if flow == "feature-left-two-stack" and count == 3:
        left_width = (width - gap) * 0.52
        right_width = width - gap - left_width
        row_height = (height - gap) / 2
        return [
            _flow_rect(x, y, left_width, height),
            _flow_rect(x + left_width + gap, y, right_width, row_height),
            _flow_rect(
                x + left_width + gap,
                y + row_height + gap,
                right_width,
                row_height,
            ),
        ]
    if flow == "feature-left-three-stack" and count == 4:
        left_width = (width - gap) * 0.48
        right_width = width - gap - left_width
        row_height = (height - gap * 2) / 3
        return [
            _flow_rect(x, y, left_width, height),
            *[
                _flow_rect(
                    x + left_width + gap,
                    y + index * (row_height + gap),
                    right_width,
                    row_height,
                )
                for index in range(3)
            ],
        ]
    if flow == "matrix-2x2" and count == 4:
        column_width = (width - gap) / 2
        row_height = (height - gap) / 2
        return [
            _flow_rect(
                x + column * (column_width + gap),
                y + row * (row_height + gap),
                column_width,
                row_height,
            )
            for row in range(2)
            for column in range(2)
        ]
    if flow == "header-pair-footer" and count == 4:
        edge_height = (height - gap * 2) * 0.24
        middle_height = height - gap * 2 - edge_height * 2
        column_width = (width - gap) / 2
        return [
            _flow_rect(x, y, width, edge_height),
            _flow_rect(x, y + edge_height + gap, column_width, middle_height),
            _flow_rect(
                x + column_width + gap,
                y + edge_height + gap,
                column_width,
                middle_height,
            ),
            _flow_rect(
                x,
                y + edge_height + gap + middle_height + gap,
                width,
                edge_height,
            ),
        ]
    raise ValueError(f"retained text flow does not match its source blocks: {flow}")


def _title_safe_flow_rect(
    content_rect: dict[str, float],
    title_bounds: dict[str, float],
) -> dict[str, float]:
    """Keep every registered semantic text flow at least 12px from its title."""

    gap = 12.0
    content_right = content_rect["x"] + content_rect["width"]
    content_bottom = content_rect["y"] + content_rect["height"]
    title_right = title_bounds["x"] + title_bounds["width"]
    title_bottom = title_bounds["y"] + title_bounds["height"]
    already_separated = (
        content_rect["x"] >= title_right + gap
        or content_right <= title_bounds["x"] - gap
        or content_rect["y"] >= title_bottom + gap
        or content_bottom <= title_bounds["y"] - gap
    )
    if already_separated:
        return dict(content_rect)
    safe_top = max(content_rect["y"], title_bottom + gap)
    safe_height = content_bottom - safe_top
    if safe_height < 48.0:
        raise ValueError("retained semantic body has no title-free readable region")
    return {
        "x": content_rect["x"],
        "y": round(safe_top, 4),
        "width": content_rect["width"],
        "height": round(safe_height, 4),
    }


def _source_silhouette_decoration(
    source: dict[str, Any],
    *,
    content_rect: dict[str, float],
    index: int,
) -> dict[str, Any] | None:
    """Keep exact mapped source geometry after its text moves to a safe region."""

    source_bounds = source["bounds"]
    left = max(float(source_bounds["x"]), float(content_rect["x"]))
    top = max(float(source_bounds["y"]), float(content_rect["y"]))
    right = min(
        float(source_bounds["x"]) + float(source_bounds["width"]),
        float(content_rect["x"]) + float(content_rect["width"]),
    )
    bottom = min(
        float(source_bounds["y"]) + float(source_bounds["height"]),
        float(content_rect["y"]) + float(content_rect["height"]),
    )
    if right <= left or bottom <= top:
        return None

    return {
        "id": f"{source['id']}-source-silhouette",
        # Native materialization intentionally accepts a closed decoration
        # vocabulary. Provenance remains explicit through the retained source
        # identity/bounds while the visual role uses its existing allowlisted
        # template-accent contract.
        "semanticRole": "template-accent",
        "kind": source.get("shape") or "rect",
        "bounds": _flow_rect(left, top, right - left, bottom - top),
        "fill": source.get("fill"),
        "stroke": source.get("stroke"),
        "strokeWidth": float(source.get("strokeWidth") or 0.0),
        "zIndex": min(8, int(source.get("zIndex") or 10) - 2 + index),
        "sourceShapeId": source.get("sourceShapeId"),
        "sourceBounds": source.get("sourceBounds"),
        "sourceSlideNumber": source.get("sourceSlideNumber"),
    }


def _clip_rendered_decoration(
    decoration: dict[str, Any],
    *,
    content_rect: dict[str, float],
) -> dict[str, Any] | None:
    """Clip rendered template decoration to the company-safe content region."""

    bounds = decoration["bounds"]
    left = max(float(bounds["x"]), float(content_rect["x"]))
    top = max(float(bounds["y"]), float(content_rect["y"]))
    right = min(
        float(bounds["x"]) + float(bounds["width"]),
        float(content_rect["x"]) + float(content_rect["width"]),
    )
    bottom = min(
        float(bounds["y"]) + float(bounds["height"]),
        float(content_rect["y"]) + float(content_rect["height"]),
    )
    if right <= left or bottom <= top:
        return None
    return {
        **decoration,
        "bounds": _flow_rect(left, top, right - left, bottom - top),
    }


def _fitted_retained_title_style(
    title: dict[str, Any],
    *,
    text: str,
    slide_role: str,
    text_flow: str | None,
    background_color: str,
    blocks: list[dict[str, Any]],
    decorations: list[dict[str, Any]],
) -> dict[str, Any]:
    style = dict(title.get("textStyle") or {})
    title_bounds = title["bounds"]

    def intersection_area(bounds: dict[str, Any]) -> float:
        width = max(
            0.0,
            min(
                float(title_bounds["x"]) + float(title_bounds["width"]),
                float(bounds["x"]) + float(bounds["width"]),
            )
            - max(float(title_bounds["x"]), float(bounds["x"])),
        )
        height = max(
            0.0,
            min(
                float(title_bounds["y"]) + float(title_bounds["height"]),
                float(bounds["y"]) + float(bounds["height"]),
            )
            - max(float(title_bounds["y"]), float(bounds["y"])),
        )
        return width * height

    local_layers = [
        layer
        for layer in [*decorations, *blocks]
        if isinstance(layer.get("bounds"), dict)
        and isinstance(layer.get("fill"), str)
        and intersection_area(layer["bounds"]) > 0
    ]
    backdrop = (
        max(local_layers, key=lambda layer: intersection_area(layer["bounds"]))["fill"]
        if local_layers
        else background_color
    )

    def luminance(color: str) -> float:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise ValueError("retained title color must be canonical hex")
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722

    def contrast(left: str, right: str) -> float:
        lighter, darker = sorted((luminance(left), luminance(right)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)

    source_color = str(style.get("color") or "#404040")
    if contrast(source_color, backdrop) < 4.5:
        palette_values = [
            title.get("accent", {}).get("stroke"),
            title.get("accent", {}).get("fill"),
            *[
                layer.get(key)
                for layer in [*decorations, *blocks]
                for key in ("accent", "stroke", "textColor", "fill")
            ],
        ]
        candidates = list(
            dict.fromkeys(
                value
                for value in palette_values
                if isinstance(value, str)
                and re.fullmatch(r"#[0-9A-Fa-f]{6}", value)
                and contrast(value, backdrop) >= 4.5
            )
        )
        if not candidates:
            raise ValueError("retained title palette has no color readable on its local backdrop")
        style["color"] = max(
            candidates,
            key=lambda color: (contrast(color, backdrop), -candidates.index(color)),
        )
    style["backdropColor"] = backdrop
    # OSB already has independently verified title geometry. The registered
    # template flows opt into deterministic fitting of unusually large source
    # display type while keeping their font family, weight, and palette.
    if slide_role != "cover" or text_flow is None:
        return style
    bounds = title_bounds
    line_height = float(style.get("lineHeight") or 1.2)
    minimum = 18.0
    candidate = min(44.0, float(style.get("fontPt") or 44.0))
    while candidate >= minimum:
        line_count = _wrapped_line_count(text, float(bounds["width"]), candidate)
        required_height = line_count * candidate * (4 / 3) * line_height
        if line_count <= 3 and required_height <= float(bounds["height"]):
            style["fontPt"] = round(candidate, 2)
            return style
        candidate -= 0.5
    raise ValueError("retained cover title cannot fit its template-owned title bounds")


def _v3_snapshot(
    contract: DeckMasterContract,
    slide: SlideSpecV2,
    retained: dict[str, Any],
) -> dict[str, Any]:
    layout = next(
        item
        for item in (
            contract.layouts.cover,
            contract.layouts.section,
            contract.layouts.standard,
            contract.layouts.long_title,
        )
        if item.id == slide.layout_id
    )
    slide_role = _v2_slide_role(layout.kind, slide.title.text, slide.body)
    available_archetypes = {
        str(item.get("archetype")) for item in retained["layouts"]
    }
    archetype = _retained_archetype(
        slide_role,
        slide,
        available_archetypes,
    )
    reference_layout = next(
        (item for item in retained["layouts"] if item.get("archetype") == archetype),
        None,
    )
    if reference_layout is None:
        raise ValueError(f"retained reference has no {archetype} layout")
    native_scene = reference_layout.get("nativeScene")
    if native_scene is not None:
        if not isinstance(native_scene, dict):
            raise ValueError("retained native scene must be an object")
        native_scene_digest = native_scene.get("digest")
        native_scene_payload = {
            key: value for key, value in native_scene.items() if key != "digest"
        }
        if native_scene_digest != canonical_reference_digest(native_scene_payload):
            raise ValueError("retained native scene digest mismatch")
        if native_scene.get("sourcePackageDigest") != retained.get("sourceDigest"):
            raise ValueError("retained native scene source digest mismatch")
        if native_scene.get("fullSlideRasterAllowed") is not False:
            raise ValueError("retained native scene permits full-slide raster")
    else:
        native_scene_digest = None
    source_blocks = reference_layout["blocks"]
    content_rect = reference_layout["contentRect"]
    decorations = [
        clipped
        for decoration in reference_layout["decorations"]
        if (
            clipped := _clip_rendered_decoration(
                decoration,
                content_rect=content_rect,
            )
        )
    ]
    text_flow = reference_layout.get("textFlow")
    if isinstance(text_flow, str):
        flow_content_rect = _title_safe_flow_rect(
            reference_layout["contentRect"],
            reference_layout["title"]["bounds"],
        )
        safe_bounds = _retained_text_flow_bounds(
            flow_content_rect,
            flow=text_flow,
            count=len(source_blocks),
        )
        decorations.extend(
            clipped
            for index, source in enumerate(source_blocks)
            if (
                clipped := _source_silhouette_decoration(
                    source,
                    content_rect=content_rect,
                    index=index,
                )
            )
        )
        source_blocks = [
            {**source, "bounds": bounds}
            for source, bounds in zip(source_blocks, safe_bounds, strict=True)
        ]
    minimum_font_pt = 7.0
    fit_safety_factor = 1.5 if isinstance(text_flow, str) else 1.0
    prepared_blocks: list[
        tuple[dict[str, Any], dict[str, Any], dict[str, float], float, float]
    ] = []
    capacities: list[float] = []
    for source_block in source_blocks:
        source = dict(source_block)
        text_style = dict(source.get("textStyle") or {})
        insets = _fitted_text_insets(
            source.get("textInsets")
            or {"top": 12.0, "right": 12.0, "bottom": 12.0, "left": 12.0},
            source["bounds"],
        )
        insets = _compact_v3_insets(
            archetype,
            insets,
            source["bounds"],
            registered_text_flow=isinstance(text_flow, str),
        )
        preferred = max(
            minimum_font_pt,
            float(text_style.get("fontPt") or contract.semantic_styles.body.font_pt),
        )
        line_height = float(text_style.get("lineHeight") or 1.25)
        prepared_blocks.append((source, text_style, insets, preferred, line_height))
        capacities.append(
            _block_text_capacity(
                source["bounds"],
                insets,
                font_pt=minimum_font_pt,
                line_height=line_height,
                safety_factor=fit_safety_factor,
            )
        )
    parts = _partition_text_by_capacity(slide.body, capacities)
    blocks: list[dict[str, Any]] = []
    for index, (text, prepared) in enumerate(zip(parts, prepared_blocks, strict=True)):
        source, text_style, insets, preferred, line_height = prepared
        source.update(
            {
                "sequence": index + 1,
                "text": text,
                "textInsets": insets,
                "fontPt": _fitted_block_font_pt(
                    text,
                    source["bounds"],
                    insets,
                    preferred_font_pt=preferred,
                    line_height=line_height,
                    minimum_font_pt=minimum_font_pt,
                    safety_factor=fit_safety_factor,
                ),
                "lineHeight": line_height,
            }
        )
        blocks.append(source)
    title = dict(reference_layout["title"])
    title["textStyle"] = _fitted_retained_title_style(
        title,
        text=slide.title.text,
        slide_role=slide_role,
        text_flow=text_flow if isinstance(text_flow, str) else None,
        background_color=contract.tokens.colors.background,
        blocks=blocks,
        decorations=decorations,
    )
    title.update({"text": slide.title.text, "lines": list(slide.title.lines)})
    body_slot = next(slot for slot in layout.slots if slot.role == "body")
    metadata = _deck_metadata(contract)
    semantic_roles = list(
        dict.fromkeys(
            block["semanticRole"]
            for block in blocks
            if block.get("semanticRole")
        )
    )
    render_kind = {
        "cover": "cover-statement",
        "section": "section-bridge",
        "content": "content-cards",
        "data": "data-metrics",
        "process": "process-grid",
    }.get(archetype, archetype)
    payload: dict[str, Any] = {
        "schemaVersion": BODY_GRAMMAR_SCHEMA_VERSION_V3,
        "styleId": authored_body_grammar_style_id(contract),
        "kind": render_kind,
        "archetype": archetype,
        "slideRole": slide_role,
        "bodyRole": body_slot.id.removeprefix("slot:"),
        "sourceTextDigest": _raw_digest(slide.body),
        # The retained template owns the whole body geometry.  bodyRole still
        # identifies the value binding, while the v3 body slot is the exact
        # per-slide content rectangle used by both preview and native OOXML.
        "bodySlot": dict(reference_layout["contentRect"]),
        "contentRect": dict(reference_layout["contentRect"]),
        "referenceLayoutId": reference_layout["id"],
        "referenceLayoutDigest": retained["digest"],
        "referenceSourceDigest": retained["sourceDigest"],
        "nativeScene": native_scene,
        "nativeSceneDigest": native_scene_digest,
        "title": title,
        "blocks": blocks,
        "decorations": decorations,
        "semanticRolesUsed": semantic_roles,
        "documentMetadata": (
            {
                **metadata,
                "visible": slide_role in {"cover", "closing"},
            }
            if metadata is not None
            else None
        ),
    }
    payload["digest"] = _canonical_digest(payload)
    if "".join(block["text"] for block in blocks) != slide.body:
        raise ValueError("retained body grammar changed source text")
    return payload


def build_body_grammar_snapshot(
    contract: DeckMasterContract,
    slide: SlideSpecV2,
) -> dict[str, Any] | None:
    """Build the immutable body grammar used by both HTML and OOXML renderers."""

    style_id, grammar_version = _authored_body_grammar_identity(contract)
    if style_id is None:
        return None
    retained = _retained_reference_layout(contract)
    if grammar_version == "v3":
        if retained is None:
            raise ValueError("retained body grammar requires its frozen layout snapshot")
        return _v3_snapshot(contract, slide, retained)
    layout = next(
        layout
        for layout in (
            contract.layouts.cover,
            contract.layouts.section,
            contract.layouts.standard,
            contract.layouts.long_title,
        )
        if layout.id == slide.layout_id
    )
    if layout.kind == "cover" and grammar_version == "v1":
        # Cover body slots are shallow subtitle regions. Three fitted cards are
        # technically bounded but unreadable, so covers retain one authored
        # body while the effective v3 panel/accent styling remains applied.
        return None
    body_slot = next(slot for slot in layout.slots if slot.role == "body")
    body_role = body_slot.id.removeprefix("slot:")
    if grammar_version == "v2":
        slide_role = _v2_slide_role(layout.kind, slide.title.text, slide.body)
        kind = _v2_archetype(style_id, slide_role, slide.title.text, slide.body)
        desired = (
            1
            if kind in {"cover-statement", "section-bridge", "closing"}
            else 3
        )
    else:
        slide_role = "cover" if layout.kind == "cover" else "content"
        kind = (
            "evidence-stack"
            if style_id == "visual-discovery"
            else "process-grid"
            if style_id == "osb-smart-banking"
            else "content-cards"
        )
        desired = 3
    parts = _partition_text(slide.body, desired=desired)
    colors = contract.tokens.colors
    blocks = []
    for index, text in enumerate(parts):
        bounds = _block_bounds(
            body_slot.bounds,
            kind=kind,
            index=index,
            count=len(parts),
        )
        text_insets = _text_insets(kind)
        blocks.append(
            {
                "id": f"body-block-{index + 1}",
                "kind": (
                    _semantic_role(kind)
                ),
                **(
                    {"semanticRole": _semantic_role(kind)}
                    if grammar_version == "v2"
                    else {}
                ),
                "sequence": index + 1,
                "text": text,
                "bounds": bounds,
                "textInsets": text_insets,
                "fontPt": _fitted_block_font_pt(
                    text,
                    bounds,
                    text_insets,
                    preferred_font_pt=contract.semantic_styles.body.font_pt,
                    line_height=contract.semantic_styles.body.line_height,
                ),
                "lineHeight": contract.semantic_styles.body.line_height,
                "fill": colors.surface,
                "stroke": colors.divider,
                "textColor": colors.text_secondary,
                "accent": colors.accent,
            }
        )
    payload: dict[str, Any] = {
        "schemaVersion": (
            BODY_GRAMMAR_SCHEMA_VERSION_V2
            if grammar_version == "v2"
            else BODY_GRAMMAR_SCHEMA_VERSION
        ),
        "styleId": style_id,
        "kind": kind,
        "bodyRole": body_role,
        "sourceTextDigest": _raw_digest(slide.body),
        "bodySlot": _rect(body_slot.bounds),
        "blocks": blocks,
    }
    if grammar_version == "v2":
        metadata = _deck_metadata(contract)
        payload.update(
            {
                "archetype": kind,
                "slideRole": slide_role,
                "contentRect": _rect(body_slot.bounds),
                "documentMetadata": (
                    {
                        **metadata,
                        "visible": slide_role in {"cover", "closing"},
                    }
                    if metadata is not None
                    else None
                ),
            }
        )
    payload["digest"] = _canonical_digest(payload)
    if "".join(block["text"] for block in blocks) != slide.body:
        raise ValueError("authored body grammar did not preserve source text")
    return payload


__all__ = [
    "BODY_GRAMMAR_ROLE_PREFIX",
    "BODY_GRAMMAR_SCHEMA_VERSION",
    "BODY_GRAMMAR_SCHEMA_VERSION_V2",
    "BODY_GRAMMAR_VALUE_KEY",
    "authored_body_grammar_style_id",
    "build_body_grammar_snapshot",
]
