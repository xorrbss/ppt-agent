"""Authored-mode core: have the configured LLM AUTHOR a complete, self-contained
1280x720 HTML slide (bespoke layout) within a shared design system — instead of
filling a fixed React archetype. This is the crux of the high-quality "authored"
generation mode (vs. the fast default adaptive/template path). Provider-agnostic:
one plain `client.generate` text call against the selected provider (codex /
anthropic / ...). Opt-in — never on the fast deterministic default path."""

import asyncio
import functools
import html as _html
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional, Protocol, Sequence

from llmai import get_client
from llmai.shared import ImageContentPart, SystemMessage, TextContentPart, UserMessage

from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import get_llm_config
from utils.llm_provider import get_model, is_codex_selected
from utils.llm_utils import extract_text, get_generate_kwargs

logger = logging.getLogger(__name__)

# A bespoke full-bleed HTML slide with inline CSS is large; give the model room so
# the document is never truncated mid-markup (truncation => broken render).
AUTHOR_MAX_TOKENS = 16000
REFERENCE_IMAGE_MAX_BYTES = 8 * 1024 * 1024


def _env_int(name: str, default: int) -> int:
    """Positive-int env override, falling back to `default` on unset/blank/invalid."""
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# Per-slide authoring makes a blocking provider call (~75s of model reasoning). Run it
# in a DEDICATED thread pool — NOT asyncio's shared default executor — so a deck's dozen
# concurrent authoring threads can't starve unrelated blocking work (other LLM calls,
# exports) that also relies on the default executor. The pool size IS the process-wide
# authoring concurrency cap: every caller (author_deck's first pass AND the vision-QA
# re-author) funnels through author_slide_html, so excess calls queue here instead of
# fanning out to hundreds of provider requests at once. Tune via AUTHORED_AUTHOR_CONCURRENCY.
AUTHOR_CONCURRENCY = _env_int("AUTHORED_AUTHOR_CONCURRENCY", 12)
_AUTHOR_EXECUTOR = ThreadPoolExecutor(
    max_workers=AUTHOR_CONCURRENCY, thread_name_prefix="author-slide"
)

# Appendix A — design-system brief (the crux IP). The non-negotiable rendering,
# fit, language and output constraints stay in this common template. The visual
# direction lives in a replaceable style brief so authored presets can vary the
# deck without weakening those constraints.
_DESIGN_SYSTEM_RULES = """\
DESIGN SYSTEM (obey on EVERY slide so the deck is cohesive):
- Brand primary colour: {primary}. Brand fonts: {fonts}. Deck language: {language}.
- Canvas: a single 16:9 slide that renders at EXACTLY 1280x720px. Inline <style> only, no
  external JS. Body 1280x720, margin 0, overflow hidden.
{style_brief}
- FORBIDDEN: default round bullet dots; identical heavy-shadow boxed cards on every item;
  clutter; lorem/placeholder; text overflowing/clipping the frame; low contrast; off-palette hues.
- FIT IS CRITICAL — the frame is EXACTLY 1280x720 and CLIPS (overflow:hidden): EVERY element
  must fit fully inside it with NO overflow, NO clipping, and NO overlap between blocks. This is
  the single most common failure — prioritise it over visual ambition. Use less/shorter text and
  generous spacing rather than cramming.
- Heading sizing: size headings to the ACTUAL text length so they never exceed their area. For a
  long heading, REDUCE the font-size (e.g. `clamp()` or a smaller fixed size) instead of letting
  it grow extra lines that collide with the block below. Budget vertical space for the real number
  of wrapped lines.
- No fragile positioning: do NOT hard-pin a block to a fixed top/absolute offset that a longer
  heading could overlap. Prefer normal document flow (flex/grid column with gaps); reserve
  absolute positioning for decorative layers only, and keep clear gaps between text blocks.
- Korean/CJK: set `word-break: keep-all` AND `overflow-wrap: anywhere` on text containers so
  words are never split mid-word across lines.
- Boxes/cards/labels: each one's text must fit ENTIRELY inside it with padding — never let text
  touch or clip an edge. Size the box to its content (or shrink the content to the box).
- All visible text in {language}.
Return ONLY the complete HTML document."""

_DEFAULT_STYLE_BRIEF = """\
- Fonts: load the brand fonts ({fonts}) from Google Fonts; headings 800-900 weight, body 400-500.
- Palette: build everything from the brand primary ({primary}) + neutrals (ink #0F172A, muted
  #64748B, hairline #E2E8F0, surface #F8FAFC, white). The primary is the ONE accent; no other hues.
- Margins ~64px (except intentional full-bleed colour zones).
- Consistent eyebrow: UPPERCASE, letter-spaced 0.15em, ~13px, primary, with a short tick/line.
- Consistent footer on content slides: small muted slide marker left + a small wordmark right.
- Strong, confident type hierarchy; make the single most important element dominant.
- Allowed (vary per slide): full-bleed/partial colour-blocked zones, asymmetric grids, a left
  sidebar, oversized numerals, thin rules, subtle tints of the primary, inline-SVG icons/charts."""


