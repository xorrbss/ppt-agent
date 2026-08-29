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
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_visual_plan import (
    AuthoredSlideVisualIntent,
    AuthoredVisualPlan,
    StoryKind,
)
from models.presentation_outline_model import PresentationOutlineModel
from utils.authored_header_validation import validate_header_contract
from services.source_images.models import SourceImageCandidate
from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    SlideCapacity,
    aggregate_owner_capacity_violations,
    build_authored_capacity_plan,
    reduce_authored_owner_text_capacity,
)
from utils.llm_calls.author_slide import (
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
    validate_page_number_mode,
)
from utils.authored_styles import reference_images_for_role
from utils.renderer_worker_policy import RendererPolicyError

logger = logging.getLogger(__name__)

_MAX_AUTHOR_CALLS_PER_SLIDE = 2


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
    # The deck's frozen top-area design. None for decks authored before contracts
    # existed and for exact reconstruction, which reproduces a source verbatim.
    header_contract: AuthoredHeaderContract | None = None
    # Per-slide eyebrow prefixes derived from the deck's frozen chapter plan,
    # separator included; an empty tuple is a deck with no chapters. Kept as one
    # value per slide rather than as the plan itself so the authoring layer never
    # has to re-derive which chapter a position falls in.
    chapter_prefixes: Tuple[str | None, ...] = ()
    # Whether each slide may carry a generated illustration, one entry per FINAL
    # outline index. Slides are authored independently and in parallel, so a
    # slide's own prompt cannot see how many siblings already took a picture:
    # steering the count from inside that prompt produced 4/77, then 73/77, then
    # 1/77 illustrated slides on the same real deck. The deck answers it once and
    # every slide is handed its own share here. An empty tuple is a deck with no
    # plan, which keeps the pre-plan behaviour of letting the model decide.
    illustration_slots: Tuple[bool, ...] = ()
    # The caller's own free-text request, repeated into every slide prompt so a
    # slide-level instruction ("no pictures", "keep it text-only") reaches the
    # slide it is about rather than only the deck-scoped planners.
    instructions: Optional[str] = None
    # Deck-wide narrative/silhouette decisions for ordinary Authored HTML.
    # None is intentional for exact reconstruction and structured/template-owned
    # geometry: those modes must not be recomposed by this prompt-only plan.
    visual_plan: AuthoredVisualPlan | None = None
    # Server-frozen text/image envelope.  ``None`` keeps stand-in plans used by
    # older remake/checkpoint callers source-compatible.
    capacity_plan: AuthoredCapacityPlan | None = None


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
_COMPARISON_HINTS = (
    "비교",
    "대비",
    "증감",
    "추이",
    "변화",
    "전년",
    "versus",
    " vs ",
    "trend",
    "compare",
    "comparison",
)
_DATA_FACT_PATTERN = re.compile(
    r"(?<![\w.-])(?:[$€£¥₩]?\d[\d,.]*(?:%|배|명|건|개|원|억|조|일|월|년|분기|x)?)(?![\w.-])",
    re.IGNORECASE,
)
_MARKDOWN_TABLE_DIVIDER = re.compile(
    r"(?m)^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)

_SILHOUETTES: dict[StoryKind, tuple[str, ...]] = {
    "cover": ("cover-asymmetric-hero",),
    "closing": ("closing-single-statement",),
    "section": ("section-full-bleed-index",),
    "agenda": ("agenda-numbered-spine",),
    "editorial": ("editorial-left-rail", "editorial-top-focus"),
    "pillars": ("pillars-asymmetric-cluster", "pillars-numbered-spine"),
    "metric": ("metric-hero-with-evidence", "metric-evidence-rail"),
    "process": ("process-horizontal-rail", "process-stepped-path"),
    "architecture": ("architecture-layered-map", "architecture-core-and-satellites"),
    "image": ("copy-left-image-right", "image-left-copy-right"),
    "dataTable": ("table-main-callout-side", "callout-top-table-below"),
    "dataChart": ("chart-left-insight-right", "insight-left-chart-right"),
}
_SILHOUETTE_FALLBACKS = (
    "editorial-diagonal-balance",
    "editorial-narrow-wide",
    "editorial-wide-narrow",
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


def _requirements_enabled(contract: AuthoredHeaderContract | None) -> bool:
    return contract is not None and contract.requirements.enabled


def _chapter_breadcrumb(contract: AuthoredHeaderContract | None) -> bool:
    """Whether this deck's frozen header asks every eyebrow to open with a chapter."""
    return contract is not None and contract.eyebrow.form == "chapterBreadcrumb"


@dataclass(frozen=True)
class _AuthorValidationCorrection:
    category: str
    hint: str


class AuthoredProviderBudgetExhausted(ValueError):
    """A deterministic author rejection remained after the slide call budget."""

    def __init__(
        self,
        code: str,
        detail: object,
        *,
        rejected_html: str | None = None,
    ) -> None:
        self.code = code
        self.detail = str(detail)
        self.rejected_html = rejected_html
        super().__init__(f"{code}:{self.detail}")


class AuthoredAggregateOwnerCapacityExhausted(AuthoredProviderBudgetExhausted):
    """The aggregate owner envelope still failed after both author calls."""

    def __init__(self, violations: Sequence[str], *, rejected_html: str) -> None:
        self.violations = tuple(str(value) for value in violations)
        super().__init__(
            "authored_aggregate_owner_capacity_exhausted",
            ",".join(self.violations),
            rejected_html=rejected_html,
        )


def _classify_author_validation_error(
    error: object,
    *,
    source_image_ids: Sequence[str] = (),
) -> _AuthorValidationCorrection | None:
    """Classify fail-closed local rejections without weakening their policies."""

    message = str(error)
    if isinstance(error, AuthoredAggregateOwnerCapacityExhausted) or (
        "authored_aggregate_owner_capacity_exceeded:" in message
    ):
        violations = (
            ",".join(error.violations)
            if isinstance(error, AuthoredAggregateOwnerCapacityExhausted)
            else message.partition("authored_aggregate_owner_capacity_exceeded:")[
                2
            ].strip()
        )
        return _AuthorValidationCorrection(
            "aggregate_owner_capacity",
            "The previous candidate exceeded the server-frozen aggregate owner "
            f"envelope ({violations or 'capacity'}). Preserve every title/header "
            "and source-owned QID, role, and text. Keep all limits unchanged; shorten "
            "or consolidate only low-priority body/supporting copy and never remove "
            "more than three such leaf owners.",
        )
    if "authored_source_image_slots_missing" in message:
        return _AuthorValidationCorrection(
            "source_image_slots_missing",
            "The previous candidate omitted mandatory source images. Repair the "
            "slide and place every offered source slot exactly once using "
            "<img data-source-slot-id=\"SLOT_ID\" data-qid=\"unique.image.qid\">. "
            "Do not replace a source slot with a generated illustration.",
        )
    qid_match = re.search(r"\bauthored_semantic_qid_([a-z0-9_]+)(?::([^\s]+))?", message)
    if qid_match is not None and qid_match.group(1) == "missing":
        missing_tag = (qid_match.group(2) or "").strip()
        missing_detail = (
            f" The first rejected element was <{missing_tag}>."
            if missing_tag
            else ""
        )
        return _AuthorValidationCorrection(
            "semantic_qid_missing",
            "The previous candidate omitted edit identities. Repair the supplied document. "
            "Every rendered HTML element except html/head/body/meta/title/style and SVG primitive "
            "internals must have one explicit unique data-qid. This includes section, main, div, "
            "header, footer, h1-h6, p, span, ul, ol, li, table, thead, tbody, tr, th and td. "
            "An img intended for native crop editing must also have a unique data-qid. Preserve "
            "the existing structure and content; do not omit elements to avoid declaring QIDs."
            + missing_detail,
        )
    if qid_match is not None and qid_match.group(1) == "invalid":
        return _AuthorValidationCorrection(
            "semantic_qid_invalid",
            "Replace only invalid data-qid declarations with explicit unique stable tokens "
            "containing letters, numbers, dot, underscore, colon or hyphen and starting with a "
            "letter or number. Preserve all valid QIDs, elements, text and layout.",
        )
    if qid_match is not None and qid_match.group(1) == "duplicate":
        return _AuthorValidationCorrection(
            "semantic_qid_duplicate",
            "Repair duplicate data-qid declarations so every rendered element has its own "
            "explicit unique stable QID. Preserve the first valid owner and change only duplicate "
            "declarations; do not remove elements or rely on the server to invent QIDs.",
        )
    if qid_match is not None:
        return _AuthorValidationCorrection(
            "semantic_qid_contract",
            f"Repair the explicit data-qid declarations to satisfy {qid_match.group(0)}. Preserve "
            "the supplied document's content, structure and valid identities; every edit target "
            "must declare a valid unique stable QID and the server will not generate one.",
        )

    if "images must be base64 raster data URLs" in message:
        offered_id = (
            " ".join(str(source_image_ids[0]).split()).replace('"', "'")
            if len(source_image_ids) == 1
            else ""
        )
        source_rule = (
            f"Use exactly one offered canonical source asset as <img data-asset-id=\"{offered_id}\" "
            "data-qid=\"unique.image.qid\" ...> with no src attribute."
            if offered_id
            else "Remove every img element because no canonical source image was offered."
        )
        return _AuthorValidationCorrection(
            "renderer_image_source",
            "The previous candidate emitted an untrusted ordinary image src. "
            + source_rule
            + " Never emit a filename, URL, relative path or invented data URL in src; the trusted "
            "source-image stage supplies verified bytes after authoring.",
        )
    if "HTML attribute is not allowed: data-native-candidate" in message:
        return _AuthorValidationCorrection(
            "renderer_native_candidate_attribute",
            "Remove every data-native-candidate attribute. Native-candidate is descriptive text; "
            "data-qid is the only edit identity attribute.",
        )

    if isinstance(error, RendererPolicyError):
        property_match = re.fullmatch(
            r"CSS property is not allowed:\s*([a-z0-9_-]+)\s*:\s*(.*)",
            message,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if property_match is not None:
            property_name = property_match.group(1).lower()
            rejected_value = " ".join(property_match.group(2).split())
            declaration = f"{property_name}: {rejected_value}".rstrip()
            visible_content_rule = (
                " The CSS content property and pseudo-element-generated content are forbidden; "
                "represent visible text or decoration with a real span or div carrying its own "
                "data-qid."
                if property_name == "content"
                else " If that declaration or a ::before/::after rule creates visible content or "
                "decoration, represent it with a real span or div carrying its own data-qid."
            )
            return _AuthorValidationCorrection(
                "renderer_css_property",
                f"Remove the disallowed CSS declaration `{declaration}` wherever it occurs. "
                "Preserve the visual intent using only properties already accepted by the renderer; "
                "do not request or assume an allowlist change."
                + visible_content_rule,
            )
        if re.fullmatch(
            r"CSS declaration (?:is )?malformed", message, flags=re.IGNORECASE
        ):
            return _AuthorValidationCorrection(
                "renderer_css_declaration",
                "Repair malformed CSS declarations in every style block and inline style. Each "
                "declaration must have exactly one property/value separator, a non-empty allowed "
                "property name and a valid value; remove stray declaration fragments and keep rule "
                "braces balanced. Preserve all unrelated valid CSS and HTML.",
            )
        element_match = re.fullmatch(
            r"HTML element is not allowed:\s*([a-z0-9:_-]+)",
            message,
            flags=re.IGNORECASE,
        )
        if element_match is not None:
            tag = element_match.group(1).lower()
            replacement = (
                "Replace each <code> element with a styled <span> for inline code or a <div> for "
                "a block, preserving its text and giving the replacement its own data-qid."
                if tag == "code"
                else f"Replace each <{tag}> with an allowed semantic equivalent: span for inline "
                "content or div for a block. Preserve its visible content and give the replacement "
                "its own data-qid."
            )
            return _AuthorValidationCorrection(
                "renderer_html_element",
                replacement + " Do not relax or bypass the renderer element allowlist.",
            )
        return _AuthorValidationCorrection(
            "renderer_policy",
            "Repair the supplied HTML so it satisfies the reported renderer policy rejection. "
            "Preserve compliant content and layout, use only the existing HTML/CSS allowlists and "
            "do not encode the rejected construct another way.",
        )

    return None


def _author_retry_correction(
    error: object,
    *,
    source_image_ids: Sequence[str] = (),
) -> str:
    """Return corrective guidance while preserving the legacy string interface."""

    classified = _classify_author_validation_error(
        error,
        source_image_ids=source_image_ids,
    )
    if classified is not None:
        return classified.hint
    return (
        "The previous candidate failed local admission. Return a complete self-contained HTML "
        "document that follows every renderer, image-slot and data-qid rule exactly."
    )


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


def plan_deck_roles(
    outline: PresentationOutlineModel,
    chapter_plan: AuthoredChapterPlan | None = None,
) -> List[str]:
    """The deck-plan: one ROLE per slide. Single source of role derivation, shared by
    the authoring pass and the vision-QA re-author pass.

    A chapter plan overrides the keyword derivation on the slides it inserted,
    and only on those. Their role is not a guess about their text — the deck
    decided it when it decided to insert them — and letting a divider page whose
    chapter is called "시스템 구성" be derived into ARCHITECTURE would give it a
    content slide's reference image and a content slide's header."""
    slides = list(outline.slides)
    n = len(slides)
    roles = [derive_role(i, n, slides[i].content) for i in range(n)]
    if chapter_plan is not None:
        for index in range(n):
            forced = chapter_plan.role_at(index)
            if forced is not None:
                roles[index] = forced
    return roles


def _has_structured_table(content: str) -> bool:
    lines = [line for line in content.splitlines() if line.count("|") >= 2]
    return len(lines) >= 3 and bool(_MARKDOWN_TABLE_DIVIDER.search(content))


def _story_kind(
    content: str,
    role: str,
    *,
    illustration_allowed: bool,
) -> StoryKind:
    normalized_role = (role or "").strip().upper()
    if normalized_role == "COVER":
        return "cover"
    if normalized_role == "CLOSING":
        return "closing"
    if normalized_role == "SECTION":
        return "section"
    if normalized_role == "AGENDA":
        return "agenda"

    lowered = content.lower()
    # A table is promoted only from a real row/column contract.  Mere wording
    # such as "표로 보여줘" is not structured data and must not become a native
    # table claim or invite the author to invent cells.
    if _has_structured_table(content):
        return "dataTable"
    fact_count = len(_DATA_FACT_PATTERN.findall(content))
    if fact_count >= 3 and (
        normalized_role == "OUTCOMES"
        or any(hint.lower() in lowered for hint in _TIMELINE_HINTS)
        or any(hint in lowered for hint in _COMPARISON_HINTS)
    ):
        return "dataChart"
    if normalized_role == "ARCHITECTURE":
        return "architecture"
    if normalized_role == "ROADMAP":
        return "process"
    if illustration_allowed:
        return "image"
    if normalized_role == "OUTCOMES" or fact_count:
        return "metric"
    if normalized_role == "PILLARS":
        return "pillars"
    return "editorial"


def _hybrid_metadata(story_kind: StoryKind) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidates: tuple[str, ...]
    reasons = ["bespoke_css_and_decorations_require_raster_backplate"]
    if story_kind == "dataTable":
        candidates = ("text", "shape", "table")
        reasons.append("native_table_requires_structured_cell_readback")
    elif story_kind == "dataChart":
        candidates = ("text", "shape", "chart")
        reasons.append("native_chart_requires_structured_series_readback")
    elif story_kind == "image":
        candidates = ("text", "shape", "image")
        reasons.append("unsupported_image_effect_or_mask_requires_raster")
    elif story_kind in {"process", "architecture"}:
        candidates = ("text", "shape")
        reasons.append("complex_connectors_or_groups_require_raster")
    else:
        candidates = ("text", "shape")
    return candidates, tuple(reasons)


def plan_authored_visual_story(
    contents: Sequence[str],
    roles: Sequence[str],
    *,
    illustration_slots: Sequence[bool] = (),
) -> AuthoredVisualPlan:
    """Freeze deck-wide story/silhouette choices before parallel authoring.

    This planner only steers Authored HTML composition.  It does not emit Scene
    geometry and its Hybrid metadata deliberately describes candidates plus the
    raster remainder rather than claiming complete native editability.
    """
    if len(contents) != len(roles):
        raise ValueError("visual plan contents and roles must have matching lengths")
    if illustration_slots and len(illustration_slots) != len(contents):
        raise ValueError("visual plan illustration slots must match slide count")

    planned: list[AuthoredSlideVisualIntent] = []
    for index, (content, role) in enumerate(zip(contents, roles)):
        story_kind = _story_kind(
            content,
            role,
            illustration_allowed=bool(illustration_slots and illustration_slots[index]),
        )
        candidates = _SILHOUETTES[story_kind]
        recent = {item.silhouette for item in planned[-2:]}
        pivot = index % len(candidates)
        ordered = candidates[pivot:] + candidates[:pivot]
        silhouette = next(
            (candidate for candidate in ordered if candidate not in recent),
            next(
                candidate
                for candidate in _SILHOUETTE_FALLBACKS
                if candidate not in recent
            ),
        )
        native_candidates, fallback_reasons = _hybrid_metadata(story_kind)
        planned.append(
            AuthoredSlideVisualIntent(
                slide_index=index,
                role=role,
                story_kind=story_kind,
                silhouette=silhouette,
                hybrid_native_candidates=native_candidates,
                raster_fallback_reasons=fallback_reasons,
            )
        )
    return AuthoredVisualPlan(slides=tuple(planned))


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
    header_contract: AuthoredHeaderContract | None = None,
    chapter_prefix: str | None = None,
    instructions: str | None = None,
    illustration_allowed: bool | None = None,
    visual_intent: AuthoredSlideVisualIntent | None = None,
    # Pictures the user already owns that this slide may place. Empty is the
    # pre-feature request, byte for byte.
    source_images: Sequence[SourceImageCandidate] = (),
    require_all_source_images: bool = False,
    capacity_brief: str | None = None,
    capacity: SlideCapacity | None = None,
    allow_deterministic_owner_reduction: bool = False,
    attempt_observer: Callable[[int], None] | None = None,
) -> str:
    """Author one slide with one explicit correction and a branded fallback.

    Every slide has one initial author call and, only when that candidate fails,
    one correction call.  Validation, provider and permanent failures share this
    same two-call ceiling; none receives a private retry budget. Exact reconstruction
    fails closed rather than replacing the source slide.

    A slide that renders but breaks the deck's header contract is retried like any
    other failure. If the retries run out, the best rendered attempt is still
    returned: a slightly drifted header is worth more than a placeholder slide.
    """
    reference_mode = normalize_reference_mode(reference_mode)
    if reference_mode == EXACT_RECONSTRUCTION and len(reference_images) != 1:
        raise ValueError(
            "Exact reconstruction requires exactly one reference image per slide"
        )
    source_image_ids = tuple(candidate.asset_id for candidate in source_images)
    last_error: object = "invalid HTML"
    attempts_made = 0
    header_reject: str | None = None
    correction_hint: str | None = None
    rejected_html: str | None = None
    while attempts_made < _MAX_AUTHOR_CALLS_PER_SLIDE:
        attempts_made += 1
        if attempt_observer is not None:
            attempt_observer(attempts_made)
        local_correction: _AuthorValidationCorrection | None = None
        try:
            kwargs: dict[str, Any] = {
                "receipt_retry_class": (
                    "initial" if attempts_made == 1 else "recovery"
                ),
                "receipt_attempt_ordinal": attempts_made,
                # author_deck owns the complete two-call budget. An inner
                # multimodal -> text-only provider retry would make it unauditable.
                "allow_provider_fallback": False,
            }
            if reference_images:
                kwargs["reference_images"] = reference_images
            if reference_mode == EXACT_RECONSTRUCTION:
                kwargs["reference_mode"] = reference_mode
            if source_images:
                kwargs["source_images"] = source_images
                kwargs["require_all_source_images"] = require_all_source_images
            if correction_hint:
                kwargs["correction_hint"] = correction_hint
                if rejected_html is not None:
                    kwargs["rejected_html"] = rejected_html
            html = await author_slide_html(
                content,
                design_system,
                brand,
                role,
                index,
                n,
                structured_context=structured_context,
                page_number_mode=page_number_mode,
                requirements_enabled=_requirements_enabled(header_contract),
                chapter_breadcrumb=_chapter_breadcrumb(header_contract),
                chapter_prefix=chapter_prefix,
                instructions=instructions,
                illustration_allowed=illustration_allowed,
                visual_intent=visual_intent,
                capacity_brief=capacity_brief,
                **kwargs,
            )
            if is_valid_slide_html(html):
                constrained = _constrain_structured_zone_html(html, structured_context)
                violations = (
                    validate_header_contract(
                        constrained,
                        header_contract,
                        role=role,
                        content=content,
                    )
                    if header_contract is not None
                    else ()
                )
                if not violations:
                    owner_violations = (
                        aggregate_owner_capacity_violations(constrained, capacity)
                        if capacity is not None
                        else ()
                    )
                    if not owner_violations:
                        return constrained
                    if (
                        allow_deterministic_owner_reduction
                        and capacity is not None
                    ):
                        reduced = reduce_authored_owner_text_capacity(
                            constrained,
                            capacity,
                            profile_id="ordinary",
                            allow_summarization=True,
                        )
                        reduced_violations = aggregate_owner_capacity_violations(
                            reduced,
                            capacity,
                        )
                        if reduced != constrained and not reduced_violations:
                            return reduced
                        if reduced != constrained:
                            constrained = reduced
                            owner_violations = reduced_violations
                    capacity_error = AuthoredAggregateOwnerCapacityExhausted(
                        owner_violations,
                        rejected_html=constrained,
                    )
                    raise capacity_error
                header_reject = constrained
                last_error = "header contract violated: " + ", ".join(violations)
                correction_hint = _author_retry_correction(
                    last_error,
                    source_image_ids=source_image_ids,
                )
                rejected_html = None
                logger.warning(
                    "[authored] slide %d/%d %s (attempt %d)",
                    index + 1,
                    n,
                    last_error,
                    attempts_made,
                )
            else:
                last_error = "invalid HTML"
                correction_hint = _author_retry_correction(last_error)
                rejected_html = None
        except Exception as e:  # noqa: BLE001 — resilience boundary, recorded below
            last_error = e
            local_correction = _classify_author_validation_error(
                e,
                source_image_ids=source_image_ids,
            )
            if local_correction is not None:
                correction_hint = local_correction.hint
                candidate = getattr(e, "rejected_html", None)
                rejected_html = candidate if isinstance(candidate, str) else None
            else:
                correction_hint = _author_retry_correction(
                    e,
                    source_image_ids=source_image_ids,
                )
                rejected_html = None
                if reference_mode == EXACT_RECONSTRUCTION and isinstance(e, ValueError):
                    raise

        if attempts_made < _MAX_AUTHOR_CALLS_PER_SLIDE:
            logger.warning(
                "[authored] slide %d/%d scheduling explicit correction %d/%d "
                "(class=%s; last error: %s)",
                index + 1,
                n,
                attempts_made + 1,
                _MAX_AUTHOR_CALLS_PER_SLIDE,
                (
                    local_correction.category
                    if local_correction is not None
                    else "author_failure"
                ),
                last_error,
            )

    if header_reject is not None and not (
        raise_on_failure
        or reference_mode == EXACT_RECONSTRUCTION
        or structured_context is not None
    ):
        logger.warning(
            "[authored] slide %d/%d kept a header-contract-violating render after "
            "%d attempts (%s)",
            index + 1,
            n,
            attempts_made,
            last_error,
        )
        return header_reject
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
        if (
            isinstance(last_error, AuthoredAggregateOwnerCapacityExhausted)
            and allow_deterministic_owner_reduction
            and capacity is not None
            and last_error.rejected_html is not None
        ):
            reduced = reduce_authored_owner_text_capacity(
                last_error.rejected_html,
                capacity,
                profile_id="ordinary",
                allow_summarization=True,
            )
            if reduced != last_error.rejected_html:
                return reduced
        if isinstance(last_error, AuthoredProviderBudgetExhausted):
            raise last_error
        deterministic_failure = _classify_author_validation_error(
            last_error,
            source_image_ids=source_image_ids,
        )
        if deterministic_failure is not None:
            raise AuthoredProviderBudgetExhausted(
                f"authored_{deterministic_failure.category}_budget_exhausted",
                last_error,
                rejected_html=rejected_html,
            )
        if header_reject is not None:
            raise AuthoredProviderBudgetExhausted(
                "authored_header_contract_budget_exhausted",
                last_error,
                rejected_html=header_reject,
            )
        if not isinstance(last_error, BaseException):
            raise AuthoredProviderBudgetExhausted(
                "authored_invalid_html_budget_exhausted",
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
    header_contract: AuthoredHeaderContract | None = None,
    chapter_plan: AuthoredChapterPlan | None = None,
    illustration_plan: AuthoredIllustrationPlan | None = None,
    instructions: Optional[str] = None,
    composition_profile: CompositionProfile | None = None,
) -> AuthoredDeckPlan:
    """Prepare the shared prompt inputs once, before slide-level pipelining starts.

    ``header_contract`` is the deck's already-decided top-area design. It is
    rendered into ``design_system`` exactly once here, which is what guarantees
    every slide receives the identical header brief.

    ``chapter_plan`` is the deck's already-decided structure. It is resolved
    against the SAME outline this plan is built from, so a caller that expanded
    the outline must hand over the plan that expansion produced; a plan from a
    different outline would move every divider by the number of slides that
    changed.

    ``illustration_plan`` is the deck's already-decided set of illustrated
    slides, indexed into that same final outline. It is flattened to one boolean
    per slide here for the same reason the chapter plan is flattened to one
    prefix per slide: the authoring layer answers "may THIS slide have a
    picture", never "which slides did the deck choose".
    """
    slides = list(outline.slides)
    validate_page_number_mode(page_number_mode)
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
    roles = tuple(plan_deck_roles(outline, chapter_plan))
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
    contents = tuple(slide.content for slide in slides)
    profile = composition_profile or CompositionProfile(
        "ordinary", "authored", True, True, 1
    )
    capacity_plan = build_authored_capacity_plan(contents, roles, profile)
    # Exact reconstruction reproduces a source slide verbatim, so imposing a
    # header design on it would contradict the mode.
    #
    # Structured authoring is the same kind of contradiction, one layer down: the
    # template owns the geometry there, and _constrain_structured_zone_html pins
    # every zone container at the template's own top. A per-slide derived band
    # cannot also be authoritative — a zone pinned at y=140 under a 180px band
    # renders 40px inside the header, and a body wrapper pinned below the band
    # becomes the containing block the zone offsets then resolve against. Only
    # one of the two can decide where content starts, and for these decks it is
    # the template.
    structured = any(context is not None for context in structured_contexts)
    effective_contract = (
        None
        if reference_mode == EXACT_RECONSTRUCTION or structured
        else header_contract
    )
    illustration_slots = (
        tuple(illustration_plan.allows(i) for i in range(len(slides)))
        if illustration_plan is not None
        else ()
    )
    visual_plan = (
        None
        if reference_mode == EXACT_RECONSTRUCTION or structured
        else plan_authored_visual_story(
            contents,
            roles,
            illustration_slots=illustration_slots,
        )
    )
    return AuthoredDeckPlan(
        contents=contents,
        roles=roles,
        reference_images=references,
        reference_mode=reference_mode,
        brand=effective_brand,
        design_system=build_design_system(
            effective_brand,
            style,
            page_number_mode=page_number_mode,
            header_contract=effective_contract,
        ),
        structured_contexts=structured_contexts,
        page_number_mode=page_number_mode,
        header_contract=effective_contract,
        chapter_prefixes=(
            tuple(chapter_plan.eyebrow_prefix_at(i) for i in range(len(slides)))
            if chapter_plan is not None and effective_contract is not None
            else ()
        ),
        illustration_slots=illustration_slots,
        instructions=instructions,
        visual_plan=visual_plan,
        capacity_plan=capacity_plan,
    )


async def author_planned_slide(
    plan: AuthoredDeckPlan,
    index: int,
    *,
    raise_on_failure: bool = False,
    source_images: Sequence[SourceImageCandidate] = (),
    require_all_source_images: bool = False,
    attempt_observer: Callable[[int], None] | None = None,
) -> str:
    """Author one planned slide; safe to compose with illustration/render stages.

    ``source_images`` is the offer for THIS slide only: the pipeline ranks the
    deck's candidates per slide, so the plan stays one immutable deck-wide value.
    """
    # Read defensively: the remake and checkpoint paths build stand-in plan
    # objects that carry only the fields they need, and a missing field here has
    # a well-defined meaning — no plan, so the model decides as it did before.
    illustration_slots = getattr(plan, "illustration_slots", ())
    visual_plan = getattr(plan, "visual_plan", None)
    capacity_plan = getattr(plan, "capacity_plan", None)
    capacity_brief = None
    capacity = None
    if capacity_plan is not None:
        capacity = capacity_plan.at(index).with_source_images(len(source_images))
        capacity_brief = capacity.prompt_brief(capacity_plan.profile)
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
        header_contract=plan.header_contract,
        chapter_prefix=(
            plan.chapter_prefixes[index] if plan.chapter_prefixes else None
        ),
        instructions=getattr(plan, "instructions", None),
        illustration_allowed=(
            illustration_slots[index] if illustration_slots else None
        ),
        visual_intent=(visual_plan.at(index) if visual_plan is not None else None),
        source_images=source_images,
        require_all_source_images=require_all_source_images,
        capacity_brief=capacity_brief,
        capacity=capacity,
        allow_deterministic_owner_reduction=bool(
            capacity_plan is not None
            and capacity_plan.profile.profile_id == "ordinary"
            and capacity_plan.profile.allow_summarization
            and not source_images
        ),
        attempt_observer=attempt_observer,
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
