"""Shared finite allowance for source-image understanding and placement."""

from pathlib import Path

MAX_SOURCE_IMAGE_ANALYSES = 32
SOURCE_IMAGE_PHYSICAL_CALL_LIMIT = 2
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_DOCUMENT_SUFFIXES = {".pdf", ".pptx", ".pptm", ".ppt"}
_DOCUMENT_MEDIA = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    "application/vnd.ms-powerpoint",
}


def source_image_admission_budget(snapshots=(), files=()) -> int:
    """Reserve from admitted identities; document extraction has a fixed ceiling."""
    from utils.get_env import is_authored_source_images_enabled

    if not is_authored_source_images_enabled():
        return 0
    direct = set()
    for snapshot in snapshots or ():
        for binding in snapshot.asset_bindings:
            media = (binding.media_type or "").lower()
            if media in _DOCUMENT_MEDIA:
                return MAX_SOURCE_IMAGE_ANALYSES
            if media in {"image/png", "image/jpeg", "image/webp"}:
                direct.add(str(binding.asset_id))
    for filename in files or ():
        suffix = Path(filename).suffix.lower()
        if suffix in _DOCUMENT_SUFFIXES:
            return MAX_SOURCE_IMAGE_ANALYSES
        if suffix in _IMAGE_SUFFIXES:
            direct.add(str(filename))
    return min(MAX_SOURCE_IMAGE_ANALYSES, len(direct))
