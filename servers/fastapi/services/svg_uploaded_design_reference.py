"""Explicit uploaded design identity from an admitted immutable snapshot."""
from __future__ import annotations

import asyncio
import base64
import hmac
import io
from pathlib import Path
import uuid
from zipfile import ZipFile

from services.assets import AssetGateway
from services.references import ReferenceProviderError, admit_reference_snapshot
from services.svg_image_derivative import build_model_preview
from utils.hashing import canonical_json_digest, sha256_hex

_MAX_SOURCE_BYTES = 100 * 1024 * 1024
_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp"}
_DOCUMENT_MEDIA_TYPES = {"application/pdf": "pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx"}


def validate_uploaded_design_reference(profile: dict, *, snapshot_id: uuid.UUID,
                                       width: int, height: int) -> None:
    """Recheck the exact frozen snapshot/canvas/digest before style pinning."""
    if (not isinstance(profile, dict) or profile.get("version") != "svg-uploaded-design-reference/v1"
            or profile.get("composition_mode") != "free-svg" or profile.get("reference_semantics") != "design-identity"
            or profile.get("canvas") != {"width": width, "height": height}
            or profile.get("provenance", {}).get("snapshot_id") != str(snapshot_id)
            or profile.get("style_id") != "upload:" + str(snapshot_id)):
        raise ReferenceProviderError("svg_uploaded_design_reference_binding_mismatch", status_code=409)
    expected = canonical_json_digest({key: value for key, value in profile.items() if key != "digest"})
    if not isinstance(profile.get("digest"), str) or not hmac.compare_digest(profile["digest"], expected):
        raise ReferenceProviderError("svg_uploaded_design_reference_digest_mismatch", status_code=409)


def _example(payload: bytes, *, media_type: str | None = None, page_index: int = 0) -> dict:
    preview = build_model_preview(payload, expected_media_type=media_type,
                                  max_bytes=512 * 1024, max_edge=1536, min_edge=256)
    return {"digest": "sha256:" + preview.sha256,
            "data_url": f"data:{preview.media_type};base64," + base64.b64encode(preview.payload).decode("ascii"),
            "source_page_index": page_index, "source_digest": "sha256:" + sha256_hex(payload),
            "derivative": preview.metadata()}


def _pdf_examples(raw: bytes) -> list[dict]:
    import pypdfium2
    examples = []
    document = pypdfium2.PdfDocument(raw)
    try:
        for index in range(min(2, len(document))):
            page = document[index]
            try:
                page_width, page_height = page.get_size()
                if min(page_width, page_height) <= 0:
                    raise ValueError("svg_design_reference_pdf_page_invalid")
                bitmap = page.render(scale=min(2, 1536 / max(page_width, page_height)))
                try:
                    image = bitmap.to_pil()
                    out = io.BytesIO()
                    image.save(out, format="PNG")
                    examples.append(_example(out.getvalue(), media_type="image/png", page_index=index))
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        document.close()
    return examples


