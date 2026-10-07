"""Vision-QA design critique for authored figure slides, with a closed answer set.

This is the same multimodal reviewer the authored vision-QA stage already uses
(``utils.llm_calls.critique_slide``), asked a different question: not "what is
broken" but "is the figure focused, balanced, aligned, generously spaced, and
emphasised in the right place". The answer is deliberately *not* HTML. The model
may only return operations from ``utils.authored_figure_variants.operations`` --
swap the density variant, move the emphasis, shorten one label -- so a critique
can never rewrite a slide's content or its stylesheet.

The call is best-effort: a provider failure, a schema failure or an empty answer
leaves the slide exactly as it was rendered.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, List, Literal, Optional

from llmai.shared import (
    ImageContentPart,
    JSONSchemaResponse,
    SystemMessage,
    TextContentPart,
    UserMessage,
)
from pydantic import BaseModel, Field

from utils.authored_figure_variants.markup import DENSITY_CLASSES
from utils.authored_figure_variants.operations import FigureOperation
from utils.llm_calls.critique_slide import _image_mime_type
from utils.llm_config import enforce_llm_egress_policy, get_client, get_llm_config
from utils.llm_provider import get_model
from utils.llm_utils import generate_structured_with_schema_retries
from utils.schema_utils import prepare_schema_for_validation

LOGGER = logging.getLogger(__name__)
DESIGN_CRITIQUE_PROMPT_VERSION = "authored-design-critique-v1"
MAX_DESIGN_OPERATIONS = 3

RubricAxis = Literal["focus", "balance", "alignment", "whitespace", "emphasis"]


class DesignRubricScore(BaseModel):
    axis: RubricAxis
    #: 1 is unusable, 3 is acceptable, 5 is the reference design.
    score: int = Field(ge=1, le=5)
    detail: str = Field(max_length=160)


class DesignCorrection(BaseModel):
    kind: Literal["set_density", "move_emphasis", "shorten_label"]
    #: ``set_density`` only: the figure root class to switch to.
    density: Optional[Literal["n3", "n4", "n5", "n6"]] = None
    #: ``move_emphasis`` and ``shorten_label``: the zero-based figure node.
    node_index: Optional[int] = Field(default=None, ge=0, le=15)
    #: ``shorten_label`` only: a shorter piece of the label's own text.
    text: Optional[str] = Field(default=None, max_length=40)


class DesignCritique(BaseModel):
    needs_fix: bool
    scores: List[DesignRubricScore] = Field(default_factory=list, max_length=5)
    corrections: List[DesignCorrection] = Field(
        default_factory=list, max_length=MAX_DESIGN_OPERATIONS
    )

    def operations(self) -> tuple[FigureOperation, ...]:
        """The corrections as closed operations; the applier vets each one."""
        return tuple(
            FigureOperation(
                kind=item.kind,
                density=item.density,
                node_index=item.node_index,
                text=item.text,
            )
            for item in self.corrections
        )

    @property
    def critic_score(self) -> float | None:
        """The rubric mean on the 1-5 scale, or None when nothing was scored."""
        if not self.scores:
            return None
        return round(sum(item.score for item in self.scores) / len(self.scores), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs_fix": self.needs_fix,
            "scores": {item.axis: item.score for item in self.scores},
            "critic_score": self.critic_score,
            "corrections": [item.model_dump() for item in self.corrections],
        }


_SYSTEM = (
    "You review one rendered presentation slide as a graphic designer. Judge only "
    "these five axes and score each 1-5: focus (one clear subject, nothing competing), "
    "balance (visual weight spread across the canvas), alignment (shared edges and a "
    "consistent grid), whitespace (generous, even margins; nothing crammed), emphasis "
    "(the single most important node stands out, and it is the right one).\n"
    "You may NOT rewrite the slide. You may only request corrections from this closed "
    "set, at most three, and only when a correction clearly raises a score:\n"
    "- set_density: change the figure's type scale class to one of "
    f"{', '.join(DENSITY_CLASSES)} (n3 is the largest type, n6 the smallest).\n"
    "- move_emphasis: give the highlight to the figure node at node_index, or omit "
    "node_index to remove the highlight entirely.\n"
    "- shorten_label: replace the label of the node at node_index with a shorter piece "
    "of that same label's text. Never invent words; the text you return must appear "
    "inside the current label.\n"
    "Set needs_fix false and return no corrections when the slide is already good."
)


def _build_messages(
    image: bytes,
    *,
    mime_type: str,
    slide_context: Optional[str],
    archetype: Optional[str],
    density_class: Optional[str],
    labels: Sequence[str],
):
    facts = [
        f"Figure archetype: {archetype}" if archetype else "",
        f"Current density class: {density_class}" if density_class else "",
        (
            "Figure node labels, in node_index order: "
            + "; ".join(f"{index}={label}" for index, label in enumerate(labels))
        )
        if labels
        else "",
        f"Intended content: {slide_context}" if slide_context else "",
    ]
    prompt = "Review this rendered slide against the five design axes.\n" + "\n".join(
        line for line in facts if line
    )
    return [
        SystemMessage(content=_SYSTEM),
        UserMessage(
            content=[
                TextContentPart(text=prompt),
                ImageContentPart(data=image, mime_type=mime_type),
            ]
        ),
    ]


async def critique_authored_design(
    image: bytes,
    *,
    slide_context: Optional[str] = None,
    archetype: Optional[str] = None,
    density_class: Optional[str] = None,
    labels: Sequence[str] = (),
) -> Optional[DesignCritique]:
    """Return one design critique, or ``None`` when the reviewer is unavailable."""
    if not image:
        return None
    try:
        mime_type = _image_mime_type(image)
    except ValueError:
        return None
    schema = prepare_schema_for_validation(DesignCritique.model_json_schema(), strict=False)
    messages = _build_messages(
        image,
        mime_type=mime_type,
        slide_context=slide_context,
        archetype=archetype,
        density_class=density_class,
        labels=labels,
    )
    try:
        enforce_llm_egress_policy()
        content = await generate_structured_with_schema_retries(
            get_client(config=get_llm_config()),
            get_model(),
            messages=messages,
            response_format=JSONSchemaResponse(
                name="design_critique", json_schema=schema, strict=False
            ),
            json_schema=schema,
            strict=False,
            validate_schema=True,
        )
        return DesignCritique(**content)
    except Exception as error:  # noqa: BLE001 - the critique is always optional
        LOGGER.warning("[authored] design critique failed: %s", error)
        return None


__all__ = [
    "DESIGN_CRITIQUE_PROMPT_VERSION",
    "MAX_DESIGN_OPERATIONS",
    "DesignCorrection",
    "DesignCritique",
    "DesignRubricScore",
    "critique_authored_design",
]
