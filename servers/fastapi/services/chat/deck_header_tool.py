"""The one chat tool that edits an authored deck: its deck-wide slide header.

Authored slides are closed to per-slide chat replacement, so a header request
cannot be served by editing slides one at a time. The header is deck-scoped by
construction, which is exactly what this tool changes.
"""

from __future__ import annotations

from typing import Any
import uuid

from llmai.shared import Tool  # type: ignore[import-not-found]

from services.authored_header_update_service import (
    HeaderUpdateError,
    HeaderUpdateOutcome,
    update_deck_header,
)
from services.chat.schemas import UpdateDeckHeaderInput

_DESCRIPTION = (
    "Change the deck-wide slide header (the top area: accent tick, eyebrow, "
    "title, subline, right-hand slot, RFP requirement badges, divider) and "
    "apply the same change to every slide. Use this for any request about the "
    "top of the slides, the header, the title band, or the requirement "
    "numbers. It also owns whether the deck prints a page number at the bottom "
    "of every slide, so use it for page/slide numbering requests too. It is the "
    "only way to edit an authored deck's header, and it never touches slide "
    "body content. Whether requirement badges exist at all is fixed by the "
    "deck's content and cannot be changed here."
)


def deck_header_tool() -> Tool:
    return Tool(
        name="updateDeckHeader",
        description=_DESCRIPTION,
        schema=UpdateDeckHeaderInput,
        strict=False,
    )


def header_update_message(outcome: HeaderUpdateOutcome) -> str:
    """A factual one-liner the assistant can repeat without overclaiming."""
    if outcome.failures:
        return (
            "The header was left unchanged because "
            f"{len(outcome.failures)} slide(s) could not be patched safely."
        )
    if not outcome.applied:
        return "The request did not change the deck's header contract."
    changed = len(outcome.updated_slides)
    if outcome.structural:
        return (
            f"Restyled the header on {changed} slide(s). "
            f"{', '.join(outcome.structural)} changed the header's structure, so "
            "those slides must be re-authored to take full effect."
        )
    return f"Restyled the header on {changed} slide(s)."


async def run_deck_header_update(
    sql_session: Any,
    presentation_id: uuid.UUID | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Execute one header change and report it in tool-result shape."""
    payload = UpdateDeckHeaderInput(**args)
    if presentation_id is None or sql_session is None:
        return {
            "ok": False,
            "error": "presentation_unavailable",
            "message": "No presentation is bound to this conversation.",
        }
    try:
        outcome = await update_deck_header(
            sql_session, presentation_id, payload.instruction
        )
    except HeaderUpdateError as error:
        return {"ok": False, "error": str(error)}

    return {
        "ok": True,
        "applied": outcome.applied,
        "updated_slide_indexes": list(outcome.updated_slides),
        "unchanged_slide_indexes": list(outcome.unchanged_slides),
        "needs_reauthor": outcome.needs_reauthor,
        "structural_changes": list(outcome.structural),
        "failed_slide_indexes": [index for index, _ in outcome.failures],
        "message": header_update_message(outcome),
    }


__all__ = ["deck_header_tool", "header_update_message", "run_deck_header_update"]
