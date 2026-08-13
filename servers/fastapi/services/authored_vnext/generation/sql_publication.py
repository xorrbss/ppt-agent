"""Atomic SQL publication of canonical generation deck revisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models.authored_vnext.deck import DeckGenerationSnapshot
from models.sql.authored_vnext_repository import (
    AuthoredVNextDeckRevision,
    AuthoredVNextEntityHead,
    AuthoredVNextSlideRevision,
)
from services.authored_vnext.generation.errors import GenerationConflictError


def _payload(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def append_generation_deck(
    session: Session,
    *,
    owner_id,
    owner_scope: str,
    tenant_id: str,
    deck: DeckGenerationSnapshot,
    expected_revision: int | None,
    expected_digest: str | None,
) -> None:
    scope = (
        AuthoredVNextEntityHead.owner_scope == owner_scope,
        AuthoredVNextEntityHead.tenant_id == tenant_id,
    )
    head = session.scalar(
        select(AuthoredVNextEntityHead).where(
            *scope,
            AuthoredVNextEntityHead.entity_kind == "deck",
            AuthoredVNextEntityHead.entity_id == deck.deck_id,
        )
    )
    if head is None:
        if expected_revision is not None or expected_digest is not None or deck.revision != 1:
            raise GenerationConflictError("new deck fence failed")
    elif (
        head.revision != expected_revision
        or head.digest != expected_digest
        or deck.revision != head.revision + 1
    ):
        raise GenerationConflictError("deck CAS failed")
    session.add(AuthoredVNextDeckRevision(
        owner_id=owner_id,
        owner_scope=owner_scope,
        tenant_id=tenant_id,
        deck_id=deck.deck_id,
        revision=deck.revision,
        digest=deck.digest,
        master_id=deck.master.master_id,
        master_revision=deck.master.master_revision,
        master_digest=deck.master.master_digest,
        payload=_payload(deck),
    ))
    for item in deck.slides:
        slide = item.slide
        exists = session.scalar(
            select(AuthoredVNextSlideRevision.id).where(
                AuthoredVNextSlideRevision.owner_scope == owner_scope,
                AuthoredVNextSlideRevision.tenant_id == tenant_id,
                AuthoredVNextSlideRevision.deck_id == deck.deck_id,
                AuthoredVNextSlideRevision.slide_id == slide.slide_id,
                AuthoredVNextSlideRevision.revision == slide.revision,
            )
        )
        if exists is not None:
            continue
        session.add(AuthoredVNextSlideRevision(
            owner_id=owner_id,
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            deck_id=deck.deck_id,
            slide_id=slide.slide_id,
            revision=slide.revision,
            spec_digest=slide.spec_digest,
            master_digest=slide.master_digest,
            payload=_payload(item),
        ))
    session.flush()
    if head is None:
        session.add(AuthoredVNextEntityHead(
            owner_id=owner_id,
            owner_scope=owner_scope,
            tenant_id=tenant_id,
            entity_kind="deck",
            entity_id=deck.deck_id,
            revision=deck.revision,
            digest=deck.digest,
            generation=1,
        ))
        return
    changed = session.execute(
        update(AuthoredVNextEntityHead)
        .where(
            AuthoredVNextEntityHead.id == head.id,
            AuthoredVNextEntityHead.revision == expected_revision,
            AuthoredVNextEntityHead.digest == expected_digest,
        )
        .values(
            revision=deck.revision,
            digest=deck.digest,
            generation=head.generation + 1,
        )
    ).rowcount
    if changed != 1:
        raise GenerationConflictError("deck head race")


__all__ = ["append_generation_deck"]
