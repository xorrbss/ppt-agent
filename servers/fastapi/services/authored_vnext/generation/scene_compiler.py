"""Compile authored visual plans into the canonical scene and reseal edits."""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from PIL import ImageFont
from pydantic import TypeAdapter

from models.authored_vnext.deck.scene import (
    DeckSceneIR,
    SceneAssetManifestEntry,
    SceneCapability,
    SceneChartElement,
    SceneConnectorElement,
    SceneDataManifestEntry,
    SceneEditability,
    SceneElement,
    SceneGroupElement,
    SceneImageElement,
    SceneShapeElement,
    SceneTableCellStyle,
    SceneTableElement,
    SceneTextElement,
    SceneTextStyle,
    SlideSceneIR,
    scene_digest,
    seal_deck_scene,
    seal_slide_scene,
)
from models.authored_vnext.deck.visual_plan import (
    BodyChartElement,
    BodyConnectorElement,
    BodyGroupElement,
    BodyImageElement,
    BodyShapeElement,
    BodyTableElement,
    BodyTextElement,
    SlideVisualPlan,
)
from models.authored_vnext.master.contract import DeckMasterContract, Rect
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.generation.body_grammar import build_body_grammar_snapshot
from services.authored_vnext.master.digests import canonical_digest


SceneMutationKind = Literal[
    "text.replace",
    "text.style.update",
    "geometry.update",
    "shape.style.update",
    "asset.set",
    "image.crop.update",
    "data.update",
    "table.cell.update",
    "table.column-widths.update",
]

_SCENE_ELEMENT_ADAPTER = TypeAdapter(SceneElement)
_STRUCTURED_ROLE_HINTS = frozenset(
    {
        "card",
        "process",
        "timeline",
        "comparison",
        "metric",
        "infographic",
        "image",
        "table",
        "chart",
    }
)

# The Scene is the canonical authored document, not a materialized company
# master. Company-standard colors, fonts, canvas fills, and edge furniture are
# applied by preview/export adapters after the byte-stable body Scene has been
# sealed. Keeping this token set here makes that ownership boundary executable:
# swapping only the company standard cannot mutate raw Scene JSON.
_CANONICAL_BODY_FONT = "Malgun Gothic"
_CANONICAL_BODY_FONT_PT = 20.0
_CANONICAL_BODY_MIN_FONT_PT = 16.0
_CANONICAL_BODY_COLORS = {
    "background": "#FFFFFF",
    # Semantic panels need enough tonal separation to remain legible in the
    # real PowerPoint renderer.  This is the canonical body surface (not a
    # company-master color) and is only applied to content-bearing structures.
    "surface": "#DCEAFF",
    "textPrimary": "#102A56",
    "textSecondary": "#102A56",
    "accent": "#1473E6",
    "divider": "#D2D7DF",
    "warning": "#B42318",
}


@dataclass(frozen=True, slots=True)
class _DesignIntent:
    archetype: str
    source: str
    cjk_ratio: float
    density: Literal["sparse", "balanced", "dense"]
    variant: int
    grammar_parts: tuple[str, ...]


class SceneTextFitError(ValueError):
    code = "authored_vnext_text_fit_rejected"
    status_code = 422
    retryable = False


def default_scene_capabilities() -> list[SceneCapability]:
    """Declare the real product boundary; unsupported native kinds stay closed."""

    return [
        SceneCapability(
            kind="text",
            preview="supported",
            workbench_edit="supported",
            native_pptx_export="supported",
        ),
        SceneCapability(
            kind="image",
            preview="supported",
            workbench_edit="supported",
            native_pptx_export="supported",
        ),
        SceneCapability(
            kind="shape",
            preview="supported",
            workbench_edit="supported",
            native_pptx_export="supported",
        ),
        SceneCapability(
            kind="connector",
            preview="partial",
            workbench_edit="partial",
            native_pptx_export="unsupported",
            reason="connector routing is preserved but native connector export is not implemented",
        ),
        SceneCapability(
            kind="table",
            preview="supported",
            workbench_edit="supported",
            native_pptx_export="supported",
        ),
        SceneCapability(
            kind="chart",
            preview="supported",
            workbench_edit="supported",
            native_pptx_export="supported",
        ),
        SceneCapability(
            kind="group",
            preview="supported",
            workbench_edit="readOnly",
            native_pptx_export="unsupported",
            reason="native group export is not implemented; child identities remain preserved",
        ),
    ]


def compatibility_composition_digest(*, master_pin: Any) -> str:
    """Explicit identity for old master-only generation, never an authored pin."""

    return canonical_digest(
        {
            "schemaVersion": "authored-vnext-compatibility-composition/v1",
            "master": master_pin.model_dump(mode="json", by_alias=True),
        }
    )


def _scene_element_id(slide_id: str, body_element_id: str) -> str:
    slide = slide_id.removeprefix("slide:")
    suffix = body_element_id.removeprefix("body:")
    return f"element:{slide}.{suffix}"


def _title_element_id(slide_id: str) -> str:
    return f"element:{slide_id.removeprefix('slide:')}.title"


def _asset_id(digest: str) -> str:
    return f"asset:{digest.removeprefix('sha256:')[:48]}"


def _data_id(digest: str) -> str:
    return f"data:{digest.removeprefix('sha256:')[:48]}"


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))


def _relative_luminance(color: str) -> float:
    channels = tuple(
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in _rgb(color)
    )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def _contrasting_authored_color(
    contract: DeckMasterContract,
    *,
    background: str,
    consumer: str,
) -> str:
    """Choose only from Authored-owned tokens and fail closed below WCAG AA."""

    del contract
    candidates = list(
        dict.fromkeys(
            (
                _CANONICAL_BODY_COLORS["textPrimary"],
                _CANONICAL_BODY_COLORS["textSecondary"],
                _CANONICAL_BODY_COLORS["accent"],
                _CANONICAL_BODY_COLORS["warning"],
            )
        )
    )
    selected = max(candidates, key=lambda color: _contrast_ratio(color, background))
    ratio = _contrast_ratio(selected, background)
    if ratio < 4.5:
        raise ValueError(
            f"{consumer} has no Authored color token with 4.5:1 contrast "
            f"against {background}; best={selected}:{ratio:.2f}"
        )
    return selected


def _body_style(contract: DeckMasterContract, role: str) -> SceneTextStyle:
    font_size, font_weight = {
        "note": (14.0, 400),
        "rightCallout": (18.0, 700),
        "warning": (18.0, 700),
        "caption": (12.0, 400),
        "source": (10.0, 400),
    }.get(role, (_CANONICAL_BODY_FONT_PT, 400))
    color = _contrasting_authored_color(
        contract,
        background=_CANONICAL_BODY_COLORS["surface"],
        consumer=f"{role} body text",
    )
    return SceneTextStyle(
        font_family=_CANONICAL_BODY_FONT,
        font_size=font_size,
        font_weight=font_weight,
        color=color,
        text_align="left",
        vertical_align="top",
    )


def _emphasis_style(
    contract: DeckMasterContract,
    role: str,
    *,
    preferred_font_pt: float,
) -> SceneTextStyle:
    """Create readable Authored-owned emphasis without accepting unsafe color."""

    style = _body_style(contract, role)
    accent = _CANONICAL_BODY_COLORS["accent"]
    surface = _CANONICAL_BODY_COLORS["surface"]
    color = (
        accent
        if _contrast_ratio(accent, surface) >= 4.5
        else _contrasting_authored_color(
            contract,
            background=surface,
            consumer=f"{role} emphasis",
        )
    )
    return style.model_copy(
        update={
            "font_size": preferred_font_pt,
            "font_weight": max(700, int(style.font_weight or 700)),
            "color": color,
        }
    )


def _color_token(contract: DeckMasterContract, token: str) -> str:
    del contract
    return _CANONICAL_BODY_COLORS[token]


def _subrect(
    bounds: Rect,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
) -> Rect:
    """Return a normalized child rectangle fenced inside the body envelope."""

    if min(x, y, width, height) < 0 or x + width > 1 or y + height > 1:
        raise ValueError("authored body composition escaped its body envelope")
    return Rect(
        x=bounds.x + bounds.width * x,
        y=bounds.y + bounds.height * y,
        width=bounds.width * width,
        height=bounds.height * height,
    )


def _intersect_rect(bounds: Rect, fence: Rect, *, consumer: str) -> Rect:
    left = max(bounds.x, fence.x)
    top = max(bounds.y, fence.y)
    right = min(bounds.right, fence.right)
    bottom = min(bounds.bottom, fence.bottom)
    if right - left < 1 or bottom - top < 1:
        raise ValueError(f"{consumer} has no usable area inside canvas safeArea")
    return Rect(x=left, y=top, width=right - left, height=bottom - top)


def _require_canonical_canvas(contract: DeckMasterContract) -> None:
    if (
        abs(contract.canvas.width_px - 1280.0) > 0.01
        or abs(contract.canvas.height_px - 720.0) > 0.01
    ):
        raise ValueError(
            "authored_vnext_canonical_canvas_unsupported: body Scene requires "
            "the 1280x720 authored canvas"
        )


def _canonical_safe_area(contract: DeckMasterContract) -> Rect:
    _require_canonical_canvas(contract)
    return Rect(x=60.0, y=80.0, width=1160.0, height=560.0)


def _safe_title_bounds(
    title_bounds: Rect,
    contract: DeckMasterContract,
    *,
    slide_role: str,
) -> Rect:
    # Validate the source binding, but never let company layout geometry become
    # authored Scene state. The canonical authored template owns these bounds.
    _intersect_rect(
        title_bounds,
        Rect(x=0, y=0, width=1280, height=720),
        consumer="authored title source binding",
    )
    _require_canonical_canvas(contract)
    if slide_role == "cover":
        return Rect(x=92.0, y=176.0, width=1096.0, height=180.0)
    if slide_role == "section":
        return Rect(x=92.0, y=146.0, width=1096.0, height=180.0)
    if slide_role == "longTitle":
        return Rect(x=92.0, y=84.0, width=1096.0, height=240.0)
    return Rect(x=92.0, y=84.0, width=1096.0, height=140.0)


def _safe_body_bounds(
    body_bounds: Rect,
    *,
    title_bounds: Rect,
    contract: DeckMasterContract,
) -> Rect:
    # Retained-reference body placeholders describe source bindings, not the
    # authored composition's final canvas.  Some source layouts expose only a
    # short caption-sized placeholder; clipping the authored composition to
    # that rectangle made native cards and rows physically unreadable.  The
    # authored template owns body layout, so use the full safe-area remainder
    # below the canonical title while retaining the placeholder solely as the
    # source/content binding.
    _intersect_rect(
        body_bounds,
        Rect(x=0, y=0, width=1280, height=720),
        consumer="authored body source binding",
    )
    safe = _canonical_safe_area(contract)
    gap = 24.0
    top = max(safe.y, title_bounds.bottom + gap)
    if safe.bottom - top < 48:
        raise ValueError(
            "authored body has insufficient safeArea below title; "
            "title/body overlap is not publishable"
        )
    return Rect(x=safe.x, y=top, width=safe.width, height=safe.bottom - top)


def _normalized_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


