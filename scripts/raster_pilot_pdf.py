"""PDF admission and readback for the local raster pilot."""

from __future__ import annotations

import base64
from contextlib import closing
import hashlib
from io import BytesIO
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
import uuid
from xml.etree import ElementTree as ET

SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


class _PngPathStore:
    def verified_path(self, path: Path) -> Path:
        return path


def _admit_pdf_sources(
    sources: list[str],
    pages: list[int],
    original_sha256: list[str],
    canvas,
    output_dir: Path,
) -> tuple[list, list[dict], list[dict]]:
    from services.svg_asset_manifest import (
        FitPolicy,
        PlacementManifest,
        RasterDerivative,
        SourceManifest,
        SourceSlot,
        SvgPlacement,
        decide_preservation_policy,
    )
    from services.svg_contract import source_digest
    from services.svg_export_assets import materialize_export_assets
    from services.isolated_worker_contracts import ArtifactRef
    from utils.renderer_isolation import admit_authorized_svg_materialized
    from workers.public_worker_store import LocalContentAddressedStore

    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    seed = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="local-pilot",
        kind="export-bundle",
        sha256=hashlib.sha256(b"").hexdigest(),
        size_bytes=0,
        media_type="application/json",
        storage_key="local-pilot-seed",
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    job = SimpleNamespace(input_artifact=seed, operation_id=operation_id)
    admitted = []
    blockers = []
    lineage = []
    with tempfile.TemporaryDirectory(prefix="raster-a4-cas-") as scratch:
        scratch_path = Path(scratch).resolve()
        store = LocalContentAddressedStore(
            input_root=scratch_path,
            output_root=scratch_path,
            active_deadline_epoch_seconds=time.time() + 240,
        )
        for output_index, (page, svg, original_digest) in enumerate(zip(pages, sources, original_sha256, strict=False)):
            page_lineage = {
                "page": page,
                "originalSvgSha256": original_digest,
                "canonicalSvgSha256": None,
                "materializedSvgSha256": None,
                "canonicalPath": None,
                "materializedPath": None,
            }
            lineage.append(page_lineage)
            try:
                root = ET.fromstring(svg)
                slots = []
                placements = []
                derivative_refs = {}
                for image_index, image_node in enumerate(node for node in root.iter() if node.tag == f"{SVG_NS}image"):
                    href = image_node.attrib.get("href") or image_node.attrib.get(XLINK_HREF, "")
                    payload = base64.b64decode(href.split(",", 1)[1], validate=True)
                    from PIL import Image

                    with Image.open(BytesIO(payload)) as png:
                        image_width, image_height = png.size
                    slot_id = f"source-slot-p{page:03d}-{image_index:03d}"
                    image_node.attrib.pop(XLINK_HREF, None)
                    image_node.set("href", slot_id)
                    derivative_ref = store.put(
                        payload,
                        source=seed,
                        operation_id=operation_id,
                        kind="image-derivative",
                        media_type="image/png",
                    )
                    derivative_refs[slot_id] = derivative_ref.to_wire()
                    raw_digest = hashlib.sha256(payload).hexdigest()
                    derivative = RasterDerivative(
                        ref=derivative_ref.artifact_id,
                        sha256=raw_digest,
                        width=image_width,
                        height=image_height,
                        media_type="image/png",
                        byte_size=len(payload),
                        recipe_digest="sha256:"
                        + hashlib.sha256(f"local-pilot:{page}:{image_index}:{raw_digest}".encode("utf-8")).hexdigest(),
                    )
                    occurrence_id = uuid.uuid5(presentation_id, slot_id)
                    fit = FitPolicy(fit="contain", allow_crop=False)
                    slots.append(
                        SourceSlot(
                            slot_id=slot_id,
                            source_asset_id=uuid.uuid5(owner_id, slot_id),
                            source_sha256=raw_digest,
                            source_width=image_width,
                            source_height=image_height,
                            semantic_description="Source screenshot crop",
                            occurrence_id=occurrence_id,
                            source_locator={
                                "source_format": "pptx",
                                "page_index": page - 1,
                                "coordinate_space": "intrinsic",
                                "object_id": slot_id,
                            },
                            extraction_kind="region_render",
                            source_appearance_sha256=raw_digest,
                            preservation_class="critical",
                            importance_uncertain=False,
                            policy=decide_preservation_policy(
                                selected=True,
                                preservation_class="critical",
                                source_preservation_required=True,
                            ),
                            selection_reason="Local pilot source screenshot",
                            model_derivative=derivative,
                            screen_policy=fit,
                            export_policy=fit,
                        )
                    )
                    placements.append(
                        SvgPlacement(
                            slot_id=slot_id,
                            occurrence_id=occurrence_id,
                            x=float(image_node.attrib.get("x", "0")),
                            y=float(image_node.attrib.get("y", "0")),
                            width=float(image_node.attrib["width"]),
                            height=float(image_node.attrib["height"]),
                            fit=fit,
                            derivative=derivative,
                        )
                    )
                ET.register_namespace("", SVG_NS[1:-1])
                canonical = ET.tostring(root, encoding="unicode")
                canonical_path = output_dir / f"page-{page:03d}.canonical.svg"
                canonical_path.write_bytes(canonical.encode("utf-8"))
                canonical = canonical_path.read_bytes().decode("utf-8")
                page_lineage["canonicalSvgSha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                page_lineage["canonicalPath"] = str(canonical_path)
                source_manifest = SourceManifest.seal(
                    {
                        "presentation_id": presentation_id,
                        "source_revision": 0,
                        "slots": slots,
                        "offered_pages": [
                            {
                                "page_index": output_index,
                                "slot_ids": [slot.slot_id for slot in slots],
                            }
                        ],
                    }
                )
                placement_manifest = PlacementManifest.seal(
                    {
                        "canonical_svg_digest": source_digest(canonical),
                        "source_manifest_digest": source_manifest.digest,
                        "slide_revision": 0,
                        "purpose": "export-pdf",
                        "placements": placements,
                        "renderer_version": "local-raster-pilot/v1",
                    }
                )
                source_ref = store.put(
                    source_manifest.model_dump_json(by_alias=True).encode("utf-8"),
                    source=seed,
                    operation_id=operation_id,
                    kind="source-manifest",
                    media_type="application/json",
                )
                placement_ref = store.put(
                    placement_manifest.model_dump_json(by_alias=True).encode("utf-8"),
                    source=seed,
                    operation_id=operation_id,
                    kind="placement-manifest",
                    media_type="application/json",
                )
                binding = {
                    "sourceManifestRef": source_ref.to_wire(),
                    "placementManifestRef": placement_ref.to_wire(),
                    "sourceManifestDigest": source_manifest.digest,
                    "placementManifestDigest": placement_manifest.digest,
                    "pageIndex": output_index,
                    "slideRevision": 0,
                    "derivatives": derivative_refs,
                }
                resolved, contract = materialize_export_assets(
                    binding,
                    svg=canonical,
                    job=job,
                    store=store,
                    presentation_id=presentation_id,
                    export_format="pdf",
                    width=canvas.logical_size.width,
                    height=canvas.logical_size.height,
                )
                materialized_path = output_dir / f"page-{page:03d}.materialized.svg"
                materialized_path.write_bytes(resolved.encode("utf-8"))
                resolved = materialized_path.read_bytes().decode("utf-8")
                page_lineage["materializedSvgSha256"] = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
                page_lineage["materializedPath"] = str(materialized_path)
                admitted.append(
                    admit_authorized_svg_materialized(
                        canonical,
                        resolved,
                        source_manifest=contract,
                        width_px=canvas.logical_size.width,
                        height_px=canvas.logical_size.height,
                    )
                )
            except Exception as exc:
                message = str(exc)
                token = message.split(":", 1)[0]
                code = token if re.fullmatch(r"(?:svg|public_worker)_[a-z0-9_]+", token) else type(exc).__name__
                blocker = {"page": page, "code": code}
                if code == "svg_contract_size_limit":
                    blocker["canonicalSvgBytes"] = len(canonical.encode("utf-8"))
                    blocker["limitBytes"] = 2 * 1024 * 1024
                blockers.append(blocker)
    return admitted, blockers, lineage


def _make_pdf(admitted: list[str], path: Path, canvas) -> None:
    from utils.slide_capture import _render_admitted_htmls_over_pipe
    from workers.public_export_worker import _build_pdf

    rendered = _render_admitted_htmls_over_pipe(
        admitted,
        timeout=120,
        width_px=canvas.logical_size.width,
        height_px=canvas.logical_size.height,
    )
    if len(rendered) != len(admitted):
        raise ValueError("PDF render page count differs from source SVG count")
    with tempfile.TemporaryDirectory(prefix="raster-a4-pdf-") as scratch:
        png_paths = []
        for index, (png, _metadata) in enumerate(rendered):
            png_path = Path(scratch) / f"page-{index + 1:03d}.png"
            png_path.write_bytes(png)
            png_paths.append(png_path)
        _build_pdf(png_paths, _PngPathStore(), path, canvas)


def _inspect_pdf(path: Path, expected_count: int, canvas) -> None:
    import pypdfium2 as pdfium

    with closing(pdfium.PdfDocument(str(path))) as document:
        if len(document) != expected_count:
            raise ValueError("PDF page count differs from source SVG count")
        expected = (
            canvas.physical_size.width_emu / 12700,
            canvas.physical_size.height_emu / 12700,
        )
        for index in range(len(document)):
            with closing(document[index]) as page:
                actual = page.get_size()
                if any(abs(a - b) > 0.1 for a, b in zip(actual, expected, strict=False)):
                    raise ValueError(f"PDF page {index + 1} differs from A4 physical size")
