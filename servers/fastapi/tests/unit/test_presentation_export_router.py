from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from zipfile import BadZipFile
import uuid

import pytest

from models.presentation_and_path import PresentationAndPath
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import presentation_export_adapters as adapter_module
from services import presentation_export_router as router
from services.presentation_export_adapters import (
    PresentationExportAdapterRequest,
    PresentationExportAdapterResult,
)
from services.presentation_export_capabilities import (
    PresentationExportCapabilityAnalysis,
    PresentationExportContractError,
    PresentationExportFreshnessError,
    PresentationExportSlideInput,
    UnsupportedCapability,
    analyze_presentation_export_capabilities,
)
from services.presentation_export_router import route_presentation_export
from templates.v2.models.layouts import SlideLayout


def _fixed_layout() -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": "fixed",
            "description": "One fixed editable title",
            "components": [
                {
                    "id": "hero",
                    "description": "Editable hero title",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "text",
                            "name": "title",
                            "position": {"x": 0, "y": 0},
                            "size": {"width": 400, "height": 80},
                            "runs": [{"text": "Original"}],
                            "decorative": False,
                            "min_length": 1,
                            "max_length": 80,
                        }
                    ],
                }
            ],
        }
    )


def _repeat_layout() -> SlideLayout:
    return SlideLayout.model_validate(
        {
            "id": "repeat",
            "description": "Repeatable editable callouts",
            "components": [
                {
                    "id": "callouts",
                    "description": "Repeatable callouts",
                    "position": {"x": 0, "y": 0},
                    "elements": [
                        {
                            "type": "group",
                            "name": f"callout_{index}",
                            "position": {"x": index * 180, "y": 0},
                            "size": {"width": 160, "height": 60},
                            "children": [
                                {
                                    "type": "text",
                                    "name": f"item_title_{index}",
                                    "position": {"x": 0, "y": 0},
                                    "size": {"width": 160, "height": 60},
                                    "runs": [{"text": "Original"}],
                                    "decorative": False,
                                    "min_length": 1,
                                    "max_length": 40,
                                }
                            ],
                        }
                        for index in range(1, 4)
                    ],
                }
            ],
        }
    )


def test_analyzer_selects_clone_for_fixed_and_reconstructed_for_repeat_growth() -> None:
    fixed = analyze_presentation_export_capabilities(
        export_format="pptx",
        slides=(
            PresentationExportSlideInput(
                layout=_fixed_layout(),
                content={"hero": {"title": "Changed"}},
                source_part="ppt/slides/slide1.xml",
            ),
        ),
        native_clone_available=True,
    )
    repeated = analyze_presentation_export_capabilities(
        export_format="pptx",
        slides=(
            PresentationExportSlideInput(
                layout=_repeat_layout(),
                content={
                    "callouts": {
                        "items": [
                            {"item_title": "One"},
                            {"item_title": "Two"},
                        ]
                    }
                },
                source_part="ppt/slides/slide1.xml",
            ),
        ),
        native_clone_available=True,
    )

    assert fixed.candidate_chain == (
        "native_clone",
        "native_reconstructed",
        "general",
    )
    assert repeated.candidate_chain == ("native_reconstructed", "general")
    assert repeated.capability_evidence["repeat_materialization_required"] is True


def _request(tmp_path: Path) -> PresentationExportAdapterRequest:
    return PresentationExportAdapterRequest(
        presentation_id=uuid.uuid4(),
        title="Router",
        export_format="pptx",
        expected_revision=7,
        cookie_header=None,
    )


def _result(
    request: PresentationExportAdapterRequest,
    *,
    adapter: str,
    path: Path,
) -> PresentationExportAdapterResult:
    path.write_bytes(b"pptx")
    return PresentationExportAdapterResult(
        presentation=PresentationAndPath(
            presentation_id=request.presentation_id,
            path=str(path),
        ),
        selected_adapter=adapter,
        artifact_sha256="a" * 64,
        artifact_size_bytes=4,
        compiler=adapter,
        compiler_version="1",
        runtime="test",
        runtime_version="1",
        package_sha256=None,
    )