@lru_cache(maxsize=64)
def _system_font_path(font_family: str) -> str | None:
    """Resolve a real font file without silently substituting another family."""

    normalized = _normalized_font_name(font_family)
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts",
            )
            try:
                index = 0
                while True:
                    try:
                        display_name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if normalized not in _normalized_font_name(display_name):
                        continue
                    filename = str(value).split(",", 1)[0].strip()
                    candidate = Path(filename)
                    if not candidate.is_absolute():
                        candidate = (
                            Path(os.environ.get("WINDIR", r"C:\Windows"))
                            / "Fonts"
                            / candidate
                        )
                    if candidate.is_file():
                        return str(candidate)
            finally:
                winreg.CloseKey(key)
        except (ImportError, OSError):
            pass
    roots = (
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for suffix in (".ttf", ".otf", ".ttc"):
            for candidate in root.rglob(f"*{suffix}"):
                if normalized in _normalized_font_name(candidate.stem):
                    return str(candidate)
    return None


def _measurement_font_path(
    contract: DeckMasterContract,
    font_family: str,
) -> str:
    del contract
    resolved = _system_font_path(font_family)
    if resolved is None:
        raise ValueError(
            "authored_vnext_font_measurement_unavailable: "
            f"the Authored-owned font '{font_family}' has no readable font file"
        )
    return resolved


@lru_cache(maxsize=128)
def _measurement_font(path: str, pixel_size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size=pixel_size)
    except OSError as exc:
        raise ValueError(
            "authored_vnext_font_measurement_unavailable: "
            f"could not load {path}"
        ) from exc


def _actual_wrapped_line_count(
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    width: float,
) -> int:
    if width <= 0:
        return 0
    lines = 0
    for paragraph in text.splitlines() or [text]:
        if not paragraph:
            lines += 1
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and font.getlength(candidate) > width:
                lines += 1
                current = character.lstrip()
                if current and font.getlength(current) > width:
                    return 0
            else:
                current = candidate
        lines += 1
    return lines


def _estimated_wrapped_text_height(
    text: str,
    *,
    style: SceneTextStyle,
    width: float,
    contract: DeckMasterContract,
) -> float:
    """Measure the pinned font's real glyph advances for native fit gating."""

    if style.font_family is None or style.font_size is None:
        raise ValueError("authored scene text style is incomplete")
    font_px = max(1, round(style.font_size * (96.0 / 72.0)))
    if width < font_px * 0.5:
        return float("inf")
    font = _measurement_font(
        _measurement_font_path(contract, style.font_family),
        font_px,
    )
    lines = _actual_wrapped_line_count(text, font=font, width=width)
    if lines == 0:
        return float("inf")
    ascent, descent = font.getmetrics()
    return lines * (ascent + descent) * 1.16 + 8.0


def _fit_text_style(
    *,
    contract: DeckMasterContract,
    text: str,
    bounds: Rect,
    style: SceneTextStyle,
    minimum_font_pt: float,
    consumer: str,
    maximum_lines: int | None = None,
) -> SceneTextStyle:
    if style.font_size is None:
        raise ValueError(f"{consumer} has no font size")
    candidate = float(style.font_size)
    minimum = min(candidate, max(8.0, float(minimum_font_pt)))
    while candidate + 1e-9 >= minimum:
        measured = style.model_copy(update={"font_size": round(candidate, 2)})
        estimated_height = _estimated_wrapped_text_height(
            text,
            style=measured,
            width=bounds.width,
            contract=contract,
        )
        if maximum_lines is not None:
            if measured.font_family is None or measured.font_size is None:
                raise ValueError(f"{consumer} has incomplete measured typography")
            font = _measurement_font(
                _measurement_font_path(contract, measured.font_family),
                max(1, round(measured.font_size * (96.0 / 72.0))),
            )
            line_count = _actual_wrapped_line_count(
                text,
                font=font,
                width=bounds.width,
            )
            if line_count == 0 or line_count > maximum_lines:
                candidate -= 0.5
                continue
        if estimated_height <= bounds.height:
            return measured
        candidate -= 0.5
    raise SceneTextFitError(
        "authored_vnext_text_fit_failed: "
        f"{consumer} does not fit the Authored template at {minimum:.1f}pt"
    )


def _require_readable_panel(
    *,
    panel: Rect,
    text_bounds: Rect,
    text: str,
    style: SceneTextStyle,
    consumer: str,
    minimum_panel_height: float,
    minimum_text_height: float,
    minimum_text_width: float | None = None,
    minimum_inset: float = 10.0,
    contract: DeckMasterContract,
) -> None:
    if panel.height < minimum_panel_height:
        raise ValueError(
            f"{consumer} panel has {panel.height:.1f}px height; "
            f"requires {minimum_panel_height:.1f}px"
        )
    if text_bounds.height < minimum_text_height:
        raise ValueError(
            f"{consumer} body has {text_bounds.height:.1f}px height; "
            f"requires {minimum_text_height:.1f}px"
        )
    if minimum_text_width is not None and text_bounds.width < minimum_text_width:
        raise ValueError(
            f"{consumer} body has {text_bounds.width:.1f}px width; "
            f"requires {minimum_text_width:.1f}px"
        )
    insets = (
        text_bounds.x - panel.x,
        text_bounds.y - panel.y,
        panel.right - text_bounds.right,
        panel.bottom - text_bounds.bottom,
    )
    if min(insets) < minimum_inset:
        raise ValueError(
            f"{consumer} body requires at least {minimum_inset:.1f}px panel inset"
        )
    estimated_height = _estimated_wrapped_text_height(
        text,
        style=style,
        width=text_bounds.width,
        contract=contract,
    )
    if estimated_height > text_bounds.height:
        raise ValueError(
            f"{consumer} body requires about {estimated_height:.1f}px for native "
            f"wrapping but only {text_bounds.height:.1f}px is available"
        )


def _safe_element_bounds(
    bounds: Rect,
    *,
    title_bounds: Rect,
    contract: DeckMasterContract,
) -> Rect:
    safe = _intersect_rect(
        bounds,
        _canonical_safe_area(contract),
        consumer="authored scene element",
    )
    gap = 24.0
    top = max(safe.y, title_bounds.bottom + gap)
    if safe.bottom - top < 1:
        raise ValueError("authored scene element overlaps title or leaves safeArea")
    return Rect(x=safe.x, y=top, width=safe.width, height=safe.bottom - top)


def _authored_element_id(slide_id: str, suffix: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]", "-", suffix.lower()).strip("-.")
    if not normalized:
        raise ValueError("authored element suffix must not be empty")
    return f"element:{slide_id.removeprefix('slide:')}.authored.{normalized}"


def _normalized_copy(value: str) -> str:
    return " ".join(value.split())


_STRUCTURAL_DELIMITER = re.compile(r"(?:\s+·\s+|\s*[|→➜▶▸]+\s*)")
_LEADING_STRUCTURAL_DELIMITER = re.compile(r"^[|→➜▶▸·]+\s*")
_TRAILING_STRUCTURAL_DELIMITER = re.compile(r"\s*[|→➜▶▸·]+$")
_LEADING_YEAR = re.compile(r"^(?:19|20)\d{2}(?:年|년)?$")
_QUARTER_FRAGMENT = re.compile(r"^[Qq][1-4](?:\b|\s)")


def _semantic_copy(value: str) -> str:
    """Project only recognized visual separators out of immutable source copy."""

    return _normalized_copy(_STRUCTURAL_DELIMITER.sub(" ", value))


def _partition_words(text: str, *, desired: int) -> list[str]:
    """Partition prose deterministically while preserving every source token."""

    words = text.split()
    if desired <= 1 or len(words) < desired * 2:
        return [text.strip()]
    result: list[str] = []
    start = 0
    for index in range(desired):
        remaining_parts = desired - index
        remaining_words = len(words) - start
        take = max(1, round(remaining_words / remaining_parts))
        result.append(" ".join(words[start : start + take]))
        start += take
    if start < len(words):
        result[-1] = f"{result[-1]} {' '.join(words[start:])}"
    return result


def _body_parts(
    text: str,
    *,
    maximum: int,
    grammar_parts: tuple[str, ...] = (),
) -> list[str]:
    """Split source prose without inventing copy or collapsing explicit steps."""

    source = text.strip()
    candidates = [line.strip() for line in source.splitlines() if line.strip()]
    if len(candidates) < 2:
        # Preserve inline sequence markers. This is the common path for Korean
        # outlines such as ``1분기 진단 2분기 구축 ...`` that the earlier Scene
        # compiler incorrectly reduced to two oversized boxes.
        marked = [
            item.strip()
            for item in re.split(
                r"(?=(?:[-•▪◦]\s+|(?:\(?\d{1,2}[.)]|[Qq][1-4]|[1-4]분기)\s*))",
                source,
            )
            if item.strip()
        ]
        if len(marked) >= 2:
            candidates = marked
    if len(candidates) < 2:
        separated = [
            item.strip()
            for item in re.split(r"(?=\s*(?:[→➜▶▸|]\s*|·\s+))", source)
            if item.strip()
        ]
        if len(separated) >= 2:
            candidates = separated
    if len(candidates) < 2:
        candidates = [
            item.strip()
            for item in re.split(r"(?<=[.!?。！？])\s+", source)
            if item.strip()
        ]
    if len(candidates) < 2 and len(grammar_parts) >= 2:
        grammar_copy = "".join(grammar_parts)
        if _normalized_copy(grammar_copy) == _normalized_copy(source):
            candidates = [item.strip() for item in grammar_parts if item.strip()]
    if len(candidates) < 2:
        desired = min(maximum, max(2, len(grammar_parts)))
        candidates = _partition_words(source, desired=desired)
    if not candidates:
        candidates = [source]
    if (
        len(candidates) >= 2
        and _LEADING_YEAR.fullmatch(candidates[0]) is not None
        and _QUARTER_FRAGMENT.match(
            _LEADING_STRUCTURAL_DELIMITER.sub("", candidates[1])
        )
        is not None
    ):
        candidates = [f"{candidates[0]} {candidates[1]}", *candidates[2:]]
    source_projection = _semantic_copy(source)
    candidates = [
        _TRAILING_STRUCTURAL_DELIMITER.sub(
            "",
            (
                candidate
                if index == 0
                else _LEADING_STRUCTURAL_DELIMITER.sub("", candidate)
            ),
        ).strip()
        for index, candidate in enumerate(candidates)
    ]
    if _semantic_copy(" ".join(candidates)) != source_projection:
        raise ValueError(
            "authored_vnext_structural_delimiter_projection_changed_source_copy"
        )
    if len(candidates) <= maximum:
        return candidates
    # Keep every source token while balancing verbose outline fragments across
    # the available native slots. Folding the entire tail into the last card
    # makes real Korean/CJK outlines fail even when the aggregate canvas has
    # ample room.
    balanced = _partition_words(source, desired=maximum)
    if len(balanced) == maximum:
        return balanced
    return [*candidates[: maximum - 1], "\n".join(candidates[maximum - 1 :])]


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _cjk_ratio(value: str) -> float:
    visible = [character for character in value if not character.isspace()]
    if not visible:
        return 0.0
    cjk = sum(
        1
        for character in visible
        if (
            "\u1100" <= character <= "\u11ff"
            or "\u2e80" <= character <= "\u9fff"
            or "\uac00" <= character <= "\ud7af"
            or "\uf900" <= character <= "\ufaff"
            or "\uff00" <= character <= "\uffef"
        )
    )
    return cjk / len(visible)


def _measured_copy_units(contract: DeckMasterContract, value: str) -> float:
    """Return real-glyph width in canonical CJK-em units.

    Character counts systematically understate mixed Korean/CJK density and
    overstate narrow Latin copy. This uses the exact font that native export
    receives, while normalizing the result so density thresholds remain stable.
    """

    font_px = round(_CANONICAL_BODY_FONT_PT * (96.0 / 72.0))
    font = _measurement_font(
        _measurement_font_path(contract, _CANONICAL_BODY_FONT),
        font_px,
    )
    em = max(font.getlength("한"), font.getlength("M"), 1.0)
    return sum(font.getlength(line or " ") for line in value.splitlines()) / em


def _structured_slide_inputs(
    structured_inputs: dict[str, Any] | None,
) -> dict[str, Any]:
    if structured_inputs is None:
        return {}
    if not isinstance(structured_inputs, dict):
        raise ValueError("scene structuredInputs must be an object")
    unknown = sorted(set(structured_inputs) - {"roleHint", "sourceImage", "sourceData"})
    if unknown:
        raise ValueError(f"scene structuredInputs contains unsupported fields: {unknown}")
    hint = structured_inputs.get("roleHint")
    if hint is not None and hint not in _STRUCTURED_ROLE_HINTS:
        raise ValueError(f"unsupported authored scene roleHint: {hint}")
    for key in ("sourceImage", "sourceData"):
        value = structured_inputs.get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"scene structuredInputs.{key} must be an object")
    return structured_inputs


