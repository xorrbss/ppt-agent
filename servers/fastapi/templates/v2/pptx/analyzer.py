from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from .analyzer_contract import (
    AnalyzerProviderMetadata,
    ArtifactMetadata,
    CandidateAnalysis,
    CandidateAnalysisSummary,
    CandidateAnalyzer,
    CandidateAnalyzerInput,
    CanvasMetadata,
    CanvasSlideMetadata,
    ValidatedPresentationCandidates,
    candidate_payload_sha256,
)


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
CANDIDATE_MEDIA_TYPE = (
    "application/vnd.presenton.template-v2-pptx-candidates+json;version=1"
)
LOCAL_STATIC_PROVIDER = AnalyzerProviderMetadata(
    id="deterministic-ooxml-static",
    version="1",
    capability="ooxml-structure",
    execution="local",
    status="available",
    network_access=False,
    external_ai=False,
)


def _candidate_input(
    candidates: CandidateAnalyzerInput,
) -> dict[str, Any] | Mapping[str, Any]:
    if isinstance(candidates, BaseModel):
        return candidates.model_dump(mode="python")
    return candidates


class LocalStaticCandidateAnalyzer:
    """Deterministic local provider; performs no rendering or external calls."""

    @property
    def provider(self) -> AnalyzerProviderMetadata:
        return LOCAL_STATIC_PROVIDER

    def analyze(
        self,
        candidates: CandidateAnalyzerInput,
        /,
    ) -> CandidateAnalysis:
        validated = ValidatedPresentationCandidates.model_validate(
            _candidate_input(candidates),
            strict=True,
        )
        shape_count = sum(len(slide.shapes) for slide in validated.slides)
        unsupported_count = sum(
            shape.kind == "unsupported"
            for slide in validated.slides
            for shape in slide.shapes
        )
        return CandidateAnalysis(
            contract_version=1,
            provider=self.provider,
            status="completed",
            source=ArtifactMetadata(
                role="source",
                status="available",
                media_type=PPTX_MEDIA_TYPE,
                sha256=validated.source_sha256,
            ),
            preview=ArtifactMetadata(
                role="preview",
                status="not_provided",
                reason="preview_artifact_not_supplied_to_static_analyzer",
            ),
            render=ArtifactMetadata(
                role="render",
                status="not_run",
                reason="rendering_outside_static_analyzer_contract",
            ),
            candidate=ArtifactMetadata(
                role="candidate",
                status="available",
                media_type=CANDIDATE_MEDIA_TYPE,
                sha256=candidate_payload_sha256(validated),
            ),
            canvas=CanvasMetadata(
                coordinate_space="template-v2",
                origin="top-left",
                units="px",
                slides=[
                    CanvasSlideMetadata(
                        slide_index=index,
                        source_part=slide.source_part,
                        width=slide.width,
                        height=slide.height,
                    )
                    for index, slide in enumerate(validated.slides, start=1)
                ],
            ),
            candidates=validated,
            summary=CandidateAnalysisSummary(
                slide_count=len(validated.slides),
                shape_count=shape_count,
                supported_shape_count=shape_count - unsupported_count,
                unsupported_shape_count=unsupported_count,
                visual_fidelity_status="not_evaluated",
                review_required=True,
            ),
        )


LOCAL_STATIC_ANALYZER: CandidateAnalyzer = LocalStaticCandidateAnalyzer()


def analyze_ooxml_candidates(
    candidates: CandidateAnalyzerInput,
) -> CandidateAnalysis:
    """Compatibility function for the built-in local static analyzer."""

    return LOCAL_STATIC_ANALYZER.analyze(candidates)
