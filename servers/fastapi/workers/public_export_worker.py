"""Sessionless PNG bundle to verified PPTX/PDF export worker."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping

from PIL import Image
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
MAX_SLIDES = 100


def _read_bundle(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> tuple[str, list[bytes]]:
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
    if set(value) != {"schemaVersion", "exportFormat", "slides"}:
        raise PublicWorkerExecutionError("public_export_bundle_invalid_shape")
    if value.get("schemaVersion") != EXPORT_BUNDLE_SCHEMA:
        raise PublicWorkerExecutionError("public_export_bundle_schema_unsupported")
    export_format = value.get("exportFormat")
    if export_format not in {"pptx", "pdf"}:
        raise PublicWorkerExecutionError("public_export_format_invalid")
    raw_slides = value.get("slides")
    if not isinstance(raw_slides, list) or not 1 <= len(raw_slides) <= MAX_SLIDES:
        raise PublicWorkerExecutionError("public_export_slide_count_invalid")
    slides: list[bytes] = []
    for raw in raw_slides:
        if not isinstance(raw, Mapping):
            raise PublicWorkerExecutionError("public_export_slide_ref_invalid")
        try:
            reference = ArtifactRef.from_wire(raw)
        except IsolatedWorkerContractError as error:
            raise PublicWorkerExecutionError("public_export_slide_ref_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.media_type != "image/png"
            or reference.kind != "slide-png"
        ):
            raise PublicWorkerExecutionError("public_export_slide_binding_mismatch")
        png = store.read(reference)
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PublicWorkerExecutionError("public_export_slide_not_png")
        slides.append(png)
    return export_format, slides


def _build_pptx(slides: list[bytes]) -> bytes:
    deck = Presentation()
    deck.slide_width = Inches(13.333333)
    deck.slide_height = Inches(7.5)
    blank = deck.slide_layouts[6]
    while deck.slides:
        relationship_id = deck.slides._sldIdLst[-1].rId
        deck.part.drop_rel(relationship_id)
        del deck.slides._sldIdLst[-1]
    for png in slides:
        slide = deck.slides.add_slide(blank)
        slide.shapes.add_picture(
            io.BytesIO(png),
            0,
            0,
            width=deck.slide_width,
            height=deck.slide_height,
        )
    output = io.BytesIO()
    deck.save(output)
    payload = output.getvalue()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
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
    return payload


def _build_pdf(slides: list[bytes]) -> bytes:
    images: list[Image.Image] = []
    try:
        for png in slides:
            image = Image.open(io.BytesIO(png))
            image.load()
            images.append(image.convert("RGB"))
        output = io.BytesIO()
        images[0].save(
            output,
            format="PDF",
            save_all=True,
            append_images=images[1:],
            resolution=144.0,
        )
        payload = output.getvalue()
        if not payload.startswith(b"%PDF-") or payload.count(b"/Type /Page") < len(slides):
            raise PublicWorkerExecutionError("public_export_pdf_readback_failed")
        return payload
    finally:
        for image in images:
            image.close()


async def execute_export(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> ArtifactRef:
    export_format, slides = _read_bundle(job, store)
    payload = _build_pptx(slides) if export_format == "pptx" else _build_pdf(slides)
    return store.put(
        payload,
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
