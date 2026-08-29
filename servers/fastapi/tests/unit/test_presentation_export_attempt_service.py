from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.presentation_and_path import PresentationAndPath
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
    ExportPowerPointQaEvidence,
    ExportVisualParityEvidence,
    PresentationExportCoverageManifest,
    PresentationExportAttempt,
    validate_export_attempt_provenance,
)
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceOperation,
)
from services.presentation_export_attempt_service import (
    begin_export_attempt,
    build_export_source_snapshot_sha256,
    sha256_canonical_json,
    terminalize_export_attempt_failed,
    terminalize_export_attempt_succeeded,
)
from services.authored_quality_receipt_v2 import (
    QualityReceiptLedger,
    create_quality_receipt_v2,
)
from services.presentation_export_direct_attempt_service import (
    _visual_parity_status_from_powerpoint_qa,
    begin_direct_fidelity_export_attempt,
    complete_direct_hybrid_export_attempt,
    _validate_hybrid_atomic_binding,
    list_direct_hybrid_export_attempt_history,
)
import services.presentation_export_direct_attempt_service as direct_attempt_service
import services.presentation_export_powerpoint_qa as powerpoint_qa_service
from services.presentation_export_adapters import PresentationExportAdapterResult
from services.presentation_export_capabilities import (
    PresentationExportContractError,
    PresentationExportFreshnessError,
    analyze_presentation_export_capabilities,
)
from services.presentation_export_router import (
    PresentationExportExecution,
    PresentationExportRouteResult,
    terminalize_standalone_presentation_export,
)


def test_hybrid_sidecar_pointer_survives_second_replace_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "deck.pptx"
    artifact.write_bytes(b"pptx")
    first = powerpoint_qa_service.publish_finalized_hybrid_sidecars(
        artifact,
        quality={"generation": 1, "kind": "quality"},
        coverage={"generation": 1, "kind": "coverage"},
    )
    pointer = Path(f"{artifact}.evidence.json")
    assert json.loads(pointer.read_text(encoding="utf-8")) == first

    real_replace = powerpoint_qa_service.os.replace
    replacements = 0

    def interrupt_second_replace(source, destination):
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected second replace interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(powerpoint_qa_service.os, "replace", interrupt_second_replace)
    with pytest.raises(
        PresentationExportContractError,
        match="presentation_export_evidence_sidecar_finalization_failed",
    ):
        powerpoint_qa_service.publish_finalized_hybrid_sidecars(
            artifact,
            quality={"generation": 2, "kind": "quality"},
            coverage={"generation": 2, "kind": "coverage"},
        )
    assert json.loads(pointer.read_text(encoding="utf-8")) == first

    monkeypatch.setattr(powerpoint_qa_service.os, "replace", real_replace)
    second = powerpoint_qa_service.publish_finalized_hybrid_sidecars(
        artifact,
        quality={"generation": 2, "kind": "quality"},
        coverage={"generation": 2, "kind": "coverage"},
    )
    assert json.loads(pointer.read_text(encoding="utf-8")) == second
    for member in (second["quality"], second["coverage"]):
        payload = (tmp_path / member["file"]).read_bytes()
        assert sha256(payload).hexdigest() == member["sha256"]
    powerpoint_qa_service.require_matching_hybrid_sidecars(
        artifact,
        quality={"generation": 2, "kind": "quality"},
        coverage={"generation": 2, "kind": "coverage"},
        canonical_digest=sha256_canonical_json,
    )


def test_powerpoint_qa_does_not_self_promote_visual_parity() -> None:
    passed = ExportPowerPointQaEvidence.model_validate(
        {
            "status": "passed",
            "platform": "windows",
            "supported": True,
            "automation": "PowerPoint.COM",
            "reopened": True,
            "rendered": True,
            "readback": True,
            "repairDialogs": 0,
            "renderedPngSha256": ["a" * 64],
            "slideCount": 1,
            "inspectionFailures": 0,
        }
    )
    failed = passed.model_copy(update={"status": "failed"})

    assert _visual_parity_status_from_powerpoint_qa(passed) == "not-verified"
    assert _visual_parity_status_from_powerpoint_qa(failed) == "failed"
    assert passed.inspection_failures == 0


def test_passed_powerpoint_qa_allows_truthful_unverified_visual_parity() -> None:
    digest = "a" * 64
    binding = {
        "presentation_id": str(uuid.uuid4()),
        "export_attempt_id": str(uuid.uuid4()),
        "presentation_revision": 1,
        "canonical_source_sha256": digest,
        "renderer_source_sha256": digest,
        "pptx_sha256": digest,
    }
    qa = {
        "status": "passed",
        "platform": "windows",
        "supported": True,
        "automation": "PowerPoint.COM",
        "reopened": True,
        "rendered": True,
        "readback": True,
        "repairDialogs": 0,
        "renderedPngSha256": [digest],
        "slideCount": 1,
        "typographyIssuesGt5Pt": 0,
        "typographyIssuesGt10Pt": 0,
        "nativeText": 1,
        "nativeImage": 0,
        "nativeShape": 0,
        "nativeTable": 0,
        "nativeChart": 0,
        "nativeImageCrop": 0,
    }
    evidence = ExportVisualParityEvidence.model_validate(
        {
            "status": "not-verified",
            "binding": binding,
            "qualitySidecarSha256": digest,
            "coverageSidecarSha256": digest,
            "powerpointQa": qa,
        }
    )

    assert evidence.status == "not-verified"


