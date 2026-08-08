import asyncio
import uuid

import pytest
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
    build_slidespec_artifact_bundle,
    load_active_slidespec_bundle,
    publish_slidespec_bundle_cas,
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

    await engine.dispose()


def test_artifact_bundle_publish_cas_and_readback_are_self_verifying(tmp_path) -> None:
    asyncio.run(_artifact_bundle_publish_cas_and_readback_are_self_verifying(tmp_path))
