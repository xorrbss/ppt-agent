"""Crash-safe, immutable storage for reviewed authored-slide PNGs.

Presentation versions retain the slide JSON, including its image reference.  A
content-addressed filename therefore makes the referenced bytes durable across a
later quality-review update and subsequent version restore.  Published assets are
intentionally not deleted here: retention cannot be decided safely without checking
both live slides and every persisted presentation version.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_reference,
    get_images_directory,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory sync; opening directories is unsupported on Windows."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        # The file itself was already flushed and atomically published. Some
        # filesystems do not implement fsync for directory handles.
        pass
    finally:
        os.close(directory_fd)


def publish_reviewed_png(
    presentation_id: uuid.UUID,
    slide_position: int,
    png: bytes,
) -> str:
    """Atomically publish PNG bytes under an immutable content-addressed name.

    The caller must update slide HTML/JSON only after this returns successfully.
    On failure, no pre-existing reviewed asset is changed and the unpublished
    temporary file is removed on a best-effort basis.
    """
    if isinstance(slide_position, bool) or not isinstance(slide_position, int):
        raise TypeError("slide_position must be an integer")
    if slide_position < 0:
        raise ValueError("slide_position must be non-negative")
    if not isinstance(png, bytes):
        raise TypeError("png must be bytes")
    if not png:
        raise ValueError("png must not be empty")

    content_sha256 = hashlib.sha256(png).hexdigest()
    relative_directory = Path("authored") / str(presentation_id)
    filename = f"slide_{slide_position}_{content_sha256}.png"
    temporary_path: Path | None = None
    try:
        absolute_directory = Path(get_images_directory()) / relative_directory
        absolute_path = absolute_directory / filename
        absolute_directory.mkdir(parents=True, exist_ok=True)
        if absolute_path.exists():
            if (
                not absolute_path.is_file()
                or _sha256_file(absolute_path) != content_sha256
            ):
                raise RuntimeError("Existing reviewed PNG violates its content hash")
            return filesystem_image_path_to_app_data_reference(str(absolute_path))

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=absolute_directory,
            prefix=f".{filename}.",
            suffix=".tmp",
            delete=False,
        ) as image_file:
            temporary_path = Path(image_file.name)
            image_file.write(png)
            image_file.flush()
            os.fsync(image_file.fileno())

        os.replace(temporary_path, absolute_path)
        temporary_path = None
        _fsync_directory(absolute_directory)
        if _sha256_file(absolute_path) != content_sha256:
            raise RuntimeError("Published reviewed PNG failed content verification")
        return filesystem_image_path_to_app_data_reference(str(absolute_path))
    except OSError:
        # This error can be persisted in a public background-task response. Do not
        # include the host's app-data or temporary path in it.
        raise RuntimeError("Reviewed PNG could not be published atomically") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
