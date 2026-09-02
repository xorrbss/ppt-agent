"""Authored icon packs and scene cutouts (thin facade).

Stage 3 of the infographic pipeline: transparent pictogram icons generated
as sprite sheets on ``gpt-image-2``, split, vetted and cached per
template/style as an icon pack, plus one large object cutout for a
``scene`` slide. The slide plan names concepts; this package hands back
file paths and data URIs. It never touches slide HTML.

    from services.authored_icon_pack import resolve_icons, icon_data_uri

    resolution = await resolve_icons(["감지", "예측"], pack_key="korean-biz/default")
    uri = icon_data_uri(resolution.path_for("감지"))

Submodules: ``provider`` (alpha PNG on the configured provider), ``sheet``
(prompt + grid split), ``checks`` (transparency, no-text), ``store`` (pack
index on disk), ``service`` (resolution), ``scene`` (cutout), ``inline``
(data URIs).
"""

from services.authored_icon_pack.checks import (
    TextCheck,
    TransparencyCheck,
    check_no_text,
    check_transparency,
)
from services.authored_icon_pack.inline import cutout_data_uri, icon_data_uri
from services.authored_icon_pack.provider import (
    TransparentPngProvider,
    TransparentPngUnsupported,
    transparent_model_for,
)
from services.authored_icon_pack.scene import (
    SceneCutout,
    build_cutout_prompt,
    generate_scene_cutout,
)
from services.authored_icon_pack.service import (
    IconResolution,
    ResolvedIcon,
    resolve_icons,
)
from services.authored_icon_pack.store import IconEntry, IconPack, concept_key

__all__ = [
    "IconEntry",
    "IconPack",
    "IconResolution",
    "ResolvedIcon",
    "SceneCutout",
    "TextCheck",
    "TransparencyCheck",
    "TransparentPngProvider",
    "TransparentPngUnsupported",
    "build_cutout_prompt",
    "check_no_text",
    "check_transparency",
    "concept_key",
    "cutout_data_uri",
    "generate_scene_cutout",
    "icon_data_uri",
    "resolve_icons",
    "transparent_model_for",
]
