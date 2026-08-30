"""Sessionless PNG bundle to verified PPTX/PDF export worker."""

from __future__ import annotations

import json
import re
import zipfile
import tempfile
from pathlib import Path
from collections.abc import Mapping

from PIL import Image
import pypdfium2 as pdfium
from pptx import Presentation
from pptx.util import Inches

from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    reject_sensitive_worker_payload,
)
from workers.public_worker_runtime import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
    main,
)


EXPORT_BUNDLE_SCHEMA = "public-export-bundle/v1"
EXPORT_CHECKPOINT_BUNDLE_SCHEMA = "public-export-bundle/v2"
MAX_SLIDES = 100
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _read_bundle(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> tuple[str, list[ArtifactRef]]:
    if job.input_artifact.media_type != "application/vnd.presenton.export-bundle+json":
        raise PublicWorkerExecutionError("public_export_bundle_media_type_invalid")
    payload = store.read(job.input_artifact)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicWorkerExecutionError("public_export_bundle_invalid_json") from error
    if not isinstance(value, Mapping):
        raise PublicWorkerExecutionError("public_export_bundle_invalid_shape")
    reject_sensitive_worker_payload(value)
    schema_version = value.get("schemaVersion")
    if schema_version == EXPORT_BUNDLE_SCHEMA:
        expected_keys = {"schemaVersion", "exportFormat", "slides"}
    elif schema_version == EXPORT_CHECKPOINT_BUNDLE_SCHEMA:
        expected_keys = {
            "schemaVersion",
            "canonicalExportIdentity",
            "exportFormat",
            "exportMode",
            "renderAdapter",
            "slides",
        }
        if (
            not isinstance(value.get("canonicalExportIdentity"), str)
            or not _SHA256.fullmatch(value["canonicalExportIdentity"])
            or value.get("exportMode") != "fidelity-raster"
            or not isinstance(value.get("renderAdapter"), str)
            or not 1 <= len(value["renderAdapter"].strip()) <= 128
        ):
            raise PublicWorkerExecutionError("public_export_bundle_identity_invalid")
    else:
        raise PublicWorkerExecutionError("public_export_bundle_schema_unsupported")
    if set(value) != expected_keys:
        raise PublicWorkerExecutionError("public_export_bundle_invalid_shape")
    export_format = value.get("exportFormat")
    if export_format not in {"pptx", "pdf"}:
        raise PublicWorkerExecutionError("public_export_format_invalid")
    raw_slides = value.get("slides")
    if not isinstance(raw_slides, list) or not 1 <= len(raw_slides) <= MAX_SLIDES:
        raise PublicWorkerExecutionError("public_export_slide_count_invalid")
    slides: list[ArtifactRef] = []
    for slide_index, raw in enumerate(raw_slides):
        if not isinstance(raw, Mapping):
            raise PublicWorkerExecutionError("public_export_slide_ref_invalid")
        raw_reference: Mapping[str, object] = raw
        if schema_version == EXPORT_CHECKPOINT_BUNDLE_SCHEMA:
            if (
                set(raw) != {"slideIndex", "artifactRef", "validationDigest"}
                or raw.get("slideIndex") != slide_index
                or not isinstance(raw.get("validationDigest"), str)
                or not _SHA256.fullmatch(raw["validationDigest"])
                or not isinstance(raw.get("artifactRef"), Mapping)
            ):
                raise PublicWorkerExecutionError(
                    "public_export_slide_checkpoint_invalid"
                )
            raw_reference = raw["artifactRef"]
        try:
            reference = ArtifactRef.from_wire(raw_reference)
        except IsolatedWorkerContractError as error:
            raise PublicWorkerExecutionError("public_export_slide_ref_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.media_type != "image/png"
            or reference.kind != "slide-png"
        ):
            raise PublicWorkerExecutionError("public_export_slide_binding_mismatch")
        path = store.verified_path(reference)
        with path.open("rb") as stream:
            signature = stream.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise PublicWorkerExecutionError("public_export_slide_not_png")
        slides.append(reference)
    return export_format, slides


def _build_pptx(slides: list[ArtifactRef], store: LocalContentAddressedStore, target: Path) -> None:
    deck = Presentation()
    deck.slide_width = Inches(13.333333)
    deck.slide_height = Inches(7.5)
    blank = deck.slide_layouts[6]
    while deck.slides:
        relationship_id = deck.slides._sldIdLst[-1].rId
        deck.part.drop_rel(relationship_id)
        del deck.slides._sldIdLst[-1]
    for reference in slides:
        slide = deck.slides.add_slide(blank)
        slide.shapes.add_picture(
            str(store.verified_path(reference)),
            0,
            0,
            width=deck.slide_width,
            height=deck.slide_height,
        )
    deck.save(str(target))
    try:
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            slide_parts = [
                name
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ]
            if (
                "[Content_Types].xml" not in names
                or "ppt/presentation.xml" not in names
                or len(slide_parts) != len(slides)
            ):
                raise PublicWorkerExecutionError("public_export_pptx_readback_failed")
    except zipfile.BadZipFile as error:
        raise PublicWorkerExecutionError("public_export_pptx_readback_failed") from error


def _build_pdf(slides: list[ArtifactRef], store: LocalContentAddressedStore, target: Path) -> None:
    document = pdfium.PdfDocument.new()
    try:
        for reference in slides:
            with Image.open(store.verified_path(reference)) as image:
                rgb = image.convert("RGB")
                try:
                    bitmap = pdfium.PdfBitmap.from_pil(rgb)
                    page = document.new_page(960, 540)
                    pdf_image = pdfium.PdfImage.new(document)
                    pdf_image.set_bitmap(bitmap)
                    pdf_image.set_matrix(pdfium.PdfMatrix(960, 0, 0, 540, 0, 0))
                    page.insert_obj(pdf_image)
                    page.gen_content()
                    page.close()
                finally:
                    rgb.close()
        document.save(target)
        with pdfium.PdfDocument(target) as readback:
            page_count = len(readback)
        if page_count != len(slides):
            raise PublicWorkerExecutionError("public_export_pdf_readback_failed")
    finally:
        document.close()


async def execute_export(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> ArtifactRef:
    export_format, slides = _read_bundle(job, store)
    with tempfile.TemporaryDirectory(prefix="public-export-") as temporary:
        target = Path(temporary) / f"result.{export_format}"
        if export_format == "pptx":
            _build_pptx(slides, store, target)
        else:
            _build_pdf(slides, store, target)
        return store.put_path(
            target,
            source=job.input_artifact,
            operation_id=job.operation_id,
            kind=f"presentation-{export_format}",
            media_type=(
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                if export_format == "pptx"
                else "application/pdf"
            ),
        )


if __name__ == "__main__":
    raise SystemExit(main("export", execute_export))
