"""Fail-closed local image replacement contract for Template V2.

Only caller-supplied bytes are accepted.  There is intentionally no URL,
network, object-storage, or eager-orphan-deletion path in this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from typing import Any, Mapping, Sequence

from PIL import Image, UnidentifiedImageError

from .wire_codec import decode_wire_layouts


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ("JPEG", "jpg"),
    "image/png": ("PNG", "png"),
    "image/webp": ("WEBP", "webp"),
}


class TemplateV2LocalAssetError(ValueError):
    """Stable content-free rejection for an unsafe local image."""


@dataclass(frozen=True, slots=True)
class ValidatedLocalImage:
    asset_id: str
    filename: str
    media_type: str
    extension: str
    size_bytes: int
    width: int
    height: int
    sha256: str

    def provenance(self) -> dict[str, Any]:
        return {
            "source": "local-upload",
            "original_filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ImageReplacementPreview:
    preview_id: str
    source_digest: str
    element_path: tuple[str | int, ...]
    before_reference: str
    previous_asset_record: dict[str, Any] | None
    after_reference: str
    asset: ValidatedLocalImage


@dataclass(frozen=True, slots=True)
class AssetRetentionIntent:
    previous_reference: str
    replacement_reference: str
    previous_asset_record: dict[str, Any] | None
    defer_orphan_cleanup: bool = True
    delete_immediately: bool = False


@dataclass(frozen=True, slots=True)
class ImageReplacementResult:
    layouts: dict[str, Any]
    revision: int
    preview_id: str
    asset_record: dict[str, Any]
    retention: AssetRetentionIntent


@dataclass(frozen=True, slots=True)
class CropCandidate:
    candidate_id: str
    strategy: str
    reason_code: str
    focus_x: float
    focus_y: float
    crop_scale: float
    render_digest: str


@dataclass(frozen=True, slots=True)
class CropCandidatePreview:
    preview_id: str
    source_digest: str
    element_path: tuple[str | int, ...]
    asset: ValidatedLocalImage
    candidates: tuple[CropCandidate, ...]


@dataclass(frozen=True, slots=True)
class CropCandidateResult:
    layouts: dict[str, Any]
    revision: int
    preview_id: str
    selected_candidate_id: str


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sniff_media_type(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        len(payload) >= 12
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def _safe_filename(filename: str) -> str:
    if (
        not isinstance(filename, str)
        or not filename.strip()
        or "://" in filename
        or any(ord(character) < 32 for character in filename)
    ):
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_filename_invalid"
        )
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not basename or basename in {".", ".."} or len(basename) > 180:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_filename_invalid"
        )
    return basename


def validate_local_image(
    *,
    filename: str,
    declared_media_type: str,
    payload: bytes,
) -> ValidatedLocalImage:
    """Validate bounded in-memory bytes without fetching or storing anything."""

    safe_filename = _safe_filename(filename)
    if declared_media_type not in ALLOWED_IMAGE_TYPES:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_type_not_allowed"
        )
    if not isinstance(payload, bytes) or not payload:
        raise TemplateV2LocalAssetError("template_v2_local_image_empty")
    if len(payload) > MAX_IMAGE_BYTES:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_bytes_exceeded"
        )
    sniffed_media_type = _sniff_media_type(payload)
    if sniffed_media_type != declared_media_type:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_magic_mismatch"
        )

    expected_format, extension = ALLOWED_IMAGE_TYPES[declared_media_type]
    try:
        with Image.open(BytesIO(payload)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_decode_failed"
        ) from error
    if image_format != expected_format:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_format_mismatch"
        )
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
    ):
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_dimension_exceeded"
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_pixels_exceeded"
        )

    digest = hashlib.sha256(payload).hexdigest()
    return ValidatedLocalImage(
        asset_id=f"local-{digest[:24]}",
        filename=safe_filename,
        media_type=declared_media_type,
        extension=extension,
        size_bytes=len(payload),
        width=width,
        height=height,
        sha256=digest,
    )


def _read_path(source: Mapping[str, Any], path: Sequence[str | int]) -> Any:
    value: Any = source
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or not 0 <= part < len(value):
                raise TemplateV2LocalAssetError(
                    "template_v2_local_image_target_invalid"
                )
            value = value[part]
        else:
            if not isinstance(value, Mapping) or part not in value:
                raise TemplateV2LocalAssetError(
                    "template_v2_local_image_target_invalid"
                )
            value = value[part]
    return value


def preview_local_image_replacement(
    layouts: Mapping[str, Any],
    *,
    element_path: Sequence[str | int],
    asset: ValidatedLocalImage,
    previous_asset_record: Mapping[str, Any] | None = None,
) -> ImageReplacementPreview:
    """Preview one image ``data`` patch; asset bytes remain caller-owned."""

    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    target = _read_path(source, element_path)
    if not isinstance(target, Mapping) or target.get("type") != "image":
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_target_not_image"
        )
    before_reference = target.get("data")
    if not isinstance(before_reference, str) or not before_reference:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_target_invalid"
        )
    after_reference = (
        f"/app_data/images/template-v2/{asset.asset_id}.{asset.extension}"
    )
    previous_record = (
        deepcopy(dict(previous_asset_record))
        if isinstance(previous_asset_record, Mapping)
        else None
    )
    source_digest = _digest(source)
    preview_payload = {
        "source_digest": source_digest,
        "element_path": list(element_path),
        "before_reference": before_reference,
        "after_reference": after_reference,
        "asset_sha256": asset.sha256,
        "previous_asset_record": previous_record,
    }
    preview_id = hashlib.sha256(
        json.dumps(
            preview_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return ImageReplacementPreview(
        preview_id=preview_id,
        source_digest=source_digest,
        element_path=tuple(element_path),
        before_reference=before_reference,
        previous_asset_record=previous_record,
        after_reference=after_reference,
        asset=asset,
    )


def apply_local_image_replacement(
    layouts: Mapping[str, Any],
    preview: ImageReplacementPreview,
    *,
    expected_revision: int,
    current_revision: int,
) -> ImageReplacementResult:
    """Apply the preview under CAS and emit deferred retention bookkeeping."""

    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
        or isinstance(current_revision, bool)
        or not isinstance(current_revision, int)
        or current_revision < 1
    ):
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_revision_invalid"
        )
    if expected_revision != current_revision:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_stale_revision"
        )
    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    if _digest(source) != preview.source_digest:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_preview_stale"
        )
    expected_preview = preview_local_image_replacement(
        source,
        element_path=preview.element_path,
        asset=preview.asset,
        previous_asset_record=preview.previous_asset_record,
    )
    if expected_preview != preview:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_preview_tampered"
        )
    result = deepcopy(source)
    target = _read_path(result, preview.element_path)
    if not isinstance(target, dict) or target.get("type") != "image":
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_target_not_image"
        )
    if target.get("data") != preview.before_reference:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_preview_tampered"
        )
    target["data"] = preview.after_reference
    decode_wire_layouts(result).validate_strict()
    return ImageReplacementResult(
        layouts=result,
        revision=current_revision + 1,
        preview_id=preview.preview_id,
        asset_record={
            "id": preview.asset.asset_id,
            "reference": preview.after_reference,
            "provenance": preview.asset.provenance(),
        },
        retention=AssetRetentionIntent(
            previous_reference=preview.before_reference,
            replacement_reference=preview.after_reference,
            previous_asset_record=deepcopy(preview.previous_asset_record),
        ),
    )


def _crop_candidate(
    *,
    asset: ValidatedLocalImage,
    strategy: str,
    reason_code: str,
    focus_x: float,
    focus_y: float,
    crop_scale: float,
) -> CropCandidate:
    patch = {
        "focus_x": focus_x,
        "focus_y": focus_y,
        "crop_scale": crop_scale,
    }
    render_digest = _digest(
        {
            "asset_sha256": asset.sha256,
            "patch": patch,
        }
    )
    candidate_id = hashlib.sha256(
        f"{strategy}:{render_digest}".encode("utf-8")
    ).hexdigest()
    return CropCandidate(
        candidate_id=candidate_id,
        strategy=strategy,
        reason_code=reason_code,
        focus_x=focus_x,
        focus_y=focus_y,
        crop_scale=crop_scale,
        render_digest=render_digest,
    )


def preview_deterministic_crop_candidates(
    layouts: Mapping[str, Any],
    *,
    element_path: Sequence[str | int],
    asset: ValidatedLocalImage,
) -> CropCandidatePreview:
    """Return three bounded local crop candidates without image inference."""

    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    target = _read_path(source, element_path)
    if not isinstance(target, Mapping) or target.get("type") != "image":
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_target_not_image"
        )
    if target.get("is_icon") is True:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_crop_unsupported_icon"
        )

    aspect_ratio = asset.width / asset.height
    if aspect_ratio > 1.2:
        adaptive = (50.0, 42.0, 1.15)
        thirds = (33.333, 50.0, 1.25)
        reason_code = "LANDSCAPE_SAFE_CROP"
    elif aspect_ratio < (1 / 1.2):
        adaptive = (50.0, 38.0, 1.15)
        thirds = (50.0, 33.333, 1.25)
        reason_code = "PORTRAIT_SAFE_CROP"
    else:
        adaptive = (50.0, 45.0, 1.1)
        thirds = (33.333, 33.333, 1.2)
        reason_code = "SQUARE_SAFE_CROP"

    candidates = (
        _crop_candidate(
            asset=asset,
            strategy="center",
            reason_code="CENTER_SAFE_CROP",
            focus_x=50.0,
            focus_y=50.0,
            crop_scale=1.0,
        ),
        _crop_candidate(
            asset=asset,
            strategy="adaptive_focus",
            reason_code=reason_code,
            focus_x=adaptive[0],
            focus_y=adaptive[1],
            crop_scale=adaptive[2],
        ),
        _crop_candidate(
            asset=asset,
            strategy="rule_of_thirds",
            reason_code="RULE_OF_THIRDS_CROP",
            focus_x=thirds[0],
            focus_y=thirds[1],
            crop_scale=thirds[2],
        ),
    )
    source_digest = _digest(source)
    preview_id = _digest(
        {
            "source_digest": source_digest,
            "element_path": list(element_path),
            "asset_sha256": asset.sha256,
            "candidate_ids": [
                candidate.candidate_id for candidate in candidates
            ],
        }
    )
    return CropCandidatePreview(
        preview_id=preview_id,
        source_digest=source_digest,
        element_path=tuple(element_path),
        asset=asset,
        candidates=candidates,
    )


def apply_crop_candidate(
    layouts: Mapping[str, Any],
    preview: CropCandidatePreview,
    *,
    candidate_id: str,
    expected_revision: int,
    current_revision: int,
) -> CropCandidateResult:
    """Apply one previewed crop through the same revision-CAS boundary."""

    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
        or isinstance(current_revision, bool)
        or not isinstance(current_revision, int)
        or current_revision < 1
    ):
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_revision_invalid"
        )
    if expected_revision != current_revision:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_stale_revision"
        )
    wire = decode_wire_layouts(layouts)
    wire.validate_strict()
    source = wire.to_wire_value()
    if _digest(source) != preview.source_digest:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_crop_preview_stale"
        )
    expected_preview = preview_deterministic_crop_candidates(
        source,
        element_path=preview.element_path,
        asset=preview.asset,
    )
    if expected_preview != preview:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_crop_preview_tampered"
        )
    selected = next(
        (
            candidate
            for candidate in preview.candidates
            if candidate.candidate_id == candidate_id
        ),
        None,
    )
    if selected is None:
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_crop_candidate_unknown"
        )

    result = deepcopy(source)
    target = _read_path(result, preview.element_path)
    if not isinstance(target, dict) or target.get("type") != "image":
        raise TemplateV2LocalAssetError(
            "template_v2_local_image_target_not_image"
        )
    target["focus_x"] = selected.focus_x
    target["focus_y"] = selected.focus_y
    target["crop_scale"] = selected.crop_scale
    decode_wire_layouts(result).validate_strict()
    return CropCandidateResult(
        layouts=result,
        revision=current_revision + 1,
        preview_id=preview.preview_id,
        selected_candidate_id=selected.candidate_id,
    )


__all__ = [
    "ALLOWED_IMAGE_TYPES",
    "AssetRetentionIntent",
    "CropCandidate",
    "CropCandidatePreview",
    "CropCandidateResult",
    "ImageReplacementPreview",
    "ImageReplacementResult",
    "TemplateV2LocalAssetError",
    "ValidatedLocalImage",
    "apply_crop_candidate",
    "apply_local_image_replacement",
    "preview_deterministic_crop_candidates",
    "preview_local_image_replacement",
    "validate_local_image",
]
