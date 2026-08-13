from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO


def copy_and_hash(source: Path, output: BinaryIO) -> tuple[str, int]:
    """Copy one source while computing its immutable artifact identity."""

    digest = hashlib.sha256()
    size_bytes = 0
    with source.open("rb") as input_file:
        while chunk := input_file.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes
