from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field

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


SlideSpecUnion = Annotated[
    Union[CoverSpec, OneColumnBulletsSpec, StatHeroSpec],
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
    return {"archetype": a, "blocks": blocks}
