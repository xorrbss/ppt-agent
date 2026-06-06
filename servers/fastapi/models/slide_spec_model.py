from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# Asset markers — the composer fills the *_prompt/_query (description) and leaves
# *_url empty; process_slide_and_fetch_assets fills the URL afterwards. Pydantic
# field names can't be dunders, so the magic keys are aliases (populate_by_name
# lets the LLM emit the alias form). spec_to_blocks re-emits the dunder dict.
class IconRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    icon_url: str = Field(default="", alias="__icon_url__")
    icon_query: str = Field(default="", alias="__icon_query__", max_length=60)


class ImageRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    image_url: str = Field(default="", alias="__image_url__")
    image_prompt: str = Field(default="", alias="__image_prompt__", max_length=300)
    alt: Optional[str] = Field(default=None, max_length=120)


def _icon_dict(icon: Optional[IconRef]) -> Optional[dict]:
    if not icon:
        return None
    return {"__icon_url__": icon.icon_url, "__icon_query__": icon.icon_query}


def _image_dict(image: ImageRef) -> dict:
    d = {"__image_url__": image.image_url, "__image_prompt__": image.image_prompt}
    if image.alt:
        d["alt"] = image.alt
    return d

# Per-archetype composer output: named slots / bounded homogeneous arrays.
# Phase 3 ships 3 text-only archetypes. Capacity is DECLARED in
# utils/archetype_profiles.py, NOT walked from these schemas (avoids the
# union-array degenerate walk). compose_slides emits one of these per slide;
# spec_to_blocks() converts the named spec into the {archetype, blocks[]} dict
# that AdaptiveSlide renders and SlideModel.content stores.


class CoverSpec(BaseModel):
    archetype: Literal["cover"]
    eyebrow: Optional[str] = Field(default=None, max_length=40)
    title: str = Field(max_length=80)
    subtitle: Optional[str] = Field(default=None, max_length=140)
    speaker_note: str = Field(default="", max_length=500)


class BulletItem(BaseModel):
    text: str = Field(max_length=120)


class OneColumnBulletsSpec(BaseModel):
    archetype: Literal["one-column-bullets"]
    title: str = Field(max_length=80)
    lead: Optional[str] = Field(default=None, max_length=420)
    bullets: List[BulletItem] = Field(min_length=1, max_length=6)
    speaker_note: str = Field(default="", max_length=500)


class StatItem(BaseModel):
    value: str = Field(max_length=8)
    label: str = Field(max_length=28)
    delta: Optional[str] = Field(default=None, max_length=16)
    caption: Optional[str] = Field(default=None, max_length=60)


class StatHeroSpec(BaseModel):
    archetype: Literal["stat-hero"]
    title: str = Field(max_length=80)
    stats: List[StatItem] = Field(min_length=1, max_length=4)
    speaker_note: str = Field(default="", max_length=500)


class SectionDividerSpec(BaseModel):
    archetype: Literal["section-divider"]
    eyebrow: Optional[str] = Field(default=None, max_length=40)
    title: str = Field(max_length=80)
    speaker_note: str = Field(default="", max_length=500)


class BigStatementSpec(BaseModel):
    archetype: Literal["big-statement"]
    text: str = Field(max_length=240)
    attribution: Optional[str] = Field(default=None, max_length=60)
    speaker_note: str = Field(default="", max_length=500)


class AgendaSpec(BaseModel):
    archetype: Literal["agenda"]
    title: str = Field(max_length=80)
    items: List[BulletItem] = Field(min_length=2, max_length=8)
    speaker_note: str = Field(default="", max_length=500)


class ClosingSpec(BaseModel):
    archetype: Literal["closing"]
    title: str = Field(max_length=80)
    subtitle: Optional[str] = Field(default=None, max_length=140)
    items: Optional[List[BulletItem]] = Field(default=None, max_length=4)
    speaker_note: str = Field(default="", max_length=500)


class CardItem(BaseModel):
    title: str = Field(max_length=40)
    text: str = Field(max_length=140)
    icon: Optional[IconRef] = None


class CardGridSpec(BaseModel):
    archetype: Literal["card-grid"]
    title: str = Field(max_length=80)
    cards: List[CardItem] = Field(min_length=3, max_length=8)
    speaker_note: str = Field(default="", max_length=500)


class ComparisonColumn(BaseModel):
    heading: str = Field(max_length=40)
    items: List[str] = Field(min_length=1, max_length=6)


class ComparisonSpec(BaseModel):
    archetype: Literal["comparison"]
    title: str = Field(max_length=80)
    columns: List[ComparisonColumn] = Field(min_length=2, max_length=3)
    speaker_note: str = Field(default="", max_length=500)


class TimelineStep(BaseModel):
    label: str = Field(max_length=20)
    title: str = Field(max_length=40)
    text: str = Field(max_length=120)


