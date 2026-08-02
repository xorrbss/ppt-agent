"""Deterministic page-number styling for authored STYLE_REFERENCE slides.

Model-authored HTML is intentionally flexible, but the deck-level page marker is
not: every style-reference slide uses one blue, zero-padded number in the same
bottom-right position.  Exact reconstruction bypasses this helper so the source
image remains the sole visual truth.
"""

from __future__ import annotations

import html as html_module
import re

from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, normalize_reference_mode


_DEFAULT_PRIMARY = "#2563EB"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_NORMALIZATION_BLOCK = re.compile(
    r"\s*<!-- presenton-page-number:start -->.*?"
    r"<!-- presenton-page-number:end -->\s*",
    re.DOTALL,
)
_OLD_PAGE_WRAPPER = re.compile(
    r'<span\b[^>]*data-presenton-old-page-number[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
_FOOTER = re.compile(r"(<footer\b[^>]*>)(.*?)(</footer>)", re.IGNORECASE | re.DOTALL)


def _safe_primary(primary: str | None) -> str:
    candidate = (primary or "").strip()
    return candidate.upper() if _HEX_COLOR.fullmatch(candidate) else _DEFAULT_PRIMARY


def _hide_unclassified_page_text(footer_body: str, page_number: str) -> str:
    """Hide a bare trailing ``07``/``07 / 90`` token inside a footer element.

    Most authored footers identify the token with ``.page``.  A few model
    outputs use a plain final span instead, so CSS cannot distinguish the page
    marker from the left-hand wordmark.  Restrict the replacement to a text node
    containing only the current page number (and optional total) to avoid
    touching narrative footer copy or other numerals.
    """

    numeric = str(int(page_number))
    token = re.compile(
        rf"(?P<open>>)(?P<lead>\s*)"
        rf"(?P<value>0*{re.escape(numeric)}\s*(?:/\s*\d+)?)(?P<trail>\s*)"
        rf"(?P<close><)",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        value = html_module.escape(match.group("value"), quote=False)
        return (
            f'{match.group("open")}{match.group("lead")}'
            '<span data-presenton-old-page-number '
            'style="visibility:hidden!important">'
            f"{value}</span>{match.group('trail')}{match.group('close')}"
        )

    return token.sub(replace, footer_body)


def normalize_authored_page_number(
    html: str,
    *,
    slide_index: int,
    primary: str | None,
    reference_mode: str,
) -> str:
    """Return HTML with the canonical authored page marker injected.

    The operation is idempotent and deliberately leaves exact-reconstruction
    HTML byte-for-byte unchanged. ``slide_index`` is zero based.
    """

    if normalize_reference_mode(reference_mode) == EXACT_RECONSTRUCTION:
        return html
    if slide_index < 0:
        raise ValueError("slide_index must be non-negative")
    if not html:
        return html

    page_number = f"{slide_index + 1:02d}"
    cleaned = _NORMALIZATION_BLOCK.sub("", html)
    # Restore text wrapped by an earlier pass before classifying it again.  This
    # keeps normalization stable when an API response is normalized repeatedly.
    cleaned = _OLD_PAGE_WRAPPER.sub(r"\1", cleaned)

    def normalize_footer(match: re.Match[str]) -> str:
        return (
            match.group(1)
            + _hide_unclassified_page_text(match.group(2), page_number)
            + match.group(3)
        )

    cleaned = _FOOTER.sub(normalize_footer, cleaned)
    color = _safe_primary(primary)
    block = f"""
<!-- presenton-page-number:start -->
<style data-presenton-page-number-style>
  footer .page,
  footer .page-number,
  footer .page-no,
  footer [data-page-number],
  footer [data-slide-number],
  footer .footer-line + :last-child,
  footer [data-presenton-old-page-number] {{
    visibility: hidden !important;
  }}
  [data-presenton-page-number] {{
    all: initial !important;
    position: fixed !important;
    right: 58px !important;
    bottom: 24px !important;
    z-index: 2147483646 !important;
    display: block !important;
    color: {color} !important;
    font-family: "Noto Sans KR", "Malgun Gothic", sans-serif !important;
    font-size: 12px !important;
    font-style: normal !important;
    font-weight: 900 !important;
    line-height: 16px !important;
    letter-spacing: 0 !important;
    text-align: right !important;
    white-space: nowrap !important;
    pointer-events: none !important;
  }}
</style>
<div data-presenton-page-number aria-hidden="true">{page_number}</div>
<!-- presenton-page-number:end -->
""".strip()

    body_close = re.search(r"</body\s*>", cleaned, re.IGNORECASE)
    if body_close:
        return f"{cleaned[:body_close.start()]}\n{block}\n{cleaned[body_close.start():]}"
    return f"{cleaned}\n{block}"
