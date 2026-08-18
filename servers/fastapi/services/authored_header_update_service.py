"""Change one authored deck's header contract and apply it to every slide.

This is the deck-scoped counterpart to per-slide editing: a chat request about
the header updates the contract once and then rewrites the same header on all
slides, which is the only way the deck stays uniform after an edit.

Slide body content is never touched. Style-level changes are applied as local
patches with no further model calls; changes that would restructure the header
are reported back so the caller can decide whether re-authoring is worth it.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_header_contract import (
    AuthoredHeaderContract,
    default_header_contract,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_header_contract_service import load_header_contract
from services.presentation_version_service import snapshot_slides
from utils.authored_header_restyle import apply_header_restyle, structural_changes
from utils.llm_calls.author_header_contract import revise_header_contract

LOGGER = logging.getLogger(__name__)

VERSION_LABEL = "header-contract-update"


class HeaderUpdateError(Exception):
    """The deck cannot take a header update at all."""


@dataclass(frozen=True)
class HeaderUpdateOutcome:
    """What a header update did, in terms the chat layer can report verbatim."""

    applied: bool
    contract: AuthoredHeaderContract
    updated_slides: tuple[int, ...] = ()
    unchanged_slides: tuple[int, ...] = ()
    structural: tuple[str, ...] = ()
    failures: tuple[tuple[int, str], ...] = ()

    @property
    def needs_reauthor(self) -> bool:
        return bool(self.structural)


async def _authored_slides(
    sql_session: AsyncSession, presentation_id: uuid.UUID
) -> list[SlideModel]:
    result = await sql_session.execute(
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index)
    )
    return list(result.scalars().all())


def current_header_contract(
    presentation: PresentationModel,
) -> AuthoredHeaderContract:
    """The deck's contract, reconstructed conservatively when none was stored.

    Decks authored before contracts existed fall back to the plain default with
    the requirement slot off, so an update can never invent badges for them.
    """
    stored = load_header_contract(presentation.theme)
    if stored is not None:
        return stored
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    return default_header_contract(primary=str(theme.get("primary") or "#2563EB"))


async def update_deck_header(
    sql_session: AsyncSession,
    presentation_id: uuid.UUID,
    instruction: str,
) -> HeaderUpdateOutcome:
    """Revise the deck's header contract and restyle every authored slide."""
    presentation = await sql_session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise HeaderUpdateError("presentation_not_found")
    slides = await _authored_slides(sql_session, presentation_id)
    authored = [slide for slide in slides if (slide.html_content or "").strip()]
    if not authored:
        raise HeaderUpdateError("presentation_has_no_authored_slides")

    current = current_header_contract(presentation)
    revised = await revise_header_contract(current, instruction)
    if revised == current:
        return HeaderUpdateOutcome(applied=False, contract=current)

    structural = structural_changes(current, revised)
    restyled: dict[uuid.UUID, str] = {}
    updated: list[int] = []
    unchanged: list[int] = []
    failures: list[tuple[int, str]] = []
    for slide in authored:
        result = apply_header_restyle(slide.html_content or "", current, revised)
        if result.failure_reason is not None:
            failures.append((slide.index, result.failure_reason))
            continue
        if result.changed:
            restyled[slide.id] = result.html
            updated.append(slide.index)
        else:
            unchanged.append(slide.index)

    if failures:
        # Leave the deck exactly as it was rather than publishing a header that
        # is new on some slides and old on others.
        LOGGER.warning(
            "Header update aborted for %s; %d slide(s) could not be patched",
            presentation_id,
            len(failures),
        )
        return HeaderUpdateOutcome(
            applied=False,
            contract=current,
            structural=structural,
            failures=tuple(failures),
        )

    if restyled:
        await snapshot_slides(
            sql_session,
            presentation_id,
            [slide.model_dump(mode="json") for slide in slides],
            label=VERSION_LABEL,
            force=True,
        )
        for slide in authored:
            replacement = restyled.get(slide.id)
            if replacement is not None:
                slide.html_content = replacement
                sql_session.add(slide)

    theme = dict(presentation.theme) if isinstance(presentation.theme, dict) else {}
    theme["headerContract"] = revised.model_dump(mode="json")
    presentation.theme = theme
    sql_session.add(presentation)
    await sql_session.commit()

    return HeaderUpdateOutcome(
        applied=True,
        contract=revised,
        updated_slides=tuple(updated),
        unchanged_slides=tuple(unchanged),
        structural=structural,
    )


__all__ = [
    "HeaderUpdateError",
    "HeaderUpdateOutcome",
    "VERSION_LABEL",
    "current_header_contract",
    "update_deck_header",
]
