"""Restartable bridges from private legacy locators to canonical assets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import os
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.image_asset import ImageAsset
from models.sql.workspace import WorkspaceAsset
from services.assets.contracts import AssetBinding, AssetGatewayError, AssetKind
from services.assets.gateway import AssetGateway
from services.workspace_asset_service import import_workspace_document_asset
from services.workspace_service import WorkspaceServiceError
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    get_current_owner_id,
    get_current_owner_is_admin,
    reset_current_owner_id,
    set_current_owner_id,
)


@dataclass(frozen=True, slots=True)
class ImageAssetBackfillResult:
    scanned: int
    backfilled: int
    failed: int


def _owner_predicate(*, aggregate_all_owners: bool = False):
    if aggregate_all_owners:
        return None
    owner_id = get_current_owner_id()
    if owner_id is None:
        return ImageAsset.owner_id.is_(None)
    return ImageAsset.owner_id == owner_id


def _path_below(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normcase(str(path)), os.path.normcase(str(root))]
        ) == os.path.normcase(str(root))
    except (OSError, ValueError):
        return False


def _resolve_image_source(
    image_root: Path,
    path: str,
    admin_legacy_image_root: Path | None = None,
) -> tuple[Path, Path]:
    try:
        source_root = Path(image_root).resolve(strict=True)
    except FileNotFoundError:
        source_root = None
    unresolved_source = Path(path)
    if unresolved_source.is_symlink():
        raise AssetGatewayError("legacy_image_path_forbidden", status_code=400)
    source = Path(path).resolve(strict=True)
    if (
        source_root is not None
        and _path_below(source, source_root)
        and source.is_file()
    ):
        return source_root, source
    if admin_legacy_image_root is not None:
        legacy_root = Path(admin_legacy_image_root).resolve(strict=True)
        try:
            relative = source.relative_to(legacy_root)
        except ValueError:
            relative = None
        # Bootstrap-era gallery bytes were direct children of APP_DATA/images.
        # Never treat users/<uuid> or an arbitrary nested tree as legacy admin
        # provenance, even for an administrator.
        if relative is not None and len(relative.parts) == 1 and source.is_file():
            return legacy_root, source
    raise AssetGatewayError("legacy_image_path_forbidden", status_code=400)


def _cleanup_imported_copy(path: Path | None) -> None:
    if path is None:
        return
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


async def _record_canonicalization_failure(
    session: AsyncSession,
    *,
    image_id: uuid.UUID,
    attempt_count: int,
    code: str,
) -> None:
    """Best-effort durable telemetry after the canonical transaction rolled back."""

    try:
        current = (
            await session.execute(
                select(ImageAsset).where(
                    ImageAsset.id == image_id,
                    _owner_predicate(),
                )
            )
        ).scalar_one_or_none()
        if current is None:
            return
        current.canonicalization_attempts = max(
            current.canonicalization_attempts + 1,
            attempt_count,
        )
        current.canonicalization_error_code = code
        session.add(current)
        await session.commit()
    except BaseException:
        # Failure telemetry must never mask the stable canonicalization error.
        await session.rollback()


async def _binding_after_competing_canonicalization(
    session: AsyncSession,
    *,
    image_id: uuid.UUID,
    gateway: AssetGateway,
) -> AssetBinding:
    """Reload a winning CAS after releasing this transaction's snapshot."""

    await session.rollback()
    current = (
        await session.execute(
            select(ImageAsset).where(
                ImageAsset.id == image_id,
                _owner_predicate(),
            )
        )
    ).scalar_one_or_none()
    if current is None:
        raise AssetGatewayError("legacy_image_not_found", status_code=404)
    if current.canonical_asset_id is None:
        raise AssetGatewayError(
            "legacy_image_canonicalization_conflict",
            status_code=409,
            retryable=True,
        )
    return await gateway.bind(
        session,
        asset_id=current.canonical_asset_id,
        kind=AssetKind.IMAGE,
    )


