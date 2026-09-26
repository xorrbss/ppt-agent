"""Independent, reviewable contracts for screenshot source reconstruction.

The models and helpers live in sibling modules and are re-exported here so
existing imports of ``services.source_content.raster_contracts`` keep working:
``raster_contracts_base`` (source identity, geometry, OCR, region and page
models, and the approval pin), ``raster_contracts_timing`` (analysis run
timing receipts), ``raster_contracts_analysis`` (the sealed ``RasterAnalysis``
record and its cross-page validation) and ``raster_contracts_digest`` (digest
sealing and legacy-compatible digest verification).
"""
from __future__ import annotations

from services.source_content.raster_contracts_analysis import RasterAnalysis
from services.source_content.raster_contracts_base import (
    RasterApprovalPin,
    RasterBox,
    RasterContract,
    RasterOCRText,
    RasterPage,
    RasterRegion,
    RasterSourceIdentity,
    RasterTargetCanvas,
)
from services.source_content.raster_contracts_digest import (
    approval_digest_matches,
    raster_digest,
    require_analysis_digest,
    seal_analysis,
    seal_approval,
)
from services.source_content.raster_contracts_timing import (
    RasterPageTimings,
    RasterStageTimings,
    RasterVisionCallTiming,
    TimingStage,
)

__all__ = [
    "RasterAnalysis",
    "RasterApprovalPin",
    "RasterBox",
    "RasterContract",
    "RasterOCRText",
    "RasterPage",
    "RasterPageTimings",
    "RasterRegion",
    "RasterSourceIdentity",
    "RasterStageTimings",
    "RasterTargetCanvas",
    "RasterVisionCallTiming",
    "TimingStage",
    "approval_digest_matches",
    "raster_digest",
    "require_analysis_digest",
    "seal_analysis",
    "seal_approval",
]
