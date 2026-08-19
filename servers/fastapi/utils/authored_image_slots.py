"""The ``<img>`` slots an authored slide hands to the illustration pipeline.

An authored slide marks the picture it wants with one of two attributes:
``data-illust-prompt`` (draw this scene) or ``data-asset-id`` (place the source
picture with this id). Both are model output, so this module only *locates* and
*rewrites* those tags — deciding what a slot resolves to, and whether an id may
be resolved at all, belongs to ``authored_illustrations``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

_IMG_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ILLUST_PROMPT_ATTRIBUTE = re.compile(
    r"\bdata-illust-prompt\s*=\s*\"([^\"]*)\"", re.IGNORECASE
)
_ASSET_ID_ATTRIBUTE = re.compile(r"\bdata-asset-id\s*=\s*\"([^\"]*)\"", re.IGNORECASE)
_SRC_ATTRIBUTE_PATTERN = re.compile(r"\bsrc\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE)


@dataclass(frozen=True)
class Slot:
    """One ``<img>`` the illustration pipeline owns, located in the slide HTML."""

    start: int
    end: int
    tag: str
    asset_id: Optional[str]
    scene: Optional[str]


def scan_slots(html: str, *, want_assets: bool, want_illust: bool) -> List[Slot]:
    """Collect the img tags the pipeline owns, in document order.

    A kind the caller did not ask for stays invisible, so a deck generated with
    source images disabled sees exactly the slots it saw before that feature.
    """
    slots: List[Slot] = []
    for match in _IMG_TAG_PATTERN.finditer(html):
        tag = match.group(0)
        asset = _ASSET_ID_ATTRIBUTE.search(tag) if want_assets else None
        illust = _ILLUST_PROMPT_ATTRIBUTE.search(tag) if want_illust else None
        if asset is None and illust is None:
            continue
        slots.append(
            Slot(
                start=match.start(),
                end=match.end(),
                tag=tag,
                asset_id=asset.group(1) if asset is not None else None,
                scene=illust.group(1) if illust is not None else None,
            )
        )
    return slots


def _resolve_slot(tag: str, data_uri: Optional[str]) -> str:
    """Rewrite one slot tag: inject the resolved src, or drop the tag entirely."""
    if data_uri is None:
        return ""
    without_src = _SRC_ATTRIBUTE_PATTERN.sub("", tag)
    return without_src.replace("<img", f'<img src="{data_uri}"', 1)


def rewrite_slots(
    html: str,
    slots: Sequence[Slot],
    keep_index: Optional[int],
    data_uri: Optional[str],
) -> str:
    """Give the kept slot its image and remove every other one.

    Replacing from the last slot backwards is what keeps the earlier spans
    valid. Without a data URI every slot goes, which is the degradation path
    each failure already takes: the layout reserves the area explicitly, so a
    slide without its picture still renders.
    """
    result = html
    for position, slot in reversed(list(enumerate(slots))):
        keep = position == keep_index and data_uri is not None
        replacement = _resolve_slot(slot.tag, data_uri) if keep else ""
        result = result[: slot.start] + replacement + result[slot.end :]
    return result
