from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from services.source_locked.contracts import (
    AdmissionContractV1,
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
)
from services.source_locked.digests import basename, media_type_from_filename
from services.source_locked.errors import SourceLockedError


def admit_source_locked_v1(
    filenames: Sequence[str],
    *,
    generation_mode: GenerationMode,
    page_count_policy: PageCountPolicy,
    preserve_all_image_occurrences: bool,
) -> AdmissionContractV1:
    if len(filenames) != 1:
        raise SourceLockedError(
            "SOURCE_COUNT_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            "source_locked v1 requires exactly one source file",
        )
    if generation_mode != GenerationMode.SOURCE_LOCKED:
        raise SourceLockedError(
            "GENERATION_MODE_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            "The source-locked analysis endpoint requires generation_mode=source_locked",
        )

    filename = basename(filenames[0])
    if filename.lower().endswith(".pptm"):
        raise SourceLockedError(
            "SOURCE_PPTM_UNSUPPORTED",
            AnalysisStage.SOURCE_ANALYSIS,
            "source_locked v1 does not accept macro-enabled .pptm files",
        )
    try:
        media_type = media_type_from_filename(filename)
    except ValueError as exc:
        raise SourceLockedError(
            "SOURCE_TYPE_UNSUPPORTED",
            AnalysisStage.SOURCE_ANALYSIS,
            str(exc),
        ) from exc

    try:
        return AdmissionContractV1(
            generation_mode=generation_mode,
            page_count_policy=page_count_policy,
            preserve_all_image_occurrences=preserve_all_image_occurrences,
            source_count=1,
            source_filename=filename,
            source_media_type=media_type,
        )
    except ValidationError as exc:
        raise SourceLockedError(
            "SOURCE_LOCKED_CONTRACT_INVALID",
            AnalysisStage.SOURCE_ANALYSIS,
            "source_locked requires page_count_policy=source and preserve_all_image_occurrences=true",
        ) from exc
