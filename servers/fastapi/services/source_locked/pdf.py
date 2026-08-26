from __future__ import annotations

from io import BytesIO

import pdfplumber

from services.source_locked.contracts import (
    AnalysisStage,
    OcrSidecarPolicyV1,
    PdfPageRenderV1,
    PdfSourceManifestV1,
)
from services.source_locked.digests import sha256_digest
from services.source_locked.errors import SourceLockedError


def analyze_pdf(data: bytes) -> PdfSourceManifestV1:
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
    except Exception as exc:
        raise SourceLockedError(
            "PDF_PACKAGE_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            "The uploaded .pdf cannot be parsed",
        ) from exc
    if page_count < 1:
        raise SourceLockedError(
            "PDF_EMPTY",
            AnalysisStage.SOURCE_ANALYSIS,
            "The PDF source contains no pages",
        )

    bundle = tuple(
        PdfPageRenderV1(
            page_number=page_number,
            target_filename=f"page-{page_number:04d}.png",
        )
        for page_number in range(1, page_count + 1)
    )
    return PdfSourceManifestV1(
        page_count=page_count,
        slide_count=page_count,
        page_render_bundle=bundle,
        ocr_sidecar=OcrSidecarPolicyV1(),
        package_sha256=sha256_digest(data),
    )
