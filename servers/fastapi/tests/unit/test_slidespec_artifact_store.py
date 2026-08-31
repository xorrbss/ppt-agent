import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import JSON, null, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    LayoutNode,
    Provenance,
    RenderRequest,
    RendererManifest,
    SlideDigests,
    SlideSpec,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.slidespec_artifact_bundle import SlideSpecArtifactBundle
from services.slidespec_artifact_store import (
    ACTIVE_POINTER_KEY,
    SlideSpecArtifactError,
    _json_cas_predicate,
    active_pointer,
    build_slidespec_artifact_bundle,
    load_active_slidespec_bundle,
    publish_slidespec_bundle_cas,
    rollback_active_slide_cas,
)
from utils.slidespec_core import ZERO_DIGEST, seal_slidespec


def _render_request(slide_id: uuid.UUID) -> RenderRequest:
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId=str(slide_id),
            content=(
                ContentNode(
                    semanticId=f"sem:{slide_id}/title",
                    kind="title",
                    text="5개월 조기완료 전략",
                ),
            ),
            layout=LayoutNode(
                layoutId=f"lay:{slide_id}/root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId=f"lay:{slide_id}/title",
                        kind="content",
                        contentSemanticId=f"sem:{slide_id}/title",
                        bounds=Bounds(x=0.08, y=0.06, width=0.84, height=0.12),
                    ),
                ),
            ),
            provenance=Provenance(source="native"),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    empty = "sha256:" + "0" * 64
    return RenderRequest(
        spec=spec,
        renderer=RendererManifest(name="slidespec-html", version="1", runtime="python"),
        fonts=FontManifest(digest=empty),
        assets=AssetManifest(digest=empty),
    )