@dataclass
class Brand:
    """The shared design tokens injected into every slide of one deck."""

    topic: str
    language: str = "Korean"
    primary: str = "#2563EB"
    fonts: str = "Noto Sans KR"
    wordmark: str = ""
    primary_is_explicit: bool = False
    fonts_are_explicit: bool = False


class AuthoredStyleLike(Protocol):
    """The intentionally small interface supplied by ``utils.authored_styles``."""

    id: str
    brief: str
    preview_accent: str
    primary_color: Optional[str]
    background_color: Optional[str]
    heading_font: Optional[str]
    body_font: Optional[str]
    mono_font: Optional[str]
    illustration_prompt: Optional[str]
    reference_images: Mapping[str, tuple[Path, ...]]


# Appended to the style brief only for illustration-enabled styles. The image is
# generated AFTER authoring (utils/authored_illustrations.py fills the slot), so
# the model designs the composition and all labels/callouts in HTML around a
# reserved area — text inside generated images is unreliable (especially Korean),
# HTML text is always crisp and correct.
_ILLUSTRATION_SLOT_RULES = """\
- ILLUSTRATION SLOT: you MAY include AT MOST ONE bespoke illustration placeholder:
  `<img data-illust-prompt="..." style="...">`. In data-illust-prompt, describe the
  desired SCENE in English (concrete objects, composition, viewpoint) — subject matter
  only; the visual style is applied automatically. The image will contain NO text, so
  put every label, callout and annotation in HTML positioned around/over the slot.
  Reserve the slot's exact area with explicit width/height (or absolute inset) styles
  so the layout holds even if the image is dropped. Do not reference any other image
  (no external URLs, no data URIs)."""


def apply_style_defaults(brand: Brand, style: Optional[AuthoredStyleLike]) -> Brand:
    """Apply preset brand defaults without overriding explicit request values.

    Most style presets describe their palette in prose rather than a structured
    ``primary_color`` token, but every preset carries a ``preview_accent`` (the
    signal colour shown in the picker swatch). Falling back to it keeps the deck's
    ``Brand primary colour`` line and the persisted ``theme.primary`` consistent
    with the style's palette instead of leaving the generic brand blue, which would
    contradict the brief the model is authoring from.
    """
    if style is None or style.id == "default":
        return brand
    primary = brand.primary
    fonts = brand.fonts
    if not brand.primary_is_explicit:
        primary = (
            getattr(style, "primary_color", None)
            or getattr(style, "preview_accent", None)
            or primary
        )
    if not brand.fonts_are_explicit:
        fonts = (
            getattr(style, "body_font", None)
            or getattr(style, "heading_font", None)
            or fonts
        )
    return replace(brand, primary=primary, fonts=fonts)


def build_design_system(
    brand: Brand, style: Optional[AuthoredStyleLike] = None
) -> str:
    """Build the concrete shared brief for a brand and optional authored style.

    ``None`` and the canonical ``default`` style deliberately use the built-in
    legacy brief so the generated prompt remains byte-for-byte compatible.
    Custom style briefs are already validated plain text from the A1 loader and
    are inserted verbatim apart from surrounding whitespace.
    """
    if style is None or style.id == "default":
        style_brief = _DEFAULT_STYLE_BRIEF.format(
            primary=brand.primary or "#2563EB",
            fonts=brand.fonts or "Noto Sans KR",
        )
    else:
        style_brief = style.brief.strip()
        token_lines = []
        background = getattr(style, "background_color", None)
        heading_font = getattr(style, "heading_font", None)
        body_font = getattr(style, "body_font", None)
        mono_font = getattr(style, "mono_font", None)
        if background:
            token_lines.append(f"- Default canvas background: {background}.")
        if heading_font or body_font or mono_font:
            token_lines.append(
                "- Typography roles: "
                f"headings {heading_font or brand.fonts}; "
                f"body {body_font or brand.fonts}; "
                f"labels/data {mono_font or body_font or brand.fonts}."
            )
        if token_lines:
            style_brief = f"{style_brief}\n" + "\n".join(token_lines)
        if getattr(style, "illustration_prompt", None):
            style_brief = f"{style_brief}\n{_ILLUSTRATION_SLOT_RULES}"
    return _DESIGN_SYSTEM_RULES.format(
        primary=brand.primary or "#2563EB",
        fonts=brand.fonts or "Noto Sans KR",
        language=brand.language or "Korean",
        style_brief=style_brief,
    )


