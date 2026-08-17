"""Authored-mode deck builder: turn an outline into a list of bespoke HTML slides
(authored concurrently against one shared design system for cohesion) and assemble
the rendered slide images into an image-per-slide PPTX that opens in PowerPoint with
perfect fidelity. The render step itself reuses utils/slide_capture. Opt-in — the
fast default template path is untouched."""

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.author_slide import (
    COMPANY_STANDARD_PAGE_NUMBER_MODE,
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    TEMPLATE_PAGE_NUMBER_MODE,
    AuthoredStyleLike,
    Brand,
    author_slide_html,
    apply_style_defaults,
    build_design_system,
    fallback_slide_html,
    is_valid_slide_html,
    normalize_reference_mode,
)
from utils.authored_styles import reference_images_for_role

logger = logging.getLogger(__name__)

_DEFAULT_AUTHOR_ATTEMPTS = 2
_TRANSIENT_AUTHOR_ATTEMPTS = 4
_TRANSIENT_RETRY_BASE_SECONDS = 1.5
_TRANSIENT_RETRY_MAX_SECONDS = 8.0
_TRANSIENT_ERROR_HINTS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection aborted",
    "connection reset",
    "incomplete chunked read",
    "overload",
    "peer closed",
    "rate limit",
    "temporarily unavailable",
    "timed out",
    "timeout",
)


def _is_transient_author_error(error: object) -> bool:
    """Return True for provider/network failures that are safe to retry."""
    message = str(error).lower()
    return any(hint in message for hint in _TRANSIENT_ERROR_HINTS)


@dataclass(frozen=True)
class AuthoredDeckResult:
    """First-pass slides plus the exact design system reused by vision-QA."""

    htmls: List[str]
    design_system: str


@dataclass(frozen=True)
class AuthoredDeckPlan:
    """Immutable inputs shared by independently-running slide author tasks."""

    contents: Tuple[str, ...]
    roles: Tuple[str, ...]
    reference_images: Tuple[Tuple[Path, ...], ...]
    reference_mode: str
    brand: Brand
    design_system: str
    structured_contexts: Tuple[dict[str, Any] | None, ...]
    page_number_mode: str


# Concurrency is bounded PROCESS-WIDE inside author_slide_html (the AUTHOR_CONCURRENCY-sized
# dedicated executor), so the per-slide fan-out here doesn't need its own semaphore — excess
# calls queue at that single chokepoint shared with the vision-QA re-author pass.

# A light deck-plan: a slide's ROLE nudges the model toward a fitting bespoke layout
# (cover hero / editorial problem / numbered pillars / phased roadmap / hero metric /
# bold closing). Position drives the frame; content keywords pick the middle role.
_METRIC_HINTS = (
    "%", "퍼센트", "성장", "매출", "수익", "지표", "비율", "증가", "감소", "달성",
    "ROI", "조원", "억원", "billion", "million", "growth", "revenue",
)
_TIMELINE_HINTS = (
    "단계", "로드맵", "분기", "타임라인", "절차", "순서", "스텝",
    "단계", "로드맵", "프로세스", "대응 흐름", "phase", "roadmap", "timeline", "step",
    "pipeline", "quarter", "2024", "2025", "2026", "2027",
)
_ARCHITECTURE_HINTS = (
    "아키텍처", "구조", "시스템", "에이전트", "architecture", "system", "platform",
    "layer", "integration", "agent",
)

_ZONE_ID_PATTERN = re.compile(r"^ai-zone:[a-z0-9][a-z0-9._-]*$")
_HEX_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b")
_RGB_COLOR_PATTERN = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})(?:\s*[,/]\s*[\d.]+%?)?\s*\)",
    re.IGNORECASE,
)
_FONT_FAMILY_PATTERN = re.compile(r"font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_SAFE_FONT_FALLBACKS = frozenset(
    {
        "sans-serif",
        "serif",
        "monospace",
        "malgun gothic",
        "aptos",
        "batang",
        "cambria",
        "consolas",
    }
)
_VOID_HTML_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)


