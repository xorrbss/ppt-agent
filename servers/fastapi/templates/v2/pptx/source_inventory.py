from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Literal
import unicodedata


InventoryScope = Literal["artifact", "candidate"]

SOURCE_INVENTORY_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)
_MAX_IDENTIFIER_CHARACTERS = 1_024
_MAX_DISPLAY_FILENAME_CHARACTERS = 240


def _require_sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("invalid_inventory_sha256")
    return value


def _has_unsafe_text(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _require_identifier(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_IDENTIFIER_CHARACTERS
        or _has_unsafe_text(value)
    ):
        raise ValueError("invalid_inventory_identifier")
    return value


def _require_media_type(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 127
        or _MEDIA_TYPE.fullmatch(value) is None
    ):
        raise ValueError("invalid_inventory_media_type")
    return value


@dataclass(frozen=True)
class SecretFreeSourceMetadata:
    """Public source facts that cannot carry a storage path, URL, or credential."""

    display_filename: str
    media_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.display_filename, str)
            or not self.display_filename
            or len(self.display_filename) > _MAX_DISPLAY_FILENAME_CHARACTERS
            or "/" in self.display_filename
            or "\\" in self.display_filename
            or _has_unsafe_text(self.display_filename)
        ):
            raise ValueError("invalid_source_display_filename")
        _require_media_type(self.media_type)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes <= 0
        ):
            raise ValueError("invalid_source_size_bytes")
        _require_sha256(self.sha256)

    def to_manifest(self) -> dict[str, str | int]:
        return {
            "display_filename": self.display_filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class HashedInventoryItem:
    scope: InventoryScope
    identifier: str
    size_bytes: int
    sha256: str
    media_type: str | None = None

    def __post_init__(self) -> None:
        if self.scope not in {"artifact", "candidate"}:
            raise ValueError("invalid_inventory_scope")
        _require_identifier(self.identifier)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("invalid_inventory_size_bytes")
        _require_sha256(self.sha256)
        if self.media_type is not None:
            _require_media_type(self.media_type)

    def to_manifest(self) -> dict[str, str | int]:
        result: dict[str, str | int] = {
            "identifier": self.identifier,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }
        if self.media_type is not None:
            result["media_type"] = self.media_type
        return result


def hashed_inventory_item(
    scope: InventoryScope,
    identifier: str,
    payload: bytes,
    *,
    media_type: str | None = None,
) -> HashedInventoryItem:
    if not isinstance(payload, bytes):
        raise TypeError("inventory_payload_must_be_bytes")
    return HashedInventoryItem(
        scope=scope,
        identifier=identifier,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        media_type=media_type,
    )


def candidate_inventory_item(
    identifier: str,
    candidate: object,
) -> HashedInventoryItem:
    """Hash a canonical JSON candidate without retaining its possibly sensitive body."""

    try:
        payload = json.dumps(
            candidate,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise ValueError("candidate_inventory_requires_canonical_json") from error
    return hashed_inventory_item(
        "candidate",
        identifier,
        payload,
        media_type="application/json",
    )


@dataclass(frozen=True)
class SourceInventory:
    source: SecretFreeSourceMetadata
    artifacts: tuple[HashedInventoryItem, ...] = field(default_factory=tuple)
    candidates: tuple[HashedInventoryItem, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.source, SecretFreeSourceMetadata):
            raise TypeError("inventory_source_metadata_required")
        self._validate_group(self.artifacts, "artifact")
        self._validate_group(self.candidates, "candidate")

    @staticmethod
    def _validate_group(
        items: tuple[HashedInventoryItem, ...],
        scope: InventoryScope,
    ) -> None:
        if not isinstance(items, tuple):
            raise TypeError("inventory_items_must_be_tuple")
        identifiers: set[str] = set()
        for item in items:
            if not isinstance(item, HashedInventoryItem) or item.scope != scope:
                raise ValueError("inventory_scope_group_mismatch")
            if item.identifier in identifiers:
                raise ValueError("duplicate_inventory_identifier")
            identifiers.add(item.identifier)

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema_version": SOURCE_INVENTORY_SCHEMA_VERSION,
            "hash_algorithm": "sha256",
            "source": self.source.to_manifest(),
            "artifacts": [
                item.to_manifest()
                for item in sorted(self.artifacts, key=lambda item: item.identifier)
            ],
            "candidates": [
                item.to_manifest()
                for item in sorted(self.candidates, key=lambda item: item.identifier)
            ],
        }
