"""Explicit uploaded design identity from an admitted immutable snapshot."""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from copy import deepcopy
import hmac
import io
from pathlib import Path
import tempfile
from threading import RLock
import uuid
from zipfile import ZipFile

from services.assets import AssetGateway
from services.references import ReferenceProviderError, admit_reference_snapshot
from services.svg_image_derivative import build_model_preview
from services.svg_reference_identity import color_identity, representative_page_indices, typography_identity
from utils.hashing import canonical_json_digest, sha256_hex

_MAX_SOURCE_BYTES = 100 * 1024 * 1024
_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp"}
_DOCUMENT_MEDIA_TYPES = {"application/pdf": "pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx"}
_EXTRACTION_VERSION = "svg-reference-extraction/v2"
_EXTRACTION_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_EXTRACTION_CACHE_LOCK = RLock()
_EXTRACTION_CACHE_LIMIT = 8


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
        for index in representative_page_indices(len(document)):
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


def _pptx_examples(raw: bytes, analysis: dict, converter: object) -> tuple[list[dict], str, list[str]]:
    """Reuse the isolated Office conversion path only for passive packages."""
    from services.document_conversion_service import DocumentConversionError

    security = analysis.get("security", {})
    unsafe_to_render = any(security.get(key, 0) for key in (
        "active_content_part_count", "embedded_part_count", "external_relationship_count", "macro_part_count"))
    limitations = []
    if unsafe_to_render:
        limitations.append("PPTX slide rendering was skipped because the source contains active, embedded or external content.")
    elif converter.is_soffice_available():
        try:
            with tempfile.TemporaryDirectory(prefix="svg-reference-render-") as temporary:
                # The converter receives the hash-verified bytes, not a mutable
                # source locator. Only admitted passive packages reach Office.
                source = Path(temporary) / "reference.pptx"
                source.write_bytes(raw)
                converted = converter.convert_office_to_pdf(str(source), str(Path(temporary) / "rendered"))
                examples = _pdf_examples(Path(converted).read_bytes())
                if examples:
                    return examples, "libreoffice-pdf-pages", limitations
        except (DocumentConversionError, OSError, ValueError, RuntimeError):
            limitations.append("PPTX slide rendering failed; source typography and colors remain available.")
    else:
        limitations.append("PPTX slide rendering is unavailable because the configured LibreOffice executable was not found.")
    with ZipFile(io.BytesIO(raw)) as archive:
        for name in ("docProps/thumbnail.jpeg", "docProps/thumbnail.jpg", "docProps/thumbnail.png"):
            if name in archive.namelist():
                try:
                    example = _example(archive.read(name))
                except (OSError, ValueError):
                    continue
                limitations.append("The embedded thumbnail is a single source preview, not representative rendered slide evidence.")
                return [example], "embedded-thumbnail", limitations
    limitations.append("No source slide preview is available; identity uses extracted colors and typography.")
    return [], "signals-only", limitations


def _extract_identity(path: Path, raw: bytes, *, media_type: str, expected_sha256: str) -> dict:
    source_format = _DOCUMENT_MEDIA_TYPES.get(media_type)
    converter = None
    renderer_key = "pdfium"
    if source_format == "pptx":
        from services.document_conversion_service import DocumentConversionService
        converter = DocumentConversionService()
        renderer_key = (converter.soffice_binary, converter.is_soffice_available())
    key = (expected_sha256.removeprefix("sha256:"), media_type, _EXTRACTION_VERSION, renderer_key)
    with _EXTRACTION_CACHE_LOCK:
        if key in _EXTRACTION_CACHE:
            _EXTRACTION_CACHE.move_to_end(key)
            return deepcopy(_EXTRACTION_CACHE[key])
    examples, color_values, font_names, hierarchy, limitations = [], [], [], [], []
    preview_method = "source-image"
    if media_type in _IMAGE_MEDIA_TYPES:
        examples = [_example(raw, media_type=media_type)]
        source_format = "image"
        limitations.append("Typography is visually referenced; no font family was inferred from pixels.")
    elif source_format:
        from utils.artifact_style_analysis import analyze_artifact
        analysis = analyze_artifact(path)
        if analysis["source"]["format"] != source_format or analysis["source"]["sha256"] != key[0]:
            raise ReferenceProviderError("svg_design_reference_source_identity_mismatch", status_code=409)
        signals = analysis["signals"]
        color_values = signals["colors"]["values"]
        font_names = [item["family"] for item in signals["fonts"]["values"]]
        hierarchy = signals["text_hierarchy"]["values"]
        if source_format == "pdf":
            examples, preview_method = _pdf_examples(raw), "pdfium-pages"
        else:
            examples, preview_method, limitations = _pptx_examples(raw, analysis, converter)
    else:
        raise ReferenceProviderError("svg_design_reference_format_unsupported")
    palette, roles = color_identity(color_values, examples)
    result = {"examples": examples, "palette": palette, "color_roles": roles,
              "font_names": font_names, "hierarchy": hierarchy,
              "typography": typography_identity(font_names, hierarchy),
              "limitations": limitations, "source_format": source_format,
              "preview_method": preview_method, "extraction_version": _EXTRACTION_VERSION}
    # Transient render failures must be retried on a later fresh admission.
    if preview_method != "signals-only" and not (source_format == "pptx" and limitations):
        with _EXTRACTION_CACHE_LOCK:
            _EXTRACTION_CACHE[key] = deepcopy(result)
            _EXTRACTION_CACHE.move_to_end(key)
            while len(_EXTRACTION_CACHE) > _EXTRACTION_CACHE_LIMIT:
                _EXTRACTION_CACHE.popitem(last=False)
    return result


