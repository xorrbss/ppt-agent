"""Owner-scoped immutable PNG assets for authored vNext masters."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import struct
from uuid import uuid4


PNG_SIGNATURE = bytes.fromhex("89504e470d0a1a0a")
MAX_MASTER_ASSET_BYTES = 5_000_000
_ASSET_ID = re.compile(r"^asset:([0-9a-f]{32})$")
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class MasterAssetError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _png_dimensions(value: bytes) -> tuple[int, int]:
    if len(value) < 33 or not value.startswith(PNG_SIGNATURE):
        raise MasterAssetError("authored_vnext_master_asset_png_required")
    if value[12:16] != b"IHDR":
        raise MasterAssetError("authored_vnext_master_asset_png_invalid")
    width, height = struct.unpack(">II", value[16:24])
    if width < 1 or height < 1 or width > 8192 or height > 8192:
        raise MasterAssetError("authored_vnext_master_asset_dimensions_invalid")
    if b"IEND" not in value[-32:]:
        raise MasterAssetError("authored_vnext_master_asset_png_invalid")
    return width, height


class OwnerMasterAssetStore:
    """Content-addressed storage whose root is already bound to one owner."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def digest_bytes(value: bytes) -> str:
        return f"sha256:{hashlib.sha256(value).hexdigest()}"

    def _path(self, digest: str) -> Path:
        match = _DIGEST.fullmatch(digest)
        if match is None:
            raise MasterAssetError("authored_vnext_master_asset_digest_invalid")
        hexadecimal = match.group(1)
        return self.root / hexadecimal[:2] / f"{hexadecimal}.png"

    def _require_unfenced(self) -> None:
        fence = self.root.parent / ".fences" / f"{self.root.name}.fenced"
        if fence.exists():
            raise MasterAssetError("authored_vnext_master_asset_owner_fenced")

    def publish_png(self, value: bytes) -> dict[str, object]:
        self._require_unfenced()
        if not value or len(value) > MAX_MASTER_ASSET_BYTES:
            raise MasterAssetError("authored_vnext_master_asset_size_invalid")
        width, height = _png_dimensions(value)
        digest = self.digest_bytes(value)
        hexadecimal = digest.removeprefix("sha256:")
        destination = self._path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != value:
                raise MasterAssetError("authored_vnext_master_asset_collision")
        else:
            temporary = destination.parent / f".{destination.name}.{uuid4().hex}.candidate"
            try:
                with temporary.open("xb") as handle:
                    handle.write(value)
                    handle.flush()
                    os.fsync(handle.fileno())
                if temporary.read_bytes() != value:
                    raise MasterAssetError("authored_vnext_master_asset_readback_failed")
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
                except OSError:
                    try:
                        with destination.open("xb") as handle:
                            handle.write(value)
                            handle.flush()
                            os.fsync(handle.fileno())
                    except FileExistsError:
                        pass
            finally:
                temporary.unlink(missing_ok=True)
        reopened = destination.read_bytes()
        if reopened != value or self.digest_bytes(reopened) != digest:
            raise MasterAssetError("authored_vnext_master_asset_readback_failed")
        return {
            "assetId": f"asset:{hexadecimal[:32]}",
            "digest": digest,
            "mediaType": "image/png",
            "byteLength": len(value),
            "width": width,
            "height": height,
        }

    def open_verified(self, asset_id: str, digest: str) -> bytes:
        self._require_unfenced()
        asset_match = _ASSET_ID.fullmatch(asset_id)
        digest_match = _DIGEST.fullmatch(digest)
        if asset_match is None or digest_match is None:
            raise MasterAssetError("authored_vnext_master_asset_reference_invalid")
        if asset_match.group(1) != digest_match.group(1)[:32]:
            raise MasterAssetError("authored_vnext_master_asset_reference_mismatch")
        path = self._path(digest)
        if not path.is_file():
            raise MasterAssetError("authored_vnext_master_asset_not_found")
        value = path.read_bytes()
        _png_dimensions(value)
        if self.digest_bytes(value) != digest:
            raise MasterAssetError("authored_vnext_master_asset_readback_failed")
        return value


def quarantine_owner_master_assets(
    app_data_root: Path,
    *,
    owner_id: str,
    expected_generation: int,
) -> dict[str, object]:
    """Fence an owner and atomically retain their assets under quarantine."""

    if (
        not owner_id
        or Path(owner_id).name != owner_id
        or expected_generation < 1
    ):
        raise MasterAssetError("authored_vnext_master_asset_fence_invalid")
    base = app_data_root.resolve() / "authored_vnext" / "master-assets"
    users = base / "users"
    source = users / owner_id
    fences = users / ".fences"
    quarantine = base / "quarantine" / owner_id
    destination = quarantine / f"generation-{expected_generation}"
    fences.mkdir(parents=True, exist_ok=True)
    fence = fences / f"{owner_id}.fenced"
    if fence.exists():
        try:
            recorded_generation = int(fence.read_text(encoding="ascii"))
        except (OSError, ValueError) as error:
            raise MasterAssetError(
                "authored_vnext_master_asset_fence_corrupt"
            ) from error
        if recorded_generation != expected_generation:
            raise MasterAssetError(
                "authored_vnext_master_asset_fence_generation_mismatch"
            )
    else:
        try:
            with fence.open("x", encoding="ascii") as handle:
                handle.write(str(expected_generation))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return quarantine_owner_master_assets(
                app_data_root,
                owner_id=owner_id,
                expected_generation=expected_generation,
            )
    def asset_count_without_links(root: Path) -> int:
        count = 0
        for current, directories, files in os.walk(root, followlinks=False):
            for name in [*directories, *files]:
                path = Path(current) / name
                metadata = os.lstat(path)
                if path.is_symlink() or getattr(
                    metadata, "st_file_attributes", 0
                ) & 0x400:
                    raise MasterAssetError(
                        "authored_vnext_master_asset_owner_path_unsafe"
                    )
            count += sum(name.endswith(".png") for name in files)
        return count

    if destination.exists():
        if source.exists():
            raise MasterAssetError(
                "authored_vnext_master_asset_fence_state_diverged"
            )
        asset_count = asset_count_without_links(destination)
        return {
            "ownerId": owner_id,
            "generation": expected_generation,
            "quarantined": True,
            "assetCount": asset_count,
        }
    if not source.exists():
        return {
            "ownerId": owner_id,
            "generation": expected_generation,
            "quarantined": False,
            "assetCount": 0,
        }
    metadata = os.lstat(source)
    if source.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise MasterAssetError("authored_vnext_master_asset_owner_path_unsafe")
    asset_count = asset_count_without_links(source)
    quarantine.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    return {
        "ownerId": owner_id,
        "generation": expected_generation,
        "quarantined": True,
        "assetCount": asset_count,
    }


__all__ = [
    "MAX_MASTER_ASSET_BYTES",
    "MasterAssetError",
    "OwnerMasterAssetStore",
    "quarantine_owner_master_assets",
]
