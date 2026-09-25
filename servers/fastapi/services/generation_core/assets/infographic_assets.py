"""Put the icon pack's pictures into a planned figure slide.

The archetype library draws every figure with a built-in stroke icon in each
node's ``.ig-ic`` slot and, for a scene, one ``<img data-illust-prompt>``
slot in ``.ig-art``. This module swaps those placeholders for the generated
transparent PNGs of ``services.authored_icon_pack`` once the slide has passed
its contract check — so the checker always sees the library's own SVG, and a
pack that is missing an icon simply leaves that SVG in place.

Both substitutions degrade rather than fail: a slide never loses its figure
because a picture could not be made. The scene cutout is tried before the
ordinary illustration stage, which then finds no slot left to fill; when the
cutout fails, the slot is still there and the ordinary path (fill, or strip on
failure) applies unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from models.authored_illustration_plan import illustrations_are_prohibited
from models.authored_infographic_plan import AuthoredSlideInfographic
from services.generation_core.assets.icon_pack import (
    generate_scene_cutout,
    icon_data_uri,
    resolve_icons,
    transparent_model_for,
)
from utils.asset_directory_utils import get_images_directory
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, normalize_reference_mode

LOGGER = logging.getLogger(__name__)

DEFAULT_PACK_KEY = "default"
DEFAULT_ICON_COLOR = "#1E5EFF"

_ICON_SLOT = re.compile(
    r'<div\b(?P<attrs>[^>]*\bclass="ig-ic"[^>]*\bdata-qid="[^"]*\.n(?P<node>\d+)\.ic"[^>]*)>'
    r"(?P<inner>.*?)</div>",
    re.IGNORECASE | re.DOTALL,
)
_ART_SLOT = re.compile(
    r'(?P<open><div\b[^>]*\bclass="ig-art"[^>]*>)\s*(?P<img><img\b[^>]*\bdata-illust-prompt=[^>]*>)',
    re.IGNORECASE | re.DOTALL,
)
_ILLUST_ATTR = re.compile(r'\s*\bdata-illust-prompt\s*=\s*("[^"]*"|\'[^\']*\')', re.IGNORECASE)


def deck_has_picture_source(style: object, settings: Any = None) -> bool:
    """Whether this deck can make any picture at all.

    A style's illustration prompt drives the ordinary illustration provider;
    a transparent-capable image provider drives scene cutouts and icons and
    needs no style prompt. Either is a source. ``DISABLE_IMAGE_GENERATION``
    is honoured inside ``transparent_model_for``.
    """
    if (getattr(style, "illustration_prompt", None) or "").strip():
        return True
    return transparent_model_for(settings) is not None


@dataclass(frozen=True)
class InfographicAssetContext:
    """Everything one deck needs to fetch icons and cutouts for its figures."""

    pack_key: str
    output_directory: str
    style_prompt: Optional[str] = None
    color: str = DEFAULT_ICON_COLOR
    #: Test seam: a stand-in for ``TransparentPngProvider``. None means the
    #: configured provider, resolved by the icon pack itself.
    provider: Any = None
    settings: Any = None
    #: Test seam: where the icon pack lives. None means the app-data directory.
    app_data: Optional[str] = None


def build_infographic_asset_context(
    *,
    style: object,
    brand: object,
    instructions: Optional[str] = None,
    image_service: object = None,
    settings: Any = None,
    provider: Any = None,
) -> Optional[InfographicAssetContext]:
    """The deck's asset context, or None when it may not spend a transparent PNG.

    Exact reconstruction reproduces a source and takes no pictures of its own;
    a request that refused images is honoured for icons as well as scenes; and
    without a provider that can output alpha there is nothing to substitute.
    """
    if normalize_reference_mode(
        getattr(style, "reference_mode", None)
    ) == EXACT_RECONSTRUCTION:
        return None
    if illustrations_are_prohibited(instructions):
        return None
    if provider is None and transparent_model_for(settings) is None:
        return None
    if provider is not None and not getattr(provider, "available", False):
        return None
    style_prompt = (getattr(style, "illustration_prompt", None) or "").strip() or None
    style_id = (getattr(style, "id", None) or "").strip() or DEFAULT_PACK_KEY
    output_directory = getattr(image_service, "output_directory", None) or get_images_directory()
    return InfographicAssetContext(
        pack_key=style_id,
        output_directory=str(output_directory),
        style_prompt=style_prompt,
        color=(getattr(brand, "primary", None) or DEFAULT_ICON_COLOR),
        provider=provider,
        settings=settings,
    )


async def substitute_figure_icons(
    html: str,
    *,
    slide: Optional[AuthoredSlideInfographic],
    context: Optional[InfographicAssetContext],
) -> str:
    """Replace each node's built-in SVG icon with its pack icon, where one exists.

    Concepts are the plan's node labels, in node order; the slot's ``data-qid``
    names the node it belongs to, so a node the pack cannot draw keeps its
    library SVG and the rest still get theirs.
    """
    if context is None or slide is None or not slide.has_figure or not slide.nodes:
        return html
    if not _ICON_SLOT.search(html):
        return html
    labels = [node.label for node in slide.nodes]
    try:
        resolution = await resolve_icons(
            labels,
            pack_key=context.pack_key,
            style_prompt=context.style_prompt,
            color=context.color,
            settings=context.settings,
            provider=context.provider,
            app_data=context.app_data,
        )
    except Exception as error:  # noqa: BLE001 - icons are optional
        LOGGER.warning("[authored] figure icon resolution failed: %s", error)
        return html
    uris: dict[int, str] = {}
    for position, label in enumerate(labels):
        path = resolution.path_for(label)
        if path is None:
            continue
        uri = icon_data_uri(path)
        if uri:
            uris[position] = uri
    if not uris:
        return html

    def replace(match: re.Match[str]) -> str:
        position = int(match.group("node")) - 1
        uri = uris.get(position)
        if uri is None:
            return match.group(0)
        return f'<div{match.group("attrs")}><img src="{uri}" alt=""></div>'

    return _ICON_SLOT.sub(replace, html)


async def fill_scene_cutout(
    html: str,
    *,
    slide: Optional[AuthoredSlideInfographic],
    context: Optional[InfographicAssetContext],
    slide_index: int = 0,
) -> str:
    """Fill a scene's ``.ig-art`` slot with one object cutout.

    Returns the HTML unchanged when this is not a scene, the slot is absent, or
    the cutout could not be made or vetted — the ordinary illustration stage
    then sees the untouched slot and applies its own fill-or-strip rule.
    """
    if context is None or slide is None or slide.archetype != "scene":
        return html
    match = _ART_SLOT.search(html)
    if match is None:
        return html
    labels = [node.label for node in slide.nodes]
    subject = slide.takeaway or (labels[0] if labels else "")
    try:
        cutout = await generate_scene_cutout(
            takeaway=subject,
            nodes=labels,
            output_directory=context.output_directory,
            style_prompt=context.style_prompt,
            settings=context.settings,
            provider=context.provider,
        )
    except Exception as error:  # noqa: BLE001 - the ordinary path still applies
        LOGGER.warning(
            "[authored] scene cutout failed on slide %d: %s", slide_index + 1, error
        )
        return html
    if cutout is None:
        return html
    img = _ILLUST_ATTR.sub("", match.group("img"), count=1)
    img = re.sub(r"^<img\b", f'<img src="{cutout.data_uri}"', img, count=1, flags=re.IGNORECASE)
    return html[: match.start("img")] + img + html[match.end("img") :]


__all__ = [
    "DEFAULT_ICON_COLOR",
    "DEFAULT_PACK_KEY",
    "InfographicAssetContext",
    "build_infographic_asset_context",
    "deck_has_picture_source",
    "fill_scene_cutout",
    "substitute_figure_icons",
]
