"""Conservative empty-media composition findings; never rewrite source HTML.

Deleting a panel does not collapse explicit grid tracks, so these findings feed
the existing bounded repair and flagged-slide path instead of normalization.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from typing import Any

from utils.authored_dom_tags import VOID_TAGS

EMPTY_MEDIA_CATEGORY = "empty_media_panel"
EMPTY_MEDIA_DETAIL = (
    "Large empty painted media container: remove the placeholder and its reserved "
    "layout space; recompose existing source content without inventing an image."
)
_MEDIA_TOKEN = re.compile(r"(?:^|[._-])(?:image|img|media|photo|picture)(?:$|[._-])", re.I)


def source_reflow_empty_media_instruction(outline: Any) -> str:
    """The admitted outline preserves each manifest image occurrence per page."""
    pages = [
        str(index + 1) for index, slide in enumerate(outline.slides)
        if not slide.image_occurrence_ids
    ]
    if not pages:
        return ""
    return (
        " Source manifest image occurrences are zero on output pages "
        + ", ".join(pages)
        + ". On those pages, do not create image placeholders, image cards, or "
        "empty media panels. Do not reserve an image column or blank media area; "
        "compose only the supplied text and intentional non-media decoration."
    )


class _LeafContainers(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict[str, Any]] = []
        self.leaves: dict[str, dict[str, str | None]] = {}
        self.valid = True

    def handle_starttag(self, tag, attrs):
        if self.stack:
            self.stack[-1]["occupied"] = True
        if tag not in VOID_TAGS:
            self.stack.append({"tag": tag, "attrs": dict(attrs), "occupied": False})

    def handle_startendtag(self, tag, attrs):
        if self.stack:
            self.stack[-1]["occupied"] = True

    def handle_data(self, data):
        if self.stack and data.strip():
            self.stack[-1]["occupied"] = True

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1]["tag"] != tag:
            self.valid = False
            return
        node = self.stack.pop()
        attrs = node["attrs"]
        if tag in {"div", "section", "aside", "figure", "span"} and not node["occupied"]:
            qid = attrs.get("data-qid")
            if qid:
                self.leaves[qid] = attrs


def _number(value: object) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def find_empty_media_panels(
    html: str, elements: Sequence[Mapping[str, Any]], *, width: float, height: float,
    exact_reconstruction: bool = False,
) -> tuple[str, ...]:
    """Require DOM emptiness and browser paint/visibility/size evidence together.

    Leaf-only detection deliberately excludes CSS drawings, nested media and
    text containers. Size is relative to the canvas (4% area, 12% each axis),
    independent of a slide number, panel position, color or exact dimensions.
    Missing measurements and ambiguous markup cannot establish this defect.
    """
    parser = _LeafContainers()
    parser.feed(html)
    parser.close()
    if not parser.valid or parser.stack or width <= 0 or height <= 0:
        return ()
    findings: list[str] = []
    for item in elements:
        qid = str(item.get("qid") or "")
        attrs = parser.leaves.get(qid)
        if attrs is None or item.get("intentional"):
            continue
        if exact_reconstruction and not item.get("unintentional"):
            continue
        role = str(
            attrs.get("data-semantic-role") or item.get("semantic_role")
            or attrs.get("role") or ""
        )
        if role not in {"image", "img", "media", "photo", "picture"} and not _MEDIA_TOKEN.search(qid):
            continue
        if any(
            key.startswith("data-source-")
            or key in {"data-asset-id", "data-illust-prompt"} for key in attrs
        ):
            continue
        if item.get("source_block_id") or item.get("source_image_kind"):
            continue
        if any(str(item.get(key) or "").strip() for key in ("rendered_text", "owned_text")):
            continue
        if item.get("css_visible") is not True or item.get("has_owned_paint") is not True:
            continue
        if _number(item.get("effective_opacity")) <= 0 or _number(item.get("viewport_intersection_ratio")) < 0.9:
            continue
        style = item.get("computed_style")
        if not isinstance(style, Mapping) or style.get("backgroundImage") != "none":
            continue
        # A CSS background/mask can be the media itself, even without children.
        if style.get("maskImage", "none") != "none":
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, Mapping):
            continue
        w, h = _number(bbox.get("width")), _number(bbox.get("height"))
        if w >= width * 0.12 and h >= height * 0.12 and w * h >= width * height * 0.04:
            findings.append(qid)
    return tuple(dict.fromkeys(findings))
