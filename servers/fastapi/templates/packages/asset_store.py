"""Private content-addressed store for already verified package assets."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath

from .models import PackageAssetInventory
from .validator import TemplatePackageValidationError


class TemplatePackageAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def import_inventory(
        self, source_root: Path, inventory: PackageAssetInventory
    ) -> dict[str, str]:
        mappings: dict[str, str] = {}
        for item in inventory.files:
            logical = PurePosixPath(item.path)
            if logical.is_absolute() or ".." in logical.parts or "\\" in item.path:
                raise TemplatePackageValidationError("template_package_path_invalid")
            source = (source_root / Path(*logical.parts)).resolve(strict=True)
            try:
                source.relative_to(source_root.resolve(strict=True))
            except ValueError as error:
                raise TemplatePackageValidationError("template_package_path_escape") from error
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != item.sha256:
                raise TemplatePackageValidationError("template_package_file_hash_mismatch")
            destination = self.root / "blobs" / "sha256" / digest[:2] / digest
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise TemplatePackageValidationError("template_package_cas_corrupt")
            else:
                temporary = destination.with_name(f".{digest}.{uuid.uuid4().hex}.tmp")
                try:
                    with source.open("rb") as reader, temporary.open("xb") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                        writer.flush()
                        os.fsync(writer.fileno())
                    if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                        raise TemplatePackageValidationError("template_package_cas_write_mismatch")
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            mappings[item.path] = f"sha256:{digest}"
        return mappings


__all__ = ["TemplatePackageAssetStore"]
