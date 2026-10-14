import math
import re
from typing import Optional

from models.presentation_outline_model import PresentationOutlineModel


HEADING_PATTERN = re.compile(r"^\s{0,3}#+\s*(.+)$", re.MULTILINE)
FIRST_SENTENCE_PATTERN = re.compile(r"^\s*([^.?!]+?[.?!])", re.DOTALL)


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


def _get_toc_count_for_total_slides(total_slides: int, title_slide: bool) -> int:
    if total_slides <= 0:
        return 0

    first_pass = math.ceil(((total_slides - 1) if title_slide else total_slides) / 10)
    return math.ceil((total_slides - first_pass) / 10)


def get_no_of_toc_required_for_n_outlines(
    *,
    n_outlines: int,
    title_slide: bool,
    target_total_slides: Optional[int] = None,
) -> int:
    if target_total_slides is not None:
        adjusted_total = max(target_total_slides, n_outlines)
        return _get_toc_count_for_total_slides(adjusted_total, title_slide)

    if n_outlines <= 0:
        return 0

    return math.ceil(((n_outlines - 1) if title_slide else n_outlines) / 10)


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


def _extract_outline_title(content: str) -> str:
    """Get a human-friendly title from an outline's markdown content."""
    text = content or ""

    heading_match = HEADING_PATTERN.search(text)
    if heading_match:
        return heading_match.group(1).strip()

    sentence_match = FIRST_SENTENCE_PATTERN.search(text.strip())
    if sentence_match:
        return sentence_match.group(1).strip()

    for line in text.splitlines():
        stripped_line = line.strip()
        if stripped_line:
            return stripped_line

    return "Slide"