def _design_intent(
    *,
    slide: SlideSpecV2,
    visual_plan: SlideVisualPlan,
    contract: DeckMasterContract,
    structured_inputs: dict[str, Any] | None,
) -> _DesignIntent:
    structured = _structured_slide_inputs(structured_inputs)
    hint = structured.get("roleHint")
    grammar = build_body_grammar_snapshot(contract, slide)
    grammar_kind = None if grammar is None else str(
        grammar.get("archetype") or grammar.get("kind") or ""
    )
    grammar_blocks = [] if grammar is None else grammar.get("blocks")
    grammar_parts = tuple(
        str(item["text"])
        for item in grammar_blocks
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ) if isinstance(grammar_blocks, list) else ()
    if grammar_parts and _normalized_copy("".join(grammar_parts)) != _normalized_copy(slide.body):
        raise ValueError("authored body grammar changed source copy before Scene composition")
    mapped_grammar = {
        "content": "card",
        "content-cards": "card",
        "evidence-stack": "card",
        "data": "metric",
        "data-metrics": "metric",
        "process": "process",
        "process-grid": "process",
        "timeline": "timeline",
        "architecture": "infographic",
    }.get(grammar_kind or "")
    value = f"{slide.title.text}\n{slide.body}"
    if hint is not None:
        archetype = str(hint)
        source = "structuredRoleHint"
    elif mapped_grammar is not None:
        archetype = mapped_grammar
        source = "authoredBodyGrammar"
    elif _contains_any(
        value,
        ("비교", "대비", "장단점", "versus", " vs ", "before/after", "전환 전", "전환 후"),
    ):
        archetype = "comparison"
        source = "contentAnalysis"
    elif _contains_any(
        value,
        ("로드맵", "timeline", "일정", "분기", "주차", "연도별", "단계별 계획"),
    ):
        archetype = "timeline"
        source = "contentAnalysis"
    elif _contains_any(
        value,
        ("프로세스", "process", "절차", "워크플로", "실행 순서", "운영 흐름"),
    ):
        archetype = "process"
        source = "contentAnalysis"
    elif _contains_any(
        value,
        ("아키텍처", "architecture", "구조", "프레임워크", "생태계", "인포그래픽"),
    ):
        archetype = "infographic"
        source = "contentAnalysis"
    elif _contains_any(
        value,
        ("kpi", "지표", "성과", "증가", "감소", "달성률", "전환율", "억원"),
    ) or len(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?\s*[%배건억원천만]?", value)) >= 3:
        archetype = "metric"
        source = "contentAnalysis"
    else:
        archetype = "card"
        source = "contentAnalysis"
    ratio = _cjk_ratio(value)
    units = _measured_copy_units(contract, slide.body)
    density: Literal["sparse", "balanced", "dense"] = (
        "sparse" if units <= 72 else "balanced" if units <= 220 else "dense"
    )
    if units > 900:
        raise ValueError(
            "authored_vnext_content_density_unsupported: body exceeds the "
            "canonical scene's fail-closed visual density budget"
        )
    variant = 1 if ratio >= 0.35 and density != "sparse" else _variant_index(slide.slide_id)
    return _DesignIntent(
        archetype=archetype,
        source=source,
        cjk_ratio=ratio,
        density=density,
        variant=variant,
        grammar_parts=grammar_parts,
    )


def _variant_index(slide_id: str) -> int:
    numeric = re.search(r"([0-9]+)(?!.*[0-9])", slide_id)
    if numeric is not None:
        return int(numeric.group(1)) % 3
    digest = hashlib.sha256(slide_id.encode("utf-8")).digest()
    return digest[0] % 3


def _shape(
    *,
    slide_id: str,
    suffix: str,
    z_order: int,
    bounds: Rect,
    semantic_role: str,
    shape: Literal["rect", "roundRect", "ellipse", "triangle", "diamond", "line"],
    fill: str | None,
    stroke: str | None = None,
    stroke_width: float | None = None,
    geometry_editable: bool = True,
) -> SceneShapeElement:
    return SceneShapeElement(
        kind="shape",
        element_id=_authored_element_id(slide_id, suffix),
        z_order=z_order,
        bounds=bounds,
        rotation=0,
        semantic_role=semantic_role,
        editability=SceneEditability(
            geometry=geometry_editable,
            style=geometry_editable,
        ),
        shape=shape,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
    )


def _text(
    *,
    slide_id: str,
    suffix: str,
    z_order: int,
    bounds: Rect,
    semantic_role: str,
    text: str,
    style: SceneTextStyle,
    text_editable: bool,
) -> SceneTextElement:
    return SceneTextElement(
        kind="text",
        element_id=_authored_element_id(slide_id, suffix),
        z_order=z_order,
        bounds=bounds,
        rotation=0,
        semantic_role=semantic_role,
        editability=SceneEditability(text=text_editable, geometry=True),
        text=text,
        style=style,
    )


def _compose_cover(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
) -> list[SceneElement]:
    bounds = body.bounds
    accent = _CANONICAL_BODY_COLORS["accent"]
    surface = _CANONICAL_BODY_COLORS["surface"]
    source_parts = _body_parts(body.text, maximum=12)
    if len(source_parts) > 4:
        parts: list[str] = []
        start = 0
        for group_index in range(4):
            remaining_groups = 4 - group_index
            remaining_parts = len(source_parts) - start
            take = max(1, round(remaining_parts / remaining_groups))
            parts.append("\n".join(source_parts[start : start + take]))
            start += take
    else:
        parts = source_parts
    # A cover is a single editorial statement, not a dashboard. Preserve the
    # source fragments as explicit lines inside one field so evidence phrases
    # do not become a grid of interchangeable cards.
    if len(parts) == 4:
        # Metric-led covers read as one typographic statement: the two headline
        # measures form the first line and the capability proof closes it.
        editorial_copy = f"{parts[0]}     {parts[1]}\n{parts[2]}  ·  {parts[3]}"
    else:
        editorial_copy = "\n".join(parts)
    field = _subrect(bounds, x=0.03, y=0.13, width=0.94, height=0.74)
    text_bounds = _subrect(bounds, x=0.095, y=0.22, width=0.81, height=0.56)
    style = _fit_text_style(
        contract=contract,
        text=editorial_copy,
        bounds=text_bounds,
        style=_emphasis_style(
            contract,
            body.semantic_role,
            preferred_font_pt=28.0 if len(parts) <= 4 else 22.0,
        ).model_copy(update={"vertical_align": "middle"}),
        minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
        consumer="cover editorial statement",
        maximum_lines=max(1, editorial_copy.count("\n") + 1),
    )
    _require_readable_panel(
        panel=field,
        text_bounds=text_bounds,
        text=editorial_copy,
        style=style,
        consumer="cover editorial statement",
        minimum_panel_height=110,
        minimum_text_height=72,
        minimum_text_width=760,
        contract=contract,
    )
    return [
        _shape(
            slide_id=slide_id,
            suffix="cover-editorial-field",
            z_order=5,
            bounds=field,
            semantic_role="heroField",
            shape="rect",
            fill=surface,
        ),
        _shape(
            slide_id=slide_id,
            suffix="cover-accent-rail",
            z_order=6,
            bounds=_subrect(bounds, x=0.03, y=0.13, width=0.014, height=0.74),
            semantic_role="heroAccent",
            shape="rect",
            fill=accent,
        ),
        _shape(
            slide_id=slide_id,
            suffix="cover-accent-rule",
            z_order=7,
            bounds=_subrect(bounds, x=0.03, y=0.04, width=0.24, height=0.018),
            semantic_role="heroAccent",
            shape="rect",
            fill=accent,
        ),
        _text(
            slide_id=slide_id,
            suffix="cover-editorial-body",
            z_order=20,
            bounds=text_bounds,
            semantic_role="body",
            text=editorial_copy,
            style=style,
            text_editable=True,
        ),
    ]


def _compose_section(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
) -> list[SceneElement]:
    bounds = body.bounds
    accent = _CANONICAL_BODY_COLORS["accent"]
    surface = _CANONICAL_BODY_COLORS["surface"]
    style = _body_style(contract, body.semantic_role).model_copy(
        update={"font_size": 28.0}
    )
    panel = _subrect(bounds, x=0.045, y=0.1, width=0.91, height=0.78)
    text_bounds = _subrect(bounds, x=0.12, y=0.24, width=0.62, height=0.48)
    style = _fit_text_style(
        contract=contract,
        text=body.text,
        bounds=text_bounds,
        style=style,
        minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
        consumer="section",
    )
    _require_readable_panel(
        panel=panel,
        text_bounds=text_bounds,
        text=body.text,
        style=style,
        consumer="section",
        minimum_panel_height=150,
        minimum_text_height=110,
        minimum_text_width=520,
        contract=contract,
    )
    return [
        _shape(
            slide_id=slide_id,
            suffix="section-panel",
            z_order=5,
            bounds=panel,
            semantic_role="sectionPanel",
            shape="roundRect",
            fill=surface,
            stroke=accent,
            stroke_width=2,
        ),
        _shape(
            slide_id=slide_id,
            suffix="section-band",
            z_order=6,
            bounds=_subrect(bounds, x=0.045, y=0.1, width=0.018, height=0.78),
            semantic_role="sectionBand",
            shape="rect",
            fill=accent,
        ),
        _shape(
            slide_id=slide_id,
            suffix="section-marker",
            z_order=7,
            bounds=_subrect(bounds, x=0.82, y=0.2, width=0.085, height=0.18),
            semantic_role="sectionMarker",
            shape="ellipse",
            fill=accent,
        ),
        _text(
            slide_id=slide_id,
            suffix="section-body",
            z_order=20,
            bounds=text_bounds,
            semantic_role="body",
            text=body.text,
            style=style,
            text_editable=True,
        ),
    ]


def _compose_cards(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=3, grammar_parts=grammar_parts)
    bounds = body.bounds
    count = len(parts)
    gap = 0.025
    width = (0.96 - gap * (count - 1)) / count
    style = _body_style(contract, body.semantic_role).model_copy(
        update={"font_size": 20.0}
    )
    elements: list[SceneElement] = []
    for index, part in enumerate(parts):
        x = 0.02 + index * (width + gap)
        panel = _subrect(bounds, x=x, y=0.08, width=width, height=0.82)
        text_bounds = _subrect(
            bounds, x=x + 0.025, y=0.23, width=width - 0.05, height=0.57
        )
        part_style = _fit_text_style(
            contract=contract,
            text=part,
            bounds=text_bounds,
            style=style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"card {index + 1}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=part,
            style=part_style,
            consumer=f"card {index + 1}",
            minimum_panel_height=100,
            minimum_text_height=68,
            contract=contract,
        )
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"card-{index + 1}-surface",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="cardSurface",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=_CANONICAL_BODY_COLORS["accent"],
                    stroke_width=2 if index == 0 else 1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"card-{index + 1}-accent",
                    z_order=9 + index,
                    bounds=_subrect(bounds, x=x + 0.025, y=0.13, width=width * 0.22, height=0.025),
                    semantic_role="cardAccent",
                    shape="rect",
                    fill=_CANONICAL_BODY_COLORS["accent"],
                ),
                _text(
                    slide_id=slide_id,
                    suffix=f"card-{index + 1}-body",
                    z_order=20 + index,
                    bounds=text_bounds,
                    semantic_role="body" if index == 0 else "cardBody",
                    text=part,
                    style=part_style,
                    text_editable=count == 1 or index > 0,
                ),
            ]
        )
    return elements