def test_hybrid_coverage_accepts_strict_semantic_receipt_digests() -> None:
    digest = "sha256:" + ("a" * 64)
    manifest = PresentationExportCoverageManifest.model_validate(
        {
            "schemaVersion": "presenton.authored-hybrid-coverage/v2",
            "mode": "hybrid",
            "cjkCoverageProfileId": "ja-ko-zh-bounded-fixture-v1",
            "nativeTextPolicy": {
                "koreanLatin": "bounded-native",
                "japanese": "raster-required",
                "chinese": "raster-required",
            },
            "source": {
                "exportAttemptId": str(uuid.uuid4()),
                "presentationRevision": 1,
                "canonicalSourceDigest": "b" * 64,
                "sourceDigest": "c" * 64,
            },
            "totalSlides": 1,
            "nativeText": 0,
            "nativeImage": 0,
            "nativeShape": 0,
            "nativeTable": 0,
            "nativeChart": 0,
            "logicalCoverage": {
                "nativeText": 0,
                "nativeImage": 0,
                "nativeShape": 0,
                "nativeTable": 0,
                "nativeChart": 0,
                "rasterRemaining": 1,
            },
            "ooxmlReadback": {
                "status": "passed",
                "nativeText": 0,
                "nativeImage": 0,
                "nativeShape": 0,
                "nativeTable": 0,
                "nativeChart": 0,
                "rasterRemaining": 1,
            },
            "backplateCount": 1,
            "typographyIssues": [],
            "rasterRemaining": 1,
            "fullyEditable": False,
            "visualParity": "not-verified",
            "slides": [
                {
                    "slideNumber": 1,
                    "nativeText": 0,
                    "nativeImage": 0,
                    "nativeShape": 0,
                    "nativeTable": 0,
                    "nativeChart": 0,
                    "logicalCoverage": {
                        "nativeText": 0,
                        "nativeImage": 0,
                        "nativeShape": 0,
                        "nativeTable": 0,
                        "nativeChart": 0,
                        "rasterRemaining": 1,
                    },
                    "ooxmlReadback": {
                        "status": "passed",
                        "nativeText": 0,
                        "nativeImage": 0,
                        "nativeShape": 0,
                        "nativeTable": 0,
                        "nativeChart": 0,
                        "rasterRemaining": 1,
                    },
                    "backplateCount": 1,
                    "typographyIssues": [],
                    "rasterRemaining": 1,
                    "fallbackReasons": ["complex-chart"],
                    "visualParity": "not-verified",
                    "nativeBackplateDeDuplication": "not-applicable",
                    "semanticReceipts": {
                        "rasterDegradationReceiptDigest": digest
                    },
                }
            ],
        }
    )
    assert manifest.slides[0].semantic_receipts is not None
    assert (
        manifest.slides[0].semantic_receipts.raster_degradation_receipt_digest
        == digest
    )


def test_hybrid_coverage_accepts_table_chart_and_crop_only_with_matching_readback() -> None:
    slide = {
        "slideNumber": 1,
        "nativeText": 0,
        "nativeImage": 1,
        "nativeShape": 0,
        "nativeTable": 1,
        "nativeChart": 1,
        "nativeImageCrop": 1,
        "logicalCoverage": {
            "nativeText": 0,
            "nativeImage": 1,
            "nativeShape": 0,
            "nativeTable": 1,
            "nativeChart": 1,
            "nativeImageCrop": 1,
            "rasterRemaining": 1,
        },
        "ooxmlReadback": {
            "status": "passed",
            "nativeText": 0,
            "nativeImage": 1,
            "nativeShape": 0,
            "nativeTable": 1,
            "nativeChart": 1,
            "nativeImageCrop": 1,
            "rasterRemaining": 1,
        },
        "backplateCount": 0,
        "typographyIssues": [],
        "rasterRemaining": 1,
        "fallbackReasons": ["raster-residual"],
        "visualParity": "not-verified",
        "nativeBackplateDeDuplication": "applied",
    }
    document = {
        "schemaVersion": "presenton.authored-hybrid-coverage/v2",
        "mode": "hybrid",
        "cjkCoverageProfileId": "ja-ko-zh-bounded-fixture-v1",
        "nativeTextPolicy": {
            "koreanLatin": "bounded-native",
            "japanese": "raster-required",
            "chinese": "raster-required",
        },
        "source": {
            "exportAttemptId": str(uuid.uuid4()),
            "presentationRevision": 1,
            "canonicalSourceDigest": "b" * 64,
            "sourceDigest": "c" * 64,
        },
        "totalSlides": 1,
        "nativeText": 0,
        "nativeImage": 1,
        "nativeShape": 0,
        "nativeTable": 1,
        "nativeChart": 1,
        "nativeImageCrop": 1,
        "logicalCoverage": slide["logicalCoverage"],
        "ooxmlReadback": slide["ooxmlReadback"],
        "backplateCount": 0,
        "typographyIssues": [],
        "rasterRemaining": 1,
        "fullyEditable": False,
        "visualParity": "not-verified",
        "slides": [slide],
    }
    manifest = PresentationExportCoverageManifest.model_validate(document)
    assert manifest.native_table == 1
    assert manifest.native_chart == 1
    assert manifest.native_image_crop == 1
    document["slides"] = [{**slide, "nativeChart": 2}]
    document["nativeChart"] = 2
    with pytest.raises(ValueError, match="matching semantic and OOXML readback"):
        PresentationExportCoverageManifest.model_validate(document)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceAsset.__table__,
    PresentationExportAttempt.__table__,
    SlideModel.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _presentation(*, presentation_id: uuid.UUID | None = None) -> PresentationModel:
    return PresentationModel(
        id=presentation_id or uuid.uuid4(),
        content="semantic deck",
        n_slides=1,
        language="en",
        revision=4,
        title="Stable",
        mode="authored",
        theme={"mode": "authored"},
    )


