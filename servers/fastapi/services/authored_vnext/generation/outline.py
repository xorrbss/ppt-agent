"""Published-master-aware deterministic outline planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from models.authored_vnext.master.contract import DeckMasterContract, LayoutSlot
from models.authored_vnext.master.operations import MasterRevisionResponse
from models.authored_vnext.master.slide import ExplicitTitle, TitleFitResult
from services.authored_vnext.master.errors import (
    MasterDigestConflictError,
    MasterNotPublishedError,
)

from .title_fit import fit_and_promote_title


OutlineKind = Literal["content", "section"]
SlideRole = Literal["cover", "section", "standard", "long-title"]


@dataclass(frozen=True, slots=True)
class OutlineItem:
    slide_id: str
    title: str
    body: str
    kind: OutlineKind = "content"


@dataclass(frozen=True, slots=True)
class BodySlotModelInput:
    """The complete and deliberately narrow model-visible generation surface."""

    slot_id: str
    role: Literal["body"]
    bounds: tuple[float, float, float, float]
    style: str
    body: str

    def to_model_payload(self) -> dict[str, object]:
        """Serialize only the writable body slot; master furniture is impossible."""

        x, y, width, height = self.bounds
        return {
            "bodySlot": {
                "id": self.slot_id,
                "role": self.role,
                "bounds": {"x": x, "y": y, "width": width, "height": height},
                "style": self.style,
            },
            "body": self.body,
        }


@dataclass(frozen=True, slots=True)
class PlannedSlide:
    index: int
    slide_id: str
    role: SlideRole
    layout_id: str
    title: ExplicitTitle
    title_fit: TitleFitResult
    model_input: BodySlotModelInput


@dataclass(frozen=True, slots=True)
class GenerationOutlinePlan:
    master_id: str
    master_revision: int
    master_digest: str
    slides: tuple[PlannedSlide, ...]


def _coerce_item(value: OutlineItem | Mapping[str, object], index: int) -> OutlineItem:
    if isinstance(value, OutlineItem):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"outline[{index}] must be OutlineItem or mapping")
    aliases = {"slideId": "slide_id"}
    normalized = {aliases.get(str(key), str(key)): item for key, item in value.items()}
    allowed = {"slide_id", "title", "body", "kind"}
    unknown = set(normalized) - allowed
    if unknown:
        raise ValueError(f"outline[{index}] contains unsupported fields: {sorted(unknown)}")
    try:
        item = OutlineItem(**normalized)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"outline[{index}] is incomplete") from exc
    if item.kind not in {"content", "section"}:
        raise ValueError(f"outline[{index}].kind is invalid")
    if not item.slide_id or not item.title.strip() or not item.body.strip():
        raise ValueError(f"outline[{index}] requires slide_id, title and body")
    return item


def _require_published(master: MasterRevisionResponse) -> DeckMasterContract:
    if master.status != "published":
        raise MasterNotPublishedError()
    contract = master.contract
    if contract.digest is None or contract.font_manifest.digest is None:
        raise MasterDigestConflictError("published master is not sealed")
    if contract.digest != master.digest or contract.master_id != master.master_id:
        raise MasterDigestConflictError("published master identity/digest mismatch")
    return contract


def _body_slot(contract: DeckMasterContract, layout_id: str) -> LayoutSlot:
    layout = next(
        (
            item
            for item in (
                contract.layouts.cover,
                contract.layouts.section,
                contract.layouts.standard,
                contract.layouts.long_title,
            )
            if item.id == layout_id
        ),
        None,
    )
    if layout is None:
        raise ValueError("layout is not registered by the published master")
    slots = [slot for slot in layout.slots if slot.role == "body"]
    if len(slots) != 1:
        raise ValueError("layout must contain exactly one writable body slot")
    slot = slots[0]
    if slot.ownership.locked or not slot.ownership.model_writable:
        raise ValueError("body slot must be model-writable")
    return slot


def _model_input(slot: LayoutSlot, body: str) -> BodySlotModelInput:
    bounds = slot.bounds
    return BodySlotModelInput(
        slot_id=slot.id,
        role="body",
        bounds=(bounds.x, bounds.y, bounds.width, bounds.height),
        style=slot.style,
        body=body,
    )


def plan_outline(
    published_master: MasterRevisionResponse,
    outline: Sequence[OutlineItem | Mapping[str, object]],
    *,
    effective_contract: DeckMasterContract | None = None,
) -> GenerationOutlinePlan:
    """Assign roles/layouts and seal a body-only prompt plan.

    Cover and section roles use their required semantic layouts. Ordinary
    standard slides may be promoted to long-title after the CJK title-fit gate.
    """

    published_contract = _require_published(published_master)
    contract = effective_contract or published_contract
    if (
        contract.digest is None
        or contract.master_id != published_contract.master_id
        or contract.canvas != published_contract.canvas
    ):
        raise MasterDigestConflictError("effective planning contract is invalid")
    if not outline:
        raise ValueError("outline must contain at least one slide")
    items = tuple(_coerce_item(value, index) for index, value in enumerate(outline))
    slide_ids = [item.slide_id for item in items]
    if len(set(slide_ids)) != len(slide_ids):
        raise ValueError("outline slide IDs must be unique")

    slides: list[PlannedSlide] = []
    for index, item in enumerate(items):
        if index == 0:
            base_role: SlideRole = "cover"
        elif item.kind == "section":
            base_role = "section"
        else:
            base_role = "standard"
        semantic_fixed = base_role in {"cover", "section"}
        initial_layout_id = {
            "cover": contract.layouts.cover.id,
            "section": contract.layouts.section.id,
            "standard": contract.layouts.standard.id,
        }[base_role]
        planned_title = fit_and_promote_title(
            contract,
            item.title,
            initial_layout_id,
            allow_promotion=not semantic_fixed,
        )
        role: SlideRole = (
            "long-title" if planned_title.promoted else base_role
        )
        slot = _body_slot(contract, planned_title.layout_id)
        slides.append(
            PlannedSlide(
                index=index,
                slide_id=item.slide_id,
                role=role,
                layout_id=planned_title.layout_id,
                title=planned_title.title,
                title_fit=planned_title.fit,
                model_input=_model_input(slot, item.body),
            )
        )
    return GenerationOutlinePlan(
        master_id=published_master.master_id,
        master_revision=published_master.revision,
        master_digest=published_master.digest,
        slides=tuple(slides),
    )


__all__ = [
    "BodySlotModelInput",
    "GenerationOutlinePlan",
    "OutlineItem",
    "PlannedSlide",
    "SlideRole",
    "plan_outline",
]
