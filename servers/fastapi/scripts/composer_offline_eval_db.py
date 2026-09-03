"""Read-only PostgreSQL lookup for the Composer offline harness.

Loads a stored manifest by reference snapshot id or manifest id, then follows
the snapshot's asset bindings to the uploaded PPTX under ``workspace_assets``
so the enriched extraction can be re-run offline. Nothing is written: the
manifest store's ``analyze_reference_snapshot`` is deliberately not called
(re-analysis of an analyzed snapshot now yields a different digest and 409).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Sequence

from scripts.composer_offline_eval_inputs import (
    SourceBundle,
    SourceLookupError,
    extract_bundle,
    file_digest,
)

STORED_ONLY = "stored (original PPTX bytes not reachable; no typography enrichment)"


def existing_pptx(root: Path, storage_key: str, original_name: str) -> Path | None:
    """Resolve a workspace asset storage key to an existing PPTX file, or ``None``."""

    parts = [part for part in storage_key.split("/") if part]
    if not parts or "\\" in storage_key or any(part in {".", ".."} for part in parts):
        return None
    path = root.joinpath(*parts)
    looks_like_pptx = path.suffix.lower() == ".pptx" or original_name.lower().endswith(".pptx")
    return path if looks_like_pptx and path.is_file() else None


async def _find_manifest(session: Any, *, manifest_id: str | None, snapshot_id: str | None) -> dict[str, Any] | None:
    from sqlalchemy import select

    from models.sql.source_content import SourceContentManifestRecord

    if manifest_id:
        query = select(SourceContentManifestRecord).where(SourceContentManifestRecord.manifest_id == manifest_id)
    elif snapshot_id:
        query = (
            select(SourceContentManifestRecord)
            .where(SourceContentManifestRecord.reference_snapshot_id == uuid.UUID(snapshot_id))
            .order_by(SourceContentManifestRecord.created_at.desc())
            .limit(1)
        )
    else:
        raise SourceLookupError("database mode needs --snapshot or --manifest-id")
    row = (await session.execute(query)).scalars().first()
    if row is None:
        return None
    return {
        "manifestJson": dict(row.manifest_json),
        "ownerScope": str(row.owner_scope),
        "referenceSnapshotId": str(row.reference_snapshot_id),
        "sourceDigest": str(row.source_digest),
        "manifestDigest": str(row.manifest_digest),
    }


async def _locate_pptx(session: Any, *, snapshot_id: str, root: Path, owner: str) -> tuple[Path | None, list[str]]:
    from sqlalchemy import select

    from models.sql.reference_snapshot import ReferenceSnapshot
    from models.sql.workspace import WorkspaceAsset

    notes: list[str] = []
    snapshot = (
        await session.execute(select(ReferenceSnapshot).where(ReferenceSnapshot.id == uuid.UUID(snapshot_id)))
    ).scalar_one_or_none()
    if snapshot is None:
        return None, [f"owner {owner}: snapshot {snapshot_id} not visible"]
    for binding in snapshot.asset_bindings or ():
        asset_id = str(binding.get("assetId") or binding.get("asset_id") or "")
        if not asset_id:
            continue
        asset = (
            await session.execute(select(WorkspaceAsset).where(WorkspaceAsset.id == uuid.UUID(asset_id)))
        ).scalar_one_or_none()
        if asset is None:
            notes.append(f"owner {owner}: asset {asset_id} not visible")
            continue
        path = existing_pptx(root, asset.storage_key or "", asset.original_name or "")
        if path is None:
            notes.append(f"owner {owner}: asset {asset_id} has no PPTX file at its storage key")
            continue
        notes.append(
            f"owner {owner}: asset {asset_id} state={asset.state} expires_at={asset.expires_at}"
            " (lifecycle checks bypassed: read-only offline evaluation)"
        )
        return path, notes
    return None, notes


async def _lookup(
    *, snapshot_id: str | None, manifest_id: str | None, owners: Sequence[str], tenant_id: str | None
) -> tuple[dict[str, Any] | None, Path | None, list[str]]:
    from services.database import async_session_maker, dispose_engines
    from utils.owner_context import set_current_owner_id, set_current_tenant_id
    from utils.path_helpers import get_writable_path

    root = Path(get_writable_path("workspace_assets"))
    notes: list[str] = []
    record: dict[str, Any] | None = None
    pptx_path: Path | None = None
    try:
        for owner in owners:
            set_current_owner_id(uuid.UUID(owner))
            set_current_tenant_id(tenant_id)
            async with async_session_maker() as session:
                if record is None:
                    record = await _find_manifest(session, manifest_id=manifest_id, snapshot_id=snapshot_id)
                    if record is None:
                        notes.append(f"owner {owner}: manifest not visible")
                        continue
                    notes.append(f"owner {owner}: manifest found (owner_scope={record['ownerScope']})")
                pptx_path, path_notes = await _locate_pptx(
                    session, snapshot_id=record["referenceSnapshotId"], root=root, owner=owner
                )
                notes.extend(path_notes)
                if pptx_path is not None:
                    break
    finally:
        await dispose_engines()
    return record, pptx_path, notes


def _re_extract(
    record: dict[str, Any], pptx_path: Path, notes: list[str]
) -> tuple[dict[str, Any], Path | None, str, dict[str, bytes]]:
    """Enriched extraction of the located bytes; falls back to the stored manifest."""

    stored = record["manifestJson"]
    digest = file_digest(pptx_path)
    if digest != record["sourceDigest"]:
        notes.append(f"PPTX digest {digest} differs from manifest sourceDigest; using stored manifest")
        return stored, None, STORED_ONLY, {}
    try:
        manifest, assets = extract_bundle(
            pptx_path, owner_id=record["ownerScope"], reference_snapshot_id=record["referenceSnapshotId"]
        )
    except Exception as error:  # noqa: BLE001 - report and fall back, never abort the evaluation
        notes.append(f"re-extraction failed ({type(error).__name__}: {error}); using stored manifest")
        return stored, pptx_path, STORED_ONLY, {}
    same = manifest.get("manifestDigest") == record["manifestDigest"]
    notes.append(
        "re-extracted manifest digest "
        + ("matches the stored manifest" if same else "differs from the stored manifest (enrichment changed it)")
    )
    return manifest, pptx_path, "re-extracted", assets


async def bundle_from_database(
    *,
    snapshot_id: str | None,
    manifest_id: str | None,
    owners: Sequence[str],
    tenant_id: str | None,
    label: str,
) -> SourceBundle:
    record, pptx_path, notes = await _lookup(
        snapshot_id=snapshot_id, manifest_id=manifest_id, owners=owners, tenant_id=tenant_id
    )
    if record is None:
        raise SourceLookupError(
            f"manifest not found for snapshot={snapshot_id!r} manifest_id={manifest_id!r} under owners {list(owners)}"
        )
    assets: dict[str, bytes] = {}
    if pptx_path is None:
        manifest, typography_source = record["manifestJson"], STORED_ONLY
        notes.append("no PPTX bytes: image markers cannot be resolved offline; image slides will error")
    else:
        manifest, pptx_path, typography_source, assets = _re_extract(record, pptx_path, notes)
    return SourceBundle(
        label=label,
        mode="database",
        manifest=manifest,
        stored_manifest=record["manifestJson"],
        pptx_path=pptx_path,
        owner_scope=record["ownerScope"],
        reference_snapshot_id=record["referenceSnapshotId"],
        typography_source=typography_source,
        notes=notes,
        assets=assets,
    )


__all__ = ["STORED_ONLY", "bundle_from_database", "existing_pptx"]