class _ZoneHTMLProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.active_zone: tuple[str, int] | None = None
        self.zone_occurrences: dict[str, int] = {}
        self.zone_chars: dict[str, int] = {}
        self.zone_items: dict[str, int] = {}
        self.zone_blocks: dict[str, set[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value for name, value in attrs}
        zone_id = values.get("data-ai-zone-id")
        if zone_id is not None:
            if self.active_zone is not None:
                raise ValueError("structured zones cannot be nested")
            self.active_zone = (zone_id, len(self.stack))
            self.zone_occurrences[zone_id] = self.zone_occurrences.get(zone_id, 0) + 1
            self.zone_chars.setdefault(zone_id, 0)
            self.zone_items.setdefault(zone_id, 0)
            self.zone_blocks.setdefault(zone_id, set())
        if self.active_zone is not None:
            active_id = self.active_zone[0]
            block_id = values.get("data-ai-block-id")
            if block_id is not None:
                self.zone_blocks[active_id].add(block_id)
            if "data-ai-item" in values:
                self.zone_items[active_id] += 1
        if tag.casefold() not in _VOID_HTML_TAGS:
            self.stack.append(tag.casefold())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_HTML_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        if self.active_zone is not None and len(self.stack) - 1 == self.active_zone[1]:
            self.active_zone = None
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.active_zone is not None:
            self.zone_chars[self.active_zone[0]] += len(data.strip())


def _normalized_color(value: str) -> str:
    lowered = value.lower()
    if len(lowered) == 4:
        return "#" + "".join(character * 2 for character in lowered[1:])
    return lowered


def _constrain_structured_zone_html(
    html: str,
    context: Mapping[str, Any] | None,
) -> str:
    """Fail closed and pin model-authored zone containers to contract geometry."""

    if context is None:
        return html
    if context.get("schemaVersion") != "authored-vnext-ai-edit-zone-context/v1":
        raise ValueError("unsupported structured authoring context")
    raw_zones = context.get("zones")
    design = context.get("templateDesignContext")
    if not isinstance(raw_zones, list) or not isinstance(design, Mapping):
        raise ValueError("structured authoring context is incomplete")
    token_context = design.get("tokens")
    color_context = token_context.get("colors") if isinstance(token_context, Mapping) else None
    if not isinstance(color_context, Mapping):
        raise ValueError("inheritTemplate requires template color tokens")
    allowed_colors = {
        _normalized_color(str(value))
        for value in color_context.values()
        if isinstance(value, str) and _HEX_COLOR_PATTERN.fullmatch(value)
    }
    allowed_fonts = {
        str(value).strip().strip("\"'").lower()
        for value in design.get("allowedFonts", [])
        if isinstance(value, str) and str(value).strip()
    }
    if not allowed_colors or not allowed_fonts:
        raise ValueError("inheritTemplate requires an explicit color and font allowlist")

    css_sources = re.findall(r"<style\b[^>]*>(.*?)</style\s*>", html, re.I | re.S)
    css_sources.extend(re.findall(r"\bstyle\s*=\s*[\"']([^\"']*)[\"']", html, re.I | re.S))
    emitted_css = "\n".join(css_sources)
    invented_colors = {
        _normalized_color(value)
        for value in _HEX_COLOR_PATTERN.findall(emitted_css)
        if _normalized_color(value) not in allowed_colors
    }
    for match in _RGB_COLOR_PATTERN.finditer(emitted_css):
        channels = tuple(int(value) for value in match.groups())
        if any(value > 255 for value in channels):
            invented_colors.add(match.group(0).lower())
            continue
        normalized = "#" + "".join(f"{value:02x}" for value in channels)
        if normalized not in allowed_colors:
            invented_colors.add(normalized)
    if re.search(r"\b(?:hsl|hsla|lab|lch|oklab|oklch|color)\s*\(", emitted_css, re.I):
        invented_colors.add("unsupported-functional-color")
    if invented_colors:
        raise ValueError(
            "structured zone output invented colors: " + ", ".join(sorted(invented_colors))
        )
    invented_fonts: set[str] = set()
    for declaration in _FONT_FAMILY_PATTERN.findall(emitted_css):
        for family in declaration.split(","):
            normalized = family.strip().strip("\"'").lower()
            if normalized and normalized not in allowed_fonts and normalized not in _SAFE_FONT_FALLBACKS:
                invented_fonts.add(normalized)
    if invented_fonts:
        raise ValueError(
            "structured zone output invented fonts: " + ", ".join(sorted(invented_fonts))
        )

    probe = _ZoneHTMLProbe()
    probe.feed(html)
    probe.close()
    rules: list[str] = []
    seen_ids: set[str] = set()
    for raw_zone in raw_zones:
        if not isinstance(raw_zone, Mapping):
            raise ValueError("structured zone must be an object")
        zone_id = raw_zone.get("zoneId")
        bounds = raw_zone.get("bounds")
        if not isinstance(zone_id, str) or not _ZONE_ID_PATTERN.fullmatch(zone_id):
            raise ValueError("structured zone has an invalid zoneId")
        if zone_id in seen_ids:
            raise ValueError("structured zone IDs must be unique")
        seen_ids.add(zone_id)
        if not isinstance(bounds, Mapping):
            raise ValueError("structured zone bounds are required")
        try:
            x = float(bounds["x"])
            y = float(bounds["y"])
            width = float(bounds["width"])
            height = float(bounds["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("structured zone bounds are invalid") from error
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1280
            or y + height > 720
        ):
            raise ValueError("structured zone leaves the 1280x720 canvas")
        generation_state = raw_zone.get("generationState")
        occurrence_count = probe.zone_occurrences.get(zone_id, 0)
        if generation_state in {"hidden", "blank"}:
            if occurrence_count:
                raise ValueError("empty structured zones must not render content")
            continue
        if occurrence_count != 1:
            raise ValueError("structured zone output must contain each zone exactly once")
        raw_blocks = raw_zone.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("structured zone blocks must be a list")
        expected_blocks = {
            str(block.get("blockId"))
            for block in raw_blocks
            if isinstance(block, Mapping) and isinstance(block.get("blockId"), str)
        }
        if len(expected_blocks) != len(raw_blocks):
            raise ValueError("structured zone blocks require unique blockId values")
        if probe.zone_blocks.get(zone_id, set()) != expected_blocks:
            raise ValueError("structured zone output does not match its semantic block plan")
        max_chars = raw_zone.get("maxChars")
        max_items = raw_zone.get("maxItems")
        if not isinstance(max_chars, int) or probe.zone_chars.get(zone_id, 0) > max_chars:
            raise ValueError("structured zone output exceeds maxChars")
        if not isinstance(max_items, int) or probe.zone_items.get(zone_id, 0) > max_items:
            raise ValueError("structured zone output exceeds maxItems")
        text_align = raw_zone.get("textAlign", "inherit")
        if text_align not in {"inherit", "left", "center", "right"}:
            raise ValueError("structured zone has an invalid textAlign")
        selector_id = zone_id.replace("\\", "\\\\").replace('"', '\\"')
        text_align_rule = (
            "" if text_align == "inherit" else f"text-align:{text_align}!important;"
        )
        rules.append(
            f'[data-ai-zone-id="{selector_id}"]{{position:absolute!important;'
            f"left:{x:g}px!important;top:{y:g}px!important;width:{width:g}px!important;"
            f"height:{height:g}px!important;overflow:hidden!important;box-sizing:border-box!important;"
            f"{text_align_rule}}}"
        )
    constraint_style = (
        '<style data-ai-zone-constraints="v1">' + "".join(rules) + "</style>"
    )
    constrained, count = re.subn(
        r"</head\s*>",
        constraint_style + "</head>",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise ValueError("structured zone output has no closing head")
    return constrained


def derive_role(index: int, n: int, content: str) -> str:
    """Per-slide ROLE for the authoring prompt, from position + content keywords."""
    if index == 0:
        return "COVER"
    if index == n - 1:
        return "CLOSING"
    if index == 1:
        return "PROBLEM"
    lowered = content.lower()
    if any(h.lower() in lowered for h in _ARCHITECTURE_HINTS):
        return "ARCHITECTURE"
    if any(h.lower() in lowered for h in _TIMELINE_HINTS):
        return "ROADMAP"
    if any(h.lower() in lowered for h in _METRIC_HINTS):
        return "OUTCOMES"
    return "PILLARS"


def plan_deck_roles(outline: PresentationOutlineModel) -> List[str]:
    """The deck-plan: one ROLE per slide. Single source of role derivation, shared by
    the authoring pass and the vision-QA re-author pass."""
    slides = list(outline.slides)
    n = len(slides)
    return [derive_role(i, n, slides[i].content) for i in range(n)]


async def _author_one_resilient(
    content: str,
    design_system: str,
    brand: Brand,
    role: str,
    index: int,
    n: int,
    reference_images=(),
    *,
    reference_mode: str = STYLE_REFERENCE,
    raise_on_failure: bool = False,
    structured_context: dict[str, Any] | None = None,
    page_number_mode: str = TEMPLATE_PAGE_NUMBER_MODE,
) -> str:
    """Author one slide with bounded retries and a style-mode branded fallback.

    Ordinary invalid/permanent failures retain the original two-attempt behavior.
    Provider overloads and broken streamed responses get two additional attempts with
    exponential backoff so a brief upstream incident does not invalidate the whole deck.
    Exact reconstruction instead fails closed rather than replacing the source slide.
    """
    reference_mode = normalize_reference_mode(reference_mode)
    if reference_mode == EXACT_RECONSTRUCTION and len(reference_images) != 1:
        raise ValueError(
            "Exact reconstruction requires exactly one reference image per slide"
        )
    last_error: object = "invalid HTML"
    attempt_limit = _DEFAULT_AUTHOR_ATTEMPTS
    attempts_made = 0
    for attempt in range(_TRANSIENT_AUTHOR_ATTEMPTS):
        attempts_made = attempt + 1
        try:
            if reference_images:
                kwargs = {"reference_images": reference_images}
                if reference_mode == EXACT_RECONSTRUCTION:
                    kwargs["reference_mode"] = reference_mode
                html = await author_slide_html(
                    content,
                    design_system,
                    brand,
                    role,
                    index,
                    n,
                    structured_context=structured_context,
                    page_number_mode=page_number_mode,
                    **kwargs,
                )
            else:
                kwargs = {}
                if reference_mode == EXACT_RECONSTRUCTION:
                    kwargs["reference_mode"] = reference_mode
                html = await author_slide_html(
                    content,
                    design_system,
                    brand,
                    role,
                    index,
                    n,
                    structured_context=structured_context,
                    page_number_mode=page_number_mode,
                    **kwargs,
                )
            if is_valid_slide_html(html):
                return _constrain_structured_zone_html(html, structured_context)
        except Exception as e:  # noqa: BLE001 — resilience boundary, recorded below
            last_error = e
            if reference_mode == EXACT_RECONSTRUCTION and isinstance(e, ValueError):
                raise
            if _is_transient_author_error(e):
                attempt_limit = _TRANSIENT_AUTHOR_ATTEMPTS

        if attempts_made >= attempt_limit:
            break

        if attempt_limit == _TRANSIENT_AUTHOR_ATTEMPTS:
            delay = min(
                _TRANSIENT_RETRY_BASE_SECONDS * (2**attempt),
                _TRANSIENT_RETRY_MAX_SECONDS,
            )
            logger.warning(
                "[authored] slide %d/%d transient authoring failure; "
                "retrying attempt %d/%d in %.1fs (last error: %s)",
                index + 1,
                n,
                attempts_made + 1,
                attempt_limit,
                delay,
                last_error,
            )
            await asyncio.sleep(delay)

    if (
        raise_on_failure
        or reference_mode == EXACT_RECONSTRUCTION
        or structured_context is not None
    ):
        logger.warning(
            "[authored] slide %d/%d failed after %d attempts; refusing a placeholder "
            "for exact/strict authoring (last error: %s)",
            index + 1,
            n,
            attempts_made,
            last_error,
        )
        if isinstance(last_error, BaseException):
            raise last_error
        raise RuntimeError(str(last_error))
    logger.warning(
        "[authored] slide %d/%d fell back to a branded placeholder after %d attempts "
        "(last error: %s)",
        index + 1,
        n,
        attempts_made,
        last_error,
    )
    return fallback_slide_html(
        content,
        brand,
        role,
        index,
        n,
        page_number_mode=page_number_mode,
    )


def prepare_authored_deck(
    outline: PresentationOutlineModel,
    brand: Brand,
    style: Optional[AuthoredStyleLike] = None,
    *,
    structured_slide_contexts: Sequence[Mapping[str, Any] | None] | None = None,
    page_number_mode: str = TEMPLATE_PAGE_NUMBER_MODE,
) -> AuthoredDeckPlan:
    """Prepare the shared prompt inputs once, before slide-level pipelining starts."""
    slides = list(outline.slides)
    if page_number_mode not in {
        TEMPLATE_PAGE_NUMBER_MODE,
        COMPANY_STANDARD_PAGE_NUMBER_MODE,
    }:
        raise ValueError("unsupported page number mode")
    if structured_slide_contexts is None:
        structured_contexts: tuple[dict[str, Any] | None, ...] = tuple(
            None for _ in slides
        )
    else:
        if len(structured_slide_contexts) != len(slides):
            raise ValueError("structured slide context count must match slide count")
        structured_contexts = tuple(
            (
                dict(value)
                if value is not None and bool(value.get("zones"))
                else None
            )
            for value in structured_slide_contexts
        )
    effective_brand = apply_style_defaults(brand, style)
    roles = tuple(plan_deck_roles(outline))
    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    if reference_mode == EXACT_RECONSTRUCTION:
        sources = tuple(getattr(style, "slide_reference_images", ()) or ())
        if len(sources) != len(slides):
            raise ValueError(
                "Exact reconstruction source count must match the slide count "
                f"({len(sources)} != {len(slides)})"
            )
        resolved_sources = tuple(path.resolve() for path in sources)
        if len(set(resolved_sources)) != len(resolved_sources):
            raise ValueError(
                "Exact reconstruction requires a unique source image per slide"
            )
        missing = [str(path) for path in resolved_sources if not path.is_file()]
        if missing:
            raise ValueError(
                "Exact reconstruction source image is missing: " + ", ".join(missing)
            )
        references = tuple((path,) for path in resolved_sources)
    else:
        references = tuple(
            tuple(reference_images_for_role(style, role)) for role in roles
        )
    return AuthoredDeckPlan(
        contents=tuple(slide.content for slide in slides),
        roles=roles,
        reference_images=references,
        reference_mode=reference_mode,
        brand=effective_brand,
        design_system=build_design_system(
            effective_brand,
            style,
            page_number_mode=page_number_mode,
        ),
        structured_contexts=structured_contexts,
        page_number_mode=page_number_mode,
    )


async def author_planned_slide(
    plan: AuthoredDeckPlan, index: int, *, raise_on_failure: bool = False
) -> str:
    """Author one planned slide; safe to compose with illustration/render stages."""
    return await _author_one_resilient(
        plan.contents[index],
        plan.design_system,
        plan.brand,
        plan.roles[index],
        index,
        len(plan.contents),
        plan.reference_images[index],
        reference_mode=plan.reference_mode,
        raise_on_failure=raise_on_failure,
        structured_context=plan.structured_contexts[index],
        page_number_mode=plan.page_number_mode,
    )


async def author_deck(
    outline: PresentationOutlineModel,
    brand: Brand,
    style: Optional[AuthoredStyleLike] = None,
) -> AuthoredDeckResult:
    """Author every outline slide against the shared design system, with bounded
    concurrency and per-slide resilience. Returns the valid HTML documents (order
    preserved) plus the exact prompt shared with vision-QA. Failed style-reference
    slides fall back to a branded placeholder; exact reconstruction fails closed."""
    plan = prepare_authored_deck(outline, brand, style)
    htmls = await asyncio.gather(
        *[author_planned_slide(plan, i) for i in range(len(plan.contents))]
    )
    return AuthoredDeckResult(htmls=list(htmls), design_system=plan.design_system)


def build_image_pptx(images: List[bytes], out_path: str) -> str:
    """Assemble rendered slide PNGs into a 16:9 PPTX, one full-bleed picture per
    slide (perfect fidelity, opens in PowerPoint). Returns the saved path."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    for img in images:
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(
            io.BytesIO(img), 0, 0, width=prs.slide_width, height=prs.slide_height
        )
    prs.save(out_path)
    return out_path
