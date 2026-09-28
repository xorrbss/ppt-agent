"""The deterministic page a slide publishes when authoring is out of attempts.

D7 at the author stage. When the two ordinary author calls and the one
simplified rescue call have all failed, the deck still publishes -- with this
page in that slide's place and the slide flagged. Nothing here calls a
provider, measures a browser, or reads a checkpoint: the same inputs always
produce the same bytes, so a flagged deck is reproducible from its receipt.

The page is not a "sorry" card. It reproduces the deck's own header contract
part for part (band, tick, eyebrow, title, divider, body wrapper) with the
band's derived height DECLARED the way every authored slide declares it, so
``validate_header_contract`` accepts it and the restyle/export stages that read
those markers find what they expect. Below the divider it carries the slide's
own outline text as plain bullets in the deck's font and accent colour. That is
deliberately the least the contract permits: the page has to be right, and a
composition nobody authored has no business being ambitious.
"""

from __future__ import annotations

import html as _html
from typing import Any, Sequence

from models.authored_header_contract import (
    SLIDE_HEIGHT_PX,
    SLIDE_WIDTH_PX,
    AuthoredHeaderContract,
    label_row_px,
)

#: Bullets the body renders. Past this the page stops being readable long
#: before it stops fitting, and the outline text is preserved in full by the
#: slide row itself -- this page is a rendering of it, not its only copy.
MAX_BODY_BULLETS = 10

#: Characters one bullet keeps. The tail is dropped with an ellipsis rather
#: than wrapped indefinitely, because a body that overflows the canvas is the exact
#: defect the whole rescue ladder exists to avoid.
MAX_BULLET_CHARS = 150

_BODY_FONT_PX = 20
_BODY_LINE_HEIGHT = 1.6
_BODY_BOTTOM_PX = 48


def _lines(content: str) -> list[str]:
    """The outline's own lines, bullet markers and blank lines removed."""

    output: list[str] = []
    for raw in str(content or "").splitlines():
        line = raw.strip().lstrip("-*• \t").strip()
        if line:
            output.append(line)
    return output


def _title_budget(contract: AuthoredHeaderContract | None) -> int:
    """How many characters one title line holds at the contract's type size.

    An estimate, and it only has to be one: the title is rendered with
    ``nowrap``/``overflow: hidden`` as well, so this decides how much text the
    DOM keeps rather than whether the line fits. The text-level cut in
    ``_split_content`` (with its own "…") is the visible truncation; a CSS
    ``text-overflow: ellipsis`` is not used because authoring validation
    rejects it as content hiding.
    """

    if contract is None:
        return 60
    return max(12, int(contract.title.max_width_px / (contract.title.font_size_px * 0.62)))


def _split_content(
    content: str, *, title_budget: int
) -> tuple[str, list[str]]:
    """``(title, bullets)`` with no outline text lost to the title's truncation."""

    lines = _lines(content)
    if not lines:
        return "", []
    head, rest = lines[0], lines[1:]
    title = head
    if len(head) > title_budget:
        title = head[:title_budget].rstrip() + "…"
        # The untruncated line is still the slide's own text; keep it readable
        # in the body rather than publishing a page that silently drops it.
        rest = [head, *rest]
    bullets = [
        item if len(item) <= MAX_BULLET_CHARS
        else item[:MAX_BULLET_CHARS].rstrip() + "…"
        for item in rest[:MAX_BODY_BULLETS]
    ]
    return title, bullets


def _band_px(contract: AuthoredHeaderContract) -> int:
    """The smallest band this contract derives, clamped into its own bounds.

    One title line and no subline is what this page renders, so the band it
    declares is the band that composition actually produces.
    """

    return max(contract.band_min_px, min(contract.band_max_px, contract.minimum_band_px))


def _band_children(
    contract: AuthoredHeaderContract, *, eyebrow: str, title: str, accent: str
) -> list[str]:
    parts: list[str] = []
    if contract.eyebrow.enabled:
        row: list[str] = []
        tick = contract.eyebrow.tick
        if tick.enabled:
            row.append(
                f'<span data-header-part="tick" style="display:inline-block;'
                f"width:{tick.width_px}px;height:{tick.height_px}px;"
                f'background:{tick.color};margin-right:{tick.gap_px}px;"></span>'
            )
        row.append(
            f'<span data-header-part="eyebrow" data-qid="header.eyebrow" '
            f'data-semantic-role="supporting" style="font-size:'
            f"{contract.eyebrow.font_size_px}px;font-weight:{contract.eyebrow.weight};"
            f"letter-spacing:{contract.eyebrow.letter_spacing_em}em;"
            f'color:{contract.eyebrow.color};">{_html.escape(eyebrow)}</span>'
        )
        parts.append(
            '<div style="display:flex;align-items:center;height:'
            f'{label_row_px(contract.eyebrow.font_size_px)}px;'
            f'margin-bottom:{contract.eyebrow_gap_px}px;">' + "".join(row) + "</div>"
        )
    parts.append(
        f'<h1 data-header-part="title" data-qid="header.title" '
        f'data-semantic-role="title" style="margin:0;font-size:'
        f"{contract.title.font_size_px}px;font-weight:{contract.title.weight};"
        f"line-height:{contract.title.line_height};color:{contract.title.color};"
        f"max-width:{contract.title.max_width_px}px;white-space:nowrap;"
        f'overflow:hidden;">{_html.escape(title)}</h1>'
    )
    if contract.divider.enabled:
        parts.append(
            f'<div data-header-part="divider" style="margin-top:auto;height:'
            f'{contract.divider.thickness_px}px;background:{contract.divider.color};"></div>'
        )
    else:
        parts.append(f'<div style="margin-top:auto;height:0;color:{accent};"></div>')
    return parts


