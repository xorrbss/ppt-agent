"""The advisory vision request: vocabulary, prompt, schema and message shape.

Split out of ``services/authored_vision_advisory.py`` so the exact request the
provider receives can be asserted next to the request the working vision-QA
critique path builds (``utils/llm_calls/critique_slide.py``), which is the only
multimodal call this fork has ever seen succeed on the codex provider.

Bench run 30 (2026-09-06) came back as ``public_provider_empty_output``: the
model streamed reasoning for eight seconds after an HTTP 200 and emitted no
output text at all, so ``utils/provider_idempotency.py`` refused the response.
Two things here answer that, and one thing deliberately does not:

* the message shape is exactly the critique path's -- one ``SystemMessage``
  then one ``UserMessage`` whose content is ``[TextContentPart,
  ImageContentPart]`` in that order, asserted in the tests; and
* the model is shown the exact JSON it must answer with, not only a schema,
  so "answer nothing" is never the easiest way to satisfy the instruction.
* the output budget is NOT what starved run 30. llmai 0.3.7's chatgpt client
  drops ``max_tokens`` (``del max_tokens``; the request sends
  ``max_output_tokens=Omit()``), so on codex this number is inert. It is kept
  honest for the providers that do read it, where 1200 would truncate a long
  deck's answer.
"""

from __future__ import annotations

from typing import Any

#: Reasoning plus answer, on the providers that read it (codex ignores it, see
#: the module docstring). A closed-vocabulary answer for a 40-slide deck runs
#: to a few hundred tokens and reasoning takes the rest.
ADVISORY_MAX_OUTPUT_TOKENS = 4000
ADVISORY_REASONING_EFFORT = "low"
ADVISORY_PROMPT_VERSION = "authored-vision-advisory-v2"

#: An advisory whose vocabulary drifts cannot be compared across bench runs, so
#: the model is given a closed list and anything else it invents is dropped.
ISSUE_VOCABULARY: tuple[str, ...] = (
    "empty_band",
    "cramped",
    "unbalanced",
    "illegible",
    "off_palette",
    "broken_figure",
    "inconsistent_with_deck",
)

#: The spellings a model reaches for instead of the token. Mapped, not dropped,
#: because "off-palette" is the same finding as ``off_palette``.
ISSUE_ALIASES: dict[str, str] = {
    "emptyband": "empty_band",
    "emptyspace": "empty_band",
    "deadband": "empty_band",
    "crowded": "cramped",
    "unbalancedlayout": "unbalanced",
    "unreadable": "illegible",
    "lowcontrast": "illegible",
    "offpalette": "off_palette",
    "wrongpalette": "off_palette",
    "brokenfigure": "broken_figure",
    "brokenimage": "broken_figure",
    "inconsistentwithdeck": "inconsistent_with_deck",
    "inconsistent": "inconsistent_with_deck",
}

#: Shown to the model verbatim. A schema alone left it free to answer in prose.
ADVISORY_EXAMPLE = (
    '{"slides":[{"index":3,"issues":["empty_band","unbalanced"]},'
    '{"index":11,"issues":["broken_figure"]}]}'
)

SYSTEM_PROMPT = (
    "You are a presentation design reviewer looking at a contact sheet of one "
    "deck. Each thumbnail is one slide and carries its slide number in the top "
    "left corner. Report only what a reader would notice at a glance, using "
    "ONLY these issue words: " + ", ".join(ISSUE_VOCABULARY) + ". "
    "empty_band: a large dead area the page never fills. cramped: content "
    "packed with no breathing room. unbalanced: weight pooled on one side or "
    "corner. illegible: type too small or too low in contrast to read. "
    "off_palette: colours that disagree with the rest of the deck. "
    "broken_figure: a chart, table, icon or image that rendered wrong. "
    "inconsistent_with_deck: this page's visual language differs from its "
    "neighbours. Omit a slide entirely when it looks fine. Invent nothing, "
    "explain nothing, and use no other word. Answer with strict JSON and "
    "nothing else, in exactly this form: " + ADVISORY_EXAMPLE
)


def advisory_json_schema(*, slide_count: int | None = None) -> dict[str, Any]:
    """The closed-vocabulary response schema, as the provider receives it."""

    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["slides"],
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", "issues"],
                    "properties": {
                        "index": {"type": "integer"},
                        "issues": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(ISSUE_VOCABULARY)},
                        },
                    },
                },
            }
        },
    }
    if slide_count is not None:
        array = schema["properties"]["slides"]
        array.update(minItems=slide_count, maxItems=slide_count)
        array["items"]["properties"]["index"].update(minimum=1, maximum=slide_count)
    return schema


def build_advisory_messages(sheet: bytes, *, slide_count: int, required: bool = False) -> list[Any]:
    """One system message and one user message of [text, image], as critique does."""

    from llmai.shared import (
        ImageContentPart,
        SystemMessage,
        TextContentPart,
        UserMessage,
    )

    return [
        SystemMessage(content=(
            "Review every numbered slide for layout balance, readable text, intact charts, tables and images, "
            "and consistent design. Use only these issue words: " + ", ".join(ISSUE_VOCABULARY) + ". "
            f"Return JSON with a slides array containing exactly one entry for every index 1 through {slide_count}. "
            "Each entry must have index and issues; return an empty issues array for a slide only after inspecting it. "
            "Never omit a clean page or return an empty slides array."
            if required else SYSTEM_PROMPT)),
        UserMessage(
            content=[
                TextContentPart(
                    text=(
                        f"Review this deck contact sheet. It has {slide_count} "
                        f"slides, numbered 1 to {slide_count} in reading order "
                        "across the sheet."
                    )
                ),
                ImageContentPart(data=sheet, mime_type="image/jpeg"),
            ]
        ),
    ]


__all__ = [
    "ADVISORY_EXAMPLE",
    "ADVISORY_MAX_OUTPUT_TOKENS",
    "ADVISORY_PROMPT_VERSION",
    "ADVISORY_REASONING_EFFORT",
    "ISSUE_ALIASES",
    "ISSUE_VOCABULARY",
    "SYSTEM_PROMPT",
    "advisory_json_schema",
    "build_advisory_messages",
]
