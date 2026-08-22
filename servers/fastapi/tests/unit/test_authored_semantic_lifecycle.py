from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.authored_vnext.semantic.lifecycle import (
    AuthoredLegacyCutoverRequestV1,
    AuthoredManifestRollbackRequestV1,
    AuthoredOrderedRemakeRequestV1,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_semantic_candidate import compile_semantic_components
from services.authored_vnext.semantic.canonical import SemanticContractError, bytes_digest
from services.authored_vnext.semantic.lifecycle import (
    cutover_authored_emission_to_legacy,
    remake_ordered_authored_emission,
    rollback_authored_emission_manifest,
)
from services.authored_vnext.semantic.publication import (
    publish_authored_semantic_bundle_graph,
)
from services.authored_vnext.semantic.query import get_active_authored_emission


def _digest(seed: str) -> str:
    return bytes_digest(seed.encode())


def test_ordered_remake_stale_attempt_rollback_and_legacy_cutover() -> None:
    asyncio.run(_exercise_lifecycle())


def test_lifecycle_http_contract_accepts_uuid_strings() -> None:
    operation_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    request = AuthoredOrderedRemakeRequestV1.model_validate_json(
        "{"
        '"schemaVersion":"authored-ordered-remake-request/v1",'
        '"expectedPresentationRevision":1,'
        '"expectedActiveGeneration":1,'
        f'"expectedManifestDigest":"{_digest("manifest")}",'
        f'"operationId":"{operation_id}",'
        f'"generationAttemptId":"{attempt_id}",'
        '"semanticRetryLimit":2}'
    )
    assert request.operation_id == operation_id
    assert request.generation_attempt_id == attempt_id


async def _exercise_lifecycle() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        PresentationModel.__table__,
        SlideModel.__table__,
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
    initial_htmls = [
        '<div class="simple-shape" role="presentation" data-qid="shape.one"></div>',
        '<div class="simple-shape" role="presentation" data-qid="shape.two"></div>',
    ]
    async with sessions() as session:
        presentation = PresentationModel(
            id=presentation_id,
            owner_id=owner_id,
            content="semantic lifecycle",
            n_slides=2,
            language="ko",
            mode="authored",
        )
        slides = [
            SlideModel(
                id=uuid.uuid4(),
                owner_id=owner_id,
                presentation=presentation_id,
                layout_group="authored",
                layout="authored:content",
                index=index,
                content={"__authored__": True},
                html_content=html,
            )
            for index, html in enumerate(initial_htmls)
        ]
        session.add(presentation)
        session.add_all(slides)
        await session.flush()
        first = await publish_authored_semantic_bundle_graph(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            expected_presentation_revision=0,
            operation_id=uuid.uuid4(),
            generation_attempt_id=uuid.uuid4(),
            slides=slides,
            htmls=initial_htmls,
            semantic_drafts=compile_semantic_components(initial_htmls),
            master_contract_digest=_digest("master"),
            composition_digest=_digest("composition"),
        )
        presentation.revision = 1
        slides[0].html_content = initial_htmls[0].replace("</div>", "changed</div>")
        session.add(presentation)
        session.add(slides[0])
        await session.commit()

        remake_attempt = uuid.uuid4()
        remake = await remake_ordered_authored_emission(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            request=AuthoredOrderedRemakeRequestV1(
                expected_presentation_revision=1,
                expected_active_generation=1,
                expected_manifest_digest=first.canonical_digest,
                operation_id=uuid.uuid4(),
                generation_attempt_id=remake_attempt,
            ),
        )
        await session.commit()
        assert remake.presentation_revision == 2
        assert remake.active_generation == 2
        assert remake.ordered_slide_count == 2

        with pytest.raises(
            SemanticContractError, match="authored_semantic_lifecycle_stale_revision"
        ):
            await remake_ordered_authored_emission(
                session,
                presentation_id=presentation_id,
                owner_id=owner_id,
                request=AuthoredOrderedRemakeRequestV1(
                    expected_presentation_revision=1,
                    expected_active_generation=1,
                    expected_manifest_digest=first.canonical_digest,
                    operation_id=uuid.uuid4(),
                    generation_attempt_id=uuid.uuid4(),
                ),
            )
        await session.rollback()

        with pytest.raises(
            SemanticContractError, match="authored_semantic_lifecycle_attempt_reused"
        ):
            await rollback_authored_emission_manifest(
                session,
                presentation_id=presentation_id,
                owner_id=owner_id,
                request=AuthoredManifestRollbackRequestV1(
                    expected_presentation_revision=2,
                    expected_active_generation=2,
                    expected_manifest_digest=remake.active_manifest_digest,
                    target_manifest_digest=first.canonical_digest,
                    operation_id=uuid.uuid4(),
                    generation_attempt_id=remake_attempt,
                ),
            )
        await session.rollback()

        rollback = await rollback_authored_emission_manifest(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            request=AuthoredManifestRollbackRequestV1(
                expected_presentation_revision=2,
                expected_active_generation=2,
                expected_manifest_digest=remake.active_manifest_digest,
                target_manifest_digest=first.canonical_digest,
                operation_id=uuid.uuid4(),
                generation_attempt_id=uuid.uuid4(),
            ),
        )
        await session.commit()
        assert rollback.presentation_revision == 3
        assert rollback.target_manifest_digest == first.canonical_digest
        await session.refresh(slides[0])
        assert slides[0].html_content == initial_htmls[0]

        legacy = await cutover_authored_emission_to_legacy(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            request=AuthoredLegacyCutoverRequestV1(
                expected_presentation_revision=3,
                expected_active_generation=3,
                expected_manifest_digest=rollback.active_manifest_digest,
                operation_id=uuid.uuid4(),
            ),
        )
        await session.commit()
        assert legacy.canonical_mode == "legacyHtml"
        active = await get_active_authored_emission(
            session, presentation_id=presentation_id, owner_id=owner_id
        )
        assert active.canonical_mode == "legacyHtml"
        assert active.active_generation == 4
        assert active.canonical_digest == legacy.active_manifest_digest
        assert active.bundles == []
    await engine.dispose()
