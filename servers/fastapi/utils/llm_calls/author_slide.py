"""Authored-mode core: have the configured LLM AUTHOR a complete, self-contained
1280x720 HTML slide (bespoke layout) within a shared design system — instead of
filling a fixed React archetype. This is the crux of the high-quality "authored"
generation mode (vs. the fast default adaptive/template path). Provider-agnostic:
one plain `client.generate` text call against the selected provider (codex /
anthropic / ...). Opt-in — never on the fast deterministic default path."""

import asyncio
import html as _html
import os
import re
from dataclasses import dataclass
from typing import Optional

from llmai import get_client
from llmai.shared import SystemMessage, UserMessage

from enums.llm_provider import LLMProvider
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import get_llm_config
from utils.llm_provider import get_llm_provider, get_model
from utils.llm_utils import extract_text, get_generate_kwargs

# A bespoke full-bleed HTML slide with inline CSS is large; give the model room so
# the document is never truncated mid-markup (truncation => broken render).
AUTHOR_MAX_TOKENS = 16000

# Appendix A — design-system brief (the crux IP; rules kept verbatim). The brand
# specifics (primary colour / fonts / language) are injected per deck so EVERY
# slide shares one palette, type system and language => a cohesive deck.
_DESIGN_SYSTEM_RULES = """\
DESIGN SYSTEM (obey on EVERY slide so the deck is cohesive):
- Brand primary colour: {primary}. Brand fonts: {fonts}. Deck language: {language}.
- Canvas: a single 16:9 slide that renders at EXACTLY 1280x720px. Inline <style> only, no
  external JS. Body 1280x720, margin 0, overflow hidden.
- Fonts: load the brand fonts ({fonts}) from Google Fonts; headings 800-900 weight, body 400-500.
- Palette: build everything from the brand primary ({primary}) + neutrals (ink #0F172A, muted
  #64748B, hairline #E2E8F0, surface #F8FAFC, white). The primary is the ONE accent; no other hues.
- Margins ~64px (except intentional full-bleed colour zones).
- Consistent eyebrow: UPPERCASE, letter-spaced 0.15em, ~13px, primary, with a short tick/line.
- Consistent footer on content slides: small muted slide marker left + a small wordmark right.
- Strong, confident type hierarchy; make the single most important element dominant.
- Allowed (vary per slide): full-bleed/partial colour-blocked zones, asymmetric grids, a left
  sidebar, oversized numerals, thin rules, subtle tints of the primary, inline-SVG icons/charts.
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


@dataclass
class Brand:
    """The shared design tokens injected into every slide of one deck."""

    topic: str
    language: str = "Korean"
    primary: str = "#2563EB"
    fonts: str = "Noto Sans KR"
    wordmark: str = ""


def build_design_system(brand: Brand) -> str:
    """Concrete design-system brief for `brand`, shared verbatim across the deck."""
    return _DESIGN_SYSTEM_RULES.format(
        primary=brand.primary or "#2563EB",
        fonts=brand.fonts or "Noto Sans KR",
        language=brand.language or "Korean",
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


def _authoring_extra_body() -> Optional[dict]:
    """codex (gpt-5.x) is a reasoning model — the per-slide latency is reasoning time.
    Authoring bespoke HTML needs little reasoning, so request a LOWER effort: measured
    ~25% faster per slide with no quality loss. codex-only (the param 400s on providers
    that don't support it). Tune/disable via AUTHORED_REASONING_EFFORT (low|medium|high,
    or 'off')."""
    if get_llm_provider() != LLMProvider.CODEX:
        return None
    effort = (os.getenv("AUTHORED_REASONING_EFFORT", "low") or "low").strip().lower()
    if effort in ("low", "medium", "high"):
        return {"reasoning": {"effort": effort}}
    return None


async def author_slide_html(
    content: str,
    design_system: str,
    brand: Brand,
    role: str,
    index: int,
    n: int,
) -> str:
    """Author one bespoke, self-contained 1280x720 HTML slide. Single text-generation
    call against the configured provider; returns the cleaned HTML document."""
    client = get_client(config=get_llm_config())
    model = get_model()
    messages = [
        SystemMessage(content=_SYSTEM),
        UserMessage(content=_author_prompt(content, design_system, brand, role, index, n)),
    ]
    kwargs = get_generate_kwargs(
        model=model, messages=messages, max_tokens=AUTHOR_MAX_TOKENS
    )
    reasoning = _authoring_extra_body()
    if reasoning:
        kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), **reasoning}
    try:
        response = await asyncio.to_thread(client.generate, **kwargs)
    except Exception as e:
        raise handle_llm_client_exceptions(e)
    return extract_html_document(extract_text(response.content) or "")