def _hybrid_coverage(*, revision: int, source_digest: str) -> dict:
    return {
        "schemaVersion": "presenton.authored-hybrid-coverage/v1",
        "mode": "hybrid",
        "source": {
            "presentationRevision": revision,
            "sourceDigest": source_digest,
        },
        "totalSlides": 1,
        "nativeText": 1,
        "nativeImage": 0,
        "nativeShape": 0,
        "nativeTable": 0,
        "nativeChart": 0,
        "rasterRemaining": 1,
        "fullyEditable": False,
        "slides": [
            {
                "slideNumber": 1,
                "nativeText": 1,
                "nativeImage": 0,
                "nativeShape": 0,
                "nativeTable": 0,
                "nativeChart": 0,
                "rasterRemaining": 1,
                "fallbackReasons": ["complex-chart"],
                "visualParity": "not-verified",
                "nativeBackplateDeDuplication": "applied",
            }
        ],
    }


def test_authored_general_adapter_defaults_to_revision_sealed_fidelity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    artifact = tmp_path / "authored-general.pptx"
    artifact.write_bytes(b"fast-pptx")
    calls: dict = {}

    async def fidelity(*args, **kwargs):
        calls["fidelity"] = (args, kwargs)
        return PresentationAndPath(
            presentation_id=presentation_id,
            path=str(artifact),
        )

    async def hybrid(*_args, **_kwargs):
        raise AssertionError("default authored PPTX must not use Hybrid")

    monkeypatch.setattr(
        adapter_module,
        "export_authored_hybrid_presentation",
        hybrid,
    )
    monkeypatch.setattr(
        adapter_module,
        "export_authored_fidelity_presentation",
        fidelity,
    )

    result = asyncio.run(
        adapter_module.run_general_adapter(
            PresentationExportAdapterRequest(
                presentation_id=presentation_id,
                title="Authored",
                export_format="pptx",
                expected_revision=2,
                cookie_header="session=test",
                profile_driver_id="authored",
            )
        )
    )

    assert result.compiler == "authored-fidelity"
    assert result.runtime == "chromium-dom-export"
    assert calls["fidelity"][1]["expected_revision"] == 2


def test_authored_general_adapter_uses_hybrid_only_when_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    artifact = tmp_path / "authored-hybrid.pptx"
    artifact.write_bytes(b"editable-pptx")
    calls: dict = {}

    async def hybrid(*args, **kwargs):
        calls["hybrid"] = (args, kwargs)
        return SimpleNamespace(
            presentation_id=presentation_id,
            path=str(artifact),
            coverage_manifest=_hybrid_coverage(
                revision=2,
                source_digest="d" * 64,
            ),
            renderer_source_sha256="d" * 64,
        )

    async def general(*_args, **_kwargs):
        raise AssertionError("explicit Hybrid PPTX must not use general export")

    monkeypatch.setattr(
        adapter_module,
        "export_authored_hybrid_presentation",
        hybrid,
    )
    monkeypatch.setattr(adapter_module, "export_presentation", general)

    result = asyncio.run(
        adapter_module.run_general_adapter(
            PresentationExportAdapterRequest(
                presentation_id=presentation_id,
                title="Authored",
                export_format="pptx",
                expected_revision=2,
                cookie_header="session=test",
                profile_driver_id="authored",
                pptx_mode="hybrid",
                font_embedding=True,
            )
        )
    )

    assert result.compiler == "authored-hybrid"
    assert result.runtime == "chromium-native-layer-export"
    assert calls["hybrid"][1]["expected_revision"] == 2
    assert calls["hybrid"][1]["font_embedding"] is True
    assert result.coverage_manifest["rasterRemaining"] == 1
    assert result.renderer_source_sha256 == "d" * 64

    provenance = router.PresentationExportRouteResult(
        result=result,
        adapter_chain=("general",),
        fallback_reason=None,
        capability_evidence={"selected_adapter": "general"},
        asset_digests={},
        asset_digests_verified=True,
    ).provenance(
        source_revision=2,
        source_snapshot_sha256="a" * 64,
        html_source_sha256="b" * 64,
        composition_sha256="c" * 64,
        asset_manifest_sha256="e" * 64,
    )
    assert provenance["coverage_manifest"]["raster_remaining"] == 1
    assert provenance["freshness"] == {
        "source_revision": 2,
        "source_snapshot_sha256": "a" * 64,
        "html_source_sha256": "b" * 64,
        "composition_sha256": "c" * 64,
        "asset_manifest_sha256": "e" * 64,
        "renderer_source_sha256": "d" * 64,
        "package_sha256": None,
        "asset_digests_match": True,
        "package_digest_match": True,
    }


