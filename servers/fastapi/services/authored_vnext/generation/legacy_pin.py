"""Published-master admission bridge for the existing presentation API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
import uuid

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.contract import DeckMasterContract
from models.generate_presentation_request import AuthoredMasterPinInput
from services.authored_vnext.generation.errors import (
    GenerationNotFoundError,
    PublishedMasterRequiredError,
    StaleMasterError,
)
from services.authored_vnext.generation.master_pin import validate_master_pin
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.errors import MasterNotFoundError


@dataclass(frozen=True, slots=True)
class ResolvedLegacyMasterPin:
    owner_id: uuid.UUID
    pin: MasterDigestPin
    contract: DeckMasterContract


LegacyMasterPinIdentity = tuple[str, int, str, str, str]


def canonical_legacy_master_pin_identity(
    payload: Mapping[str, object] | object,
) -> LegacyMasterPinIdentity | None:
    """Normalize the sealed five-field master pin used at durable boundaries."""

    if not isinstance(payload, Mapping):
        return None

    def string_value(*keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    master_id = string_value("masterId", "master_id")
    revision = payload.get("masterRevision")
    if revision is None:
        revision = payload.get("revision")
    if revision is None:
        revision = payload.get("master_revision")
    master_digest = string_value("masterDigest", "digest", "master_digest")
    layout_digest = string_value(
        "layoutRegistryDigest", "layout_registry_digest"
    )
    font_digest = string_value("fontManifestDigest", "font_manifest_digest")

    def is_sha256(value: str | None) -> bool:
        if value is None or not value.startswith("sha256:"):
            return False
        hexadecimal = value.removeprefix("sha256:")
        return len(hexadecimal) == 64 and all(
            character in "0123456789abcdef" for character in hexadecimal
        )

    if (
        master_id is None
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision <= 0
        or not is_sha256(master_digest)
        or not is_sha256(layout_digest)
        or not is_sha256(font_digest)
    ):
        return None
    return (
        master_id,
        revision,
        master_digest,
        layout_digest,
        font_digest,
    )


def generation_repository_for_owner(owner_id: uuid.UUID) -> SqlGenerationRepository:
    owner_scope = str(owner_id)
    base = AuthoredVNextSqlRepository.from_application_database(
        owner_scope=owner_scope,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        owner_id=owner_id,
    )
    return SqlGenerationRepository(base)


def resolve_legacy_master_pin(
    *,
    owner_id: uuid.UUID,
    requested: AuthoredMasterPinInput,
    require_current_head: bool = True,
) -> ResolvedLegacyMasterPin:
    """Expand and validate the public three-field pin, fail-closed.

    New selections must still be the owner's published head.  A compatibility
    fork may explicitly set ``require_current_head=False`` only when replaying
    the immutable pin already persisted on its source presentation.  Both
    modes validate the exact revision, contract digest, layout registry and
    font manifest before downstream generation receives the five-field pin.
    """

    repository = generation_repository_for_owner(owner_id)
    try:
        revision = repository.get_master(requested.master_id, requested.revision)
        head = (
            repository.get_master_head(requested.master_id)
            if require_current_head
            else None
        )
    except (GenerationNotFoundError, MasterNotFoundError) as error:
        raise GenerationNotFoundError("master revision not found") from error
    if revision.status != "published":
        raise PublishedMasterRequiredError()
    if (
        revision.digest != requested.digest
        or (
            head is not None
            and (
                head.revision != requested.revision
                or head.digest != requested.digest
            )
        )
    ):
        raise StaleMasterError("published master head changed")
    font_digest = revision.contract.font_manifest.digest
    if font_digest is None:
        raise StaleMasterError("published master font manifest is unsealed")
    pin = MasterDigestPin(
        master_id=requested.master_id,
        master_revision=requested.revision,
        master_digest=requested.digest,
        layout_registry_digest=canonical_digest(
            revision.contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        font_manifest_digest=font_digest,
    )
    validated = validate_master_pin(repository, pin) if require_current_head else revision
    return ResolvedLegacyMasterPin(
        owner_id=owner_id,
        pin=pin,
        contract=validated.contract,
    )


__all__ = [
    "LegacyMasterPinIdentity",
    "ResolvedLegacyMasterPin",
    "canonical_legacy_master_pin_identity",
    "generation_repository_for_owner",
    "resolve_legacy_master_pin",
]
