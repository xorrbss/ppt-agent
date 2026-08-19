"""What an authored illustration slot asks the image provider to draw.

The slot carries a scene written by the authoring model; the label text the
picture may show is a separate, stricter question: an image model that cannot
render Korean produces broken glyphs, and one that can will happily invent a
number or a company name that never appeared in the deck. So labels are banned
by default and, where the provider is verified to render text, allowed only as
verbatim copies of the slide's own outline markdown.
"""

from __future__ import annotations

import re

from utils.image_provider import image_provider_renders_text

# The scene prompt comes from model output — cap it and strip quotes/newlines.
MAX_SCENE_PROMPT_CHARS = 600
# The slide's outline markdown is the ONLY permitted source of label text, and it
# is user/model data — cap it the same defensive way the scene prompt is capped.
MAX_SLIDE_CONTENT_CHARS = 700
# Matches the cap the authoring rules put on labels inside one illustration.
MAX_ILLUSTRATION_LABELS = 6

# Appended when the selected image provider cannot render text reliably (Gemini
# based providers, unknown backing models, stock photo providers): generated
# glyphs would be broken, so all labels live in the slide HTML instead.
NO_TEXT_SUFFIX = (
    "Absolutely no text, letters, numbers, words or labels anywhere in the image."
)


def clean_scene_prompt(raw: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw or "").strip()
    return cleaned[:MAX_SCENE_PROMPT_CHARS]


def clean_slide_content(raw: str) -> str:
    """Normalize the slide's outline markdown into the label source block.

    Markdown markers are stripped, because the prompt tells the image model to
    copy these strings character for character and it obeys: an outline line of
    ``## 조직 및 인력`` came back painted into the picture with the ``##`` still
    attached. What survives is the line structure - a heading and its bullets is
    what makes a label copyable - with runs of spaces collapsed, blank lines
    dropped, and the whole block capped like the scene prompt is.
    """
    lines: list[str] = []
    for line in (raw or "").splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+•·]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)[:MAX_SLIDE_CONTENT_CHARS]


def _labeled_text_suffix(slide_content: str) -> str:
    """Suffix for providers that CAN render Korean: labels allowed, never invented."""
    return (
        f"You may place at most {MAX_ILLUSTRATION_LABELS} short Korean label strings "
        "in the image, and only where a label genuinely helps the viewer read the "
        "picture; fewer is better and none is acceptable. "
        "EVERY label must be COPIED VERBATIM, character for character, from the "
        "SLIDE CONTENT below. NEVER invent, translate, paraphrase, abbreviate or "
        "extend anything: no invented word, number, percentage, amount, date, unit, "
        "company name, product name or person name may appear in the image. If a "
        "string does not appear verbatim in the SLIDE CONTENT below, it must not "
        "appear in the image. When in doubt, draw the element with no label at all. "
        "No paragraphs, no body copy, no sentences, no legend, no caption block, no "
        "watermark, no logo, no signature.\n"
        "SLIDE CONTENT (the only permitted source of label text):\n"
        f"{slide_content}"
    )


def _provider_renders_text(image_service: object) -> bool:
    """Ask the image service's own runtime settings whether text is safe to draw."""
    try:
        return image_provider_renders_text(getattr(image_service, "settings", None))
    except Exception:  # capability probing must never break generation
        return False


def build_illustration_prompt(
    scene: str, slide_content: str, image_service: object
) -> str:
    """Scene plus the suffix the selected provider's text capability calls for.

    Without slide content there is no verbatim source to copy from, so labels
    stay banned even on a text-capable provider.
    """
    if slide_content and _provider_renders_text(image_service):
        return f"{scene}. {_labeled_text_suffix(slide_content)}"
    return f"{scene}. {NO_TEXT_SUFFIX}"