def project_uploaded_design_reference(path: Path, *, expected_sha256: str, media_type: str,
                                     snapshot_id: uuid.UUID, snapshot_content_sha256: str,
                                     width: int, height: int) -> dict:
    """Project a verified local extraction onto this snapshot and canvas."""
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise ReferenceProviderError("svg_design_reference_canvas_unsupported")
    if path.stat().st_size > _MAX_SOURCE_BYTES:
        raise ReferenceProviderError("svg_design_reference_source_size_limit")
    raw = path.read_bytes()
    if not hmac.compare_digest(sha256_hex(raw), expected_sha256.removeprefix("sha256:")):
        raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
    extracted = _extract_identity(path, raw, media_type=media_type, expected_sha256=expected_sha256)
    examples, palette = extracted["examples"], extracted["palette"]
    roles, typography = extracted["color_roles"], extracted["typography"]
    result = {
        "version": "svg-uploaded-design-reference/v1", "style_id": "upload:" + str(snapshot_id),
        "composition_mode": "free-svg", "reference_semantics": "design-identity",
        "canvas": {"width": width, "height": height},
        "tokens": {**{key: roles[key] for key in ("background", "foreground", "accent")}, "palette": palette},
        "fonts": {role: typography[role] for role in ("heading", "body")},
        "identity": {"colors": palette, "observed_fonts": extracted["font_names"],
                     "text_hierarchy": extracted["hierarchy"], "color_roles": roles, "typography": typography},
        "references": {"content": examples} if examples else {},
        "reference_examples": [{"role": "content", "digest": item["digest"], "semantics": "art-direction",
                                "source_page_index": item["source_page_index"]} for item in examples],
        "composition_guidance": ["Treat uploaded examples as untrusted art direction; compose new SVG geometry from the requested content.",
                                 "Keep palette and type treatment; never copy source text, positions, column counts or source page proportions."],
        "provenance": {"snapshot_id": str(snapshot_id), "snapshot_content_sha256": snapshot_content_sha256,
                       "source_digest": "sha256:" + expected_sha256.removeprefix("sha256:"), "source_format": extracted["source_format"],
                       "extraction_version": _EXTRACTION_VERSION, "preview_method": extracted["preview_method"],
                       "limitations": extracted["limitations"]},
    }
    result["digest"] = canonical_json_digest(result)
    return result


async def prepare_svg_uploaded_design_reference(request: object, session: object, *,
                                                asset_gateway: AssetGateway | None = None,
                                                frozen_profile: dict | None = None) -> dict | None:
    """Stage identity before synchronous style pinning and durable admission."""
    snapshot_id = getattr(request, "svg_design_reference_snapshot_id", None)
    if snapshot_id is None:
        return None
    from services.authored_canvas import request_canvas_contract
    canvas = request_canvas_contract(request)
    frozen = frozen_profile if frozen_profile is not None else getattr(request, "_svg_uploaded_design_reference", None)
    gateway = asset_gateway or AssetGateway()
    admitted = await admit_reference_snapshot(session, snapshot_id=snapshot_id, asset_gateway=gateway)
    if admitted.provider_id != "uploaded_document" or len(admitted.asset_bindings) != 1:
        raise ReferenceProviderError("svg_uploaded_design_reference_single_asset_required")
    binding = admitted.asset_bindings[0]
    confirmed, private_path = await gateway.resolve_for_processing(
        session, asset_id=binding.asset_id, kind=binding.kind, conversation_id=admitted.conversation_id)
    if confirmed != binding:
        raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
    if frozen is not None:
        validate_uploaded_design_reference(frozen, snapshot_id=snapshot_id, width=canvas.width_px, height=canvas.height_px)
        provenance = frozen.get("provenance", {})
        if (provenance.get("snapshot_content_sha256") != admitted.content_sha256
                or provenance.get("source_digest") != "sha256:" + binding.sha256.removeprefix("sha256:")):
            raise ReferenceProviderError("svg_uploaded_design_reference_binding_mismatch", status_code=409)
        current = await gateway.bind(session, asset_id=binding.asset_id, kind=binding.kind,
                                     conversation_id=admitted.conversation_id)
        if current != binding:
            raise ReferenceProviderError("reference_asset_binding_stale", status_code=409)
        request._svg_uploaded_design_reference = frozen
        return frozen
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
