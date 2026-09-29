"""Create immutable source-native design packages below a caller-owned root."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any, Mapping

from .compiler import CompiledDesignSource, compile_design_source
from .models import DesignPackManifest, PackageFile, seal_manifest
from utils.hashing import sha256_hex


_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,119}$")
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class DesignPackIngestionError(ValueError):
    """The requested package destination or identity is unsafe."""


def _sha256(value: bytes) -> str:
    return sha256_hex(value)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def _safe_relative(value: str) -> str:
    logical = PurePosixPath(value)
    if logical.is_absolute() or ".." in logical.parts or "\\" in value:
        raise DesignPackIngestionError("design_pack_path_invalid")
    return str(logical)


def _file(
    path: str,
    value: bytes,
    *,
    media_type: str,
    role: str,
) -> PackageFile:
    return PackageFile(
        path=_safe_relative(path),
        sha256=_sha256(value),
        size_bytes=len(value),
        media_type=media_type,
        role=role,
    )


def _package_files(compiled: CompiledDesignSource) -> tuple[dict[str, bytes], tuple[PackageFile, ...]]:
    structure_bytes = _json_bytes(
        compiled.structure.model_dump(mode="json", by_alias=True)
    )
    source_suffix = ".pptx" if compiled.structure.source_kind == "pptx" else ".json"
    source_path = f"source/reference{source_suffix}"
    contents: dict[str, bytes] = {
        "structure.json": structure_bytes,
        source_path: compiled.source_bytes,
    }
    inventory: list[PackageFile] = [
        _file(
            "structure.json",
            structure_bytes,
            media_type="application/json",
            role="structure",
        ),
        _file(
            source_path,
            compiled.source_bytes,
            media_type=compiled.source_media_type,
            role="source",
        ),
    ]
    for relative, value in sorted(compiled.representative_assets.items()):
        relative = _safe_relative(relative)
        contents[relative] = value
        inventory.append(
            _file(
                relative,
                value,
                media_type="image/svg+xml",
                role="representative-render",
            )
        )
    return contents, tuple(inventory)


def _write_staged(staging: Path, contents: Mapping[str, bytes], manifest: DesignPackManifest) -> None:
    for relative, value in contents.items():
        target = staging.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    (staging / "manifest.json").write_bytes(
        _json_bytes(manifest.model_dump(mode="json", by_alias=True))
    )


def ingest_design_pack(
    source: Path | str | Mapping[str, Any],
    *,
    pack_id: str,
    package_version: str,
    storage_root: Path | str,
) -> DesignPackManifest:
    """Compile and atomically register one immutable data-only package.

    ``storage_root`` is always supplied by the caller.  The service neither scans
    a repository-global default nor edits a Python registry or style mapping.
    """

    if not _PACK_ID.fullmatch(pack_id):
        raise DesignPackIngestionError("design_pack_id_invalid")
    if not _VERSION.fullmatch(package_version):
        raise DesignPackIngestionError("design_pack_version_invalid")
    root = Path(storage_root).expanduser().resolve(strict=False)
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise DesignPackIngestionError("design_pack_storage_root_invalid")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / pack_id / package_version
    if destination.exists():
        raise DesignPackIngestionError("design_pack_version_exists")

    compiled = compile_design_source(source)
    contents, inventory = _package_files(compiled)
    manifest = seal_manifest(
        pack_id=pack_id,
        package_version=package_version,
        source_kind=compiled.structure.source_kind,
        source_filename=compiled.source_filename,
        source_digest=compiled.structure.source_digest,
        structure_digest=compiled.structure.structure_digest,
        inventory=inventory,
    )
    package_parent = destination.parent
    package_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{package_version}.", dir=package_parent)
    )
    try:
        _write_staged(staging, contents, manifest)
        try:
            os.replace(staging, destination)
        except OSError as error:
            if destination.exists():
                raise DesignPackIngestionError("design_pack_version_exists") from error
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


__all__ = ["DesignPackIngestionError", "ingest_design_pack"]