_SYSTEM = (
    "You are an elite presentation/brand designer. You author bespoke, premium slides as "
    "complete self-contained HTML documents (McKinsey / Apple-keynote quality)."
)


def _author_prompt(
    content: str, design_system: str, brand: Brand, role: str, index: int, n: int
) -> str:
    wordmark = f" Wordmark for the footer: {brand.wordmark}." if brand.wordmark else ""
    return (
        f"Author slide {index + 1} of {n}. Deck topic: {brand.topic}. This slide's ROLE: "
        f"{role}. CONTENT: {content}.{wordmark}\n\n{design_system}\n\n"
        "Design THIS slide bespoke and premium within the shared system, vary the layout to "
        "suit the role, output ONLY the complete self-contained HTML document."
    )


_REFERENCE_GUIDANCE = (
    "\n\nVISUAL REFERENCES are attached for composition, hierarchy, depth, lighting, "
    "diagram density, and overall polish. Use them as art direction only. Do NOT copy "
    "their text, logos, brand names, exact illustrations, or factual content. Rebuild "
    "an original slide from this slide's CONTENT and the shared design system."
)


def _reference_parts(reference_images: Sequence[Path]) -> list[ImageContentPart]:
    parts: list[ImageContentPart] = []
    mime_by_suffix = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    for path in reference_images:
        try:
            if path.stat().st_size > REFERENCE_IMAGE_MAX_BYTES:
                logger.warning("Skipping oversized authored reference image %s", path)
                continue
            mime = mime_by_suffix.get(path.suffix.lower())
            if mime:
                parts.append(ImageContentPart(data=path.read_bytes(), mime_type=mime))
        except OSError as exc:
            logger.warning("Unable to read authored reference image %s: %s", path, exc)
    return parts


_LEADING_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n")
_TRAILING_FENCE = re.compile(r"\n```\s*$")


def extract_html_document(text: str) -> str:
    """Pull the clean HTML document out of a model response: strip ``` fences and
    any prose before <!doctype>/<html> or after </html>."""
    s = (text or "").strip()
    s = _LEADING_FENCE.sub("", s)
    s = _TRAILING_FENCE.sub("", s).strip()
    low = s.lower()
    start = low.find("<!doctype")
    if start == -1:
        start = low.find("<html")
    if start > 0:
        s = s[start:]
    end = s.lower().rfind("</html>")
    if end != -1:
        s = s[: end + len("</html>")]
    return s.strip()


def is_valid_slide_html(html: str) -> bool:
    """A usable authored slide must be a non-trivial HTML document with a body. Guards
    against empty/truncated/garbage model output before it reaches the renderer."""
    if not html:
        return False
    low = html.lower()
    return len(html) >= 80 and ("<html" in low or "<!doctype" in low) and "<body" in low


def fallback_slide_html(
    content: str, brand: Brand, role: str, index: int, n: int
) -> str:
    """A clean, on-brand placeholder slide used when authoring a slide fails or returns
    invalid HTML — so one bad slide degrades to a simple branded slide instead of
    aborting the whole deck. System-font stack (no network dependency) for resilience."""
    primary = brand.primary or "#2563EB"
    fonts = brand.fonts or "Noto Sans KR"
    text = _html.escape((content or "").strip())
    if len(text) > 420:
        text = text[:420].rstrip() + "…"
    eyebrow = _html.escape(role or "")
    marker = f"{index + 1:02d} / {n:02d}"
    return (
        "<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\"><style>"
        f"body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#F8FAFC;"
        f"font-family:'{fonts}',system-ui,-apple-system,'Malgun Gothic',sans-serif;"
        "box-sizing:border-box;padding:64px;display:flex;flex-direction:column;}"
        f".bar{{width:56px;height:6px;background:{primary};margin-bottom:24px;}}"
        f".eyebrow{{font-size:13px;letter-spacing:.15em;text-transform:uppercase;color:{primary};"
        "font-weight:700;margin-bottom:18px;}"
        ".body{font-size:30px;line-height:1.5;color:#0F172A;font-weight:500;max-width:1000px;}"
        ".foot{margin-top:auto;font-size:13px;color:#64748B;}"
        "</style></head><body>"
        f"<div class=\"bar\"></div><div class=\"eyebrow\">{eyebrow}</div>"
        f"<div class=\"body\">{text}</div><div class=\"foot\">{marker}</div>"
        "</body></html>"
    )


