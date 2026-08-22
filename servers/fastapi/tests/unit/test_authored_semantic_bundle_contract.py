from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    AssetManifestV1,
    AuthoredEmissionBundleV1,
    AuthoredSemanticSourceV1,
    ElementIdentityRegistryV1,
    FontManifestV1,
    ShapeElementV1,
    ShapePayloadV1,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    bytes_digest,
    canonical_json,
    seal_asset_manifest,
    seal_font_manifest,
    seal_identity_registry,
    validate_and_seal_candidate,
)
from services.authored_vnext.semantic.publication import (
    publish_authored_semantic_bundle_graph,
)
from services.authored_vnext.semantic.query import (
    get_active_authored_emission,
    get_workbench_capability_projection,
)


def _digest(seed: str) -> str:
    return bytes_digest(seed.encode())


def _pointer(content: bytes, media_type: str) -> ArtifactPointerV1:
    digest = bytes_digest(content)
    return ArtifactPointerV1(
        artifact_ref=f"artifact://sha256/{digest.removeprefix('sha256:')}",
        digest=digest,
        byte_length=len(content),
        media_type=media_type,
    )


def test_declared_bundle_rejects_qid_mismatch_without_rewrite() -> None:
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    html = b'<div data-qid="shape.one"></div>'
    registry = seal_identity_registry(
        ElementIdentityRegistryV1(active_qids=["shape.two"], retired_qids=[])
    )
    source = AuthoredSemanticSourceV1(
        presentation_id=presentation_id,
        slide_id=slide_id,
        slide_revision=1,
        operation_id=operation_id,
        authored_html_digest=bytes_digest(html),
        elements=[
            ShapeElementV1(
                kind="shape",
                element_id="shape.two",
                semantic_role="decoration",
                intended_order=0,
                payload=ShapePayloadV1(preset="rectangle", fill="#001122"),
            )
        ],
        identity_registry=registry,
    )
    asset_manifest = seal_asset_manifest(AssetManifestV1())
    font_manifest = seal_font_manifest(FontManifestV1())
    asset_json = canonical_json(asset_manifest).encode()
    font_json = canonical_json(font_manifest).encode()
    bundle = AuthoredEmissionBundleV1(
        presentation_id=presentation_id,
        slide_id=slide_id,
        slide_revision=1,
        operation_id=operation_id,
        authored_html=_pointer(html, "text/html;charset=utf-8"),
        semantic_component=source,
        asset_manifest=_pointer(asset_json, "application/json"),
        font_manifest=_pointer(font_json, "application/json"),
        master_contract_digest=_digest("master"),
        composition_digest=_digest("composition"),
        element_identity_registry_digest=registry.digest,
        quality_policy_version="authored-quality/v1",
    )
    with pytest.raises(SemanticContractError, match="authored_semantic_qid_mismatch"):
        validate_and_seal_candidate(html, bundle)


def test_atomic_degraded_graph_publish_and_legacy_compatible_read() -> None:
    asyncio.run(_exercise_atomic_degraded_graph_publish())


async def _exercise_atomic_degraded_graph_publish() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        PresentationModel.__table__,
        AuthoredSemanticArtifact.__table__,
        AuthoredEmissionBundleRecord.__table__,
        AuthoredDeckEmissionManifestRecord.__table__,
        AuthoredDeckEmissionActivePointer.__table__,
    ]
    async with engine.begin() as connection:
        for table in tables:
            await connection.run_sync(table.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    async with sessions() as session:
        session.add(
            PresentationModel(
                id=presentation_id,
                owner_id=owner_id,
                content="semantic contract",
                n_slides=1,
                language="ko",
                mode="authored",
            )
        )
        await session.commit()
        legacy = await get_active_authored_emission(
            session, presentation_id=presentation_id, owner_id=owner_id
        )
        assert legacy.canonical_mode == "legacyHtml"
        assert legacy.manifest is None

        slide = SlideModel(
            id=uuid.uuid4(),
            owner_id=owner_id,
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:content",
            index=0,
            content={"__authored__": True},
            html_content='<div data-qid="shape.one"></div>',
        )
        operation_id = uuid.uuid4()
        manifest = await publish_authored_semantic_bundle_graph(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            expected_presentation_revision=0,
            operation_id=operation_id,
            generation_attempt_id=uuid.uuid4(),
            slides=[slide],
            htmls=[slide.html_content or ""],
            semantic_drafts=[
                {
                    "semanticUnavailableReceipt": {
                        "htmlDigest": bytes_digest((slide.html_content or "").encode()),
                        "failureCode": "authored_semantic_retry_exhausted",
                    }
                }
            ],
            master_contract_digest=_digest("master"),
            composition_digest=_digest("composition"),
        )
        await session.commit()
        assert manifest.presentation_revision == 1
        active = await get_active_authored_emission(
            session, presentation_id=presentation_id, owner_id=owner_id
        )
        assert active.canonical_mode == "bundleGraph"
        assert active.canonical_digest == manifest.canonical_digest
        assert len(active.bundles) == 1
        projection = await get_workbench_capability_projection(
            session, presentation_id=presentation_id, owner_id=owner_id
        )
        assert projection.degraded_fidelity_only is True
        assert projection.fidelity.state == "ready"
        assert projection.hybrid.text.state == "disabled"
        assert projection.readiness.promotion_ready is False

        declared_presentation_id = uuid.uuid4()
        session.add(
            PresentationModel(
                id=declared_presentation_id,
                owner_id=owner_id,
                content="declared shape",
                n_slides=1,
                language="ko",
                mode="authored",
            )
        )
        await session.commit()
        declared_slide = SlideModel(
            id=uuid.uuid4(),
            owner_id=owner_id,
            presentation=declared_presentation_id,
            layout_group="authored",
            layout="authored:content",
            index=0,
            content={"__authored__": True},
            html_content='<div data-qid="shape.one"></div>',
        )
        declared_html_digest = bytes_digest((declared_slide.html_content or "").encode())
        await publish_authored_semantic_bundle_graph(
            session,
            presentation_id=declared_presentation_id,
            owner_id=owner_id,
            expected_presentation_revision=0,
            operation_id=uuid.uuid4(),
            generation_attempt_id=uuid.uuid4(),
            slides=[declared_slide],
            htmls=[declared_slide.html_content or ""],
            semantic_drafts=[
                {
                    "semanticSource": {
                        "htmlDigest": declared_html_digest,
                        "elements": [
                            {
                                "kind": "shape",
                                "elementId": "shape.one",
                                "semanticRole": "decoration",
                                "childIds": [],
                                "intendedOrder": 0,
                                "requiredFeatures": [],
                                "payload": {
                                    "preset": "rectangle",
                                    "fill": "#001122",
                                },
                            }
                        ],
                        "elementIdentityRegistry": {
                            "schemaVersion": "authored-element-identity-registry/v1",
                            "activeQids": ["shape.one"],
                            "retiredQids": [],
                        },
                    }
                }
            ],
            master_contract_digest=_digest("master"),
            composition_digest=_digest("composition"),
        )
        await session.commit()
        declared_projection = await get_workbench_capability_projection(
            session, presentation_id=declared_presentation_id, owner_id=owner_id
        )
        assert declared_projection.hybrid.shape.state == "ready"
        assert declared_projection.hybrid.text.state == "shadow"
        assert declared_projection.elements[0].editable_fields == ["fill", "stroke"]
        assert declared_projection.readiness.promotion_ready is False
    await engine.dispose()