async def _binding_if_commit_succeeded(
    session: AsyncSession,
    *,
    image_id: uuid.UUID,
    imported_asset_id: uuid.UUID,
    gateway: AssetGateway,
) -> AssetBinding | None:
    """Resolve an ambiguous commit response without deleting committed bytes."""

    current = (
        await session.execute(
            select(ImageAsset).where(
                ImageAsset.id == image_id,
                _owner_predicate(),
            )
        )
    ).scalar_one_or_none()
    if current is None or current.canonical_asset_id != imported_asset_id:
        return None
    return await gateway.bind(
        session,
        asset_id=imported_asset_id,
        kind=AssetKind.IMAGE,
    )


async def canonicalize_legacy_image_asset(
    session: AsyncSession,
    *,
    image: ImageAsset | uuid.UUID,
    image_root: Path,
    storage_root: Path | None = None,
    admin_legacy_image_root: Path | None = None,
) -> AssetBinding:
    """Copy one owner-scoped legacy image into immutable canonical storage.

    The old path is retained only for rollback compatibility. It is resolved
    and containment-checked before any bytes are read, preventing traversal and
    symlink escapes. Re-entry returns the already validated binding.
    """

    image_id = image.id if isinstance(image, ImageAsset) else image
    if admin_legacy_image_root is not None and not get_current_owner_is_admin():
        raise AssetGatewayError("admin_migration_required", status_code=403)

    gateway = AssetGateway(storage_root=storage_root)
    copied_path: Path | None = None
    imported_asset_id: uuid.UUID | None = None
    attempt_count = 1
    phase = "source"
    try:
        # PostgreSQL serializes contenders at the row. The conditional UPDATE
        # is also a write-intent claim for SQLite, where FOR UPDATE is ignored;
        # its predicate is re-evaluated after a competing writer commits.
        row = (
            await session.execute(
                select(ImageAsset)
                .where(ImageAsset.id == image_id, _owner_predicate())
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise AssetGatewayError("legacy_image_not_found", status_code=404)
        if row.canonical_asset_id is not None:
            return await gateway.bind(
                session,
                asset_id=row.canonical_asset_id,
                kind=AssetKind.IMAGE,
            )

        attempt_count = row.canonicalization_attempts + 1
        claim = await session.execute(
            update(ImageAsset)
            .where(
                ImageAsset.id == image_id,
                _owner_predicate(),
                ImageAsset.canonical_asset_id.is_(None),
            )
            .values(
                canonicalization_attempts=(
                    ImageAsset.canonicalization_attempts + 1
                ),
                canonicalization_error_code=None,
            )
        )
        if claim.rowcount != 1:
            return await _binding_after_competing_canonicalization(
                session,
                image_id=image_id,
                gateway=gateway,
            )
        row = (
            await session.execute(
                select(ImageAsset)
                .where(ImageAsset.id == image_id, _owner_predicate())
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if row.canonical_asset_id is not None:
            return await _binding_after_competing_canonicalization(
                session,
                image_id=image_id,
                gateway=gateway,
            )

        _source_root, source = await asyncio.to_thread(
            _resolve_image_source,
            image_root,
            row.path,
            admin_legacy_image_root,
        )
        phase = "canonicalization"
        workspace_asset, copied_path = await import_workspace_document_asset(
            session,
            source_path=str(source),
            original_name=source.name,
            storage_root=storage_root,
        )
        imported_asset_id = workspace_asset.id
        # Gallery images are durable user assets, not orphan request uploads.
        workspace_asset.expires_at = None
        session.add(workspace_asset)
        binding = await gateway.bind(
            session,
            asset_id=workspace_asset.id,
            kind=AssetKind.IMAGE,
        )
        canonicalized = await session.execute(
            update(ImageAsset)
            .where(
                ImageAsset.id == image_id,
                _owner_predicate(),
                ImageAsset.canonical_asset_id.is_(None),
            )
            .values(
                canonical_asset_id=workspace_asset.id,
                canonicalized_at=get_current_utc_datetime(),
                canonicalization_error_code=None,
            )
        )
        if canonicalized.rowcount != 1:
            await session.rollback()
            await asyncio.to_thread(_cleanup_imported_copy, copied_path)
            copied_path = None
            return await _binding_after_competing_canonicalization(
                session,
                image_id=image_id,
                gateway=gateway,
            )
        phase = "commit"
        await session.commit()
        return binding
    except BaseException as error:
        await session.rollback()
        if phase == "commit" and imported_asset_id is not None:
            try:
                committed = await _binding_if_commit_succeeded(
                    session,
                    image_id=image_id,
                    imported_asset_id=imported_asset_id,
                    gateway=gateway,
                )
            except BaseException:
                await session.rollback()
                committed = None
            if committed is not None:
                return committed
        await asyncio.to_thread(_cleanup_imported_copy, copied_path)
        if isinstance(error, AssetGatewayError):
            code = error.code
        elif isinstance(error, WorkspaceServiceError):
            code = error.code
        elif phase == "source" and isinstance(error, (OSError, RuntimeError, ValueError)):
            code = "legacy_image_path_invalid"
        else:
            code = "legacy_image_canonicalization_failed"
        await _record_canonicalization_failure(
            session,
            image_id=image_id,
            attempt_count=attempt_count,
            code=code,
        )
        if isinstance(error, AssetGatewayError):
            raise
        if isinstance(error, WorkspaceServiceError):
            raise AssetGatewayError(
                error.code,
                status_code=error.status_code,
                retryable=error.retryable,
            ) from error
        if phase == "source" and isinstance(error, (OSError, RuntimeError, ValueError)):
            raise AssetGatewayError(
                "legacy_image_path_invalid", status_code=400
            ) from error
        if isinstance(error, Exception):
            raise AssetGatewayError(
                "legacy_image_canonicalization_failed",
                status_code=500,
                retryable=True,
            ) from error
        raise


async def backfill_legacy_image_assets(
    session: AsyncSession,
    *,
    image_root: Path,
    storage_root: Path | None = None,
    admin_legacy_image_root: Path | None = None,
    aggregate_all_owners: bool = False,
    limit: int = 100,
) -> ImageAssetBackfillResult:
    """Backfill one bounded owner-scoped batch; failures remain retryable."""

    if limit < 1 or limit > 1000:
        raise ValueError("legacy_image_backfill_limit_invalid")
    if aggregate_all_owners and not get_current_owner_is_admin():
        raise PermissionError("admin_aggregate_required")
    statement = select(ImageAsset).where(ImageAsset.canonical_asset_id.is_(None))
    if aggregate_all_owners:
        statement = statement.execution_options(skip_owner_scope=True)
    else:
        statement = statement.where(_owner_predicate())
    rows = (
        await session.execute(
            statement.order_by(ImageAsset.created_at, ImageAsset.id).limit(limit)
        )
    ).scalars().all()
    backfilled = 0
    failed = 0
    for row in rows:
        owner_token = None
        try:
            owner_image_root = image_root
            if aggregate_all_owners:
                owner_token = set_current_owner_id(row.owner_id)
                if row.owner_id is not None and admin_legacy_image_root is not None:
                    owner_image_root = (
                        Path(admin_legacy_image_root) / "users" / str(row.owner_id)
                    )
            await canonicalize_legacy_image_asset(
                session,
                image=row.id,
                image_root=owner_image_root,
                storage_root=storage_root,
                admin_legacy_image_root=admin_legacy_image_root,
            )
            backfilled += 1
        except AssetGatewayError:
            failed += 1
        finally:
            if owner_token is not None:
                reset_current_owner_id(owner_token)
    return ImageAssetBackfillResult(
        scanned=len(rows),
        backfilled=backfilled,
        failed=failed,
    )


async def dangling_canonical_image_count(
    session: AsyncSession,
    *,
    aggregate_all_owners: bool = False,
) -> int:
    """Return mappings whose canonical target disappeared or is not publishable."""

    statement = select(ImageAsset, WorkspaceAsset).outerjoin(
        WorkspaceAsset,
        WorkspaceAsset.id == ImageAsset.canonical_asset_id,
    ).where(ImageAsset.canonical_asset_id.is_not(None))
    predicate = _owner_predicate(aggregate_all_owners=aggregate_all_owners)
    if predicate is not None:
        statement = statement.where(predicate)
    if aggregate_all_owners:
        statement = statement.execution_options(skip_owner_scope=True)
    rows = (
        await session.execute(
            statement
        )
    ).all()
    return sum(
        1
        for _image, asset in rows
        if asset is None or asset.state != "ready" or asset.sha256 is None
    )


__all__ = [
    "ImageAssetBackfillResult",
    "backfill_legacy_image_assets",
    "canonicalize_legacy_image_asset",
    "dangling_canonical_image_count",
]