async def _artifact_bundle_publish_cas_and_readback_are_self_verifying(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifacts.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(
                sync,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    SlideSpecArtifactBundle.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    presentation_id = uuid.uuid4()
    slide_id = uuid.uuid4()
    async with sessions() as session:
        session.add(
            PresentationModel(
                id=presentation_id,
                content="artifact test",
                n_slides=1,
                language="ko",
                mode="authored",
            )
        )
        slide = SlideModel(
            id=slide_id,
            presentation=presentation_id,
            layout_group="authored",
            layout="authored",
            index=10,
            content={"blocks": []},
            html_content="<section>legacy</section>",
            properties={"keep": "me"},
        )
        session.add(slide)
        await session.flush()
        request = _render_request(slide_id)
        bundle = build_slidespec_artifact_bundle(
            presentation_id=presentation_id,
            slide=slide,
            route="slidespec_active",
            status="imported",
            render_request=request,
            evidence={"fixture": "synthetic-page-11"},
        )

        # Compile the real publisher's PostgreSQL branch without contacting a
        # database: neither JSON snapshot predicate may use JSON = JSON.
        pg_statements = []

        async def pg_execute(statement):
            pg_statements.append(statement)
            if statement.is_update:
                return SimpleNamespace(rowcount=1)
            return SimpleNamespace(
                one_or_none=lambda: SimpleNamespace(
                    html_content=bundle.html,
                    properties={**slide.properties, ACTIVE_POINTER_KEY: active_pointer(bundle)},
                )
            )

        pg_session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
            add=lambda _row: None,
            execute=pg_execute,
            flush=AsyncMock(),
        )
        await publish_slidespec_bundle_cas(pg_session, slide=slide, bundle=bundle)
        pg_publish_sql = str(pg_statements[0].compile(dialect=postgresql.dialect()))
        assert "slides.id =" in pg_publish_sql
        assert "slides.html_content =" in pg_publish_sql
        assert "CAST(slides.content AS JSONB) =" in pg_publish_sql
        assert "CAST(slides.properties AS JSONB) =" in pg_publish_sql
        assert await rollback_active_slide_cas(
            pg_session,
            slide_id=slide_id,
            expected_html=bundle.html,
            expected_properties=slide.properties,
            restore_html=slide.html_content,
            restore_properties=slide.properties,
        )
        pg_rollback_sql = str(pg_statements[-1].compile(dialect=postgresql.dialect()))
        assert "slides.id =" in pg_rollback_sql
        assert "slides.html_content =" in pg_rollback_sql
        assert "CAST(slides.properties AS JSONB) =" in pg_rollback_sql
        sql_null = _json_cas_predicate(pg_session, SlideModel.properties, None)
        json_null = _json_cas_predicate(pg_session, SlideModel.properties, JSON.NULL)
        assert str(sql_null.compile(dialect=postgresql.dialect())) == "slides.properties IS NULL"
        compiled_json_null = json_null.compile(dialect=postgresql.dialect())
        assert "IS NULL" not in str(compiled_json_null)
        assert "CAST(slides.properties AS JSONB) =" in str(compiled_json_null)
        assert JSON.NULL in compiled_json_null.params.values()

        await publish_slidespec_bundle_cas(session, slide=slide, bundle=bundle)
        await session.commit()

        # Append-only evidence may record the same canonical candidate in a
        # different rollout outcome.  Outcome identity, not only the spec hash,
        # is the idempotency boundary (for example import -> repair shadow).
        shadow = build_slidespec_artifact_bundle(
            presentation_id=presentation_id,
            slide=slide,
            route="repair_shadow",
            status="shadow",
            render_request=request,
            evidence={"fixture": "same-candidate-shadow"},
        )
        session.add(shadow)
        await session.commit()

    async with sessions() as session:
        slide = (await session.execute(select(SlideModel))).scalar_one()
        bundles = list(
            (await session.execute(select(SlideSpecArtifactBundle))).scalars()
        )
        assert {(item.status, item.route) for item in bundles} == {
            ("imported", "slidespec_active"),
            ("shadow", "repair_shadow"),
        }
        loaded = await load_active_slidespec_bundle(session, slide)
        assert loaded is not None
        assert loaded.id == bundle.id
        assert loaded.spec == request.spec.model_dump(mode="json", by_alias=True)
        assert slide.html_content == bundle.html
        assert slide.properties["keep"] == "me"
        assert slide.properties[ACTIVE_POINTER_KEY]["spec_hash"] == request.spec.digests.spec

        slide.properties = {
            **slide.properties,
            ACTIVE_POINTER_KEY: {
                **slide.properties[ACTIVE_POINTER_KEY],
                "html_hash": "sha256:" + "f" * 64,
            },
        }
        with pytest.raises(SlideSpecArtifactError, match="artifact.pointer.hash_mismatch"):
            await load_active_slidespec_bundle(session, slide)

    # A concurrent content or pointer change must still reject publication and
    # roll back the uncommitted candidate bundle, leaving the winner untouched.
    for field in ("content", "properties"):
        async with sessions() as session:
            stale_slide = await session.get(SlideModel, slide_id)
            rejected = build_slidespec_artifact_bundle(
                presentation_id=presentation_id,
                slide=stale_slide,
                route="slidespec_active",
                status="imported",
                render_request=request.model_copy(
                    update={
                        "spec": seal_slidespec(
                            request.spec.model_copy(update={"specRevision": 2})
                        )
                    }
                ),
            )
            rejected_id = rejected.id
            winner = {"concurrent": field}
            await session.execute(
                update(SlideModel).where(SlideModel.id == slide_id)
                .values(**{field: winner})
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            with pytest.raises(SlideSpecArtifactError, match="artifact.publish.cas_conflict"):
                await publish_slidespec_bundle_cas(
                    session, slide=stale_slide, bundle=rejected
                )
        async with sessions() as session:
            assert await session.get(SlideSpecArtifactBundle, rejected_id) is None
            stored = await session.get(SlideModel, slide_id)
            assert getattr(stored, field) == winner

    async with sessions() as session:
        stored = await session.get(SlideModel, slide_id)
        expected_html = stored.html_content
        expected_properties = deepcopy(stored.properties)
        assert not await rollback_active_slide_cas(
            session, slide_id=slide_id, expected_html=expected_html,
            expected_properties={"wrong": "snapshot"},
            restore_html="<section>must not win</section>", restore_properties={},
        )
        assert await rollback_active_slide_cas(
            session, slide_id=slide_id, expected_html=expected_html,
            expected_properties=expected_properties,
            restore_html=expected_html, restore_properties={"restored": True},
        )
        await session.commit()

        for stored_null, should_match in ((null(), True), (JSON.NULL, False)):
            await session.execute(
                update(SlideModel).where(SlideModel.id == slide_id)
                .values(properties=stored_null)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            assert await rollback_active_slide_cas(
                session, slide_id=slide_id, expected_html=expected_html,
                expected_properties=None,
                restore_html=expected_html, restore_properties={"restored": True},
            ) is should_match
            await session.commit()

    await engine.dispose()


def test_artifact_bundle_publish_cas_and_readback_are_self_verifying(tmp_path) -> None:
    asyncio.run(_artifact_bundle_publish_cas_and_readback_are_self_verifying(tmp_path))