def _compose_stacked_cards(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=5, grammar_parts=grammar_parts)
    bounds = body.bounds
    count = len(parts)
    gap = 0.025
    height = (0.9 - gap * (count - 1)) / count
    if height <= 0:
        raise ValueError("authored_vnext_card_variant_unavailable")
    base_style = _body_style(contract, body.semantic_role).model_copy(
        update={"font_size": 20.0}
    )
    elements: list[SceneElement] = []
    for index, part in enumerate(parts):
        y = 0.05 + index * (height + gap)
        panel = _subrect(bounds, x=0.035, y=y, width=0.93, height=height)
        marker = _subrect(bounds, x=0.055, y=y + height * 0.22, width=0.035, height=height * 0.56)
        text_bounds = _subrect(
            bounds,
            x=0.115,
            y=y + height * 0.16,
            width=0.8,
            height=height * 0.68,
        )
        style = _fit_text_style(
            contract=contract,
            text=part,
            bounds=text_bounds,
            style=base_style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"stacked card {index + 1}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=part,
            style=style,
            consumer=f"stacked card {index + 1}",
            minimum_panel_height=48,
            minimum_text_height=28,
            minimum_inset=8,
            contract=contract,
        )
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"stack-card-{index + 1}-surface",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="cardSurface",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=_CANONICAL_BODY_COLORS["divider"],
                    stroke_width=1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"stack-card-{index + 1}-marker",
                    z_order=10 + index,
                    bounds=marker,
                    semantic_role="cardAccent",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["accent"],
                ),
                _text(
                    slide_id=slide_id,
                    suffix=f"stack-card-{index + 1}-body",
                    z_order=20 + index,
                    bounds=text_bounds,
                    semantic_role="body" if index == 0 else "cardBody",
                    text=part,
                    style=style,
                    text_editable=count == 1 or index > 0,
                ),
            ]
        )
    return elements


def _compose_process(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=5, grammar_parts=grammar_parts)
    if len(parts) < 2:
        raise ValueError("authored_vnext_process_requires_at_least_two_steps")
    bounds = body.bounds
    count = len(parts)
    if count == 3:
        # The common three-step case gets an ascending editorial silhouette.
        # The aligned baseline still communicates sequence while unequal step
        # heights avoid another row of interchangeable UI cards.
        step_specs = (
            (0.005, 0.36, 0.32, 0.55),
            (0.34, 0.22, 0.32, 0.69),
            (0.675, 0.08, 0.32, 0.83),
        )
        style_base = _body_style(contract, body.semantic_role).model_copy(
            update={"font_size": 20.0, "vertical_align": "middle"}
        )
        elements: list[SceneElement] = [
            _shape(
                slide_id=slide_id,
                suffix="process-axis",
                z_order=3,
                bounds=_subrect(bounds, x=0.01, y=0.91, width=0.98, height=0.012),
                semantic_role="processAxis",
                shape="rect",
                fill=_CANONICAL_BODY_COLORS["accent"],
            )
        ]
        for index, (part, (x, y, width, height)) in enumerate(
            zip(parts, step_specs, strict=True)
        ):
            panel = _subrect(bounds, x=x, y=y, width=width, height=height)
            marker = _subrect(
                bounds,
                x=x + 0.025,
                y=max(0.015, y - 0.035),
                width=0.055,
                height=0.13,
            )
            text_bounds = _subrect(
                bounds,
                x=x + 0.045,
                y=y + 0.18 * height,
                width=width - 0.09,
                height=height * 0.68,
            )
            style = _fit_text_style(
                contract=contract,
                text=part,
                bounds=text_bounds,
                style=style_base,
                minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
                consumer=f"ascending process step {index + 1}",
            )
            _require_readable_panel(
                panel=panel,
                text_bounds=text_bounds,
                text=part,
                style=style,
                consumer=f"ascending process step {index + 1}",
                minimum_panel_height=110,
                minimum_text_height=58,
                minimum_inset=8,
                contract=contract,
            )
            elements.extend(
                [
                    _shape(
                        slide_id=slide_id,
                        suffix=f"process-{index + 1}-step",
                        z_order=5 + index,
                        bounds=panel,
                        semantic_role="processStep",
                        shape="rect",
                        fill=_CANONICAL_BODY_COLORS["surface"],
                    ),
                    _shape(
                        slide_id=slide_id,
                        suffix=f"process-{index + 1}-marker",
                        z_order=10 + index,
                        bounds=marker,
                        semantic_role="processMarker",
                        shape="ellipse",
                        fill=_CANONICAL_BODY_COLORS["accent"],
                    ),
                    _text(
                        slide_id=slide_id,
                        suffix=f"process-{index + 1}-body",
                        z_order=20 + index,
                        bounds=text_bounds,
                        semantic_role="body" if index == 0 else "processBody",
                        text=part,
                        style=style,
                        text_editable=True,
                    ),
                ]
            )
        return elements
    gap = 0.022
    width = (0.94 - gap * (count - 1)) / count
    style_base = _body_style(contract, body.semantic_role).model_copy(
        update={
            "font_size": 20.0,
            "vertical_align": "middle",
        }
    )
    elements: list[SceneElement] = [
        _shape(
            slide_id=slide_id,
            suffix="process-axis",
            z_order=3,
            bounds=_subrect(bounds, x=0.055, y=0.25, width=0.89, height=0.012),
            semantic_role="processAxis",
            shape="rect",
            fill=_CANONICAL_BODY_COLORS["divider"],
        )
    ]
    for index, part in enumerate(parts):
        x = 0.03 + index * (width + gap)
        panel = _subrect(bounds, x=x, y=0.1, width=width, height=0.82)
        number = _subrect(bounds, x=x + width * 0.08, y=0.07, width=width * 0.2, height=0.18)
        heading, separator, detail = part.partition(":")
        # Five-step processes are too narrow for two independent text boxes at
        # the canonical 16pt floor.  Keep the numbered process hierarchy, but
        # let the complete label/detail pair use the full card height.
        split_hierarchy = bool(
            count <= 4 and separator and heading.strip() and detail.strip()
        )
        text_specs: list[tuple[str, Rect, str, SceneTextStyle]]
        if split_hierarchy:
            heading_text = f"{heading.strip()}:"
            heading_bounds = _subrect(
                bounds,
                x=x + width * 0.09,
                y=0.29,
                width=width * 0.82,
                height=0.15,
            )
            detail_bounds = _subrect(
                bounds,
                x=x + width * 0.09,
                y=0.46,
                width=width * 0.82,
                height=0.38,
            )
            heading_style = _fit_text_style(
                contract=contract,
                text=heading_text,
                bounds=heading_bounds,
                style=_emphasis_style(
                    contract,
                    body.semantic_role,
                    preferred_font_pt=22.0,
                ),
                minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
                consumer=f"process step {index + 1} heading",
            )
            detail_style = _fit_text_style(
                contract=contract,
                text=detail.strip(),
                bounds=detail_bounds,
                style=style_base.model_copy(update={"font_size": 18.0}),
                minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
                consumer=f"process step {index + 1} detail",
            )
            text_specs = [
                ("label", heading_bounds, heading_text, heading_style),
                ("detail", detail_bounds, detail.strip(), detail_style),
            ]
        else:
            text_bounds = _subrect(
                bounds,
                x=x + width * 0.09,
                y=0.29,
                width=width * 0.82,
                height=0.52,
            )
            style = _fit_text_style(
                contract=contract,
                text=part,
                bounds=text_bounds,
                style=style_base,
                minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
                consumer=f"process step {index + 1}",
            )
            text_specs = [("body", text_bounds, part, style)]
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"process-{index + 1}-panel",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="processStep",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=_CANONICAL_BODY_COLORS["divider"],
                    stroke_width=1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"process-{index + 1}-number",
                    z_order=10 + index,
                    bounds=number,
                    semantic_role="processMarker",
                    shape="ellipse",
                    fill=_CANONICAL_BODY_COLORS["accent"],
                ),
            ]
        )
        for text_index, (suffix, text_bounds, text, style) in enumerate(text_specs):
            _require_readable_panel(
                panel=panel,
                text_bounds=text_bounds,
                text=text,
                style=style,
                consumer=f"process step {index + 1} {suffix}",
                minimum_panel_height=90,
                minimum_text_height=36 if split_hierarchy else 52,
                minimum_inset=8,
                contract=contract,
            )
            elements.append(
                _text(
                    slide_id=slide_id,
                    suffix=f"process-{index + 1}-{suffix}",
                    z_order=20 + index * 2 + text_index,
                    bounds=text_bounds,
                    semantic_role=(
                        "body"
                        if index == 0 and text_index == 0
                        else "processLabel"
                        if suffix == "label"
                        else "processBody"
                    ),
                    text=text,
                    style=style,
                    text_editable=True,
                )
            )
    return elements


