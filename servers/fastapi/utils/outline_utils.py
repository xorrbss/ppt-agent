import math
import re

import dirtyjson

from models.presentation_outline_model import PresentationOutlineModel


HEADING_PATTERN = re.compile(r"^\s{0,3}#+\s*(.+)$", re.MULTILINE)


def explicit_outline_title(content: str) -> str | None:
    """Reuse a single meaningful Markdown headline, never a body excerpt."""
    headings = HEADING_PATTERN.findall(content or "")
    if not headings:
        return None
    # Subheadings are body structure. Multiple headings at the first heading's
    # level leave several possible display titles, so do not choose one.
    matches = list(re.finditer(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", content or "", re.MULTILINE))
    if not matches:
        return None
    level = len(matches[0].group(1))
    candidates = [match.group(2).strip() for match in matches if len(match.group(1)) <= level]
    if len(candidates) != 1:
        return None
    title = candidates[0]
    if re.fullmatch(r"(?:(?:slide|page|슬라이드|페이지)\s*)?\d+[\s.:)\-]*", title, re.IGNORECASE):
        return None
    if re.fullmatch(
        r"(?:원본|original|source)\s*(?:(?:page|slide|페이지|슬라이드)\s*)?"
        r"\d+(?:\s*/\s*\d+)?\s*(?:page|slide|페이지|슬라이드)?[\s.:)\-]*",
        title,
        re.IGNORECASE,
    ):
        return None
    if not re.search(r"[A-Za-z가-힣\u3400-\u9fff]", title):
        return None
    return title


def get_presentation_title_from_presentation_outline(
    presentation_outline: PresentationOutlineModel,
) -> str:
    if not presentation_outline.slides:
        return "Untitled Presentation"

    first_content = presentation_outline.slides[0].content or ""

    if re.match(r"^\s*#{1,6}\s*Page\s+\d+\b", first_content):
        first_content = re.sub(
            r"^\s*#{1,6}\s*Page\s+\d+\b[\s,:\-]*",
            "",
            first_content,
            count=1,
        )

    return (
        first_content[:100]
        .replace("#", "")
        .replace("/", "")
        .replace("\\", "")
        .replace("\n", " ")
    )


def get_no_of_outlines_to_generate_for_n_slides(
    *,
    n_slides: int,
    toc: bool,
    title_slide: bool,
) -> int:
    if toc:
        n_toc_1 = math.ceil(((n_slides - 1) if title_slide else n_slides) / 10)
        n_toc_2 = math.ceil((n_slides - n_toc_1) / 10)

        return n_slides - n_toc_2

    else:
        return n_slides



OUTLINE_PARSE_FAILED = "Failed to generate presentation outlines. Please try again."
OUTLINE_SLIDE_COUNT_SHORT = (
    "Failed to generate presentation outlines with requested "
    "number of slides. Please try again."
)


class OutlineResponseError(ValueError):
    """The outline model's streamed answer could not become the requested outline.

    ``detail`` is the user-facing message; ``cause`` is the parse error, if any.
    """

    def __init__(self, detail: str, cause: Exception | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.cause = cause


def parse_outline_response(
    text: str, n_slides_to_generate: int | None
) -> PresentationOutlineModel:
    """Parse a streamed outline answer and fit it to the requested slide count.

    Shared by the SSE outline endpoint and the generation pipeline. Only
    undershooting is fatal: overshooting by a slide or two is common and
    recoverable, so the first ``n_slides_to_generate`` slides are kept.
    """
    try:
        payload = dict(dirtyjson.loads(text))
    except Exception as error:
        raise OutlineResponseError(OUTLINE_PARSE_FAILED, error) from error
    outline = PresentationOutlineModel(**payload)
    if n_slides_to_generate is not None:
        if len(outline.slides) < n_slides_to_generate:
            raise OutlineResponseError(OUTLINE_SLIDE_COUNT_SHORT)
        outline.slides = outline.slides[:n_slides_to_generate]
    return outline
