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
from typing import Any, Awaitable, Callable, List, Mapping, Optional, Sequence, Tuple

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_infographic_plan import (
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
)
from models.authored_visual_plan import (
    AuthoredSlideVisualIntent,
    AuthoredVisualPlan,
    StoryKind,
)
from models.presentation_outline_model import PresentationOutlineModel
from utils.authored_geometry_brief import relaxed_geometry_brief
from utils.authored_header_validation import validate_header_contract
from utils.authored_infographic import CONTRACT_ERROR_PREFIX
from services.source_images.models import SourceImageCandidate
from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    SlideCapacity,
    aggregate_owner_capacity_violations,
    build_authored_capacity_plan,
    canonicalize_authored_capacity_html,
    reduce_authored_owner_text_capacity,
)
from services.authored_generation_planner import AuthoredTextPlan, plan_authored_text
from services.authored_author_capacity_recovery import (
    authored_owner_capacity_diagnostic,
    title_capacity_recovery_boundary,
    title_capacity_recovery_diagnostic,
    title_capacity_recovery_hint,
    resolve_authored_title_capacity_html,
    title_capacity_recovery_qids,
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
from utils.llm_calls.author_batch import (
    AuthoredBatchSlideRequest,
    author_slides_html,
)
from services.authored_batch_policy import (
    advance_authored_batch_policy,
    new_authored_batch_policy_state,
    plan_authored_batch_wave,
    plan_authored_recovery_batches,
)
from utils.authored_styles import reference_images_for_role
from utils.get_env import is_authored_visual_story_plan_enabled
from utils.renderer_worker_policy import RendererPolicyError

logger = logging.getLogger(__name__)

_MAX_AUTHOR_CALLS_PER_SLIDE = 2
_DEFAULT_AUTHOR_SLIDE_HTML = author_slide_html


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
    # Slides the deck's header does not govern because their own source page has
    # none: a cover, a closing page.  Requiring a band there rejects the
    # composition the source page actually is, which cost a 35-slide deck two
    # provider calls and then the deck itself.
    header_exempt_slides: frozenset[int] = frozenset()
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
    # Each slide's frozen figure from the deck's infographic plan, one entry
    # per FINAL outline index. An empty tuple is a deck with no plan, where
    # every slide composes itself as it did before the plan existed.
    infographic_briefs: Tuple[AuthoredSlideInfographic | None, ...] = ()
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
    # Exact title/body/visible-text accounting produced before provider work.
    # It shares the capacity authority above and never mutates source content.
    text_plan: AuthoredTextPlan | None = None

    def header_contract_at(self, index: int) -> AuthoredHeaderContract | None:
        """The contract governing one slide, or None when its source has none.

        The deck's header is one design, and every slide that has a header
        reproduces it exactly.  A page whose own source carries no running
        header and no page number is not such a slide, and asking it for a band
        rejects the composition the source page is.
        """

        if index in self.header_exempt_slides:
            return None
        return self.header_contract


@dataclass(frozen=True)
class AuthoredBatchFailure:
    """One rejected slide from a provider batch, with bounded repair context."""

    error: BaseException
    correction_hint: str
    rejected_html: str | None = None


@dataclass(frozen=True)
class AuthoredPlannedBatchResult:
    """Per-slide outcomes from one physical provider call."""

    htmls: Mapping[int, str]
    failures: Mapping[int, AuthoredBatchFailure]


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
        owner_capacity_diagnostic: dict[str, Any] | None = None,
        title_token_diagnostic: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.detail = str(detail)
        self.rejected_html = rejected_html
        self.owner_capacity_diagnostic = owner_capacity_diagnostic
        self.title_token_diagnostic = title_token_diagnostic
        super().__init__(f"{code}:{self.detail}")


class AuthoredAggregateOwnerCapacityExhausted(AuthoredProviderBudgetExhausted):
    """The aggregate owner envelope still failed after both author calls."""

    def __init__(
        self, violations: Sequence[str], *, rejected_html: str,
        capacity: SlideCapacity | None = None, title_recovery_qids: Sequence[str] = (),
    ) -> None:
        self.violations = tuple(str(value) for value in violations)
        self.title_recovery_qids = tuple(title_recovery_qids)
        self.capacity = capacity
        super().__init__(
            "authored_aggregate_owner_capacity_exhausted",
            ",".join(self.violations),
            rejected_html=rejected_html,
            owner_capacity_diagnostic=(
                authored_owner_capacity_diagnostic(rejected_html, capacity)
                if capacity is not None else None
            ),
        )


def _classify_author_validation_error(
    error: object,
    *,
    source_image_ids: Sequence[str] = (),
) -> _AuthorValidationCorrection | None:
    """Classify fail-closed local rejections without weakening their policies."""

    message = str(error)
    if message.startswith(CONTRACT_ERROR_PREFIX):
        # The checker's message already names each departure from the planned
        # figure, so the retry is told exactly what to restore.
        return _AuthorValidationCorrection(
            "infographic_contract",
            "The figure on this slide departed from its FIGURE CONTRACT: "
            + message[len(CONTRACT_ERROR_PREFIX):].strip()
            + ". Reproduce the FIGURE MARKUP exactly as given (every class, data-qid and "
            "planned label), keep all styling of .ig* elements to the injected library, and "
            "change only what the contract allows. Preserve the rest of the slide.",
        )
    if isinstance(error, AuthoredAggregateOwnerCapacityExhausted) or (
        "authored_aggregate_owner_capacity_exceeded:" in message
    ):
        if isinstance(error, AuthoredAggregateOwnerCapacityExhausted) and error.title_recovery_qids:
            return _AuthorValidationCorrection(
                "aggregate_owner_title_capacity",
                title_capacity_recovery_hint(
                    error.violations, error.title_recovery_qids,
                    rejected_html=error.rejected_html, capacity=error.capacity,
                ),
            )
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


def author_correction_from_stored_failure(error: object) -> str | None:
    """Corrective guidance for a slide a previous operation attempt failed.

    The durable checkpoint keeps the reason the slide was rejected -- a header
    contract violation, missing source text, a capacity overflow.  Re-sending
    the identical prompt on the next attempt reproduces the same rejection, so
    the reason is replayed as the correction the in-slide retry would have
    used.  Unknown shapes yield ``None`` and the attempt starts clean.
    """

    if not isinstance(error, Mapping):
        return None
    parts: list[str] = []
    for candidate in (error, error.get("prior_failure")):
        if not isinstance(candidate, Mapping):
            continue
        cause = str(candidate.get("cause") or "").strip()
        code = str(candidate.get("code") or "").strip()
        detail = cause or str(candidate.get("message") or "").strip()
        if detail and detail not in parts and not detail.startswith("Authored slide"):
            parts.append(detail)
        elif code and code not in parts and not detail:
            parts.append(code)
    if not parts:
        return None
    return (
        "A previous attempt at this slide was rejected: "
        + "; ".join(parts[:2])[:400]
        + ". Fix exactly that and keep every other rule."
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


#: Which story a planned figure tells, for the archetypes whose shape the
#: story rules already describe. Every other archetype keeps the deterministic
#: story below; the figure brief itself says how it is drawn.
_ARCHETYPE_STORY: dict[str, StoryKind] = {
    "process": "process",
    "cycle": "process",
    "timeline": "process",
    "roadmap": "process",
    "swimlane": "process",
    "branch": "process",
    "hierarchy": "architecture",
    "layers": "architecture",
    "hub_spoke": "architecture",
    "kpi": "metric",
    "gauge": "metric",
}


def _story_kind(
    content: str,
    role: str,
    *,
    illustration_allowed: bool,
    infographic: AuthoredSlideInfographic | None = None,
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
    if infographic is not None and infographic.archetype in _ARCHETYPE_STORY:
        return _ARCHETYPE_STORY[infographic.archetype]

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
    infographic_briefs: Sequence[AuthoredSlideInfographic | None] = (),
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
    if infographic_briefs and len(infographic_briefs) != len(contents):
        raise ValueError("visual plan infographic briefs must match slide count")

    planned: list[AuthoredSlideVisualIntent] = []
    for index, (content, role) in enumerate(zip(contents, roles, strict=True)):
        story_kind = _story_kind(
            content,
            role,
            illustration_allowed=bool(illustration_slots and illustration_slots[index]),
            infographic=(infographic_briefs[index] if infographic_briefs else None),
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
    infographic: AuthoredSlideInfographic | None = None,
    # Pictures the user already owns that this slide may place. Empty is the
    # pre-feature request, byte for byte.
    source_images: Sequence[SourceImageCandidate] = (),
    require_all_source_images: bool = False,
    capacity_brief: str | None = None,
    capacity: SlideCapacity | None = None,
    allow_deterministic_owner_reduction: bool = False,
    allow_title_capacity_recovery: bool = False,
    attempt_observer: Callable[[int], None] | None = None,
    provider_attempt_offset: int = 0,
    attempt_reserver: Callable[[int], Awaitable[None]] | None = None,
    prior_failure_hint: str | None = None,
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
    if isinstance(provider_attempt_offset, bool) or provider_attempt_offset not in {0, 1}:
        raise ValueError("authored provider attempt offset must be 0 or 1")
    attempts_made = 0
    header_reject: str | None = None
    # A correction learned in an earlier attempt of the same operation. Without
    # it a retry that exists to redo one failed slide re-sends the prompt that
    # already failed and reproduces the same rejection.
    correction_hint: str | None = prior_failure_hint or None
    rejected_html: str | None = None
    title_recovery: tuple[str, tuple[str, ...]] | None = None
    while provider_attempt_offset + attempts_made < _MAX_AUTHOR_CALLS_PER_SLIDE:
        attempts_made += 1
        provider_attempt_ordinal = provider_attempt_offset + attempts_made
        if attempt_reserver is not None:
            # This durable reservation precedes every provider/admission await.
            # Cancellation or process death after it cannot reopen the ordinal.
            await attempt_reserver(provider_attempt_ordinal)
        if attempt_observer is not None:
            attempt_observer(provider_attempt_ordinal)
        local_correction: _AuthorValidationCorrection | None = None
        try:
            kwargs: dict[str, Any] = {
                "receipt_retry_class": (
                    "initial" if provider_attempt_ordinal == 1 else "recovery"
                ),
                "receipt_attempt_ordinal": provider_attempt_ordinal,
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
                infographic=infographic,
                capacity_brief=capacity_brief,
                **kwargs,
            )
            if is_valid_slide_html(html):
                if title_recovery is not None:
                    boundary = title_capacity_recovery_boundary(
                        title_recovery[0], html, title_recovery[1]
                    )
                    if boundary:
                        raise AuthoredProviderBudgetExhausted(
                            "authored_title_recovery_boundary_exhausted", ",".join(boundary),
                            rejected_html=html,
                            owner_capacity_diagnostic=(
                                authored_owner_capacity_diagnostic(html, capacity)
                                if capacity is not None else None
                            ),
                            title_token_diagnostic=(
                                title_capacity_recovery_diagnostic(title_recovery[0], html, title_recovery[1])
                                if boundary == ("title_protected_token_change",) else None
                            ),
                        )
                constrained = _constrain_structured_zone_html(html, structured_context)
                if capacity is not None:
                    # Header targets may not yet have QIDs. Count and reduce
                    # the same owner namespace that the render pipeline seals.
                    constrained = canonicalize_authored_capacity_html(constrained)
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
                    title_qids = (
                        title_capacity_recovery_qids(constrained, instructions=instructions)
                        if allow_title_capacity_recovery and not source_images
                        and reference_mode != EXACT_RECONSTRUCTION and structured_context is None
                        and any(item.startswith("owner_title:") for item in owner_violations)
                        else ()
                    )
                    if title_qids and capacity is not None:
                        # Substituting a larger compatible layout or applying the
                        # closed abbreviation table costs no provider call, so it
                        # runs before spending the single guarded recovery call.
                        # A terminal result keeps that call as the fallback.
                        title_result = resolve_authored_title_capacity_html(
                            constrained, capacity, title_qids,
                        )
                        if title_result["status"] != "terminal":
                            recovered_html = title_result["html"]
                            recovered_violations = aggregate_owner_capacity_violations(
                                recovered_html, capacity,
                            )
                            if not recovered_violations:
                                return recovered_html
                            if not any(
                                item.startswith("owner_title:")
                                for item in recovered_violations
                            ):
                                # Titles now fit; only non-title owners remain, and
                                # they never spend the title recovery call.
                                constrained = recovered_html
                                owner_violations = recovered_violations
                                title_qids = ()
                    capacity_error = AuthoredAggregateOwnerCapacityExhausted(
                        owner_violations,
                        rejected_html=constrained,
                        capacity=capacity,
                        title_recovery_qids=title_qids,
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
            # Admission hard limits are terminal operation contracts. Spending
            # the same slide's correction after rejection obscures the cause.
            recovery_allowed = getattr(e, "recovery_allowed", False) is True
            recovery_remaining = (
                provider_attempt_offset + attempts_made
                < _MAX_AUTHOR_CALLS_PER_SLIDE
            )
            if getattr(e, "retryable", None) is False and not (
                recovery_allowed and recovery_remaining
            ):
                raise
            local_correction = _classify_author_validation_error(
                e,
                source_image_ids=source_image_ids,
            )
            if local_correction is not None:
                correction_hint = local_correction.hint
                candidate = getattr(e, "rejected_html", None)
                rejected_html = candidate if isinstance(candidate, str) else None
                qids = getattr(e, "title_recovery_qids", ())
                title_recovery = (
                    (rejected_html, qids)
                    if isinstance(e, AuthoredAggregateOwnerCapacityExhausted)
                    and rejected_html is not None
                    and any(item.startswith("owner_title:") for item in e.violations)
                    else None
                )
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
    header_exempt_slides: frozenset[int] | None = None,
    chapter_plan: AuthoredChapterPlan | None = None,
    illustration_plan: AuthoredIllustrationPlan | None = None,
    infographic_plan: AuthoredInfographicPlan | None = None,
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
    # A relaxed envelope states no budget, so there is no text plan to freeze
    # against it; the prompt then carries neither brief.
    text_plan = (
        plan_authored_text(contents, capacity_plan)
        if _envelope_enforced(capacity_plan)
        else None
    )
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
    # The figure plan is flattened to one brief per slide for the same reason
    # the illustration plan is flattened to one boolean: the authoring layer
    # answers "what is THIS slide drawn as", never "what did the deck choose".
    # Exact reconstruction and template-owned geometry take no figure brief:
    # the first reproduces a source, the second is drawn by the template.
    infographic_briefs = (
        tuple(infographic_plan.at(i) for i in range(len(slides)))
        if infographic_plan is not None
        and reference_mode != EXACT_RECONSTRUCTION
        and not structured
        else ()
    )
    visual_plan = (
        plan_authored_visual_story(
            contents,
            roles,
            illustration_slots=illustration_slots,
            infographic_briefs=infographic_briefs,
        )
        if _visual_story_plan_applies(reference_mode, structured=structured)
        else None
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
        header_exempt_slides=frozenset(header_exempt_slides or ()),
        chapter_prefixes=(
            tuple(chapter_plan.eyebrow_prefix_at(i) for i in range(len(slides)))
            if chapter_plan is not None and effective_contract is not None
            else ()
        ),
        illustration_slots=illustration_slots,
        infographic_briefs=infographic_briefs,
        instructions=instructions,
        visual_plan=visual_plan,
        capacity_plan=capacity_plan,
        text_plan=text_plan,
    )


def _planned_slide_capacity_inputs(
    plan: AuthoredDeckPlan,
    index: int,
    source_images: Sequence[SourceImageCandidate],
) -> tuple[SlideCapacity | None, str | None]:
    capacity_plan = getattr(plan, "capacity_plan", None)
    if capacity_plan is None:
        return None, None
    capacity = capacity_plan.at(index).with_source_images(len(source_images))
    return capacity, _capacity_brief(plan, index, capacity)


def _visual_story_plan_applies(reference_mode: str, *, structured: bool) -> bool:
    """Whether the deck freezes a story kind and silhouette per slide.

    Off unless ``AUTHORED_VISUAL_STORY_PLAN_ENABLED`` is set (2026-09-05). The
    plan's silhouettes (``process-stepped-path``, ``editorial-diagonal-balance``)
    and its metric-dominance rule, fired by any count in the content, drew
    stair-stepped roadmaps with detached arrows, diagonally cascading cards and
    counts rendered as segmented pill bars; the user rejected all three against
    the August decks, which predate the plan. Exact reconstruction and
    structured authoring never carried one.
    """
    if reference_mode == EXACT_RECONSTRUCTION or structured:
        return False
    return is_authored_visual_story_plan_enabled()


def _envelope_enforced(capacity_plan: AuthoredCapacityPlan) -> bool:
    """Whether any slide of this plan states an envelope to the model."""
    return any(slide.enforced for slide in capacity_plan.slides)


def _capacity_brief(
    plan: AuthoredDeckPlan, index: int, capacity: SlideCapacity
) -> str | None:
    """The envelope/text-plan brief for one slide, or a GEOMETRY-only one.

    A relaxed capacity has no budget brief of its own and its plan froze no
    text budget; instead of a blank result it now gets the GEOMETRY-only
    brief from ``relaxed_geometry_brief`` (frame/footer-line/overlap/header
    rules, no character or qid counts).
    """
    capacity_plan = getattr(plan, "capacity_plan", None)
    if capacity_plan is None:
        return None
    if not capacity.enforced:
        return relaxed_geometry_brief(plan, index)
    brief = capacity.prompt_brief(capacity_plan.profile)
    if not brief:
        return None
    text_plan = getattr(plan, "text_plan", None)
    if text_plan is not None:
        brief += " " + text_plan.at(index, capacity=capacity).prompt_brief()
    return brief


def _planned_batch_request(
    plan: AuthoredDeckPlan,
    index: int,
    *,
    source_images: Sequence[SourceImageCandidate],
    require_all_source_images: bool,
    failure: AuthoredBatchFailure | None,
) -> AuthoredBatchSlideRequest:
    illustration_slots = getattr(plan, "illustration_slots", ())
    infographic_briefs = getattr(plan, "infographic_briefs", ())
    visual_plan = getattr(plan, "visual_plan", None)
    _capacity, capacity_brief = _planned_slide_capacity_inputs(
        plan, index, source_images
    )
    return AuthoredBatchSlideRequest(
        index=index,
        content=plan.contents[index],
        role=plan.roles[index],
        reference_images=plan.reference_images[index],
        reference_mode=plan.reference_mode,
        structured_context=plan.structured_contexts[index],
        page_number_mode=plan.page_number_mode,
        requirements_enabled=_requirements_enabled(plan.header_contract_at(index)),
        chapter_breadcrumb=_chapter_breadcrumb(plan.header_contract_at(index)),
        chapter_prefix=(
            plan.chapter_prefixes[index] if plan.chapter_prefixes else None
        ),
        instructions=getattr(plan, "instructions", None),
        illustration_allowed=(
            illustration_slots[index] if illustration_slots else None
        ),
        visual_intent=(visual_plan.at(index) if visual_plan is not None else None),
        infographic=(infographic_briefs[index] if infographic_briefs else None),
        capacity_brief=capacity_brief,
        source_images=tuple(source_images),
        require_all_source_images=require_all_source_images,
        correction_hint=(failure.correction_hint if failure is not None else None),
        rejected_html=(failure.rejected_html if failure is not None else None),
    )


def _validate_planned_batch_candidate(
    plan: AuthoredDeckPlan,
    index: int,
    html: str,
    *,
    source_images: Sequence[SourceImageCandidate],
    prior_failure: AuthoredBatchFailure | None,
    recovery: bool,
    raise_on_failure: bool,
) -> str:
    capacity_plan = getattr(plan, "capacity_plan", None)
    capacity, _brief = _planned_slide_capacity_inputs(plan, index, source_images)
    if (
        recovery
        and prior_failure is not None
        and isinstance(prior_failure.error, AuthoredAggregateOwnerCapacityExhausted)
        and prior_failure.rejected_html is not None
        and prior_failure.error.title_recovery_qids
    ):
        boundary = title_capacity_recovery_boundary(
            prior_failure.rejected_html,
            html,
            prior_failure.error.title_recovery_qids,
        )
        if boundary:
            raise AuthoredProviderBudgetExhausted(
                "authored_title_recovery_boundary_exhausted",
                ",".join(boundary),
                rejected_html=html,
            )

    constrained = _constrain_structured_zone_html(
        html, plan.structured_contexts[index]
    )
    if capacity is not None:
        constrained = canonicalize_authored_capacity_html(constrained)
    slide_contract = plan.header_contract_at(index)
    header_violations = (
        validate_header_contract(
            constrained,
            slide_contract,
            role=plan.roles[index],
            content=plan.contents[index],
        )
        if slide_contract is not None
        else ()
    )
    if header_violations:
        # Preserve the historical non-strict final fallback: after the one
        # correction, a usable render with only header drift is preferable to a
        # placeholder. Product generation passes raise_on_failure=True and
        # therefore remains fail closed.
        if recovery and not (
            raise_on_failure
            or plan.reference_mode == EXACT_RECONSTRUCTION
            or plan.structured_contexts[index] is not None
        ):
            return constrained
        error = ValueError(
            "header contract violated: " + ", ".join(header_violations)
        )
        error.rejected_html = constrained
        raise error

    owner_violations = (
        aggregate_owner_capacity_violations(constrained, capacity)
        if capacity is not None
        else ()
    )
    if not owner_violations:
        return constrained
    allow_reduction = bool(
        capacity_plan is not None
        and capacity_plan.profile.profile_id == "ordinary"
        and capacity_plan.profile.allow_summarization
        and not source_images
    )
    if allow_reduction and capacity is not None:
        reduced = reduce_authored_owner_text_capacity(
            constrained,
            capacity,
            profile_id="ordinary",
            allow_summarization=True,
        )
        reduced_violations = aggregate_owner_capacity_violations(reduced, capacity)
        if reduced != constrained and not reduced_violations:
            return reduced
        if reduced != constrained:
            constrained = reduced
            owner_violations = reduced_violations
    raise AuthoredAggregateOwnerCapacityExhausted(
        owner_violations,
        rejected_html=constrained,
        capacity=capacity,
        title_recovery_qids=(
            title_capacity_recovery_qids(
                constrained,
                instructions=getattr(plan, "instructions", None),
            )
            if capacity_plan is not None
            and capacity_plan.profile.profile_id == "ordinary"
            and capacity_plan.profile.allow_summarization
            and not source_images
            and plan.reference_mode != EXACT_RECONSTRUCTION
            and plan.structured_contexts[index] is None
            and any(item.startswith("owner_title:") for item in owner_violations)
            else ()
        ),
    )


def _batch_failure(error: BaseException) -> AuthoredBatchFailure:
    correction = _classify_author_validation_error(error, source_image_ids=())
    hint = (
        correction.hint
        if correction is not None
        else _author_retry_correction(error, source_image_ids=())
    )
    rejected = getattr(error, "rejected_html", None)
    return AuthoredBatchFailure(
        error=error,
        correction_hint=hint,
        rejected_html=rejected if isinstance(rejected, str) else None,
    )


async def author_planned_batch(
    plan: AuthoredDeckPlan,
    indexes: Sequence[int],
    *,
    source_images_by_index: Mapping[int, Sequence[SourceImageCandidate]] | None = None,
    require_all_source_images: bool = False,
    recovery: bool = False,
    prior_failures: Mapping[int, AuthoredBatchFailure] | None = None,
    raise_on_failure: bool = False,
    attempt_observers: Mapping[int, Callable[[int], None]] | None = None,
    attempt_reservers: Mapping[int, Callable[[int], Awaitable[None]]] | None = None,
) -> AuthoredPlannedBatchResult:
    """Author one to three planned slides with one fenced provider call."""

    selected = tuple(indexes)
    if not 1 <= len(selected) <= 3 or len(set(selected)) != len(selected):
        raise ValueError("planned authored batch must contain one to three unique slides")
    ordinal = 2 if recovery else 1
    # Every included slide owns the physical call before provider admission.
    # Reserving all ordinals first makes cancellation/process death fail closed
    # instead of allowing a subset to reopen the same billable attempt.
    for index in selected:
        reserver = (attempt_reservers or {}).get(index)
        if reserver is not None:
            await reserver(ordinal)
        observer = (attempt_observers or {}).get(index)
        if observer is not None:
            observer(ordinal)

    sources = source_images_by_index or {}
    requests = tuple(
        _planned_batch_request(
            plan,
            index,
            source_images=tuple(sources.get(index, ())),
            require_all_source_images=require_all_source_images,
            failure=(prior_failures or {}).get(index),
        )
        for index in selected
    )
    response = await author_slides_html(
        requests,
        design_system=plan.design_system,
        brand=plan.brand,
        slide_count=len(plan.contents),
        recovery=recovery,
    )
    htmls: dict[int, str] = {}
    failures: dict[int, AuthoredBatchFailure] = {
        index: _batch_failure(error) for index, error in response.errors.items()
    }
    for index, candidate in response.htmls.items():
        try:
            htmls[index] = _validate_planned_batch_candidate(
                plan,
                index,
                candidate,
                source_images=tuple(sources.get(index, ())),
                prior_failure=(prior_failures or {}).get(index),
                recovery=recovery,
                raise_on_failure=raise_on_failure,
            )
        except BaseException as error:  # slide-only validation boundary
            failures[index] = _batch_failure(error)
    return AuthoredPlannedBatchResult(htmls=htmls, failures=failures)


async def author_planned_slide(
    plan: AuthoredDeckPlan,
    index: int,
    *,
    raise_on_failure: bool = False,
    source_images: Sequence[SourceImageCandidate] = (),
    require_all_source_images: bool = False,
    attempt_observer: Callable[[int], None] | None = None,
    provider_attempt_offset: int = 0,
    attempt_reserver: Callable[[int], Awaitable[None]] | None = None,
    prior_failure_hint: str | None = None,
) -> str:
    """Author one planned slide; safe to compose with illustration/render stages.

    ``source_images`` is the offer for THIS slide only: the pipeline ranks the
    deck's candidates per slide, so the plan stays one immutable deck-wide value.
    """
    # Read defensively: the remake and checkpoint paths build stand-in plan
    # objects that carry only the fields they need, and a missing field here has
    # a well-defined meaning — no plan, so the model decides as it did before.
    illustration_slots = getattr(plan, "illustration_slots", ())
    infographic_briefs = getattr(plan, "infographic_briefs", ())
    visual_plan = getattr(plan, "visual_plan", None)
    capacity_plan = getattr(plan, "capacity_plan", None)
    capacity_brief = None
    capacity = None
    if capacity_plan is not None:
        capacity = capacity_plan.at(index).with_source_images(len(source_images))
        capacity_brief = _capacity_brief(plan, index, capacity)
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
        header_contract=plan.header_contract_at(index),
        chapter_prefix=(
            plan.chapter_prefixes[index] if plan.chapter_prefixes else None
        ),
        instructions=getattr(plan, "instructions", None),
        illustration_allowed=(
            illustration_slots[index] if illustration_slots else None
        ),
        visual_intent=(visual_plan.at(index) if visual_plan is not None else None),
        infographic=(infographic_briefs[index] if infographic_briefs else None),
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
        allow_title_capacity_recovery=bool(
            capacity_plan is not None
            and capacity_plan.profile.profile_id == "ordinary"
            and capacity_plan.profile.allow_summarization
        ),
        attempt_observer=attempt_observer,
        provider_attempt_offset=provider_attempt_offset,
        attempt_reserver=attempt_reserver,
        prior_failure_hint=prior_failure_hint,
    )


async def author_deck(
    outline: PresentationOutlineModel,
    brand: Brand,
    style: Optional[AuthoredStyleLike] = None,
) -> AuthoredDeckResult:
    """Author a deck in adaptive waves of profile-sized, one-call slide batches."""
    plan = prepare_authored_deck(outline, brand, style)
    if author_slide_html is not _DEFAULT_AUTHOR_SLIDE_HTML:
        # Preserve the established single-slide injection seam used by offline
        # fixtures; the unmodified product callable always takes the batch path.
        htmls = await asyncio.gather(
            *[author_planned_slide(plan, index) for index in range(len(plan.contents))]
        )
        return AuthoredDeckResult(
            htmls=list(htmls), design_system=plan.design_system
        )
    html_by_index: dict[int, str] = {}
    failures: dict[int, AuthoredBatchFailure] = {}
    pending = list(range(len(plan.contents)))
    policy = new_authored_batch_policy_state()
    while pending:
        wave = plan_authored_batch_wave(pending, policy)
        outcomes = await asyncio.gather(
            *(
                author_planned_batch(plan, batch.slide_indices)
                for batch in wave.batches
            ),
            return_exceptions=True,
        )
        consumed: set[int] = set()
        for batch, outcome in zip(wave.batches, outcomes, strict=True):
            consumed.update(batch.slide_indices)
            if isinstance(outcome, BaseException):
                policy = advance_authored_batch_policy(
                    policy, succeeded=False, error=outcome
                )
                failures.update(
                    {index: _batch_failure(outcome) for index in batch.slide_indices}
                )
                continue
            policy = advance_authored_batch_policy(
                policy, succeeded=bool(outcome.htmls)
            )
            html_by_index.update(outcome.htmls)
            failures.update(outcome.failures)
        pending = [index for index in pending if index not in consumed]

    recovery_batches = plan_authored_recovery_batches(
        failures,
        successful_slide_indices=html_by_index,
        profile=policy.profile,
    )
    for offset in range(0, len(recovery_batches), policy.concurrency):
        window = recovery_batches[offset : offset + policy.concurrency]
        outcomes = await asyncio.gather(
            *(
                author_planned_batch(
                    plan,
                    batch.slide_indices,
                    recovery=True,
                    prior_failures=failures,
                )
                for batch in window
            ),
            return_exceptions=True,
        )
        for batch, outcome in zip(window, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                policy = advance_authored_batch_policy(
                    policy, succeeded=False, error=outcome
                )
                for index in batch.slide_indices:
                    failures[index] = _batch_failure(outcome)
                continue
            policy = advance_authored_batch_policy(
                policy, succeeded=bool(outcome.htmls)
            )
            html_by_index.update(outcome.htmls)
            for index in outcome.htmls:
                failures.pop(index, None)
            failures.update(outcome.failures)

    for index, failure in sorted(failures.items()):
        if plan.reference_mode == EXACT_RECONSTRUCTION:
            raise failure.error
        html_by_index[index] = fallback_slide_html(
            plan.contents[index],
            plan.brand,
            plan.roles[index],
            index,
            len(plan.contents),
            page_number_mode=plan.page_number_mode,
        )
    return AuthoredDeckResult(
        htmls=[html_by_index[index] for index in range(len(plan.contents))],
        design_system=plan.design_system,
    )


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