def _compose_timeline(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=5, grammar_parts=grammar_parts)
    if len(parts) < 2:
        raise ValueError("authored_vnext_timeline_requires_at_least_two_milestones")
    bounds = body.bounds
    count = len(parts)
    gap = 0.025
    width = (0.92 - gap * (count - 1)) / count
    base_style = _body_style(contract, body.semantic_role).model_copy(
        update={
            "font_size": 18.0,
            "vertical_align": "middle",
        }
    )
    elements: list[SceneElement] = [
        _shape(
            slide_id=slide_id,
            suffix="timeline-axis",
            z_order=3,
            bounds=_subrect(bounds, x=0.04, y=0.49, width=0.92, height=0.014),
            semantic_role="timelineAxis",
            shape="rect",
            fill=_CANONICAL_BODY_COLORS["accent"],
        )
    ]
    for index, part in enumerate(parts):
        x = 0.04 + index * (width + gap)
        top = index % 2 == 0
        panel_y = 0.02 if top else 0.55
        panel = _subrect(bounds, x=x, y=panel_y, width=width, height=0.43)
        marker = _subrect(bounds, x=x + width * 0.39, y=0.44, width=width * 0.22, height=0.12)
        text_bounds = _subrect(
            bounds,
            x=x + width * 0.06,
            y=panel_y + 0.04,
            width=width * 0.88,
            height=0.34,
        )
        style = _fit_text_style(
            contract=contract,
            text=part,
            bounds=text_bounds,
            style=base_style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"timeline milestone {index + 1}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=part,
            style=style,
            consumer=f"timeline milestone {index + 1}",
            minimum_panel_height=72,
            minimum_text_height=42,
            minimum_inset=6,
            contract=contract,
        )
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"timeline-{index + 1}-panel",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="timelineStep",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=_CANONICAL_BODY_COLORS["divider"],
                    stroke_width=1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"timeline-{index + 1}-marker",
                    z_order=10 + index,
                    bounds=marker,
                    semantic_role="timelineMarker",
                    shape="ellipse",
                    fill=_CANONICAL_BODY_COLORS["accent"],
                ),
                _text(
                    slide_id=slide_id,
                    suffix=f"timeline-{index + 1}-body",
                    z_order=20 + index,
                    bounds=text_bounds,
                    semantic_role="body" if index == 0 else "timelineBody",
                    text=part,
                    style=style,
                    text_editable=count == 1 or index > 0,
                ),
            ]
        )
    return elements


def _compose_infographic(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=4, grammar_parts=grammar_parts)
    if len(parts) < 2:
        raise ValueError("authored_vnext_infographic_requires_at_least_two_concepts")
    bounds = body.bounds
    count = len(parts)
    base_style = _body_style(contract, body.semantic_role).model_copy(
        update={
            "font_size": 20.0,
            "vertical_align": "middle",
        }
    )
    silhouettes = (
        (
            (0.015, 0.02, 0.43, 0.4),
            (0.555, 0.3, 0.43, 0.4),
            (0.015, 0.58, 0.43, 0.4),
        )
        if count == 3
        else (
            (0.015, 0.03, 0.43, 0.38),
            (0.555, 0.03, 0.43, 0.38),
            (0.555, 0.59, 0.43, 0.38),
            (0.015, 0.59, 0.43, 0.38),
        )
    )
    elements: list[SceneElement] = [
        _shape(
            slide_id=slide_id,
            suffix="infographic-spine",
            z_order=3,
            bounds=_subrect(bounds, x=0.482, y=0.16, width=0.036, height=0.68),
            semantic_role="infographicSpine",
            shape="roundRect",
            fill=_CANONICAL_BODY_COLORS["divider"],
        ),
        _shape(
            slide_id=slide_id,
            suffix="infographic-hub",
            z_order=4,
            bounds=_subrect(bounds, x=0.45, y=0.39, width=0.1, height=0.22),
            semantic_role="infographicHub",
            shape="diamond",
            fill=_CANONICAL_BODY_COLORS["accent"],
        ),
    ]
    for index, part in enumerate(parts):
        x, y, width, height = silhouettes[index]
        panel = _subrect(bounds, x=x, y=y, width=width, height=height)
        text_bounds = _subrect(
            bounds,
            x=x + 0.05,
            y=y + 0.055,
            width=width - 0.1,
            height=height - 0.11,
        )
        style = _fit_text_style(
            contract=contract,
            text=part,
            bounds=text_bounds,
            style=base_style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"infographic concept {index + 1}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=part,
            style=style,
            consumer=f"infographic concept {index + 1}",
            minimum_panel_height=86,
            minimum_text_height=52,
            minimum_inset=8,
            contract=contract,
        )
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"infographic-{index + 1}-panel",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="infographicConcept",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=(
                        _CANONICAL_BODY_COLORS["accent"]
                        if index == 1
                        else _CANONICAL_BODY_COLORS["divider"]
                    ),
                    stroke_width=2 if index == 1 else 1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"infographic-{index + 1}-accent",
                    z_order=10 + index,
                    bounds=_subrect(
                        bounds,
                        x=x + 0.018,
                        y=y + 0.04,
                        width=0.022,
                        height=max(0.06, height - 0.08),
                    ),
                    semantic_role="infographicAccent",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["accent"],
                ),
                _text(
                    slide_id=slide_id,
                    suffix=f"infographic-{index + 1}-body",
                    z_order=20 + index,
                    bounds=text_bounds,
                    semantic_role="body" if index == 0 else "infographicBody",
                    text=part,
                    style=style,
                    text_editable=count == 1 or index > 0,
                ),
            ]
        )
    return elements


def _compose_metric_rows(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=4, grammar_parts=grammar_parts)
    bounds = body.bounds
    count = len(parts)
    row_gap_px = max(12.0, min(20.0, bounds.height * 0.04))
    row_gap = row_gap_px / bounds.height
    # Two-row metric/CJK slides previously left almost half the body slot
    # unused, then rejected a legitimate long Korean/Japanese explanation at
    # 16pt.  Allocate the available vertical space to the actual row count;
    # three/four-row metrics retain the compact cadence.
    row_height = min(
        0.36 if count <= 2 else 0.26,
        (0.88 - row_gap * (count - 1)) / count,
    )
    total = row_height * count + row_gap * (count - 1)
    y_start = (1 - total) / 2
    style = _emphasis_style(
        contract,
        body.semantic_role,
        preferred_font_pt=min(
            27.0,
            30,
        ),
    )
    elements: list[SceneElement] = []
    for index, part in enumerate(parts):
        if count >= 3:
            # Dense metric/CJK copy needs both usable line length and vertical
            # room.  A two-column grid preserves the metric pattern while a
            # four-row strip would force meaningful text below the 16pt floor.
            column = index % 2
            row = index // 2
            panel_x = 0.04 + column * 0.48
            panel_y = 0.05 + row * 0.47
            panel = _subrect(
                bounds, x=panel_x, y=panel_y, width=0.44, height=0.42
            )
            text_bounds = _subrect(
                bounds,
                x=panel_x + 0.035,
                y=panel_y + 0.07,
                width=0.37,
                height=0.28,
            )
            marker_bounds = _subrect(
                bounds,
                x=panel_x + 0.018,
                y=panel_y + 0.11,
                width=0.012,
                height=0.20,
            )
        else:
            y = y_start + index * (row_height + row_gap)
            panel = _subrect(bounds, x=0.05, y=y, width=0.9, height=row_height)
            text_bounds = _subrect(
                bounds,
                x=0.10,
                y=y + row_height * 0.12,
                width=0.81,
                height=row_height * 0.76,
            )
            marker_bounds = _subrect(
                bounds,
                x=0.075,
                y=y + row_height * 0.25,
                width=0.018,
                height=row_height * 0.5,
            )
        part_style = _fit_text_style(
            contract=contract,
            text=part,
            bounds=text_bounds,
            style=style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"metric row {index + 1}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=part,
            style=part_style,
            consumer=f"metric row {index + 1}",
            minimum_panel_height=48,
            minimum_text_height=28,
            minimum_inset=8,
            contract=contract,
        )
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"metric-{index + 1}-row",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="metricRow",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=_CANONICAL_BODY_COLORS["divider"],
                    stroke_width=2 if index == 0 else 1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"metric-{index + 1}-marker",
                    z_order=9 + index,
                    bounds=marker_bounds,
                    semantic_role="metricMarker",
                    shape="rect",
                    fill=_CANONICAL_BODY_COLORS["accent"],
                ),
                _text(
                    slide_id=slide_id,
                    suffix=f"metric-{index + 1}-body",
                    z_order=20 + index,
                    bounds=text_bounds,
                    semantic_role="body" if index == 0 else "metricBody",
                    text=part,
                    style=part_style,
                    text_editable=count == 1 or index > 0,
                ),
            ]
        )
    return elements


def _compose_split_panel(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    grammar_parts: tuple[str, ...] = (),
) -> list[SceneElement]:
    parts = _body_parts(body.text, maximum=4, grammar_parts=grammar_parts)
    if len(parts) == 1:
        return _compose_cards(
            slide_id=slide_id,
            body=body,
            contract=contract,
            grammar_parts=grammar_parts,
        )
    bounds = body.bounds
    style = _body_style(contract, body.semantic_role).model_copy(
        update={
            "font_size": 23.0,
            "vertical_align": "middle",
        }
    )
    elements: list[SceneElement] = [
        _shape(
            slide_id=slide_id,
            suffix="split-axis",
            z_order=3,
            bounds=_subrect(bounds, x=0.493, y=0.08, width=0.014, height=0.84),
            semantic_role="comparisonAxis",
            shape="roundRect",
            fill=_CANONICAL_BODY_COLORS["accent"],
        )
    ]
    for index, part in enumerate(parts):
        if len(parts) == 3:
            column = 0 if index == 0 else 1
            x = 0.03 if column == 0 else 0.525
            y = 0.08 if index < 2 else 0.52
            row_height = 0.84 if index == 0 else 0.4
        else:
            row_count = (len(parts) + 1) // 2
            row_gap = 0.04
            row_height = (0.84 - row_gap * (row_count - 1)) / row_count
            column = index % 2
            row = index // 2
            x = 0.03 if column == 0 else 0.525
            y = 0.08 + row * (row_height + row_gap)
        width = 0.445
        panel = _subrect(bounds, x=x, y=y, width=width, height=row_height)
        text_bounds = _subrect(
            bounds,
            x=x + 0.04,
            y=y + row_height * 0.18,
            width=width - 0.08,
            height=row_height * 0.64,
        )
        part_style = _fit_text_style(
            contract=contract,
            text=part,
            bounds=text_bounds,
            style=style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"split panel {index + 1}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=part,
            style=part_style,
            consumer=f"split panel {index + 1}",
            minimum_panel_height=78,
            minimum_text_height=48,
            minimum_inset=8,
            contract=contract,
        )
        elements.extend(
            [
                _shape(
                    slide_id=slide_id,
                    suffix=f"split-{index + 1}-panel",
                    z_order=5 + index,
                    bounds=panel,
                    semantic_role="splitPanel",
                    shape="roundRect",
                    fill=_CANONICAL_BODY_COLORS["surface"],
                    stroke=(
                        _CANONICAL_BODY_COLORS["accent"]
                        if column == 0
                        else _CANONICAL_BODY_COLORS["divider"]
                    ),
                    stroke_width=2 if column == 0 else 1,
                ),
                _shape(
                    slide_id=slide_id,
                    suffix=f"split-{index + 1}-accent",
                    z_order=10 + index,
                    bounds=_subrect(
                        bounds,
                        x=x + 0.025,
                        y=y + row_height * 0.12,
                        width=width * 0.16,
                        height=max(0.018, row_height * 0.025),
                    ),
                    semantic_role="comparisonAccent",
                    shape="rect",
                    fill=(
                        _CANONICAL_BODY_COLORS["accent"]
                        if column == 0
                        else _CANONICAL_BODY_COLORS["warning"]
                    ),
                ),
                _text(
                    slide_id=slide_id,
                    suffix=f"split-{index + 1}-body",
                    z_order=20 + index,
                    bounds=text_bounds,
                    semantic_role="body" if index == 0 else "splitBody",
                    text=part,
                    style=part_style,
                    text_editable=index > 0,
                ),
            ]
        )
    return elements


