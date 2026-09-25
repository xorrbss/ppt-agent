"""Bounded deterministic raster conversion, called inside a limited worker.

No filesystem writes or persistence occur here. The caller retains original
bytes and publishes the returned derivative through the existing asset gateway.
Worker wall-time/memory reservations remain required around this CPU operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from typing import Literal

from PIL import Image, ImageCms, ImageOps, __version__ as pillow_version

from services.svg_asset_manifest import DerivativeRecipe, canonical_digest


@dataclass(frozen=True)
class ModelPreview:
    payload: bytes
    source_sha256: str
    source_appearance_sha256: str
    sha256: str
    width: int
    height: int
    media_type: str
    recipe: DerivativeRecipe
    recipe_digest: str

    def metadata(self) -> dict:
        """Bytes are intentionally absent; immutable artifact ref is added later."""
        return {
            "sourceSha256": self.source_sha256,
            "sourceAppearanceSha256": self.source_appearance_sha256,
            "sha256": self.sha256, "width": self.width, "height": self.height,
            "mediaType": self.media_type, "byteSize": len(self.payload),
            "recipeDigest": self.recipe_digest,
        }


def _build_raster_derivative(source: bytes, *, semantic_kind: Literal["photo", "text", "unknown"] = "unknown",
                        max_bytes: int, max_edge: int | None = None,
                        min_edge: int = 1024, max_source_bytes: int = 32 * 1024 * 1024,
                        max_source_pixels: int = 64_000_000,
                        max_source_edge: int = 16384) -> ModelPreview:
    """PNG/JPEG/WebP -> verified sRGB PNG/JPEG without modifying originals.

    The 1536/2048 and 1024 sizes are v3 candidate profiles, not a provider
    capability claim. A stricter provider budget must be passed by admission.
    Unknown content is protected as text; JPEG is used only for explicit photos.
    """
    if semantic_kind not in {"photo", "text", "unknown"}:
        raise ValueError("svg_asset_semantic_kind_invalid")
    if min(max_bytes, min_edge, max_source_bytes, max_source_pixels, max_source_edge) <= 0:
        raise ValueError("svg_asset_preview_budget_invalid")
    target_edge = max_edge if max_edge is not None else (1536 if semantic_kind == "photo" else 2048)
    if target_edge <= 0 or target_edge < min_edge:
        raise ValueError("svg_asset_preview_resolution_budget_invalid")
    if not source or len(source) > max_source_bytes:
        raise ValueError("svg_asset_source_byte_budget_exceeded")
    with Image.open(io.BytesIO(source)) as probe:
        if probe.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("svg_asset_source_format_unsupported")
        if max(probe.size) > max_source_edge or probe.width * probe.height > max_source_pixels:
            raise ValueError("svg_asset_source_pixel_budget_exceeded")
        if getattr(probe, "n_frames", 1) != 1:
            raise ValueError("svg_asset_animated_source_unsupported")
        probe.verify()
    with Image.open(io.BytesIO(source)) as decoded:
        decoded.load()
        oriented = ImageOps.exif_transpose(decoded)
        alpha = "A" in oriented.getbands() or "transparency" in oriented.info
        alpha_channel = oriented.convert("RGBA").getchannel("A") if alpha else None
        icc = oriented.info.get("icc_profile")
        if icc:
            try:
                color = oriented if oriented.mode in {"RGB", "CMYK", "LAB", "L"} else oriented.convert("RGB")
                rgb = ImageCms.profileToProfile(color, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
            except (OSError, ValueError, ImageCms.PyCMSError) as error:
                raise ValueError("svg_asset_source_color_profile_invalid") from error
        else:
            rgb = oriented.convert("RGB")
        if alpha_channel is not None:
            rgb.putalpha(alpha_channel)
        image = rgb.copy()
        image.info.clear()
    appearance = hashlib.sha256()
    appearance.update(f"{image.mode}:{image.width}:{image.height}:srgb\0".encode("ascii"))
    appearance.update(image.tobytes())
    appearance_digest = appearance.hexdigest()
    source_digest = hashlib.sha256(source).hexdigest()
    use_png = alpha or semantic_kind != "photo"
    original_edge = max(image.size)
    floor = min(original_edge, min_edge)
    edge = min(original_edge, target_edge)
    edges = []
    while True:
        edges.append(edge)
        if edge == floor:
            break
        edge = max(floor, int(edge * 0.8))
    for edge in edges:
        scale = edge / original_edge
        dimensions = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        resized = image.resize(dimensions, Image.Resampling.LANCZOS) if dimensions != image.size else image
        for quality in ((100,) if use_png else (90, 85)):
            output = io.BytesIO()
            if use_png:
                resized.save(output, format="PNG", optimize=False, compress_level=9)
            else:
                resized.save(output, format="JPEG", quality=quality, subsampling=0, optimize=False, progressive=False)
            payload = output.getvalue()
            if len(payload) > max_bytes:
                continue
            media_type = "image/png" if use_png else "image/jpeg"
            with Image.open(io.BytesIO(payload)) as check:
                if check.size != dimensions or check.format != ("PNG" if use_png else "JPEG"):
                    raise ValueError("svg_asset_preview_readback_mismatch")
                check.verify()
            recipe = DerivativeRecipe(
                decoder_version=f"pillow-{pillow_version}",
                encoder_version=f"pillow-{pillow_version}-png9-jpeg444-v1",
                width=dimensions[0], height=dimensions[1], alpha=alpha,
                media_type=media_type, quality=quality,
            )
            return ModelPreview(payload, source_digest, appearance_digest,
                                hashlib.sha256(payload).hexdigest(), *dimensions,
                                media_type, recipe, canonical_digest(recipe))
    raise ValueError("svg_asset_preview_budget_unresolved")


def build_model_preview(source: bytes, *, expected_media_type: str | None = None,
                        semantic_kind: Literal["photo", "text", "unknown"] = "unknown",
                        max_bytes: int, max_edge: int | None = None, min_edge: int = 1024,
                        max_source_bytes: int = 32 * 1024 * 1024,
                        max_source_pixels: int = 64_000_000,
                        max_source_edge: int = 16384) -> ModelPreview:
    """Public preflight boundary: invalid raster input always raises ValueError."""
    try:
        if expected_media_type is not None:
            with Image.open(io.BytesIO(source)) as probe:
                if {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(probe.format) != expected_media_type:
                    raise ValueError("svg_asset_source_media_type_mismatch")
        return _build_raster_derivative(
            source, semantic_kind=semantic_kind, max_bytes=max_bytes,
            max_edge=max_edge, min_edge=min_edge, max_source_bytes=max_source_bytes,
            max_source_pixels=max_source_pixels, max_source_edge=max_source_edge,
        )
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("svg_asset_source_decoder_invalid") from error


def build_purpose_derivative(source: bytes, *, purpose: Literal["screen", "export"],
                             width_css_px: float, height_css_px: float,
                             fit: Literal["contain", "cover"] = "contain",
                             semantic_kind: Literal["photo", "text", "unknown"] = "unknown",
                             max_bytes: int = 32 * 1024 * 1024,
                             css_pixels_per_inch: float = 96.0) -> ModelPreview:
    """Build from original appearance at placement density, never model bytes.

    The native exporter uses 9525 EMU/CSS px, hence 96 CSS px/inch. Density
    targets are recipe inputs; raster dimensions remain capped by the source.
    """
    import math
    if (purpose not in {"screen", "export"} or fit not in {"contain", "cover"}
            or not all(math.isfinite(v) and v > 0 for v in
                       (width_css_px, height_css_px, css_pixels_per_inch))):
        raise ValueError("svg_asset_placement_resolution_invalid")
    if not source or len(source) > 32 * 1024 * 1024:
        raise ValueError("svg_asset_source_byte_budget_exceeded")
    try:
        with Image.open(io.BytesIO(source)) as probe:
            if probe.width * probe.height > 64_000_000 or max(probe.size) > 16384:
                raise ValueError("svg_asset_source_pixel_budget_exceeded")
            source_width, source_height = probe.size
            if probe.getexif().get(274) in {5, 6, 7, 8}:
                source_width, source_height = source_height, source_width
        dpi = 144 if purpose == "screen" else (200 if semantic_kind == "photo" else 300)
        desired = (width_css_px * dpi / css_pixels_per_inch,
                   height_css_px * dpi / css_pixels_per_inch)
        scale = (max if fit == "cover" else min)(desired[0] / source_width,
                                                    desired[1] / source_height)
        edge = max(1, math.ceil(max(source_width, source_height) * min(1.0, scale)))
        # A fixed target forbids silently dropping export resolution to fit a
        # byte budget. The caller records a conversion failure and can resume.
        return _build_raster_derivative(source, semantic_kind=semantic_kind,
                                        max_bytes=max_bytes, max_edge=edge, min_edge=edge)
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("svg_asset_source_decoder_invalid") from error