def _slide(presentation_id: uuid.UUID, *, content: dict | None = None) -> SlideModel:
    return SlideModel(
        id=uuid.uuid4(),
        presentation=presentation_id,
        layout_group="general",
        layout="title",
        index=0,
        content=content or {"title": "Same content"},
        speaker_note="Same note",
    )


def _provenance(
    *,
    source_revision: int = 4,
    source_snapshot_sha256: str = "c" * 64,
    asset_digests: dict[str, str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "adapter_chain": ["general"],
        "capability_evidence": {"selected_adapter": "general"},
        "fallback_reason": None,
        "asset_digests": asset_digests or {},
        "compiler_runtime": {
            "compiler": "presentation-export",
            "compiler_version": "0.4.3",
            "runtime": "chromium-dom-export",
            "runtime_version": "presenton.render-artifact/v1",
        },
        "freshness": {
            "source_revision": source_revision,
            "source_snapshot_sha256": source_snapshot_sha256,
            "package_sha256": None,
            "asset_digests_match": True,
            "package_digest_match": True,
        },
    }


def test_source_snapshot_ignores_staging_and_slide_uuids_but_tracks_content() -> None:
    durable_id = uuid.uuid4()
    first_presentation = _presentation()
    second_presentation = _presentation()
    first = build_export_source_snapshot_sha256(
        first_presentation,
        [_slide(first_presentation.id)],
        durable_presentation_id=durable_id,
    )
    retry = build_export_source_snapshot_sha256(
        second_presentation,
        [_slide(second_presentation.id)],
        durable_presentation_id=durable_id,
    )
    changed = build_export_source_snapshot_sha256(
        second_presentation,
        [_slide(second_presentation.id, content={"title": "Changed"})],
        durable_presentation_id=durable_id,
    )

    assert first == retry
    assert changed != first


def test_general_analysis_collects_persisted_assets_and_rejects_missing_digest() -> None:
    asset_id = str(uuid.uuid4())
    analysis = analyze_presentation_export_capabilities(
        export_format="pptx",
        persisted_slide_payloads=(
            {"content": {"image": {"assetId": asset_id, "sha256": "a" * 64}}},
        ),
    )

    assert analysis.candidate_chain == ("general",)
    assert analysis.asset_digests == {asset_id: "a" * 64}
    with pytest.raises(
        PresentationExportFreshnessError,
        match="presentation_export_asset_digest_required",
    ):
        analyze_presentation_export_capabilities(
            export_format="pptx",
            persisted_slide_payloads=(
                {"content": {"image": {"asset_id": asset_id}}},
            ),
        )


def test_hybrid_analysis_accepts_only_digest_sealed_scene_manifest_assets() -> None:
    content = b"canonical-scene-image"
    digest = sha256(content).hexdigest()
    asset_id = "asset:431cedf125e44b64a48b2351"
    manifest = {
        "assetId": asset_id,
        "assetDigest": f"sha256:{digest}",
        "mediaType": "image/png",
        "storageReference": (
            "data:image/png;base64," + base64.b64encode(content).decode("ascii")
        ),
        "sizeBytes": len(content),
    }
    theme_payload = {"presentationTheme": {"scene": {"assetManifest": [manifest]}}}
    slide_payload = {
        "content": {
            "elements": [
                {
                    "kind": "image",
                    "assetId": asset_id,
                    "assetDigest": f"sha256:{digest}",
                }
            ]
        }
    }

    analysis = analyze_presentation_export_capabilities(
        export_format="pptx",
        persisted_slide_payloads=(theme_payload, slide_payload),
    )
    assert analysis.asset_digests == {asset_id: digest}

    manifest["storageReference"] = "https://example.invalid/image.png"
    with pytest.raises(
        PresentationExportContractError,
        match="presentation_export_scene_asset_manifest_invalid",
    ):
        analyze_presentation_export_capabilities(
            export_format="pptx",
            persisted_slide_payloads=(theme_payload, slide_payload),
        )


def test_hybrid_v2_binds_attempt_and_separates_logical_readback_backplate() -> None:
    presentation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    canonical = "a" * 64
    renderer = "b" * 64
    pptx = "c" * 64
    counts = {
        "nativeText": 1,
        "nativeImage": 0,
        "nativeShape": 0,
        "nativeTable": 0,
        "nativeChart": 0,
        "rasterRemaining": 0,
    }
    slide = {
        "slideNumber": 1,
        **counts,
        "logicalCoverage": counts,
        "ooxmlReadback": {"status": "passed", **counts},
        "backplateCount": 1,
        "typographyIssues": [],
        "fallbackReasons": [],
        "visualParity": "not-verified",
        "nativeBackplateDeDuplication": "applied",
    }
    coverage = {
        "schemaVersion": "presenton.authored-hybrid-coverage/v2",
        "mode": "hybrid",
        "cjkCoverageProfileId": "ja-ko-zh-bounded-fixture-v1",
        "nativeTextPolicy": {
            "koreanLatin": "bounded-native",
            "japanese": "raster-required",
            "chinese": "raster-required",
        },
        "source": {
            "exportAttemptId": str(attempt_id),
            "presentationRevision": 4,
            "canonicalSourceDigest": canonical,
            "sourceDigest": renderer,
        },
        "totalSlides": 1,
        **counts,
        "logicalCoverage": counts,
        "ooxmlReadback": {"status": "passed", **counts},
        "backplateCount": 1,
        "typographyIssues": [],
        "fullyEditable": True,
        "slides": [slide],
    }
    provenance = _provenance(
        source_snapshot_sha256=canonical,
    )
    provenance["compiler_runtime"]["compiler"] = "authored-hybrid"
    provenance["freshness"]["renderer_source_sha256"] = renderer
    provenance["freshness"]["package_sha256"] = pptx
    provenance["coverage_manifest"] = coverage
    provenance["atomic_binding"] = {
        "presentation_id": str(presentation_id),
        "export_attempt_id": str(attempt_id),
        "presentation_revision": 4,
        "canonical_source_sha256": canonical,
        "renderer_source_sha256": renderer,
        "pptx_sha256": pptx,
    }
    provenance["powerpoint_qa"] = {
        "status": "not-verified",
        "platform": "windows",
    }

    validated = validate_export_attempt_provenance(provenance)
    assert validated["coverage_manifest"]["backplate_count"] == 1
    assert validated["coverage_manifest"]["logical_coverage"]["raster_remaining"] == 0

    provenance["powerpoint_qa"] = {
        "status": "passed",
        "platform": "windows",
        "supported": True,
    }
    with pytest.raises(ValueError, match="COM reopen/render/readback"):
        validate_export_attempt_provenance(provenance)

    passed_qa = {
        "status": "passed",
        "platform": "windows",
        "supported": True,
        "automation": "PowerPoint.COM",
        "reopened": True,
        "rendered": True,
        "readback": True,
        "repair_dialogs": 0,
        "rendered_png_sha256": ["d" * 64],
        "slide_count": 1,
        "typography_issues_gt_5pt": 0,
        "typography_issues_gt_10pt": 0,
        "native_text": 1,
        "native_image": 0,
        "native_shape": 0,
        "native_table": 0,
        "native_chart": 0,
        "native_image_crop": 0,
    }
    coverage["visualParity"] = "passed"
    coverage["slides"][0]["visualParity"] = "passed"
    quality = {
        "schemaVersion": "presenton.export-quality/v1",
        "status": "fully-editable",
        "exportAttemptId": str(attempt_id),
        "atomicBinding": {
            "presentationId": str(presentation_id),
            "presentationRevision": 4,
            "canonicalSourceDigest": canonical,
            "rendererSourceDigest": renderer,
            "pptxSha256": pptx,
        },
        "powerPointQa": {
            "status": "passed",
            "platform": "windows",
            "supported": True,
            "automation": "PowerPoint.COM",
            "reopened": True,
            "rendered": True,
            "readback": True,
            "repairDialogs": 0,
            "renderedPngSha256": ["d" * 64],
            "powerpointVersion": None,
            "slideCount": 1,
            "typographyIssuesGt5Pt": 0,
            "typographyIssuesGt10Pt": 0,
            "nativeText": 1,
            "nativeImage": 0,
            "nativeShape": 0,
            "nativeTable": 0,
            "nativeChart": 0,
            "nativeImageCrop": 0,
        },
    }
    provenance["coverage_manifest"] = coverage
    provenance["quality_sidecar"] = quality
    provenance["powerpoint_qa"] = passed_qa
    provenance["visual_parity"] = {
        "status": "passed",
        "binding": provenance["atomic_binding"],
        "quality_sidecar_sha256": sha256_canonical_json(quality),
        "coverage_sidecar_sha256": sha256_canonical_json(coverage),
        "powerpoint_qa": passed_qa,
    }
    assert validate_export_attempt_provenance(provenance)["visual_parity"][
        "status"
    ] == "passed"

    quality["atomicBinding"]["presentationId"] = str(uuid.uuid4())
    provenance["quality_sidecar"] = quality
    provenance["visual_parity"]["quality_sidecar_sha256"] = sha256_canonical_json(
        quality
    )
    with pytest.raises(ValueError, match="quality sidecar visual parity binding"):
        validate_export_attempt_provenance(provenance)


def test_complete_direct_hybrid_accepts_exact_bounded_cjk_coverage_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "hybrid-complete-cjk.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                slides = list(
                    await session.scalars(
                        select(SlideModel)
                        .where(SlideModel.presentation == presentation_id)
                        .order_by(SlideModel.index, SlideModel.id)
                    )
                )
                assert presentation is not None
                scene_receipt = {
                    "schemaVersion": "authored-deck-scene-envelope/v1",
                    "sceneDigest": "sha256:" + ("6" * 64),
                    "sourceDigest": "sha256:" + ("7" * 64),
                    "qidDigest": "sha256:" + ("8" * 64),
                    "slideSceneDigests": ["sha256:" + ("a" * 64)],
                    "slideRevisions": [1],
                }
                generation_receipt = create_quality_receipt_v2(
                    QualityReceiptLedger(),
                    attempt_id="generation-attempt-1",
                    revision=7,
                    ledger_digest="sha256:" + ("1" * 64),
                    deck_html_digest="sha256:" + ("2" * 64),
                    evidence={
                        "safety": {"status": "passed"},
                        "capacity": {
                            "browserObserved": True,
                            "checks": {
                                name: True
                                for name in (
                                    "text",
                                    "title",
                                    "body",
                                    "font",
                                    "qid",
                                    "image",
                                )
                            },
                        },
                        "sourceReadback": {
                            "required": False,
                            "status": "not_applicable",
                        },
                    },
                    requested_release_profile="release",
                    pre_export_evaluation_profile="default",
                    scene_envelope=scene_receipt,
                )
                presentation.theme = {
                    "authoredQualityGate": {
                        **generation_receipt.to_dict(),
                        "sceneEnvelope": scene_receipt,
                    }
                }
                canonical = build_export_source_snapshot_sha256(presentation, slides)
                attempt_id = uuid.uuid4()
                renderer = "b" * 64
                provenance = _provenance(source_snapshot_sha256=canonical)
                provenance["capability_evidence"] = {
                    "selected_adapter": "general",
                    "export_mode": "hybrid",
                }
                provenance["freshness"]["renderer_source_sha256"] = renderer
                session.add(
                    PresentationExportAttempt(
                        id=attempt_id,
                        owner_id=None,
                        owner_scope="anonymous",
                        presentation_id=presentation_id,
                        format="pptx",
                        state="running",
                        source_revision=4,
                        source_snapshot_sha256=canonical,
                        request_hash="d" * 64,
                        idempotency_key_hash="e" * 64,
                        requested_adapter="general",
                        provenance_schema_version=1,
                        provenance=provenance,
                        started_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()

            artifact = tmp_path / "bounded-cjk.pptx"
            artifact.write_bytes(b"PK\x03\x04bounded-cjk")
            pptx_sha256 = sha256(artifact.read_bytes()).hexdigest()
            counts = {
                "nativeText": 1,
                "nativeImage": 0,
                "nativeShape": 0,
                "nativeTable": 0,
                "nativeChart": 0,
                "nativeImageCrop": 0,
                "rasterRemaining": 0,
            }
            coverage = {
                "schemaVersion": "presenton.authored-hybrid-coverage/v2",
                "mode": "hybrid",
                "cjkCoverageProfileId": "ja-ko-zh-bounded-fixture-v1",
                "nativeTextPolicy": {
                    "koreanLatin": "bounded-native",
                    "japanese": "raster-required",
                    "chinese": "raster-required",
                },
                "source": {
                    "exportAttemptId": str(attempt_id),
                    "presentationRevision": 4,
                    "canonicalSourceDigest": canonical,
                    "sourceDigest": renderer,
                },
                "totalSlides": 1,
                **counts,
                "logicalCoverage": counts,
                "ooxmlReadback": {"status": "passed", **counts},
                "backplateCount": 1,
                "typographyIssues": [],
                "fullyEditable": True,
                "visualParity": "not-verified",
                "slides": [
                    {
                        "slideNumber": 1,
                        **counts,
                        "logicalCoverage": counts,
                        "ooxmlReadback": {"status": "passed", **counts},
                        "backplateCount": 1,
                        "typographyIssues": [],
                        "fallbackReasons": [],
                        "visualParity": "not-verified",
                        "nativeBackplateDeDuplication": "applied",
                    }
                ],
            }
            quality = {
                "schemaVersion": "presenton.export-quality/v1",
                "mode": "hybrid",
                "status": "fully-editable",
                "totalSlides": 1,
                "nativeTextElements": 1,
                "nativeImageElements": 0,
                "nativeShapeElements": 0,
                "nativeTableElements": 0,
                "nativeChartElements": 0,
                "rasterFallbackElements": 0,
                "logicalCoverage": counts,
                "ooxmlReadback": {"status": "passed", **counts},
                "nativeCoverage": {
                    "status": "passed",
                    "eligibleElementCount": 1,
                    "nativeElementCount": 1,
                    "ratio": 1.0,
                    "rasterFallbacks": [],
                },
                "exportAttemptId": str(attempt_id),
                "atomicBinding": {
                    "presentationId": str(presentation_id),
                    "presentationRevision": 4,
                    "canonicalSourceDigest": canonical,
                    "rendererSourceDigest": renderer,
                    "pptxSha256": pptx_sha256,
                },
                "slides": [
                    {
                        "slideNumber": 1,
                        "qidCoverage": [
                            {
                                "elementId": "s01-title",
                                "logical": "native",
                                "ooxml": "present",
                            }
                        ],
                    }
                ],
            }

            invalid_profile = {**coverage, "cjkCoverageProfileId": "unbounded"}
            with pytest.raises(ValueError, match="cjkCoverageProfileId"):
                PresentationExportCoverageManifest.model_validate(invalid_profile)
            invalid_policy = {
                **coverage,
                "nativeTextPolicy": {
                    **coverage["nativeTextPolicy"],
                    "japanese": "bounded-native",
                },
            }
            with pytest.raises(ValueError, match="nativeTextPolicy.japanese"):
                PresentationExportCoverageManifest.model_validate(invalid_policy)

            async def trusted_qa(*_args, **_kwargs):
                return {
                    "status": "passed",
                    "platform": "windows",
                    "supported": True,
                    "automation": "PowerPoint.COM",
                    "reopened": True,
                    "rendered": True,
                    "readback": True,
                    "semanticPayloadBinding": "passed",
                    "repairDialogs": 0,
                    "observationStates": [
                        "first-open",
                        "save-copy",
                        "reopen",
                        "readback",
                        "observation-sealed",
                    ],
                    "dialogWatcherStatus": "sealed",
                    "dialogWatcherArmedBeforeFirstOpen": True,
                    "ownedProcessId": 4242,
                    "readbackMatch": True,
                    "renderedPngSha256": ["f" * 64],
                    "slideCount": 1,
                    "typographyIssuesGt5Pt": 0,
                    "typographyIssuesGt10Pt": 0,
                    "nativeText": 1,
                    "nativeImage": 0,
                    "nativeShape": 0,
                    "nativeTable": 0,
                    "nativeChart": 0,
                    "nativeImageCrop": 0,
                    "fontPreflight": {
                        "status": "passed",
                        "cjkTextRuns": 1,
                        "declaredEastAsianTypefaces": ["Noto Sans KR"],
                        "missingEastAsianTypefaceRuns": 0,
                        "replacementGlyphs": 0,
                        "embeddedFontFiles": 1,
                        "embeddingRequired": True,
                        "embeddingVerified": True,
                        "ooxmlReadbackSha256": "a" * 64,
                    },
                }

            async def bind_semantic(*_args, **_kwargs):
                assert _kwargs["attempt"].state == "running"
                return "sha256:" + ("e" * 64)

            monkeypatch.setattr(
                direct_attempt_service,
                "resolve_hybrid_artifact",
                lambda _path: artifact,
            )
            monkeypatch.setattr(
                direct_attempt_service,
                "require_matching_hybrid_sidecars",
                lambda *_args, **_kwargs: None,
            )
            monkeypatch.setattr(
                direct_attempt_service,
                "trusted_powerpoint_qa",
                trusted_qa,
            )
            monkeypatch.setattr(
                direct_attempt_service,
                "bind_completed_semantic_export_attempt",
                bind_semantic,
            )
            async with maker() as session:
                real_commit = session.commit
                fail_commit_once = True

                async def interrupted_commit() -> None:
                    nonlocal fail_commit_once
                    if fail_commit_once:
                        fail_commit_once = False
                        raise RuntimeError("injected terminal commit interruption")
                    await real_commit()

                monkeypatch.setattr(session, "commit", interrupted_commit)
                with pytest.raises(
                    RuntimeError,
                    match="injected terminal commit interruption",
                ):
                    await complete_direct_hybrid_export_attempt(
                        session,
                        presentation_id=presentation_id,
                        attempt_id=attempt_id,
                        pptx_sha256=pptx_sha256,
                        pptx_size_bytes=artifact.stat().st_size,
                        artifact_path=str(artifact),
                        quality=quality,
                        coverage=coverage,
                        powerpoint_qa=None,
                    )
                await session.rollback()
                interrupted = await session.get(PresentationExportAttempt, attempt_id)
                assert interrupted is not None and interrupted.state == "running"
                assert Path(f"{artifact}.evidence.json").is_file()

                completed, finalized = await complete_direct_hybrid_export_attempt(
                    session,
                    presentation_id=presentation_id,
                    attempt_id=attempt_id,
                    pptx_sha256=pptx_sha256,
                    pptx_size_bytes=artifact.stat().st_size,
                    artifact_path=str(artifact),
                    quality=quality,
                    coverage=coverage,
                    powerpoint_qa=None,
                )
                assert completed.state == "succeeded"
                assert finalized["coverage"]["cjkCoverageProfileId"] == (
                    "ja-ko-zh-bounded-fixture-v1"
                )
                assert finalized["coverage"]["nativeTextPolicy"] == coverage[
                    "nativeTextPolicy"
                ]
                receipt_binding = completed.provenance["quality_receipt_binding"]
                assert receipt_binding["receipt_digest"] == generation_receipt.receipt_digest
                assert receipt_binding["generation_attempt_id"] == generation_receipt.attempt_id
                assert receipt_binding["release_profile"] == "release"
                assert receipt_binding["pre_export_evaluation_profile"] == "default"
                assert receipt_binding["scene_envelope_digest"] == scene_receipt[
                    "sceneDigest"
                ]
                assert receipt_binding["semantic_emission_digest"] == (
                    "sha256:" + canonical
                )
                assert receipt_binding["qid_owner_census_digest"].startswith("sha256:")
                assert receipt_binding["atomic_run_digest"].startswith("sha256:")
                assert receipt_binding["export_binding"]["pptx_sha256"] == pptx_sha256
                unified = finalized["unifiedReleaseReceipt"]
                assert unified["artifactSucceeded"] is True
                assert unified["releaseReady"] is False
                assert unified["visualParityStatus"] == "not-verified"
                assert "visual-parity-not-passed" in unified["blockers"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_hybrid_atomic_binding_rejects_cross_presentation_sidecar() -> None:
    expected = {
        "presentationId": str(uuid.uuid4()),
        "presentationRevision": 4,
        "canonicalSourceDigest": "a" * 64,
        "rendererSourceDigest": "b" * 64,
        "pptxSha256": "c" * 64,
    }
    export_attempt_id = uuid.uuid4()
    assert _validate_hybrid_atomic_binding(
        expected,
        expected,
        export_attempt_id=export_attempt_id,
    ).export_attempt_id == export_attempt_id
    wrong_presentation = {**expected, "presentationId": str(uuid.uuid4())}

    with pytest.raises(
        PresentationExportContractError,
        match="presentation_export_atomic_binding_mismatch",
    ):
        _validate_hybrid_atomic_binding(
            wrong_presentation,
            expected,
            export_attempt_id=export_attempt_id,
        )


def test_fidelity_begin_persists_renderer_binding_without_hybrid_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "fidelity-attempt.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            async with maker() as session:
                attempt, evidence = await begin_direct_fidelity_export_attempt(
                    session,
                    presentation_id=presentation_id,
                    expected_revision=4,
                    renderer_source_sha256="b" * 64,
                    idempotency_key="fidelity-test",
                )
            assert attempt.provenance["capability_evidence"]["export_mode"] == "fidelity"
            assert attempt.provenance["freshness"]["renderer_source_sha256"] == "b" * 64
            assert evidence["canonical_source_digest"] == attempt.source_snapshot_sha256
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def _seed_attempt_presentation(maker) -> uuid.UUID:
    async with maker() as session, session.begin():
        presentation = _presentation()
        session.add(presentation)
        await session.flush()
        return presentation.id


async def _begin(
    maker,
    presentation_id: uuid.UUID,
    *,
    request_hash: str = "d" * 64,
    idempotency_key_hash: str = "e" * 64,
) -> PresentationExportAttempt:
    async with maker() as session:
        return await begin_export_attempt(
            session,
            presentation_id=presentation_id,
            owner_id=None,
            owner_scope="anonymous",
            workspace_operation_id=None,
            export_format="pptx",
            source_revision=4,
            source_snapshot_sha256="c" * 64,
            request_hash=request_hash,
            idempotency_key_hash=idempotency_key_hash,
            requested_adapter="auto",
            provenance=_provenance(),
        )


def test_concurrent_begin_reuses_one_identity_and_rejects_conflicting_payload(
    tmp_path: Path,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "attempt-race.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            first, second = await asyncio.gather(
                _begin(maker, presentation_id),
                _begin(maker, presentation_id),
            )
            assert first.id == second.id
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PresentationExportAttempt)
                ) == 1

            conflict = await asyncio.gather(
                _begin(maker, presentation_id),
                _begin(maker, presentation_id, request_hash="f" * 64),
                return_exceptions=True,
            )
            assert any(
                isinstance(result, PresentationExportContractError)
                and result.code == "presentation_export_idempotency_conflict"
                for result in conflict
            )
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PresentationExportAttempt)
                ) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_terminalization_is_owner_operation_scoped_and_asset_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "attempt-freshness.db")
        app_data = tmp_path / "app-data"
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            original = b"immutable-asset"
            digest = sha256(original).hexdigest()
            asset_id = uuid.uuid4()
            storage_key = f"anonymous/{asset_id}/{digest}.bin"
            asset_path = app_data / "workspace_assets" / storage_key
            asset_path.parent.mkdir(parents=True)
            asset_path.write_bytes(original)
            async with maker() as session, session.begin():
                session.add(
                    WorkspaceAsset(
                        id=asset_id,
                        state="ready",
                        original_name="asset.bin",
                        media_type="application/octet-stream",
                        size_bytes=len(original),
                        storage_key=storage_key,
                        sha256=digest,
                    )
                )
            attempt = await _begin(maker, presentation_id)
            provenance = _provenance(asset_digests={str(asset_id): digest})

            async with maker() as session:
                with pytest.raises(
                    PresentationExportContractError,
                    match="presentation_export_attempt_terminal_conflict",
                ):
                    await terminalize_export_attempt_succeeded(
                        session,
                        attempt_id=attempt.id,
                        presentation_id=presentation_id,
                        expected_owner_id=uuid.uuid4(),
                        expected_owner_scope="anonymous",
                        expected_workspace_operation_id=None,
                        source_revision=4,
                        source_snapshot_sha256="c" * 64,
                        selected_adapter="general",
                        artifact_sha256="a" * 64,
                        artifact_size_bytes=5,
                        provenance=provenance,
                    )
                await session.rollback()

            async with maker() as session:
                with pytest.raises(
                    PresentationExportContractError,
                    match="presentation_export_attempt_terminal_conflict",
                ):
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=attempt.id,
                        expected_owner_id=None,
                        expected_owner_scope="anonymous",
                        expected_workspace_operation_id=uuid.uuid4(),
                        failure_code="wrong_operation",
                        failure_path=None,
                    )

            asset_path.write_bytes(b"modified-asset!")
            async with maker() as session:
                with pytest.raises(
                    PresentationExportFreshnessError,
                    match="presentation_export_asset_digest_mismatch",
                ):
                    await terminalize_export_attempt_succeeded(
                        session,
                        attempt_id=attempt.id,
                        presentation_id=presentation_id,
                        expected_owner_id=None,
                        expected_owner_scope="anonymous",
                        expected_workspace_operation_id=None,
                        source_revision=4,
                        source_snapshot_sha256="c" * 64,
                        selected_adapter="general",
                        artifact_sha256="a" * 64,
                        artifact_size_bytes=5,
                        provenance=provenance,
                    )
                await session.rollback()
            async with maker() as session:
                stored = await session.get(PresentationExportAttempt, attempt.id)
                assert stored is not None and stored.state == "running"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_standalone_terminalization_rejects_concurrently_overwritten_artifact(
    tmp_path: Path,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "standalone-toctou.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            attempt = await _begin(maker, presentation_id)
            artifact_path = tmp_path / "standalone.pptx"
            original = b"verified standalone bytes"
            artifact_path.write_bytes(original)
            expected_digest = sha256(original).hexdigest()
            execution = PresentationExportExecution(
                attempt_id=attempt.id,
                attempt_presentation_id=presentation_id,
                owner_id=None,
                owner_scope="anonymous",
                workspace_operation_id=None,
                source_revision=4,
                source_snapshot_sha256="c" * 64,
                html_source_sha256="d" * 64,
                composition_sha256="e" * 64,
                asset_manifest_sha256="f" * 64,
                route=PresentationExportRouteResult(
                    result=PresentationExportAdapterResult(
                        presentation=PresentationAndPath(
                            presentation_id=presentation_id,
                            path=str(artifact_path),
                        ),
                        selected_adapter="general",
                        artifact_sha256=expected_digest,
                        artifact_size_bytes=len(original),
                        compiler="presentation-export",
                        compiler_version="0.4.3",
                        runtime="chromium-dom-export",
                        runtime_version="presenton.render-artifact/v1",
                        package_sha256=None,
                    ),
                    adapter_chain=("general",),
                    fallback_reason=None,
                    capability_evidence={"selected_adapter": "general"},
                    asset_digests={},
                    asset_digests_verified=True,
                ),
            )

            artifact_path.write_bytes(b"concurrently overwritten")
            async with maker() as session:
                with pytest.raises(
                    PresentationExportFreshnessError,
                    match="presentation_export_artifact_digest_mismatch",
                ):
                    await terminalize_standalone_presentation_export(
                        session,
                        execution,
                    )

            async with maker() as session:
                stored = await session.get(PresentationExportAttempt, attempt.id)
                assert stored is not None
                assert stored.state == "failed"
                assert (
                    stored.failure_code
                    == "presentation_export_artifact_digest_mismatch"
                )
                assert stored.artifact_sha256 is None
                assert stored.artifact_size_bytes is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_hybrid_history_derives_resolved_failed_attempt_without_deleting_it(
    tmp_path: Path,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "hybrid-history.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            failed = await _begin(maker, presentation_id, request_hash="1" * 64)
            failed_provenance = dict(failed.provenance)
            failed_provenance["capability_evidence"] = {
                **failed_provenance["capability_evidence"],
                "export_mode": "hybrid",
            }
            async with maker() as session:
                await terminalize_export_attempt_failed(
                    session,
                    attempt_id=failed.id,
                    expected_owner_id=None,
                    expected_owner_scope="anonymous",
                    expected_workspace_operation_id=None,
                    failure_code="stale_presentation_revision",
                    failure_path=None,
                    provenance=failed_provenance,
                )

            succeeded = await _begin(
                maker,
                presentation_id,
                request_hash="2" * 64,
                idempotency_key_hash="f" * 64,
            )
            succeeded_provenance = dict(succeeded.provenance)
            succeeded_provenance["capability_evidence"] = {
                **succeeded_provenance["capability_evidence"],
                "export_mode": "hybrid",
            }
            async with maker() as session, session.begin():
                await terminalize_export_attempt_succeeded(
                    session,
                    attempt_id=succeeded.id,
                    presentation_id=presentation_id,
                    expected_owner_id=None,
                    expected_owner_scope="anonymous",
                    expected_workspace_operation_id=None,
                    source_revision=4,
                    source_snapshot_sha256="c" * 64,
                    selected_adapter="general",
                    artifact_sha256="a" * 64,
                    artifact_size_bytes=10,
                    provenance=succeeded_provenance,
                )

            async with maker() as session:
                history = await list_direct_hybrid_export_attempt_history(
                    session,
                    presentation_id=presentation_id,
                )
            by_id = {
                item["exportAttemptId"]: item for item in history["attempts"]
            }
            assert by_id[str(failed.id)]["resolution"] == "resolved"
            assert by_id[str(failed.id)]["supersededByAttemptId"] == str(
                succeeded.id
            )
            assert by_id[str(succeeded.id)]["resolution"] == "active"
            assert history["retention"] == {
                "attemptRetentionDays": 180,
                "evidenceRetentionDays": 30,
                "expiryAction": "mark_expired_retain_metadata",
                "deletionAuthorized": False,
            }
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(PresentationExportAttempt)
                ) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())