# codex (gpt-5.x) reasoning-effort levels we forward verbatim. 'off'/'default' are OUR
# sentinels meaning "send no override, let the provider apply its own default effort" —
# they are NOT codex levels. ('none' and 'minimal' ARE real codex levels, so they belong
# in the forward list, not the disable list.)
_REASONING_EFFORTS = ("minimal", "none", "low", "medium", "high", "xhigh")
_REASONING_DISABLE = ("off", "default")


def _authoring_extra_body(effort: Optional[str] = None) -> Optional[dict]:
    """Per-slide codex reasoning-effort override (codex-only — the param 400s on
    providers that don't support it).

    Resolution: a non-blank `effort` argument wins; otherwise the AUTHORED_REASONING_EFFORT
    env; if neither is meaningfully set the authoring default is the fast 'low' effort
    (~25% faster per slide with no first-pass quality loss). codex (gpt-5.x) is a reasoning
    model and per-slide latency is reasoning time, so authoring bespoke HTML — which needs
    little reasoning — defaults low.

    Returns {"reasoning": {"effort": L}} for any L in _REASONING_EFFORTS. Returns None (no
    override → the provider's own default effort) for the 'off'/'default' sentinels. An
    UNRECOGNIZED value is logged (attributing the env vs the argument as its source) and
    also returns None, so a misconfiguration is never silent."""
    if not is_codex_selected():
        return None
    override = (effort or "").strip()
    if override:
        raw, source = override, "reasoning_effort argument"
    else:
        raw, source = os.getenv("AUTHORED_REASONING_EFFORT", ""), "AUTHORED_REASONING_EFFORT"
    level = raw.strip().lower()
    if not level:
        level = "low"  # unset / blank / whitespace-only -> fast authoring default
    if level in _REASONING_DISABLE:
        return None
    if level in _REASONING_EFFORTS:
        return {"reasoning": {"effort": level}}
    logger.warning(
        "%s=%r is not a recognized reasoning effort %s or a disable word %s; "
        "authoring at the provider default reasoning effort.",
        source,
        raw,
        _REASONING_EFFORTS,
        _REASONING_DISABLE,
    )
    return None


async def author_slide_html(
    content: str,
    design_system: str,
    brand: Brand,
    role: str,
    index: int,
    n: int,
    reasoning_effort: Optional[str] = None,
    reference_images: Optional[Sequence[Path]] = None,
) -> str:
    """Author one bespoke, self-contained 1280x720 HTML slide. Single text-generation
    call against the configured provider; returns the cleaned HTML document.

    `reasoning_effort` overrides the codex reasoning effort for this call (see
    _authoring_extra_body); the vision-QA re-author pass passes 'off' so corrections
    run at the provider's full default effort rather than the fast authoring 'low'."""
    client = get_client(config=get_llm_config())
    model = get_model()
    prompt = _author_prompt(content, design_system, brand, role, index, n)
    image_parts = _reference_parts(reference_images or ())
    user_content = (
        [TextContentPart(text=prompt + _REFERENCE_GUIDANCE), *image_parts]
        if image_parts
        else prompt
    )
    messages = [SystemMessage(content=_SYSTEM), UserMessage(content=user_content)]
    kwargs = get_generate_kwargs(
        model=model, messages=messages, max_tokens=AUTHOR_MAX_TOKENS
    )
    reasoning = _authoring_extra_body(reasoning_effort)
    if reasoning:
        # codex-only; get_extra_body() populates extra_body only for the (mutually
        # exclusive) CUSTOM provider, so this reasoning dict is the sole source here.
        kwargs["extra_body"] = reasoning
    async def generate(generate_kwargs: dict):
        # Run the blocking call on the dedicated authoring pool (bounds concurrency to
        # AUTHOR_CONCURRENCY and keeps long authoring threads off the shared default
        # executor used by other LLM calls / exports).
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _AUTHOR_EXECUTOR, functools.partial(client.generate, **generate_kwargs)
        )

    try:
        response = await generate(kwargs)
    except Exception as e:
        if not image_parts:
            raise handle_llm_client_exceptions(e)
        logger.warning(
            "Multimodal authored generation failed for slide %d; retrying text-only: %s",
            index + 1,
            e,
        )
        fallback_messages = [
            SystemMessage(content=_SYSTEM),
            UserMessage(content=prompt),
        ]
        fallback_kwargs = get_generate_kwargs(
            model=model, messages=fallback_messages, max_tokens=AUTHOR_MAX_TOKENS
        )
        if reasoning:
            fallback_kwargs["extra_body"] = reasoning
        try:
            response = await generate(fallback_kwargs)
        except Exception as fallback_error:
            raise handle_llm_client_exceptions(fallback_error)
    return extract_html_document(extract_text(response.content) or "")