def _compose_structured_source(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
    role_hint: str,
    structured_inputs: dict[str, Any],
) -> list[SceneElement]:
    bounds = body.bounds
    if role_hint == "table":
        panel = _subrect(bounds, x=0.02, y=0.04, width=0.96, height=0.22)
        text_bounds = _subrect(bounds, x=0.055, y=0.075, width=0.89, height=0.15)
        source_bounds = _subrect(bounds, x=0.02, y=0.32, width=0.96, height=0.64)
    elif role_hint == "image":
        panel = _subrect(bounds, x=0.02, y=0.12, width=0.32, height=0.76)
        text_bounds = _subrect(bounds, x=0.055, y=0.23, width=0.25, height=0.54)
        source_bounds = _subrect(bounds, x=0.38, y=0.04, width=0.6, height=0.92)
    else:
        panel = _subrect(bounds, x=0.02, y=0.12, width=0.29, height=0.76)
        text_bounds = _subrect(bounds, x=0.055, y=0.23, width=0.22, height=0.54)
        source_bounds = _subrect(bounds, x=0.35, y=0.04, width=0.63, height=0.92)
    style = _fit_text_style(
        contract=contract,
        text=body.text,
        bounds=text_bounds,
        style=_body_style(contract, body.semantic_role),
        minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
        consumer=f"{role_hint} supporting copy",
    )
    _require_readable_panel(
        panel=panel,
        text_bounds=text_bounds,
        text=body.text,
        style=style,
        consumer=f"{role_hint} supporting copy",
        minimum_panel_height=64 if role_hint == "table" else 150,
        minimum_text_height=40 if role_hint == "table" else 100,
        minimum_inset=8,
        contract=contract,
    )
    elements: list[SceneElement] = [
        _shape(
            slide_id=slide_id,
            suffix=f"{role_hint}-copy-panel",
            z_order=5,
            bounds=panel,
            semantic_role="supportingCopyPanel",
            shape="roundRect",
            fill=_CANONICAL_BODY_COLORS["surface"],
            stroke=_CANONICAL_BODY_COLORS["divider"],
            stroke_width=1,
        ),
        _text(
            slide_id=slide_id,
            suffix=f"{role_hint}-copy",
            z_order=20,
            bounds=text_bounds,
            semantic_role="body",
            text=body.text,
            style=style,
            text_editable=True,
        ),
        _shape(
            slide_id=slide_id,
            suffix=f"{role_hint}-native-frame",
            z_order=10,
            bounds=source_bounds,
            semantic_role="nativeObjectFrame",
            shape="roundRect",
            fill=_CANONICAL_BODY_COLORS["surface"],
            stroke=(
                _CANONICAL_BODY_COLORS["accent"]
                if role_hint in {"image", "chart"}
                else _CANONICAL_BODY_COLORS["divider"]
            ),
            stroke_width=2 if role_hint in {"image", "chart"} else 1,
        ),
    ]
    if role_hint == "image":
        source = structured_inputs.get("sourceImage")
        if not isinstance(source, dict):
            raise ValueError("authored_vnext_image_source_required")
        _only(
            source,
            {"assetId", "assetDigest", "altText", "crop", "cropGeometry"},
            mutation="structured sourceImage",
        )
        asset_digest = source.get("assetDigest")
        alt_text = source.get("altText")
        if not isinstance(asset_digest, str) or not isinstance(alt_text, str) or not alt_text.strip():
            raise ValueError("authored_vnext_image_source_requires_digest_and_alt_text")
        elements.append(
            SceneImageElement(
                kind="image",
                element_id=_authored_element_id(slide_id, "source-image"),
                z_order=15,
                bounds=source_bounds,
                rotation=0,
                semantic_role="bodyImage",
                editability=SceneEditability(geometry=True, asset=True),
                asset_id=str(source.get("assetId") or _asset_id(asset_digest)),
                asset_digest=asset_digest,
                alt_text=alt_text,
                crop=str(source.get("crop") or "cover"),
                crop_geometry=source.get("cropGeometry"),
            )
        )
        return elements
    source = structured_inputs.get("sourceData")
    if not isinstance(source, dict):
        raise ValueError(f"authored_vnext_{role_hint}_source_required")
    _only(
        source,
        {
            "dataId",
            "dataDigest",
            "kind",
            "rows",
            "columns",
            "chartType",
            "accessibilitySummary",
        },
        mutation="structured sourceData",
    )
    if source.get("kind") != role_hint:
        raise ValueError(f"authored_vnext_{role_hint}_source_kind_mismatch")
    data_digest = source.get("dataDigest")
    if not isinstance(data_digest, str):
        raise ValueError(f"authored_vnext_{role_hint}_source_digest_required")
    data_id = str(source.get("dataId") or _data_id(data_digest))
    if role_hint == "table":
        elements.append(
            SceneTableElement(
                kind="table",
                element_id=_authored_element_id(slide_id, "source-table"),
                z_order=15,
                bounds=source_bounds,
                rotation=0,
                semantic_role="bodyTable",
                editability=SceneEditability(geometry=True, data=True),
                data_id=data_id,
                data_digest=data_digest,
                rows=source.get("rows"),
                columns=source.get("columns"),
            )
        )
    else:
        elements.append(
            SceneChartElement(
                kind="chart",
                element_id=_authored_element_id(slide_id, "source-chart"),
                z_order=15,
                bounds=source_bounds,
                rotation=0,
                semantic_role="bodyChart",
                editability=SceneEditability(geometry=True, data=True),
                data_id=data_id,
                data_digest=data_digest,
                chart_type=source.get("chartType"),
                accessibility_summary=source.get("accessibilitySummary"),
            )
        )
    return elements


def _compose_long_title(
    *,
    slide_id: str,
    body: BodyTextElement,
    contract: DeckMasterContract,
) -> list[SceneElement]:
    bounds = body.bounds
    accent = _CANONICAL_BODY_COLORS["accent"]
    panel = _subrect(bounds, x=0.03, y=0.14, width=0.94, height=0.72)
    heading, separator, detail = body.text.partition(":")
    split_hierarchy = bool(separator and heading.strip() and detail.strip())
    if split_hierarchy:
        text_specs = [
            (
                "label",
                _subrect(bounds, x=0.085, y=0.29, width=0.25, height=0.42),
                f"{heading.strip()}:",
                _emphasis_style(
                    contract,
                    body.semantic_role,
                    preferred_font_pt=24.0,
                ).model_copy(update={"vertical_align": "middle"}),
            ),
            (
                "detail",
                _subrect(bounds, x=0.40, y=0.25, width=0.515, height=0.50),
                detail.strip(),
                _body_style(contract, body.semantic_role).model_copy(
                    update={"font_size": 21.0, "vertical_align": "middle"}
                ),
            ),
        ]
    else:
        text_specs = [
            (
                "body",
                _subrect(bounds, x=0.085, y=0.25, width=0.83, height=0.50),
                body.text,
                _emphasis_style(
                    contract,
                    body.semantic_role,
                    preferred_font_pt=25.0,
                ),
            )
        ]
    fitted_specs: list[tuple[str, Rect, str, SceneTextStyle]] = []
    for suffix, text_bounds, text, style in text_specs:
        fitted = _fit_text_style(
            contract=contract,
            text=text,
            bounds=text_bounds,
            style=style,
            minimum_font_pt=_CANONICAL_BODY_MIN_FONT_PT,
            consumer=f"long-title {suffix}",
        )
        _require_readable_panel(
            panel=panel,
            text_bounds=text_bounds,
            text=text,
            style=fitted,
            consumer=f"long-title {suffix}",
            minimum_panel_height=90,
            minimum_text_height=40,
            contract=contract,
        )
        fitted_specs.append((suffix, text_bounds, text, fitted))
    elements: list[SceneElement] = [
        _shape(
            slide_id=slide_id,
            suffix="long-title-rule",
            z_order=5,
            bounds=_subrect(bounds, x=0.03, y=0.04, width=0.28, height=0.02),
            semantic_role="longTitleRule",
            shape="rect",
            fill=accent,
        ),
        _shape(
            slide_id=slide_id,
            suffix="long-title-panel",
            z_order=6,
            bounds=panel,
            semantic_role="longTitlePanel",
            shape="rect",
            fill=_CANONICAL_BODY_COLORS["surface"],
        ),
        _shape(
            slide_id=slide_id,
            suffix="long-title-rail",
            z_order=7,
            bounds=_subrect(bounds, x=0.03, y=0.14, width=0.014, height=0.72),
            semantic_role="longTitleAccent",
            shape="rect",
            fill=accent,
        ),
    ]
    if split_hierarchy:
        elements.append(
            _shape(
                slide_id=slide_id,
                suffix="long-title-divider",
                z_order=8,
                bounds=_subrect(bounds, x=0.365, y=0.24, width=0.004, height=0.52),
                semantic_role="longTitleAccent",
                shape="rect",
                fill=_CANONICAL_BODY_COLORS["divider"],
            )
        )
    for index, (suffix, text_bounds, text, style) in enumerate(fitted_specs):
        elements.append(
            _text(
                slide_id=slide_id,
                suffix=f"long-title-{suffix}",
                z_order=20 + index,
                bounds=text_bounds,
                semantic_role="body" if index == 0 else "longTitleBody",
                text=text,
                style=style,
                text_editable=True,
            )
        )
    return elements


