"""Replaceable immutable repository contract for authored vNext masters."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Protocol

from models.authored_vnext.master.operations import (
    MasterCatalogItem,
    MasterCatalogPage,
    MasterRevisionResponse,
)
from models.authored_vnext.master.slide import DeckSnapshot

from .errors import (
    DeckAlreadyExistsError,
    DeckDigestConflictError,
    DeckNotFoundError,
    DeckRevisionConflictError,
    MasterDigestConflictError,
    MasterRevisionConflictError,
)


class AuthoredVNextMasterRepository(Protocol):
    def allocate_master_id(self) -> str: ...

    def get_master(
        self, master_id: str, revision: int | None = None
    ) -> MasterRevisionResponse: ...

    def get_published_master(self, master_id: str) -> MasterRevisionResponse: ...

    def list_masters(
        self, *, cursor: str | None, limit: int
    ) -> MasterCatalogPage: ...

    def append_master(
        self,
        record: MasterRevisionResponse,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None: ...

    def get_deck(self, deck_id: str, revision: int | None = None) -> DeckSnapshot: ...

    def append_deck(
        self,
        deck: DeckSnapshot,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None: ...

    def save_candidate(self, candidate_digest: str, payload: dict[str, object]) -> None: ...


def encode_catalog_cursor(master_id: str) -> str:
    return base64.urlsafe_b64encode(master_id.encode("utf-8")).decode("ascii").rstrip("=")


def decode_catalog_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        master_id = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid master catalog cursor") from exc
    if not master_id.startswith("master:"):
        raise ValueError("invalid master catalog cursor")
    return master_id


def master_catalog_item(
    display: MasterRevisionResponse,
    *,
    published: MasterRevisionResponse | None,
    has_draft: bool,
    updated_time: datetime,
) -> MasterCatalogItem:
    contract = display.contract
    return MasterCatalogItem(
        master_id=display.master_id,
        name=contract.name,
        semantic_version=contract.semantic_version,
        published_revision=published.revision if published else None,
        published_digest=published.digest if published else None,
        has_draft=has_draft,
        logo=contract.brand_assets.logo,
        primary_font=contract.font_manifest.primary,
        layout_count=len(contract.layouts.model_dump()) + len(contract.custom_layouts),
        updated_time=updated_time,
    )


def _check_master_cas(
    current: MasterRevisionResponse | None,
    expected_revision: int | None,
    expected_digest: str | None,
) -> None:
    if current is None:
        if expected_revision is not None:
            raise MasterRevisionConflictError()
        if expected_digest is not None:
            raise MasterDigestConflictError()
        return
    if expected_revision is None or current.revision != expected_revision:
        raise MasterRevisionConflictError()
    if expected_digest is None or current.digest != expected_digest:
        raise MasterDigestConflictError()


def _check_deck_cas(
    current: DeckSnapshot | None,
    expected_revision: int | None,
    expected_digest: str | None,
) -> None:
    if current is None:
        if expected_revision is not None or expected_digest is not None:
            raise DeckNotFoundError()
        return
    if expected_revision is None:
        raise DeckAlreadyExistsError()
    if current.revision != expected_revision:
        raise DeckRevisionConflictError()
    if expected_digest is None or current.digest != expected_digest:
        raise DeckDigestConflictError()
