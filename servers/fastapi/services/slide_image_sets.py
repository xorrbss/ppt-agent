"""Immutable, content-addressed slide image sets under the app images directory.

Shared by every generation path that publishes slide PNGs. The ``authored/``
directory segment and the helper names are persisted or historical: stored
image references point into ``authored/<presentation id>/sets/<digest>/``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path
from typing import List

from services.authored_checkpoint_artifacts import atomic_write
from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_reference,
    get_images_directory,
)
from utils.hashing import sha256_hex


def _authored_image_set_digest(
    pngs: List[bytes],
    publication_token: str | None = None,
) -> str:
    digest = hashlib.sha256()
    if publication_token:
        digest.update(publication_token.encode("utf-8"))
        digest.update(b"\0")
    for png in pngs:
        digest.update(len(png).to_bytes(8, "big"))
        digest.update(png)
    return digest.hexdigest()


def _authored_image_set_relative_directory(
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    publication_token: str | None = None,
) -> str:
    image_set = _authored_image_set_digest(pngs, publication_token)
    return os.path.join("authored", str(presentation_id), "sets", image_set)


def _save_slide_pngs(
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    publication_token: str | None = None,
) -> List[str]:
    """Persist immutable, content-addressed slide images for one publication.

    The publication token isolates concurrent/retried operation attempts.  A
    failed attempt can therefore remove only its own image set and can never
    overwrite bytes referenced by an already-published revision.
    """

    rel_dir = _authored_image_set_relative_directory(
        presentation_id,
        pngs,
        publication_token,
    )
    abs_dir = os.path.join(get_images_directory(), rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    refs: List[str] = []
    for i, png in enumerate(pngs):
        content_hash = sha256_hex(png)
        fname = f"slide_{i}-{content_hash}.png"
        absolute_path = os.path.join(abs_dir, fname)
        atomic_write(Path(absolute_path), png)
        refs.append(filesystem_image_path_to_app_data_reference(absolute_path))
    return refs


def _remove_partial_slide_pngs(
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    publication_token: str | None = None,
) -> None:
    """Remove only one unpublished, immutable image set."""

    images_root = Path(get_images_directory()).resolve()
    authored_root = (images_root / "authored").resolve()
    presentation_root = (authored_root / str(presentation_id)).resolve()
    sets_root = (presentation_root / "sets").resolve()
    target = (
        images_root
        / _authored_image_set_relative_directory(
            presentation_id,

            pngs,
            publication_token,
        )
    ).resolve()
    if target.parent != sets_root or sets_root.parent != presentation_root:
        raise ValueError("invalid authored slide image cleanup target")
    if target.is_dir():
        shutil.rmtree(target)
