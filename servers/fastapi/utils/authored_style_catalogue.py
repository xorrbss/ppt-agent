"""Public catalogue consolidation without deleting historical preset sources."""
from __future__ import annotations

from typing import TYPE_CHECKING, Collection, Iterable

from services.authored_canvas import supported_aspect_ratios_for_style
from utils.authored_style_samples import load_style_samples

if TYPE_CHECKING:
    from utils.authored_styles import AuthoredStyle


AUTHORED_STYLE_ALIASES: dict[str, str] = {
    "atelier-monograph": "minimal-vellum",
    "decision-atlas": "strategic-navy",
    "product-lab": "silicon-refined",
    "soft-editorial": "minimal-vellum",
    "strategic-insight": "strategic-navy",
}


def canonical_authored_style_id(style_id: str, available_ids: Collection[str]) -> str:
    """Keep the historical preset usable if its replacement cannot be loaded."""
    canonical = AUTHORED_STYLE_ALIASES.get(style_id, style_id)
    return canonical if canonical in available_ids else style_id


def authored_style_catalogue(styles: Iterable[AuthoredStyle]) -> list[dict]:
    by_id = {style.id: style for style in styles}
    result = []
    for style_id, style in sorted(by_id.items()):
        if canonical_authored_style_id(style_id, by_id) != style_id:
            continue
        public = style.public_dict()
        public["supportedAspectRatios"] = supported_aspect_ratios_for_style(style_id)
        aliases = sorted(
            old_id for old_id, target in AUTHORED_STYLE_ALIASES.items()
            if target == style_id
        )
        if aliases:
            public["aliases"] = aliases
            # The existing picker searches tags, so old Korean names remain findable.
            public["tags"] = list(dict.fromkeys(
                [*public["tags"], *(by_id[old_id].name for old_id in aliases if old_id in by_id)]
            ))
        samples = load_style_samples(style_id)
        if samples is not None:
            public["samples"] = {
                "canvas": {"width": samples.canvas.width, "height": samples.canvas.height},
                "slides": [
                    {
                        "index": slide.index,
                        "title": slide.title,
                        "thumb": f"/api/v1/ppt/authored/styles/{style_id}/samples/{slide.index}?size=thumb",
                        "full": f"/api/v1/ppt/authored/styles/{style_id}/samples/{slide.index}?size=full",
                    }
                    for slide in samples.slides
                ],
            }
        result.append(public)
    return result