def _bullet_markup(bullets: Sequence[str]) -> str:
    if not bullets:
        return ""
    items = "".join(
        f'<li data-qid="body.item{position}" data-semantic-role="body" '
        f'style="margin:0 0 12px 0;">{_html.escape(item)}</li>'
        for position, item in enumerate(bullets, start=1)
    )
    return (
        f'<ul style="margin:0;padding:0 0 0 22px;font-size:{_BODY_FONT_PX}px;'
        f'line-height:{_BODY_LINE_HEIGHT};color:#0F172A;">{items}</ul>'
    )


def author_placeholder_html(
    *,
    content: str,
    brand: Any,
    role: str,
    contract: AuthoredHeaderContract | None,
    font_stack: str,
    canvas_width_px: int | None = None,
    canvas_height_px: int | None = None,
) -> str:
    """Render one slide's outline as the deck's plainest contract-conforming page.

    ``contract`` is ``None`` for a deck that has no header contract, and for a
    role the contract excludes; that page keeps the same body and simply owns
    its whole frame, which is what those roles do anyway. The page takes the
    contract's canvas unless one is given; with neither it is 1280x720.
    """

    canvas = (
        canvas_width_px or (contract.canvas_width_px if contract else SLIDE_WIDTH_PX),
        canvas_height_px or (contract.canvas_height_px if contract else SLIDE_HEIGHT_PX),
    )
    title, bullets = _split_content(content, title_budget=_title_budget(contract))
    accent = str(getattr(brand, "primary", "") or "#2563EB")
    eyebrow = str(role or "").strip().upper()[: (contract.eyebrow.max_chars if contract else 24)]
    if contract is None:
        return _unbanded_html(
            title=title, bullets=bullets, accent=accent, font_stack=font_stack, canvas=canvas
        )
    band_px = _band_px(contract)
    margin = contract.margin_x_px
    body_height = canvas[1] - band_px - contract.content_gap_px - _BODY_BOTTOM_PX
    band = (
        f'<header data-header-part="band" data-header-band="{band_px}" '
        f'style="box-sizing:border-box;height:{band_px}px;padding:'
        f"{contract.header_top_px}px {margin}px 0 {margin}px;display:flex;"
        'flex-direction:column;">'
        + "".join(_band_children(contract, eyebrow=eyebrow, title=title, accent=accent))
        + "</header>"
    )
    body = (
        f'<main data-header-part="body" style="margin-top:'
        f"{contract.content_gap_px}px;padding:0 {margin}px;height:{max(0, body_height)}px;"
        f'overflow:hidden;">{_bullet_markup(bullets)}</main>'
    )
    return _document(band + body, font_stack=font_stack, canvas=canvas)


def _unbanded_html(
    *,
    title: str,
    bullets: Sequence[str],
    accent: str,
    font_stack: str,
    canvas: tuple[int, int],
) -> str:
    body = (
        f'<div style="width:56px;height:6px;background:{accent};margin-bottom:24px;"></div>'
        f'<h1 data-qid="header.title" data-semantic-role="title" style="margin:0 0 24px 0;'
        f'font-size:34px;font-weight:800;color:#0F172A;">{_html.escape(title)}</h1>'
        + _bullet_markup(bullets)
    )
    return _document(
        f'<main style="padding:64px;height:{canvas[1]}px;box-sizing:border-box;'
        f'overflow:hidden;">{body}</main>',
        font_stack=font_stack,
        canvas=canvas,
    )


def _document(inner: str, *, font_stack: str, canvas: tuple[int, int]) -> str:
    return (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>'
        f"body{{margin:0;width:{canvas[0]}px;height:{canvas[1]}px;"
        f"overflow:hidden;background:#FFFFFF;font-family:{font_stack};}}"
        f"</style></head><body>{inner}</body></html>"
    )


__all__ = [
    "MAX_BODY_BULLETS",
    "MAX_BULLET_CHARS",
    "author_placeholder_html",
]
