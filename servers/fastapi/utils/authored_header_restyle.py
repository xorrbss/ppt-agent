"""Apply a header-contract change to an already authored slide, without an LLM.

Slides authored under a contract carry ``data-header-part`` markers, so the
elements a contract change affects can be located deterministically. That turns
"restyle every slide's header" from N re-authoring calls into N local patches
that leave slide body content untouched.

Changes that alter the header's *structure* — adding a subline that no slide
has, switching the right slot's role, turning requirement badges on — cannot be
patched and are reported instead, so the caller can decide whether to re-author.

Every term of the band's derivation belongs to that group — the gaps and the
type alike. The band height is a number each slide computed from what it
rendered and then declared on its own band root; restyling neither renders nor
writes attributes, so it can move the band's colours but never anything that
changes how tall a row comes out. ``structural_changes`` names those fields and
this module deliberately emits no action for them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re

from models.authored_header_contract import (
    HEADER_PART_VALUES,
    AuthoredHeaderContract,
)
from utils.authored_dom_quality import is_valid_authored_qid
from utils.authored_header_style_map import (
    chip_styles as _chip_styles,
    part_styles as _part_styles,
    structural_changes,
)
from utils.authored_patch_engine import AuthoredPatchPlan, apply_authored_patch
from utils.authored_header_validation import (
    HEADER_PART_ATTRIBUTE,
    VOID_HTML_TAGS,
)

#: ``AuthoredPatchPlan`` caps one plan at 16 actions; larger restyles are
#: applied as consecutive plans rather than being silently truncated.
_MAX_ACTIONS_PER_PLAN = 16


#: A chip carries an identifier or the ``+N`` overflow count. The group may also
#: contain a static label, which must never be restyled as if it were a chip.
_CHIP_TEXT = re.compile(r"^(?:[A-Z]{2,6}-\d{2,4}(?:-\d{1,3})?|\+\d{1,3})$")


@dataclass(frozen=True)
class HeaderQids:
    """Where each header part lives in one slide."""

    parts: dict[str, str] = field(default_factory=dict)
    chips: tuple[str, ...] = ()


class _HeaderQidParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: dict[str, str] = {}
        self.chips: list[str] = []
        self._depth = 0
        self._requirements_depth = 0
        self._candidate: str | None = None
        self._candidate_text = ""

    def _close_candidate(self) -> None:
        """Keep a requirement-group child only when its text reads as a chip."""
        if self._candidate and _CHIP_TEXT.match(self._candidate_text.strip().upper()):
            self.chips.append(self._candidate)
        self._candidate = None
        self._candidate_text = ""

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        # A void element HTML never closes would otherwise raise the depth for
        # good, so the requirement group would stay open past its own end tag and
        # every later element would be offered as one of its chips.
        if tag.lower() not in VOID_HTML_TAGS:
            self._depth += 1
        values = {name.lower(): (value or "") for name, value in attrs}
        qid = values.get("data-qid", "")
        part = (values.get(HEADER_PART_ATTRIBUTE) or "").strip().lower()
        if part in HEADER_PART_VALUES and qid and is_valid_authored_qid(qid):
            self.parts.setdefault(part, qid)
            if part == "requirements":
                self._requirements_depth = self._depth
            return
        # One chip is a direct child of the requirement group.
        if (
            self._requirements_depth
            and self._depth == self._requirements_depth + 1
            and qid
            and is_valid_authored_qid(qid)
        ):
            self._close_candidate()
            self._candidate = qid

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_HTML_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in VOID_HTML_TAGS:
            return
        if self._requirements_depth:
            if self._depth <= self._requirements_depth:
                self._close_candidate()
                self._requirements_depth = 0
            elif self._candidate and self._depth == self._requirements_depth + 1:
                self._close_candidate()
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._candidate:
            self._candidate_text += data


def header_qids(html: str) -> HeaderQids:
    """Locate the header parts of one authored slide."""
    parser = _HeaderQidParser()
    parser.feed(html or "")
    parser.close()
    return HeaderQids(parts=dict(parser.parts), chips=tuple(parser.chips))


def _px(value: int) -> str:
    return f"{value}px"


@dataclass(frozen=True)
class HeaderRestyleResult:
    html: str
    changed: bool
    structural: tuple[str, ...]
    failure_reason: str | None = None


def _resize_actions(
    old: AuthoredHeaderContract,
    new: AuthoredHeaderContract,
    qids: HeaderQids,
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    tick_qid = qids.parts.get("tick")
    if tick_qid and new.eyebrow.tick.enabled:
        size: dict[str, object] = {"action": "resize", "qid": tick_qid}
        if old.eyebrow.tick.width_px != new.eyebrow.tick.width_px:
            size["width"] = _px(new.eyebrow.tick.width_px)
        if old.eyebrow.tick.height_px != new.eyebrow.tick.height_px:
            size["height"] = _px(new.eyebrow.tick.height_px)
        if len(size) > 2:
            actions.append(size)
    divider_qid = qids.parts.get("divider")
    if (
        divider_qid
        and new.divider.enabled
        and old.divider.thickness_px != new.divider.thickness_px
    ):
        actions.append(
            {
                "action": "resize",
                "qid": divider_qid,
                "height": _px(new.divider.thickness_px),
            }
        )
    # A chip's height is the right block's line height, so resizing chips under a
    # band root that still declares the old number is the same defect as
    # restyling the title's font size: structural_changes reports it instead.
    return actions


def build_restyle_actions(
    old: AuthoredHeaderContract,
    new: AuthoredHeaderContract,
    qids: HeaderQids,
) -> list[dict[str, object]]:
    """Return the patch actions that carry ``old`` to ``new`` on one slide."""
    actions: list[dict[str, object]] = [
        {"action": "setStyle", "qid": qids.parts[part], "styles": styles}
        for part, styles in sorted(_part_styles(old, new).items())
        if styles and part in qids.parts
    ]
    chip_styles = _chip_styles(old, new)
    if chip_styles:
        actions.extend(
            {"action": "setStyle", "qid": chip_qid, "styles": dict(chip_styles)}
            for chip_qid in qids.chips
        )
    actions.extend(_resize_actions(old, new, qids))
    return actions


def apply_header_restyle(
    html: str,
    old: AuthoredHeaderContract,
    new: AuthoredHeaderContract,
) -> HeaderRestyleResult:
    """Rewrite one slide's header to the new contract, or explain why it cannot.

    The slide is left untouched when any action fails, so a deck never ends up
    half-restyled.
    """
    structural = structural_changes(old, new)
    qids = header_qids(html)
    actions = build_restyle_actions(old, new, qids)
    if not actions:
        return HeaderRestyleResult(html=html, changed=False, structural=structural)

    current = html
    for start in range(0, len(actions), _MAX_ACTIONS_PER_PLAN):
        chunk = actions[start : start + _MAX_ACTIONS_PER_PLAN]
        result = apply_authored_patch(
            current, AuthoredPatchPlan.model_validate({"actions": chunk})
        )
        if not result.ok:
            return HeaderRestyleResult(
                html=html,
                changed=False,
                structural=structural,
                failure_reason=result.fallback_reason or "patch_failed",
            )
        current = result.html
    return HeaderRestyleResult(html=current, changed=True, structural=structural)


__all__ = [
    "HeaderQids",
    "HeaderRestyleResult",
    "apply_header_restyle",
    "build_restyle_actions",
    "header_qids",
    "structural_changes",
]