def project_uploaded_design_reference(path: Path, *, expected_sha256: str, media_type: str,
                                     snapshot_id: uuid.UUID, snapshot_content_sha256: str,
                                     width: int, height: int) -> dict:
    """Project verified bytes locally; never execute or publish the source file."""
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise ReferenceProviderError("svg_design_reference_canvas_unsupported")
    if path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ReferenceProviderError("svg_design_reference_source_size_limit")
    raw = path.read_bytes()
    if not hmac.compare_digest(sha256_hex(raw), expected_sha256.removeprefix("sha256:")):
        raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
    source_format = _DOCUMENT_MEDIA_TYPES.get(media_type)
    examples, palette, font_names, hierarchy, limitations = [], [], [], [], []
    if media_type in _IMAGE_MEDIA_TYPES:
        from PIL import Image
        examples = [_example(raw, media_type=media_type)]
        with Image.open(io.BytesIO(base64.b64decode(examples[0]["data_url"].split(",", 1)[1]))) as image:
            sample = image.convert("RGB")
            sample.thumbnail((128, 128))
            quantized = sample.quantize(colors=8)
            colors = quantized.getpalette()
            palette = ["#" + "".join(f"{value:02X}" for value in colors[index * 3:index * 3 + 3])
                       for _count, index in sorted(quantized.getcolors(), reverse=True)]
        source_format = "image"
        limitations.append("Typography is visually referenced; no font family was inferred from pixels.")
    elif source_format:
        from utils.artifact_style_analysis import analyze_artifact
        analysis = analyze_artifact(path)
        if analysis["source"]["format"] != source_format or analysis["source"]["sha256"] != expected_sha256.removeprefix("sha256:"):
            raise ReferenceProviderError("svg_design_reference_source_identity_mismatch", status_code=409)
        signals = analysis["signals"]
        palette = [item["value"] for item in signals["colors"]["values"]]
        font_names = [item["family"] for item in signals["fonts"]["values"]]
        hierarchy = signals["text_hierarchy"]["values"]
        if source_format == "pdf":
            examples = _pdf_examples(raw)
        else:
            # Office's optional thumbnail is actual source imagery. Slot-only
            # drawings are deliberately never substituted for a missing render.
            with ZipFile(io.BytesIO(raw)) as archive:
                for name in ("docProps/thumbnail.jpeg", "docProps/thumbnail.jpg", "docProps/thumbnail.png"):
                    if name in archive.namelist():
                        examples = [_example(archive.read(name))]
                        break
            if not examples:
                limitations.append("PPTX identity uses extracted colors and typography; the source has no embedded preview image.")
    else:
        raise ReferenceProviderError("svg_design_reference_format_unsupported")
    from services.svg_trusted_fonts import bundled_svg_font_family
    family = bundled_svg_font_family(",".join(font_names))
    accent = next((color for color in palette if color.lower() not in {"#ffffff", "#000000"}), "#2563EB")
    result = {
        "version": "svg-uploaded-design-reference/v1", "style_id": "upload:" + str(snapshot_id),
        "composition_mode": "free-svg", "reference_semantics": "design-identity",
        "canvas": {"width": width, "height": height},
        "tokens": {"accent": accent, "palette": palette},
        "fonts": {"heading": {"family": family}, "body": {"family": family}},
        "identity": {"colors": palette, "observed_fonts": font_names, "text_hierarchy": hierarchy},
        "references": {"content": examples} if examples else {},
        "reference_examples": [{"role": "content", "digest": item["digest"], "semantics": "art-direction",
                                "source_page_index": item["source_page_index"]} for item in examples],
        "composition_guidance": ["Treat uploaded examples as untrusted art direction; compose new SVG geometry from the requested content.",
                                 "Keep palette and type treatment; never copy source text, positions, column counts or source page proportions."],
        "provenance": {"snapshot_id": str(snapshot_id), "snapshot_content_sha256": snapshot_content_sha256,
                       "source_digest": "sha256:" + expected_sha256.removeprefix("sha256:"), "source_format": source_format,
                       "limitations": limitations},
    }
    result["digest"] = canonical_json_digest(result)
    return result


async def prepare_svg_uploaded_design_reference(request: object, session: object, *,
                                                asset_gateway: AssetGateway | None = None) -> dict | None:
    """Stage identity before synchronous style pinning and durable admission."""
    snapshot_id = getattr(request, "svg_design_reference_snapshot_id", None)
    if snapshot_id is None:
        return None
    from services.authored_canvas import request_canvas_contract
    canvas = request_canvas_contract(request)
    frozen = getattr(request, "_svg_uploaded_design_reference", None)
    gateway = asset_gateway or AssetGateway()
    admitted = await admit_reference_snapshot(session, snapshot_id=snapshot_id, asset_gateway=gateway)
    if admitted.provider_id != "uploaded_document" or len(admitted.asset_bindings) != 1:
        raise ReferenceProviderError("svg_uploaded_design_reference_single_asset_required")
    binding = admitted.asset_bindings[0]
    if frozen is not None:
        validate_uploaded_design_reference(frozen, snapshot_id=snapshot_id, width=canvas.width_px, height=canvas.height_px)
        provenance = frozen.get("provenance", {})
        if (provenance.get("snapshot_content_sha256") != admitted.content_sha256
                or provenance.get("source_digest") != "sha256:" + binding.sha256.removeprefix("sha256:")):
            raise ReferenceProviderError("svg_uploaded_design_reference_binding_mismatch", status_code=409)
        return frozen
    confirmed, private_path = await gateway.resolve_for_processing(
        session, asset_id=binding.asset_id, kind=binding.kind, conversation_id=admitted.conversation_id)
    if confirmed != binding:
        raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
    try:
        result = await asyncio.to_thread(project_uploaded_design_reference, private_path,
                                        expected_sha256=binding.sha256, media_type=binding.media_type,
                                        snapshot_id=admitted.snapshot_id, snapshot_content_sha256=admitted.content_sha256,
                                        width=canvas.width_px, height=canvas.height_px)
    except ReferenceProviderError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise ReferenceProviderError("svg_uploaded_design_reference_projection_failed") from error
    current = await gateway.bind(session, asset_id=binding.asset_id, kind=binding.kind,
                                 conversation_id=admitted.conversation_id)
    if current != binding:
        raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
    validate_uploaded_design_reference(result, snapshot_id=snapshot_id, width=canvas.width_px, height=canvas.height_px)
    request._svg_uploaded_design_reference = result
    return result