class TimelineSpec(BaseModel):
    archetype: Literal["timeline"]
    title: str = Field(max_length=80)
    steps: List[TimelineStep] = Field(min_length=3, max_length=6)
    speaker_note: str = Field(default="", max_length=500)


class TwoColumnSpec(BaseModel):
    archetype: Literal["two-column"]
    title: str = Field(max_length=80)
    lead: Optional[str] = Field(default=None, max_length=300)
    bullets: List[BulletItem] = Field(min_length=2, max_length=6)
    image: ImageRef
    speaker_note: str = Field(default="", max_length=500)


SlideSpecUnion = Annotated[
    Union[
        CoverSpec,
        OneColumnBulletsSpec,
        StatHeroSpec,
        SectionDividerSpec,
        BigStatementSpec,
        AgendaSpec,
        ClosingSpec,
        CardGridSpec,
        ComparisonSpec,
        TimelineSpec,
        TwoColumnSpec,
    ],
    Field(discriminator="archetype"),
]


class PresentationComposition(BaseModel):
    slides: List[SlideSpecUnion]


def archetype_to_layout_id(archetype: str) -> str:
    return f"adaptive:{archetype}"


def spec_to_blocks(spec) -> dict:
    """Convert a per-archetype named spec into the {archetype, blocks[]} dict
    that AdaptiveSlide renders. Block ids match what AdaptiveSlide expects."""
    a = spec.archetype
    blocks: List[dict] = []
    if a == "cover":
        if spec.eyebrow:
            blocks.append({"id": "eyebrow", "type": "eyebrow", "text": spec.eyebrow})
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        if spec.subtitle:
            blocks.append({"id": "subtitle", "type": "subtitle", "text": spec.subtitle})
    elif a == "one-column-bullets":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        if spec.lead:
            blocks.append({"id": "lead", "type": "text", "text": spec.lead})
        blocks.append({
            "id": "bullets",
            "type": "bullets",
            "items": [
                {"id": f"b{i + 1}", "text": it.text} for i, it in enumerate(spec.bullets)
            ],
        })
    elif a == "stat-hero":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        for i, st in enumerate(spec.stats):
            block = {"id": f"s{i + 1}", "type": "stat", "value": st.value, "label": st.label}
            if st.delta:
                block["delta"] = st.delta
            if st.caption:
                block["caption"] = st.caption
            blocks.append(block)
    elif a == "section-divider":
        if spec.eyebrow:
            blocks.append({"id": "eyebrow", "type": "eyebrow", "text": spec.eyebrow})
        blocks.append({"id": "title", "type": "title", "text": spec.title})
    elif a == "big-statement":
        block = {"id": "statement", "type": "quote", "text": spec.text}
        if spec.attribution:
            block["attribution"] = spec.attribution
        blocks.append(block)
    elif a == "agenda":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        blocks.append({
            "id": "bullets",
            "type": "bullets",
            "items": [
                {"id": f"a{i + 1}", "text": it.text} for i, it in enumerate(spec.items)
            ],
        })
    elif a == "closing":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        if spec.subtitle:
            blocks.append({"id": "subtitle", "type": "subtitle", "text": spec.subtitle})
        if spec.items:
            blocks.append({
                "id": "bullets",
                "type": "bullets",
                "items": [
                    {"id": f"c{i + 1}", "text": it.text} for i, it in enumerate(spec.items)
                ],
            })
    elif a == "card-grid":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        for i, c in enumerate(spec.cards):
            blk = {"id": f"card{i + 1}", "type": "card", "title": c.title, "text": c.text}
            icon = _icon_dict(c.icon)
            if icon:
                blk["icon"] = icon
            blocks.append(blk)
    elif a == "comparison":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        for ci, col in enumerate(spec.columns):
            blocks.append({
                "id": f"col{ci + 1}",
                "type": "column",
                "heading": col.heading,
                "items": [
                    {"id": f"col{ci + 1}.{j + 1}", "text": it}
                    for j, it in enumerate(col.items)
                ],
            })
    elif a == "timeline":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        for i, st in enumerate(spec.steps):
            blocks.append({
                "id": f"step{i + 1}", "type": "step",
                "label": st.label, "title": st.title, "text": st.text,
            })
    elif a == "two-column":
        blocks.append({"id": "title", "type": "title", "text": spec.title})
        if spec.lead:
            blocks.append({"id": "lead", "type": "text", "text": spec.lead})
        blocks.append({
            "id": "bullets", "type": "bullets",
            "items": [{"id": f"b{i + 1}", "text": it.text} for i, it in enumerate(spec.bullets)],
        })
        blocks.append({"id": "image", "type": "image", "image": _image_dict(spec.image)})
    return {"archetype": a, "blocks": blocks}