def _compose_authored_body(
    *,
    slide: SlideSpecV2,
    visual_plan: SlideVisualPlan,
    title_bounds: Rect,
    contract: DeckMasterContract,
    structured_inputs: dict[str, Any] | None,
) -> list[SceneElement] | None:
    """Produce role-specific native objects for the normal single-text input."""

    body_texts = [
        element
        for element in visual_plan.elements
        if isinstance(element, BodyTextElement) and element.semantic_role == "body"
    ]
    if len(body_texts) != 1:
        return None
    if any(
        element is not body_texts[0]
        and not (
            isinstance(element, BodyShapeElement)
            and element.semantic_role == "decoration"
        )
        for element in visual_plan.elements
    ):
        return None
    source_body = body_texts[0]
    body = source_body.model_copy(
        update={
            "bounds": _safe_body_bounds(
                source_body.bounds,
                title_bounds=title_bounds,
                contract=contract,
            )
        }
    )
    slide_id = slide.slide_id
    if visual_plan.role == "cover":
        composed = _compose_cover(slide_id=slide_id, body=body, contract=contract)
    elif visual_plan.role == "section":
        composed = _compose_section(slide_id=slide_id, body=body, contract=contract)
    elif visual_plan.role == "longTitle":
        composed = _compose_long_title(
            slide_id=slide_id, body=body, contract=contract
        )
    else:
        intent = _design_intent(
            slide=slide,
            visual_plan=visual_plan,
            contract=contract,
            structured_inputs=structured_inputs,
        )
        if (
            len(slide.title.lines) > 1
            and intent.archetype == "card"
            and intent.cjk_ratio >= 0.35
        ):
            # Long mixed-CJK editorial slides can legitimately retain a
            # standard source role.  Choose the composition from the measured
            # content, while leaving explicit process/data/native roles intact.
            composed = _compose_long_title(
                slide_id=slide_id,
                body=body,
                contract=contract,
            )
        elif intent.archetype == "process":
            composed = _compose_process(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        elif intent.archetype == "timeline":
            composed = _compose_timeline(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        elif intent.archetype == "comparison":
            composed = _compose_split_panel(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        elif intent.archetype == "metric":
            composed = _compose_metric_rows(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        elif intent.archetype == "infographic":
            composed = _compose_infographic(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        elif intent.archetype in {"image", "table", "chart"}:
            composed = _compose_structured_source(
                slide_id=slide_id,
                body=body,
                contract=contract,
                role_hint=intent.archetype,
                structured_inputs=_structured_slide_inputs(structured_inputs),
            )
        elif intent.density == "dense" or (
            intent.cjk_ratio >= 0.35 and intent.density != "sparse"
        ):
            composed = _compose_stacked_cards(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        elif intent.variant == 0:
            composed = _compose_cards(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
        else:
            composed = _compose_stacked_cards(
                slide_id=slide_id,
                body=body,
                contract=contract,
                grammar_parts=intent.grammar_parts,
            )
    source_text = _semantic_copy(source_body.text)
    composed_text = " ".join(
        " ".join(element.text.split())
        for element in composed
        if isinstance(element, SceneTextElement)
    )
    if source_text != _semantic_copy(composed_text):
        raise ValueError(
            "authored body composition changed or dropped source text: "
            f"{slide_id}"
        )
    return composed


def compile_slide_scene(
    *,
    slide: SlideSpecV2,
    visual_plan: SlideVisualPlan,
    title_bounds: Rect,
    contract: DeckMasterContract,
    structured_inputs: dict[str, Any] | None = None,
) -> SlideSceneIR:
    """Compile title plus every body object; no body-only/title-body collapse."""

    if slide.slide_id != visual_plan.slide_id:
        raise ValueError("scene compiler slide and visual plan IDs differ")
    _structured_slide_inputs(structured_inputs)
    safe_title_bounds = _safe_title_bounds(
        title_bounds,
        contract,
        slide_role=visual_plan.role,
    )
    body_ids = {
        element.element_id: _scene_element_id(slide.slide_id, element.element_id)
        for element in visual_plan.elements
    }
    preferred_title_pt, minimum_title_pt = {
        "cover": (52.0, 35.0),
        "section": (48.0, 35.0),
        # Long Korean/CJK titles need a wider Office font-metric safety margin
        # than Latin headings.  Thirty points still clears the authored title
        # floor while keeping every explicit planner line at one native size.
        "longTitle": (30.0, 28.0),
        "standard": (40.0, 35.0),
    }[visual_plan.role]
    if len(slide.title.lines) > 1:
        # The outline planner may retain a content layout for a long mixed-CJK
        # title.  Honor its explicit multi-line decision without violating the
        # 28pt long-title floor used by the canonical authored composition.
        preferred_title_pt = min(preferred_title_pt, 30.0)
        minimum_title_pt = min(minimum_title_pt, 28.0)
    title_style = SceneTextStyle(
        font_family=_CANONICAL_BODY_FONT,
        font_size=preferred_title_pt,
        font_weight=700,
        color=_contrasting_authored_color(
            contract,
            background=_CANONICAL_BODY_COLORS["background"],
            consumer="title text",
        ),
        text_align="left",
        vertical_align="top",
    )
    # Preserve the planner's explicit CJK/kinsoku line decisions in the Scene.
    # A one-line title therefore remains one line in preview and native export
    # instead of being silently reflowed by a different host text engine.
    title_text = "\n".join(slide.title.lines)
    title_style = _fit_text_style(
        contract=contract,
        text=title_text,
        bounds=safe_title_bounds,
        style=title_style,
        minimum_font_pt=minimum_title_pt,
        consumer="title",
        maximum_lines=len(slide.title.lines),
    )
    elements: list[SceneElement] = [
        SceneTextElement(
            kind="text",
            element_id=_title_element_id(slide.slide_id),
            z_order=100,
            bounds=safe_title_bounds,
            rotation=0,
            semantic_role="title",
            # Title mutation remains read-only until title-fit can be
            # recomputed in the same CAS transaction. Advertising editability
            # while the mutation service rejects it would be a false capability.
            editability=SceneEditability(),
            text=title_text,
            style=title_style,
        )
    ]
    authored_body = _compose_authored_body(
        slide=slide,
        visual_plan=visual_plan,
        title_bounds=safe_title_bounds,
        contract=contract,
        structured_inputs=structured_inputs,
    )
    if authored_body is not None:
        elements.extend(authored_body)
        return seal_slide_scene(
            slide_id=slide.slide_id,
            revision=slide.revision,
            elements=elements,
        )
    for body in visual_plan.elements:
        common = {
            "element_id": body_ids[body.element_id],
            "z_order": body.z_order + 10,
            "bounds": _safe_element_bounds(
                body.bounds,
                title_bounds=safe_title_bounds,
                contract=contract,
            ),
            "rotation": body.rotation,
            "semantic_role": body.semantic_role,
        }
        if isinstance(body, BodyTextElement):
            element: SceneElement = SceneTextElement(
                kind="text",
                **common,
                editability=SceneEditability(text=True, geometry=True),
                text=body.text,
                style=_body_style(contract, body.semantic_role),
            )
        elif isinstance(body, BodyImageElement):
            element = SceneImageElement(
                kind="image",
                **common,
                editability=SceneEditability(geometry=True, asset=True),
                asset_id=_asset_id(body.asset_digest),
                asset_digest=body.asset_digest,
                alt_text=body.alt_text,
                crop=body.crop,
            )
        elif isinstance(body, BodyShapeElement):
            element = SceneShapeElement(
                kind="shape",
                **common,
                editability=SceneEditability(geometry=True, style=True),
                shape=body.shape,
                fill=_color_token(contract, body.color_token),
            )
        elif isinstance(body, BodyConnectorElement):
            element = SceneConnectorElement(
                kind="connector",
                **common,
                editability=SceneEditability(geometry=True),
                from_element_id=body_ids[body.from_element_id],
                to_element_id=body_ids[body.to_element_id],
                connector_type=body.connector_type,
            )
        elif isinstance(body, BodyTableElement):
            element = SceneTableElement(
                kind="table",
                **common,
                editability=SceneEditability(geometry=True, data=True),
                data_id=_data_id(body.data_digest),
                data_digest=body.data_digest,
                rows=body.rows,
                columns=body.columns,
            )
        elif isinstance(body, BodyChartElement):
            element = SceneChartElement(
                kind="chart",
                **common,
                editability=SceneEditability(geometry=True, data=True),
                data_id=_data_id(body.data_digest),
                data_digest=body.data_digest,
                chart_type=body.chart_type,
                accessibility_summary=body.accessibility_summary,
            )
        elif isinstance(body, BodyGroupElement):
            element = SceneGroupElement(
                kind="group",
                **common,
                editability=SceneEditability(geometry=True),
                child_element_ids=[body_ids[item] for item in body.child_element_ids],
            )
        else:  # pragma: no cover - the closed visual-plan union prevents this
            raise ValueError(f"unsupported visual plan element: {type(body).__name__}")
        elements.append(element)
    return seal_slide_scene(
        slide_id=slide.slide_id,
        revision=slide.revision,
        elements=elements,
    )


def _deck_structured_manifests(
    structured_inputs: dict[str, Any] | None,
) -> tuple[list[SceneAssetManifestEntry] | None, list[SceneDataManifestEntry] | None]:
    if structured_inputs is None:
        return None, None
    if not isinstance(structured_inputs, dict):
        raise ValueError("deck scene structuredInputs must be an object")
    unknown = sorted(set(structured_inputs) - {"assetManifest", "dataManifest"})
    if unknown:
        raise ValueError(
            f"deck scene structuredInputs contains unsupported fields: {unknown}"
        )
    raw_assets = structured_inputs.get("assetManifest")
    raw_data = structured_inputs.get("dataManifest")
    if raw_assets is not None and not isinstance(raw_assets, list):
        raise ValueError("structuredInputs.assetManifest must be an array")
    if raw_data is not None and not isinstance(raw_data, list):
        raise ValueError("structuredInputs.dataManifest must be an array")
    assets = (
        [SceneAssetManifestEntry.model_validate(item) for item in raw_assets]
        if raw_assets is not None
        else None
    )
    data = (
        [SceneDataManifestEntry.model_validate(item) for item in raw_data]
        if raw_data is not None
        else None
    )
    return assets, data


def compile_deck_scene(
    *,
    deck_id: str,
    revision: int,
    composition_digest: str,
    source_digest: str,
    slides: list[SlideSceneIR],
    asset_manifest: list[SceneAssetManifestEntry] | None = None,
    data_manifest: list[SceneDataManifestEntry] | None = None,
    structured_inputs: dict[str, Any] | None = None,
) -> DeckSceneIR:
    structured_assets, structured_data = _deck_structured_manifests(structured_inputs)
    if asset_manifest is not None and structured_assets is not None and asset_manifest != structured_assets:
        raise ValueError("explicit and structured asset manifests differ")
    if data_manifest is not None and structured_data is not None and data_manifest != structured_data:
        raise ValueError("explicit and structured data manifests differ")
    return seal_deck_scene(
        deck_id=deck_id,
        revision=revision,
        composition_digest=composition_digest,
        source_digest=source_digest,
        slides=slides,
        asset_manifest=asset_manifest if asset_manifest is not None else structured_assets,
        data_manifest=data_manifest if data_manifest is not None else structured_data,
        capabilities=default_scene_capabilities(),
    )


def _only(payload: dict[str, Any], allowed: set[str], *, mutation: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{mutation} payload contains unsupported fields: {unknown}")


def apply_scene_element_mutation(
    scene: DeckSceneIR,
    *,
    slide_id: str,
    element_id: str,
    mutation: SceneMutationKind,
    payload: dict[str, Any],
    expected_scene_digest: str,
    expected_slide_revision: int,
    expected_slide_digest: str,
) -> DeckSceneIR:
    """Apply one strict-CAS edit and reseal slide and deck scene digests."""

    slide_index = next(
        (index for index, slide in enumerate(scene.slides) if slide.slide_id == slide_id),
        None,
    )
    if slide_index is None:
        raise ValueError("scene mutation slide not found")
    slide = scene.slides[slide_index]
    if scene.scene_digest != expected_scene_digest:
        raise ValueError("scene mutation deck Scene CAS failed")
    if (
        slide.revision != expected_slide_revision
        or slide.slide_digest != expected_slide_digest
    ):
        raise ValueError("scene mutation slide CAS failed")
    element_index = next(
        (
            index
            for index, element in enumerate(slide.elements)
            if element.element_id == element_id
        ),
        None,
    )
    if element_index is None:
        raise ValueError("scene mutation element not found")
    element = slide.elements[element_index]
    values = element.model_dump(mode="json", by_alias=False)
    asset_manifest = list(scene.asset_manifest)
    data_manifest = list(scene.data_manifest)
    if mutation == "text.replace":
        _only(payload, {"text", "runs"}, mutation=mutation)
        if not isinstance(element, SceneTextElement) or not element.editability.text:
            raise ValueError("scene element does not permit text editing")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text.replace requires non-empty text")
        values["text"] = text
        # Replacing plain text must not retain a stale run map.  Advanced
        # callers may atomically provide the complete versioned run set.
        values["runs"] = payload.get("runs")
    elif mutation == "text.style.update":
        _only(
            payload,
            {
                "autoFit",
                "color",
                "fontFamily",
                "fontSize",
                "fontWeight",
                "textAlign",
            },
            mutation=mutation,
        )
        if not isinstance(element, SceneTextElement) or not element.editability.style:
            raise ValueError("scene element does not permit text style editing")
        if not payload:
            raise ValueError("text.style.update requires a style field")
        style = (
            element.style.model_dump(mode="json", by_alias=False)
            if element.style is not None
            else {}
        )
        for wire, field in (
            ("color", "color"),
            ("fontFamily", "font_family"),
            ("fontSize", "font_size"),
            ("fontWeight", "font_weight"),
            ("textAlign", "text_align"),
            ("autoFit", "auto_fit"),
        ):
            if wire in payload:
                style[field] = payload[wire]
        values["style"] = SceneTextStyle.model_validate(style).model_dump(
            mode="json", by_alias=False, exclude_none=True
        )
    elif mutation == "geometry.update":
        _only(payload, {"bounds", "rotation"}, mutation=mutation)
        if not element.editability.geometry:
            raise ValueError("scene element does not permit geometry editing")
        if not payload:
            raise ValueError("geometry.update requires bounds or rotation")
        if "bounds" in payload:
            values["bounds"] = Rect.model_validate(payload["bounds"]).model_dump()
        if "rotation" in payload:
            rotation = payload["rotation"]
            if not isinstance(rotation, (int, float)) or isinstance(rotation, bool):
                raise ValueError("geometry.update rotation must be numeric")
            values["rotation"] = rotation
    elif mutation == "shape.style.update":
        _only(payload, {"fill", "stroke", "strokeWidth"}, mutation=mutation)
        if not isinstance(element, SceneShapeElement) or not element.editability.style:
            raise ValueError("scene element does not permit shape style editing")
        if not payload:
            raise ValueError("shape.style.update requires a style field")
        for wire, field in (
            ("fill", "fill"),
            ("stroke", "stroke"),
            ("strokeWidth", "stroke_width"),
        ):
            if wire in payload:
                values[field] = payload[wire]
    elif mutation == "asset.set":
        _only(
            payload,
            {"assetId", "assetDigest", "altText", "crop", "cropGeometry", "mediaType", "storageReference", "sizeBytes"},
            mutation=mutation,
        )
        if not isinstance(element, SceneImageElement) or not element.editability.asset:
            raise ValueError("scene element does not permit asset editing")
        required = ("assetId", "assetDigest", "altText")
        if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
            raise ValueError("asset.set requires assetId, assetDigest, and altText")
        values.update(
            asset_id=payload["assetId"],
            asset_digest=payload["assetDigest"],
            alt_text=payload["altText"],
        )
        if "crop" in payload:
            values["crop"] = payload["crop"]
        if "cropGeometry" in payload:
            values["crop_geometry"] = payload["cropGeometry"]
        existing = next(
            (item for item in asset_manifest if item.asset_id == payload["assetId"]),
            None,
        )
        if existing is None or existing.asset_digest != payload["assetDigest"]:
            if (
                not isinstance(payload.get("mediaType"), str)
                or not isinstance(payload.get("storageReference"), str)
                or not isinstance(payload.get("sizeBytes"), int)
            ):
                raise ValueError("new asset.set digest requires mediaType, storageReference, and sizeBytes")
            replacement = SceneAssetManifestEntry(
                asset_id=payload["assetId"],
                asset_digest=payload["assetDigest"],
                media_type=payload["mediaType"],
                storage_reference=payload["storageReference"],
                size_bytes=payload["sizeBytes"],
            )
            asset_manifest = [
                item for item in asset_manifest if item.asset_id != replacement.asset_id
            ] + [replacement]
    elif mutation == "image.crop.update":
        _only(payload, {"cropGeometry"}, mutation=mutation)
        if not isinstance(element, SceneImageElement) or not element.editability.asset:
            raise ValueError("scene element does not permit image crop editing")
        crop_geometry = payload.get("cropGeometry")
        if not isinstance(crop_geometry, dict):
            raise ValueError("image.crop.update requires cropGeometry")
        values["crop_geometry"] = crop_geometry
    elif mutation == "data.update":
        _only(
            payload,
            {
                "dataId", "dataDigest", "dataPayload", "rows", "columns",
                "chartType", "accessibilitySummary",
            },
            mutation=mutation,
        )
        if not isinstance(element, (SceneTableElement, SceneChartElement)) or not element.editability.data:
            raise ValueError("scene element does not permit data editing")
        if not isinstance(payload.get("dataId"), str) or not isinstance(
            payload.get("dataDigest"), str
        ):
            raise ValueError("data.update requires dataId and dataDigest")
        values.update(data_id=payload["dataId"], data_digest=payload["dataDigest"])
        for wire, field in (
            ("rows", "rows"),
            ("columns", "columns"),
            ("chartType", "chart_type"),
            ("accessibilitySummary", "accessibility_summary"),
        ):
            if wire in payload:
                values[field] = payload[wire]
        existing = next(
            (item for item in data_manifest if item.data_id == payload["dataId"]),
            None,
        )
        if existing is None or existing.data_digest != payload["dataDigest"]:
            data_payload = payload.get("dataPayload")
            if not isinstance(data_payload, dict):
                raise ValueError("new data.update digest requires dataPayload")
            if scene_digest(data_payload) != payload["dataDigest"]:
                raise ValueError("data.update payload digest mismatch")
            replacement = SceneDataManifestEntry(
                data_id=payload["dataId"],
                data_digest=payload["dataDigest"],
                kind=element.kind,
                payload=data_payload,
            )
            data_manifest = [
                item for item in data_manifest if item.data_id != replacement.data_id
            ] + [replacement]
    elif mutation in {"table.cell.update", "table.column-widths.update"}:
        if not isinstance(element, SceneTableElement) or not element.editability.data:
            raise ValueError("scene element does not permit table editing")
        existing_index = next(
            (
                index
                for index, item in enumerate(data_manifest)
                if item.data_id == element.data_id
                and item.data_digest == element.data_digest
                and item.kind == "table"
            ),
            None,
        )
        if existing_index is None:
            raise ValueError("scene table data manifest binding is missing")
        data_payload = dict(data_manifest[existing_index].payload)
        cells = [list(row) for row in data_payload["cells"]]
        if mutation == "table.cell.update":
            _only(
                payload,
                {
                    "row",
                    "column",
                    "text",
                    "fill",
                    "borders",
                    "horizontalAlign",
                    "verticalAlign",
                },
                mutation=mutation,
            )
            row = payload.get("row")
            column = payload.get("column")
            if (
                isinstance(row, bool)
                or not isinstance(row, int)
                or isinstance(column, bool)
                or not isinstance(column, int)
                or row < 0
                or column < 0
                or row >= len(cells)
                or column >= len(cells[0])
            ):
                raise ValueError("table.cell.update row or column is out of range")
            if set(payload) == {"row", "column"}:
                raise ValueError("table.cell.update requires a cell mutation")
            if "text" in payload:
                text = payload["text"]
                if not isinstance(text, str) or len(text) > 5000:
                    raise ValueError("table.cell.update text is invalid")
                cells[row][column] = text
            style_fields = {
                key: payload[key]
                for key in ("fill", "borders", "horizontalAlign", "verticalAlign")
                if key in payload
            }
            if style_fields:
                styles = data_payload.get("cellStyles")
                if styles is None:
                    styles = [
                        [{} for _ in range(len(cells[0]))]
                        for _ in range(len(cells))
                    ]
                else:
                    styles = [list(style_row) for style_row in styles]
                current_style = dict(styles[row][column])
                current_style.update(style_fields)
                styles[row][column] = SceneTableCellStyle.model_validate(
                    current_style
                ).model_dump(mode="json", by_alias=True, exclude_none=True)
                data_payload["cellStyles"] = styles
        else:
            _only(payload, {"columnWidthsPt"}, mutation=mutation)
            widths = payload.get("columnWidthsPt")
            if (
                not isinstance(widths, list)
                or len(widths) != len(cells[0])
                or any(
                    isinstance(width, bool)
                    or not isinstance(width, (int, float))
                    or not math.isfinite(float(width))
                    or float(width) <= 0
                    for width in widths
                )
                or abs(sum(float(width) for width in widths) - element.bounds.width * 0.75) > 0.25
            ):
                raise ValueError(
                    "table.column-widths.update must provide every column width in points and match the table width"
                )
            data_payload["columnWidthsPt"] = widths
        data_payload["cells"] = cells
        data_digest = scene_digest(data_payload)
        replacement = SceneDataManifestEntry(
            data_id=element.data_id,
            data_digest=data_digest,
            kind="table",
            payload=data_payload,
        )
        data_manifest[existing_index] = replacement
        values["data_digest"] = data_digest
    else:  # pragma: no cover - Literal plus request validation closes this
        raise ValueError(f"unsupported scene mutation: {mutation}")
    updated_element = _SCENE_ELEMENT_ADAPTER.validate_python(values)
    updated_elements = list(slide.elements)
    updated_elements[element_index] = updated_element
    updated_slide = seal_slide_scene(
        slide_id=slide.slide_id,
        revision=slide.revision + 1,
        elements=updated_elements,
        hybrid_export=slide.hybrid_export,
    )
    slides = list(scene.slides)
    slides[slide_index] = updated_slide
    return seal_deck_scene(
        deck_id=scene.deck_id,
        revision=scene.revision + 1,
        composition_digest=scene.composition_digest,
        source_digest=scene.source_digest,
        hybrid_profile_id=scene.hybrid_profile_id,
        slides=slides,
        asset_manifest=sorted(asset_manifest, key=lambda item: item.asset_id),
        data_manifest=sorted(data_manifest, key=lambda item: item.data_id),
        capabilities=list(scene.capabilities),
    )


__all__ = [
    "SceneMutationKind",
    "apply_scene_element_mutation",
    "compatibility_composition_digest",
    "compile_deck_scene",
    "compile_slide_scene",
    "default_scene_capabilities",
]
