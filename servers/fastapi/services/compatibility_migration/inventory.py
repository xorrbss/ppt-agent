"""Checksum and drain inventory for one authored-only migration census."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Literal, TypeAlias

ChecksumStatus: TypeAlias = Literal[
    "match", "mismatch", "missing", "not_checked", "not_recorded", "unreadable"
]
ACTIVE_GENERATION_STATES = frozenset({"queued", "running", "staging", "awaiting_resume"})
ACTIVE_WORKSPACE_STATES = frozenset(
    {"queued", "running", "cancelling", "waiting_for_assets", "blocked", "approval_required", "awaiting_approval"}
)
ACTIVE_IMPORT_STATES = frozenset({"queued", "processing", "finalizing"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _id(value: object) -> str:
    identifier = _read(value, "id")
    return str(identifier) if identifier is not None else "unknown"


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_sha(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.lower().removeprefix("sha256:")
    return candidate if _SHA256.fullmatch(candidate) else None


def _path_checksum(locator: str | None, storage_root: Path | None) -> tuple[str | None, ChecksumStatus]:
    if locator is None:
        return None, "not_checked"
    path = Path(locator)
    if not path.is_absolute() and storage_root is not None:
        path = storage_root / path
    if storage_root is None and not path.is_absolute():
        return None, "not_checked"
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.is_symlink():
            return None, "unreadable"
        return sha256(resolved.read_bytes()).hexdigest(), "not_recorded"
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"


@dataclass(frozen=True, slots=True)
class InventoryItem:
    kind: str
    record_id: str
    owner_id: str | None
    locator: str | None
    recorded_sha256: str | None
    actual_sha256: str | None
    checksum_status: ChecksumStatus
    owner_isolated: bool
    state: str | None = None


@dataclass(frozen=True, slots=True)
class DrainItem:
    kind: str
    record_id: str
    state: str


@dataclass(frozen=True, slots=True)
class PresentationInventory:
    presentation_id: str
    owner_id: str | None
    source_digest: str
    runtime_digest: str
    artifact_digest: str
    items: tuple[InventoryItem, ...]
    active_jobs: tuple[DrainItem, ...]
    checkpoint_count: int
    archive_count: int
    archive_checksum_complete: bool
    owner_isolation_ok: bool
    drain_required: bool

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["items"] = [asdict(item) for item in self.items]
        value["active_jobs"] = [asdict(item) for item in self.active_jobs]
        return value


def _inventory_item(
    *,
    kind: str,
    record: object,
    presentation_owner: str | None,
    locator_field: str | None,
    digest_field: str | None,
    storage_root: Path | None,
) -> InventoryItem:
    owner_value = _read(record, "owner_id")
    owner = str(owner_value) if owner_value is not None else presentation_owner
    locator_value = _read(record, locator_field) if locator_field else None
    locator = str(locator_value) if locator_value is not None else None
    recorded = _normalized_sha(_read(record, digest_field)) if digest_field else None
    actual, status = _path_checksum(locator, storage_root)
    if actual is not None and recorded is not None:
        status = "match" if actual == recorded else "mismatch"
    elif actual is None and status == "not_checked" and recorded is not None:
        status = "not_checked"
    owner_isolated = owner == presentation_owner
    return InventoryItem(
        kind=kind,
        record_id=_id(record),
        owner_id=owner,
        locator=locator,
        recorded_sha256=recorded,
        actual_sha256=actual,
        checksum_status=status,
        owner_isolated=owner_isolated,
        state=(
            str(_read(record, "state", _read(record, "status")))
            if _read(record, "state", _read(record, "status")) is not None
            else None
        ),
    )


def build_presentation_inventory(
    presentation: object,
    slides: Sequence[object],
    *,
    reference_snapshots: Sequence[object] = (),
    assets: Sequence[object] = (),
    original_pptx: Sequence[object] = (),
    generation_jobs: Sequence[object] = (),
    workspace_operations: Sequence[object] = (),
    checkpoints: Sequence[object] = (),
    artifacts: Sequence[object] = (),
    archives: Sequence[object] = (),
    storage_root: Path | None = None,
) -> PresentationInventory:
    """Build a read-only inventory; caller supplies already-linked owner rows."""

    presentation_id = _id(presentation)
    owner_value = _read(presentation, "owner_id")
    owner_id = str(owner_value) if owner_value is not None else None
    items: list[InventoryItem] = []
    for index, path in enumerate(_read(presentation, "file_paths") or []):
        items.append(
            _inventory_item(
                kind="source",
                record={"id": f"legacy-path-{index}", "owner_id": owner_id, "path": path},
                presentation_owner=owner_id,
                locator_field="path",
                digest_field=None,
                storage_root=storage_root,
            )
        )
    for snapshot in reference_snapshots:
        items.append(
            _inventory_item(
                kind="source_snapshot",
                record=snapshot,
                presentation_owner=owner_id,
                locator_field=None,
                digest_field="content_sha256",
                storage_root=storage_root,
            )
        )
    for asset in assets:
        items.append(
            _inventory_item(
                kind="asset",
                record=asset,
                presentation_owner=owner_id,
                locator_field="storage_key",
                digest_field="sha256",
                storage_root=storage_root,
            )
        )
    for source in original_pptx:
        items.append(
            _inventory_item(
                kind="original_pptx",
                record=source,
                presentation_owner=owner_id,
                locator_field="source_storage_key",
                digest_field="source_sha256",
                storage_root=storage_root,
            )
        )
    for checkpoint in checkpoints:
        items.append(
            _inventory_item(
                kind="checkpoint",
                record=checkpoint,
                presentation_owner=owner_id,
                locator_field="render_path",
                digest_field="render_sha256",
                storage_root=storage_root,
            )
        )
    for artifact in artifacts:
        items.append(
            _inventory_item(
                kind="artifact",
                record=artifact,
                presentation_owner=owner_id,
                locator_field="storage_key",
                digest_field="sha256",
                storage_root=storage_root,
            )
        )
    for archive in archives:
        items.append(
            _inventory_item(
                kind="archive",
                record=archive,
                presentation_owner=owner_id,
                locator_field="storage_key",
                digest_field="sha256",
                storage_root=storage_root,
            )
        )

    active: list[DrainItem] = []
    for kind, rows, states in (
        ("generation_job", generation_jobs, ACTIVE_GENERATION_STATES),
        ("workspace_operation", workspace_operations, ACTIVE_WORKSPACE_STATES),
        ("template_v2_import", original_pptx, ACTIVE_IMPORT_STATES),
    ):
        for row in rows:
            state = str(_read(row, "state", ""))
            if state in states:
                active.append(DrainItem(kind, _id(row), state))

    archive_items = [item for item in items if item.kind == "archive"]
    archive_complete = bool(archive_items) and all(
        item.recorded_sha256 is not None and item.checksum_status == "match"
        for item in archive_items
    )
    source_identity = {
        "presentation": presentation,
        "reference_snapshots": list(reference_snapshots),
        "assets": list(assets),
        "original_pptx": list(original_pptx),
    }
    runtime_identity = {"presentation": presentation, "slides": list(slides)}
    artifact_identity = [
        {
            "id": item.record_id,
            "kind": item.kind,
            "sha256": item.recorded_sha256,
            "state": item.state,
        }
        for item in items
        if item.kind in {"artifact", "archive", "checkpoint"}
    ]
    return PresentationInventory(
        presentation_id=presentation_id,
        owner_id=owner_id,
        source_digest=_canonical_digest(source_identity),
        runtime_digest=_canonical_digest(runtime_identity),
        artifact_digest=_canonical_digest(artifact_identity),
        items=tuple(items),
        active_jobs=tuple(active),
        checkpoint_count=len(checkpoints),
        archive_count=len(archive_items),
        archive_checksum_complete=archive_complete,
        owner_isolation_ok=(
            all(item.owner_isolated for item in items)
            and all(
                _read(slide, "owner_id") in {None, _read(presentation, "owner_id")}
                for slide in slides
            )
        ),
        drain_required=bool(active),
    )


__all__ = [
    "ACTIVE_GENERATION_STATES",
    "ACTIVE_IMPORT_STATES",
    "ACTIVE_WORKSPACE_STATES",
    "DrainItem",
    "InventoryItem",
    "PresentationInventory",
    "build_presentation_inventory",
]