def test_router_falls_back_only_after_explicit_unsupported(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    calls: list[str] = []

    async def clone(_request):
        calls.append("native_clone")
        raise UnsupportedCapability(
            "shape_growth_unsupported",
            adapter="native_clone",
            path="slides.0",
        )

    async def general(adapter_request):
        calls.append("general")
        return _result(
            adapter_request,
            adapter="general",
            path=tmp_path / "general.pptx",
        )

    routed = asyncio.run(
        route_presentation_export(
            request,
            PresentationExportCapabilityAnalysis(
                candidate_chain=("native_clone", "general"),
                capability_evidence={"reason": "fixed"},
                asset_digests={},
            ),
            adapters={"native_clone": clone, "general": general},
        )
    )

    assert calls == ["native_clone", "general"]
    assert routed.adapter_chain == ("native_clone", "general")
    assert routed.fallback_reason == "shape_growth_unsupported"
    assert routed.provenance(
        source_revision=7,
        source_snapshot_sha256="b" * 64,
    )["freshness"]["source_revision"] == 7


@pytest.mark.parametrize(
    "hard_error",
    [
        PresentationExportContractError("invalid_input"),
        PresentationExportFreshnessError("stale_presentation_revision"),
        PresentationExportFreshnessError("asset_digest_mismatch"),
        BadZipFile("malformed_ooxml"),
        RuntimeError("unexpected_internal_error"),
    ],
    ids=["invalid", "stale", "digest", "ooxml", "internal"],
)
def test_router_never_falls_back_after_hard_failure(
    tmp_path: Path,
    hard_error: BaseException,
) -> None:
    request = _request(tmp_path)
    calls: list[str] = []

    async def clone(_request):
        calls.append("native_clone")
        raise hard_error

    async def general(adapter_request):
        calls.append("general")
        return _result(
            adapter_request,
            adapter="general",
            path=tmp_path / "should-not-exist.pptx",
        )

    with pytest.raises(type(hard_error)):
        asyncio.run(
            route_presentation_export(
                request,
                PresentationExportCapabilityAnalysis(
                    candidate_chain=("native_clone", "general"),
                    capability_evidence={"reason": "fixed"},
                    asset_digests={},
                ),
                adapters={"native_clone": clone, "general": general},
            )
        )

    assert calls == ["native_clone"]


def test_general_execution_analyzes_real_persisted_slide_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation_id = uuid.uuid4()
    asset_id = str(uuid.uuid4())
    digest = "a" * 64
    presentation = PresentationModel(
        id=presentation_id,
        content="general persisted deck",
        n_slides=1,
        language="en",
        revision=9,
    )
    slide = SlideModel(
        presentation=presentation_id,
        layout_group="general",
        layout="title",
        index=0,
        content={"image": {"assetId": asset_id, "sha256": digest}},
    )
    captured: dict = {}
    attempt_id = uuid.uuid4()
    route_result = SimpleNamespace(result=SimpleNamespace())

    class ScalarRows:
        def all(self):
            return [slide]

    class QueryResult:
        def scalars(self):
            return ScalarRows()

    class Session:
        async def get(self, model, row_id):
            assert model is PresentationModel and row_id == presentation_id
            return presentation

        async def execute(self, _statement):
            return QueryResult()

    async def verify_assets(_session, *, expected_asset_digests):
        captured["verified_assets"] = expected_asset_digests

    async def begin(_session, **kwargs):
        captured["begin"] = kwargs
        return SimpleNamespace(id=attempt_id)

    async def route(export_request, analysis, *, asset_digests_verified=False):
        captured["request"] = export_request
        captured["analysis"] = analysis
        captured["asset_digests_verified"] = asset_digests_verified
        return route_result

    monkeypatch.setattr(router, "require_export_asset_digests", verify_assets)
    monkeypatch.setattr(router, "begin_export_attempt", begin)
    monkeypatch.setattr(router, "route_presentation_export", route)

    execution = asyncio.run(
        router.execute_presentation_export(
            Session(),
            render_presentation_id=presentation_id,
            attempt_presentation_id=presentation_id,
            export_format="pptx",
            expected_render_revision=9,
            source_revision=9,
            title="General",
            cookie_header=None,
            owner_id=None,
            owner_scope="anonymous",
            workspace_operation_id=None,
            request_hash="b" * 64,
            idempotency_key_hash="c" * 64,
        )
    )

    assert execution.route is route_result
    assert captured["request"].pptx_mode == "fidelity"
    assert captured["request"].font_embedding is False
    assert captured["analysis"].candidate_chain == ("general",)
    assert captured["analysis"].asset_digests == {asset_id: digest}
    assert captured["verified_assets"] == {asset_id: digest}
    assert captured["asset_digests_verified"] is True
    assert captured["begin"]["source_snapshot_sha256"] == (
        execution.source_snapshot_sha256
    )
