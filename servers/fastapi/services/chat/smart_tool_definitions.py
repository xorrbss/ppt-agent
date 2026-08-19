"""Tool definitions offered on Smart HTML decks.

Kept beside the standard toolset rather than inside it so ``tools.py`` stays a
registry, not a catalogue of prose.
"""

from __future__ import annotations

from llmai.shared import Tool  # type: ignore[import-not-found]

from services.chat.deck_header_tool import deck_header_tool
from services.chat.schemas import (
    DeleteSlideInput,
    GenerateAssetsInput,
    GetSlideAtIndexInput,
    GetSmartPresentationContextInput,
    ReadSourceDocumentsInput,
    SaveSmartSlideInput,
    SearchSlidesInput,
)


def smart_tool_definitions() -> list[Tool]:
    return [
        deck_header_tool(),
        Tool(
            name="getSmartPresentationContext",
            description=(
                "Read Smart deck metadata, fonts, outlines, structure, and live "
                "HTML slide previews. Use before new slides, style changes, or "
                "multi-slide edits. Prefer includeSlideHtml=false; fetch exact "
                "target HTML with getSlideAtIndex."
            ),
            schema=GetSmartPresentationContextInput,
            strict=False,
        ),
        Tool(
            name="readSourceDocuments",
            description=(
                "Read parsed source documents linked to this presentation when "
                "the user asks to use, summarize, quote, or visualize their content."
            ),
            schema=ReadSourceDocumentsInput,
            strict=False,
        ),
        Tool(
            name="searchSlide",
            description=(
                "Search rendered Smart slide text and return matching slide indices "
                "and snippets."
            ),
            schema=SearchSlidesInput,
            strict=False,
        ),
        Tool(
            name="getSlideAtIndex",
            description=(
                "Read one live Smart HTML slide by zero-based index. Set "
                "includeFullContent=true before every edit to receive the complete "
                "authoritative html field."
            ),
            schema=GetSlideAtIndexInput,
            strict=False,
        ),
        Tool(
            name="generateAssets",
            description=(
                "Generate image or icon assets before inserting new visual URLs "
                "into Smart slide HTML."
            ),
            schema=GenerateAssetsInput,
            strict=False,
        ),
        Tool(
            name="saveSlide",
            description=(
                "Replace or insert one Smart HTML slide. Pass the complete HTML "
                "fragment in html. For an edit, first read the slide, keep its "
                "design and scripts, and use replaceOldSlideAtIndex=true."
            ),
            schema=SaveSmartSlideInput,
            strict=False,
        ),
        Tool(
            name="deleteSlide",
            description=(
                "Delete a Smart slide by zero-based index and reindex the rest."
            ),
            schema=DeleteSlideInput,
            strict=False,
        ),
    ]


__all__ = ["smart_tool_definitions"]
