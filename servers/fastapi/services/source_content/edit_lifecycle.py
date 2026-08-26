"""Workbench edit lifecycle for revision-scoped source coverage receipts."""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from models.sql.presentation import PresentationModel
from services.source_content.manifest_store import mark_coverage_receipts_stale_sync


_SEMANTIC_DECK = re.compile(r"^deck:semantic-([0-9a-f]{32})$")


def presentation_id_for_semantic_deck(deck_id: str) -> uuid.UUID | None:
    match = _SEMANTIC_DECK.fullmatch(deck_id)
    if match is None:
        return None
    return uuid.UUID(hex=match.group(1))


def mark_workbench_edit_modified_in_transaction(
    session: Session,
    *,
    owner_id: uuid.UUID | None,
    deck_id: str,
    prior_revision: int,
    new_revision: int,
) -> bool:
    """Stale coverage in the same transaction that publishes a Scene edit.

    Non-source-content authored decks are a strict no-op. This preserves the
    existing Workbench contract while ensuring a successful source-content edit
    can never leave a pristine 100% claim behind.
    """

    presentation_id = presentation_id_for_semantic_deck(deck_id)
    if presentation_id is None:
        return False
    presentation = session.get(PresentationModel, presentation_id)
    if presentation is None or presentation.owner_id != owner_id:
        return False
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    if not isinstance(theme.get("sourceContent"), dict):
        return False
    mark_coverage_receipts_stale_sync(
        session,
        owner_id=owner_id,
        presentation_id=presentation_id,
        prior_revision=prior_revision,
        new_revision=new_revision,
    )
    return True


__all__ = [
    "mark_workbench_edit_modified_in_transaction",
    "presentation_id_for_semantic_deck",
]
