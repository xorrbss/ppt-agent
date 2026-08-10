"""Canonical admission gateway layered over the durable workspace store."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
from pathlib import Path
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.assets.contracts import AssetBinding, AssetGatewayError, AssetKind
from services.assets.policies import (
    DEFAULT_ASSET_POLICY_REGISTRY,
    AssetPolicyRegistry,
)
from services.workspace_asset_service import resolve_workspace_asset_download
from services.workspace_service import WorkspaceServiceError
from utils.datetime_utils import get_current_utc_datetime


_ZIP_EXTENSIONS = frozenset(
    {
        ".docx",
        ".docm",
        ".pptx",
        ".pptm",
        ".xlsx",
        ".xlsm",
        ".odt",
        ".odp",
        ".ods",
        ".zip",
    }
)
_OLE_EXTENSIONS = frozenset({".doc", ".ppt", ".xls"})


def _normalized_media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _comparable_asset_time(now: datetime, value: datetime) -> datetime:
    return now.replace(tzinfo=None) if value.tzinfo is None else now


def _magic_matches(path: Path, extension: str) -> bool:
    with path.open("rb") as stream:
        first = stream.read(4096)
    if not first:
        return False
    lower = first.lstrip().lower()
    if extension == ".pdf":
        return first.startswith(b"%PDF-")
    if extension in _ZIP_EXTENSIONS:
        return first.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
    if extension in _OLE_EXTENSIONS:
        return first.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if extension == ".rtf":
        return lower.startswith(b"{\\rtf")
    if extension in {".jpg", ".jpeg"}:
        return first.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return first.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return first.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".bmp":
        return first.startswith(b"BM")
    if extension in {".tif", ".tiff"}:
        return first.startswith((b"II*\x00", b"MM\x00*"))
    if extension == ".webp":
        return first.startswith(b"RIFF") and first[8:12] == b"WEBP"
    if extension == ".svg":
        return b"<svg" in lower and b"\x00" not in first
    if extension in {".ttf", ".otf"}:
        return first.startswith((b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"))
    if extension == ".woff":
        return first.startswith(b"wOFF")
    if extension == ".woff2":
        return first.startswith(b"wOF2")
    if extension == ".json":
        return lower.startswith((b"{", b"[")) and b"\x00" not in first
    if extension in {".txt", ".md", ".markdown", ".csv", ".tsv"}:
        return b"\x00" not in first
    return False


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class AssetGateway:
    def __init__(
        self,
        policies: AssetPolicyRegistry = DEFAULT_ASSET_POLICY_REGISTRY,
        *,
        storage_root: Path | None = None,
    ) -> None:
        self._policies = policies
        self._storage_root = storage_root

    async def bind(
        self,
        session: AsyncSession,
        *,
        asset_id: uuid.UUID,
        kind: AssetKind | str,
        conversation_id: uuid.UUID | None = None,
        storage_root: Path | None = None,
        now: datetime | None = None,
    ) -> AssetBinding:
        """Return a path-free binding after revalidating durable bytes."""

        policy = self._policies.require(kind)
        try:
            asset, private_path = await resolve_workspace_asset_download(
                session,
                asset_id=asset_id,
                storage_root=storage_root or self._storage_root,
            )
        except WorkspaceServiceError as error:
            raise AssetGatewayError(
                error.code,
                status_code=error.status_code,
                retryable=error.retryable,
            ) from error

        if conversation_id is not None and asset.conversation_id != conversation_id:
            raise AssetGatewayError("asset_conversation_mismatch", status_code=404)
        if asset.state not in policy.publication_states:
            raise AssetGatewayError("asset_not_ready", status_code=409, retryable=True)
        if policy.malware_scan_required and asset.state != "ready":
            raise AssetGatewayError("asset_not_scanned", status_code=409, retryable=True)

        current = now or get_current_utc_datetime()
        expires_at = asset.expires_at
        comparable = (
            current.replace(tzinfo=None)
            if expires_at is not None and expires_at.tzinfo is None
            else current
        )
        if expires_at is not None and expires_at <= comparable:
            raise AssetGatewayError("asset_expired", status_code=409)
        if (
            policy.retention_seconds is not None
            and asset.conversation_id is None
            and asset.created_at + timedelta(seconds=policy.retention_seconds)
            <= _comparable_asset_time(current, asset.created_at)
        ):
            raise AssetGatewayError("asset_retention_expired", status_code=409)

        extension = private_path.suffix.lower()
        media_type = _normalized_media_type(asset.media_type)
        if extension not in policy.allowed_extensions:
            raise AssetGatewayError("asset_extension_disallowed", status_code=422)
        if media_type not in policy.allowed_media_types:
            raise AssetGatewayError("asset_media_type_disallowed", status_code=422)

        stat_size = private_path.stat().st_size
        if stat_size != asset.size_bytes:
            raise AssetGatewayError("asset_size_mismatch", status_code=409)
        if stat_size > policy.max_bytes:
            raise AssetGatewayError("asset_too_large", status_code=413)
        if not await asyncio.to_thread(_magic_matches, private_path, extension):
            raise AssetGatewayError("asset_signature_mismatch", status_code=422)

        if not asset.sha256:
            raise AssetGatewayError("asset_digest_missing", status_code=409)
        digest = await asyncio.to_thread(_file_sha256, private_path)
        if not hmac.compare_digest(digest, asset.sha256.lower()):
            raise AssetGatewayError("asset_digest_mismatch", status_code=409)

        return AssetBinding(
            asset_id=asset.id,
            kind=policy.kind,
            media_type=media_type,
            size_bytes=stat_size,
            sha256=digest,
            version=asset.version,
            conversation_id=asset.conversation_id,
        )

    async def bind_many(
        self,
        session: AsyncSession,
        *,
        asset_ids: Iterable[uuid.UUID],
        kind: AssetKind | str,
        conversation_id: uuid.UUID | None = None,
        storage_root: Path | None = None,
    ) -> tuple[AssetBinding, ...]:
        seen: set[uuid.UUID] = set()
        bindings: list[AssetBinding] = []
        for asset_id in asset_ids:
            if asset_id in seen:
                raise AssetGatewayError("duplicate_asset_binding", status_code=422)
            seen.add(asset_id)
            bindings.append(
                await self.bind(
                    session,
                    asset_id=asset_id,
                    kind=kind,
                    conversation_id=conversation_id,
                    storage_root=storage_root,
                )
            )
        return tuple(bindings)

    async def resolve_for_processing(
        self,
        session: AsyncSession,
        *,
        asset_id: uuid.UUID,
        kind: AssetKind | str,
        conversation_id: uuid.UUID | None = None,
        storage_root: Path | None = None,
    ) -> tuple[AssetBinding, Path]:
        """Return a server-only locator capability after canonical admission.

        This method is intentionally absent from HTTP DTOs and durable job
        payloads. Callers must consume the path immediately and compare the
        returned immutable binding after processing; only the binding may be
        persisted.
        """

        root = storage_root or self._storage_root
        binding = await self.bind(
            session,
            asset_id=asset_id,
            kind=kind,
            conversation_id=conversation_id,
            storage_root=root,
        )
        try:
            _asset, private_path = await resolve_workspace_asset_download(
                session,
                asset_id=asset_id,
                storage_root=root,
            )
        except WorkspaceServiceError as error:
            raise AssetGatewayError(
                error.code,
                status_code=error.status_code,
                retryable=error.retryable,
            ) from error
        return binding, private_path
