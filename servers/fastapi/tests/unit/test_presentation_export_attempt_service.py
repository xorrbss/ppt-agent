from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
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
    terminalize_export_attempt_failed_if_current_workspace_claim,
    terminalize_export_attempt_succeeded,
)
from services.presentation_export_direct_attempt_history import (
    list_direct_hybrid_export_attempt_history,
)
import services.presentation_export_powerpoint_qa as powerpoint_qa_service
from services.presentation_export_capabilities import (
    PresentationExportContractError,
    PresentationExportFreshnessError,
    analyze_presentation_export_capabilities,
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


def test_fenced_failure_terminalization_does_not_mutate_reclaimed_svg_attempt(
    tmp_path: Path,
) -> None:
    async def scenario():
        engine, maker = await _database(tmp_path / "svg-fenced-terminalization.db")
        try:
            presentation_id = await _seed_attempt_presentation(maker)
            async with maker() as session, session.begin():
                conversation = WorkspaceConversation(presentation_id=presentation_id)
                session.add(conversation)
                await session.flush()
                operation = WorkspaceOperation(
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="export.pptx",
                    state="running",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={},
                    expected_revision=4,
                    attempt_number=1,
                    attempt_token="svg-attempt-1",
                    fencing_token=7,
                )
                session.add(operation)
                await session.flush()
                operation_id = operation.id

            async with maker() as session:
                attempt = await begin_export_attempt(
                    session,
                    presentation_id=presentation_id,
                    owner_id=None,
                    owner_scope="anonymous",
                    workspace_operation_id=operation_id,
                    export_format="pptx",
                    source_revision=4,
                    source_snapshot_sha256="c" * 64,
                    request_hash="b" * 64,
                    idempotency_key_hash="a" * 64,
                    requested_adapter="general",
                    provenance=_provenance(),
                )

            async with maker() as session:
                assert await terminalize_export_attempt_failed_if_current_workspace_claim(
                    session,
                    attempt_id=attempt.id,
                    expected_owner_id=None,
                    expected_owner_scope="anonymous",
                    expected_workspace_operation_id=operation_id,
                    expected_attempt_token="svg-attempt-1",
                    expected_fencing_token=7,
                    failure_code="presentation_export_cancelled",
                    failure_path=None,
                    selected_adapter="general",
                )
            async with maker() as session:
                failed = await session.get(PresentationExportAttempt, attempt.id)
                assert failed is not None and failed.state == "failed"

            async with maker() as session:
                resumed = await begin_export_attempt(
                    session,
                    presentation_id=presentation_id,
                    owner_id=None,
                    owner_scope="anonymous",
                    workspace_operation_id=operation_id,
                    export_format="pptx",
                    source_revision=4,
                    source_snapshot_sha256="c" * 64,
                    request_hash="b" * 64,
                    idempotency_key_hash="a" * 64,
                    requested_adapter="general",
                    provenance=_provenance(),
                )
                assert resumed.id == attempt.id

            async with maker() as session, session.begin():
                operation = await session.get(WorkspaceOperation, operation_id)
                assert operation is not None
                operation.attempt_token = "svg-attempt-2"
                operation.fencing_token = 8

            async with maker() as session:
                assert not await terminalize_export_attempt_failed_if_current_workspace_claim(
                    session,
                    attempt_id=attempt.id,
                    expected_owner_id=None,
                    expected_owner_scope="anonymous",
                    expected_workspace_operation_id=operation_id,
                    expected_attempt_token="svg-attempt-1",
                    expected_fencing_token=7,
                    failure_code="operation_lease_lost",
                    failure_path=None,
                    selected_adapter="general",
                )
            async with maker() as session:
                current = await session.get(PresentationExportAttempt, attempt.id)
                assert current is not None and current.state == "running"

            async with maker() as session, session.begin():
                await terminalize_export_attempt_succeeded(
                    session,
                    attempt_id=attempt.id,
                    presentation_id=presentation_id,
                    expected_owner_id=None,
                    expected_owner_scope="anonymous",
                    expected_workspace_operation_id=operation_id,
                    source_revision=4,
                    source_snapshot_sha256="c" * 64,
                    selected_adapter="general",
                    artifact_sha256="d" * 64,
                    artifact_size_bytes=5,
                    provenance=_provenance(),
                )
            async with maker() as session:
                assert not await terminalize_export_attempt_failed_if_current_workspace_claim(
                    session,
                    attempt_id=attempt.id,
                    expected_owner_id=None,
                    expected_owner_scope="anonymous",
                    expected_workspace_operation_id=operation_id,
                    expected_attempt_token="svg-attempt-1",
                    expected_fencing_token=7,
                    failure_code="operation_lease_lost",
                    failure_path=None,
                    selected_adapter="general",
                )
            async with maker() as session:
                succeeded = await session.get(PresentationExportAttempt, attempt.id)
                assert succeeded is not None and succeeded.state == "succeeded"
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
