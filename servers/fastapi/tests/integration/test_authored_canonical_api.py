"""Focused product-contract coverage for canonical Authored persistence."""

from __future__ import annotations

import asyncio
import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from api.v1.ppt.endpoints.authored_operations import AUTHORED_OPERATIONS_ROUTER
from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    ExportAttemptDigestBindingV1,
    FontManifestV1,
    ResolvedFontV1,
)
from models.sql.authored_canonical_revision import AuthoredCanonicalRevision
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.slide import SlideModel
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
    AuthoredSemanticExportBinding,
)
from services import database
from services.authored_vnext.semantic.canonical import (
    bytes_digest,
    canonical_digest,
)
from services.authored_vnext.semantic.export_binding import bind_existing_export_attempt
from services.authored_vnext.semantic.publication import (
    publish_authored_semantic_bundle_graph,
)
from services.authored_vnext.semantic.query import (
    get_active_authored_emission,
    get_workbench_capability_projection,
)


def test_canonical_save_cas_rebase_undo_redo_and_sqlite_restart_replay(
    tmp_path,
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "authored-canonical.db"
        database_url = f"sqlite+aiosqlite:///{database_path}"
        presentation_id = uuid.uuid4()
        title_slide_id = uuid.uuid4()
        table_slide_id = uuid.uuid4()

        async def build_runtime():
            engine = create_async_engine(database_url)
            maker = async_sessionmaker(engine, expire_on_commit=False)

            async def session_override():
                async with maker() as session:
                    yield session

            app = FastAPI()
            app.dependency_overrides[database.get_async_session] = session_override
            app.include_router(AUTHORED_OPERATIONS_ROUTER, prefix="/api/v1/ppt")
            return engine, maker, app

        engine, maker, app = await build_runtime()
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    AuthoredCanonicalRevision.__table__,
                ],
            )
        async with maker() as session:
            session.add(
                PresentationModel(
                    id=presentation_id,
                    content="Canonical Authored test",
                    n_slides=2,
                    language="ko",
                    title="Canonical",
                    mode="authored",
                    revision=0,
                )
            )
            session.add_all(
                [
                    SlideModel(
                        id=title_slide_id,
                        presentation=presentation_id,
                        layout_group="authored",
                        layout="authored:default",
                        index=0,
                        content={"__authored__": True},
                        html_content=(
                            "<!doctype html><html><head><style>.hero{color:#111}</style></head>"
                            "<body><main data-qid='slide-root'>"
                            "<h1 data-qid='title'>Old title</h1>"
                            "<div data-qid='complex' style='filter: blur(2px)'>"
                            "<span data-qid='complex-text'>Complex</span></div>"
                            "<img data-qid='hero' src='https://assets.example/old.png'>"
                            "</main></body></html>"
                        ),
                    ),
                    SlideModel(
                        id=table_slide_id,
                        presentation=presentation_id,
                        layout_group="authored",
                        layout="authored:default",
                        index=1,
                        content={"__authored__": True},
                        html_content=(
                            "<!doctype html><html><body>"
                            "<table data-qid='table'><tr data-qid='row'>"
                            "<td data-qid='cell'>10</td></tr></table>"
                            "</body></html>"
                        ),
                    ),
                ]
            )
            await session.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            base_path = f"/api/v1/ppt/presentation/{presentation_id}/canonical"
            initial_response = await client.get(base_path)
            assert initial_response.status_code == 200, initial_response.text
            initial = initial_response.json()
            assert initial["revision"] == 0
            assert initial["sourceDigest"].startswith("sha256:")
            assert initial["slides"][0]["assetManifest"][0]["qid"] == "hero"
            assert initial["slides"][0]["assetManifest"][0]["digestKind"] == "locator"
            initial_capabilities = {
                item["qid"]: item for item in initial["slides"][0]["capabilities"]
            }
            assert initial_capabilities["title"]["editable"] is True
            assert initial_capabilities["complex"]["editable"] is False
            assert initial_capabilities["complex"]["reason"] == "unsupported_css"
            table_capabilities = {
                item["qid"]: item for item in initial["slides"][1]["capabilities"]
            }
            assert table_capabilities["table"]["editable"] is False
            assert table_capabilities["cell"]["fields"] == ["text", "style"]

            first_save_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "expectedDigest": initial["sourceDigest"],
                    "patches": [
                        {
                            "slideId": str(title_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "title", "text": "New title"},
                                {
                                    "action": "replaceImage",
                                    "qid": "hero",
                                    "src": "/app_data/images/users/owner/new.png",
                                    "alt": "Replacement",
                                },
                            ],
                        }
                    ],
                },
            )
            assert first_save_response.status_code == 200, first_save_response.text
            first_save = first_save_response.json()
            assert first_save["revision"] == 1
            assert first_save["slides"][0]["slideRevision"] == 1
            assert "New title" in first_save["slides"][0]["html"]
            assert first_save["slides"][0]["assetManifest"][0]["source"] == "/app_data/images/users/owner/new.png"

            identity_response = await client.get(base_path)
            assert identity_response.status_code == 200
            assert identity_response.json() == first_save

            stale_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "patches": [
                        {
                            "slideId": str(table_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "cell", "text": "11"}
                            ],
                        }
                    ],
                },
            )
            assert stale_response.status_code == 409
            assert stale_response.json()["code"] == "authored_canonical_revision_conflict"
            assert stale_response.json()["currentRevision"] == 1

            rebased_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "rebaseOnConflict": True,
                    "patches": [
                        {
                            "slideId": str(table_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "cell", "text": "11"}
                            ],
                        }
                    ],
                },
            )
            assert rebased_response.status_code == 200, rebased_response.text
            rebased = rebased_response.json()
            assert rebased["revision"] == 2
            assert rebased["rebasedFromRevision"] == 0
            assert rebased["slides"][1]["slideRevision"] == 1

            overlapping_rebase = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 0,
                    "rebaseOnConflict": True,
                    "patches": [
                        {
                            "slideId": str(title_slide_id),
                            "actions": [
                                {"action": "setText", "qid": "title", "text": "Conflict"}
                            ],
                        }
                    ],
                },
            )
            assert overlapping_rebase.status_code == 409
            assert overlapping_rebase.json()["code"] == "authored_canonical_rebase_conflict"

            undo_response = await client.post(
                f"{base_path}/undo",
                json={
                    "expectedRevision": 2,
                    "expectedDigest": rebased["sourceDigest"],
                },
            )
            assert undo_response.status_code == 200, undo_response.text
            undone = undo_response.json()
            assert undone["revision"] == 3
            assert ">10<" in undone["slides"][1]["html"]
            assert undone["history"]["canRedo"] is True

            redo_response = await client.post(
                f"{base_path}/redo",
                json={
                    "expectedRevision": 3,
                    "expectedDigest": undone["sourceDigest"],
                },
            )
            assert redo_response.status_code == 200, redo_response.text
            redone = redo_response.json()
            assert redone["revision"] == 4
            assert ">11<" in redone["slides"][1]["html"]
            assert redone["history"]["canRedo"] is False

            history_response = await client.get(f"{base_path}/history")
            assert history_response.status_code == 200, history_response.text
            assert [
                entry["operation"] for entry in history_response.json()["entries"]
            ] == ["initialize", "patch", "patch", "undo", "redo"]

            raw_html_response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 4,
                    "html": "<script>alert(1)</script>",
                    "patches": [],
                },
            )
            assert raw_html_response.status_code == 422

        await engine.dispose()

        restarted_engine, restarted_maker, restarted_app = await build_runtime()
        restarted_transport = httpx.ASGITransport(app=restarted_app)
        async with httpx.AsyncClient(
            transport=restarted_transport, base_url="http://test"
        ) as restarted_client:
            replay_response = await restarted_client.get(
                f"/api/v1/ppt/presentation/{presentation_id}/canonical"
            )
            assert replay_response.status_code == 200, replay_response.text
            assert replay_response.json() == redone
        async with restarted_maker() as session:
            revisions = (
                await session.execute(
                    select(AuthoredCanonicalRevision)
                    .where(AuthoredCanonicalRevision.presentation_id == presentation_id)
                    .order_by(AuthoredCanonicalRevision.revision)
                )
            ).scalars().all()
            slide = await session.get(SlideModel, table_slide_id)
            assert [row.revision for row in revisions] == [0, 1, 2, 3, 4]
            assert slide is not None and ">11<" in (slide.html_content or "")
        await restarted_engine.dispose()

    asyncio.run(scenario())


def test_canonical_patch_atomically_republishes_semantics_and_allows_export_binding(
    tmp_path,
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'canonical-semantic-republish.db'}"
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def session_override():
            async with maker() as session:
                yield session

        app = FastAPI()
        app.dependency_overrides[database.get_async_session] = session_override
        app.include_router(AUTHORED_OPERATIONS_ROUTER, prefix="/api/v1/ppt")
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    AuthoredCanonicalRevision.__table__,
                    PresentationExportAttempt.__table__,
                    AuthoredSemanticArtifact.__table__,
                    AuthoredEmissionBundleRecord.__table__,
                    AuthoredDeckEmissionManifestRecord.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    AuthoredSemanticExportBinding.__table__,
                ],
            )
        owner_id = uuid.uuid4()
        presentation_id = uuid.uuid4()
        slide_id = uuid.uuid4()
        generation_attempt_id = uuid.uuid4()
        initial_operation_id = uuid.uuid4()
        initial_html = "<!doctype html><html><body><p data-qid='headline'>Old native text</p></body></html>"
        font_bytes = b"canonical-semantic-test-font"
        font_digest = bytes_digest(font_bytes)
        font_manifest = FontManifestV1(
            fonts=[
                ResolvedFontV1(
                    face_id="font:arial-regular",
                    family="Arial",
                    face="Regular",
                    artifact=ArtifactPointerV1(
                        artifact_ref="artifact://sha256/" + font_digest.removeprefix("sha256:"),
                        digest=font_digest,
                        byte_length=len(font_bytes),
                        media_type="font/ttf",
                    ),
                    locator_digest="sha256:" + "7" * 64,
                    resolver_version="canonical-semantic-test/v1",
                    embedding_permitted=True,
                    subsetting_permitted=True,
                    retention_class="active",
                )
            ]
        )
        initial_semantic = {
            "semanticSource": {
                "elements": [
                    {
                        "elementId": "headline",
                        "kind": "text",
                        "semanticRole": "body",
                        "parentId": None,
                        "childIds": [],
                        "intendedOrder": 0,
                        "requiredFeatures": [],
                        "payload": {
                            "paragraphs": [
                                {
                                    "runs": [
                                        {
                                            "text": "Old native text",
                                            "language": "en",
                                            "script": "latin",
                                            "fontFamily": "Arial",
                                            "fontFace": "Regular",
                                            "fontSizePt": 18,
                                            "weight": 400,
                                            "italic": False,
                                            "underline": False,
                                            "color": "#111111",
                                        }
                                    ],
                                    "alignment": "left",
                                    "direction": "ltr",
                                    "explicitBreakAfter": False,
                                }
                            ],
                            "whitespace": "normal",
                        },
                    }
                ],
                "elementIdentityRegistry": {
                    "activeQids": ["headline"],
                    "retiredQids": [],
                },
            }
        }
        async with maker() as session:
            presentation = PresentationModel(
                id=presentation_id,
                owner_id=owner_id,
                content="Canonical semantic republish",
                n_slides=1,
                language="ko",
                mode="authored",
                revision=0,
            )
            slide = SlideModel(
                id=slide_id,
                owner_id=owner_id,
                presentation=presentation_id,
                layout_group="authored",
                layout="authored:default",
                index=0,
                content={"__authored__": True},
                html_content=initial_html,
            )
            session.add_all([presentation, slide])
            await session.flush()
            initial_manifest = await publish_authored_semantic_bundle_graph(
                session,
                presentation_id=presentation_id,
                owner_id=owner_id,
                expected_presentation_revision=0,
                operation_id=initial_operation_id,
                generation_attempt_id=generation_attempt_id,
                slides=[slide],
                htmls=[initial_html],
                semantic_drafts=[initial_semantic],
                master_contract_digest="sha256:" + "1" * 64,
                composition_digest="sha256:" + "2" * 64,
                slide_font_manifests=[font_manifest],
                resolved_artifact_bytes={font_digest: font_bytes},
            )
            assert initial_manifest.presentation_revision == 1
            advanced = await session.execute(
                update(PresentationModel)
                .where(
                    PresentationModel.id == presentation_id,
                    PresentationModel.revision == 0,
                )
                .values(revision=1)
            )
            assert advanced.rowcount == 1
            await session.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            base_path = f"/api/v1/ppt/presentation/{presentation_id}/canonical"
            initial = (await client.get(base_path)).json()
            response = await client.post(
                f"{base_path}/patches",
                json={
                    "expectedRevision": 1,
                    "expectedDigest": initial["sourceDigest"],
                    "patches": [
                        {
                            "slideId": str(slide_id),
                            "actions": [
                                {
                                    "action": "setText",
                                    "qid": "headline",
                                    "text": "Updated native text",
                                }
                            ],
                        }
                    ],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["revision"] == 2
            patched = response.json()
            async with maker() as verification_session:
                patched_active = await get_active_authored_emission(
                    verification_session,
                    presentation_id=presentation_id,
                    owner_id=owner_id,
                )
                patched_wire = str(
                    patched_active.bundles[0].model_dump(mode="json", by_alias=True)
                )
                assert patched_active.presentation_revision == 2
                assert "Updated native text" in patched_wire
                assert "Old native text" not in patched_wire
            undo = await client.post(
                f"{base_path}/undo",
                json={
                    "expectedRevision": 2,
                    "expectedDigest": patched["sourceDigest"],
                },
            )
            assert undo.status_code == 200, undo.text
            assert undo.json()["revision"] == 3
            async with maker() as verification_session:
                undone_active = await get_active_authored_emission(
                    verification_session,
                    presentation_id=presentation_id,
                    owner_id=owner_id,
                )
                undone_wire = str(
                    undone_active.bundles[0].model_dump(mode="json", by_alias=True)
                )
                assert undone_active.presentation_revision == 3
                assert "Old native text" in undone_wire
                assert "Updated native text" not in undone_wire
            redo = await client.post(
                f"{base_path}/redo",
                json={
                    "expectedRevision": 3,
                    "expectedDigest": undo.json()["sourceDigest"],
                },
            )
            assert redo.status_code == 200, redo.text
            assert redo.json()["revision"] == 4
            async with maker() as verification_session:
                redone_active = await get_active_authored_emission(
                    verification_session,
                    presentation_id=presentation_id,
                    owner_id=owner_id,
                )
                redone_wire = str(
                    redone_active.bundles[0].model_dump(mode="json", by_alias=True)
                )
                assert redone_active.presentation_revision == 4
                assert "Updated native text" in redone_wire
                assert "Old native text" not in redone_wire

        async with maker() as session:
            active = await get_active_authored_emission(
                session,
                presentation_id=presentation_id,
                owner_id=owner_id,
            )
            projection = await get_workbench_capability_projection(
                session,
                presentation_id=presentation_id,
                owner_id=owner_id,
            )
            assert active.presentation_revision == 4
            assert active.manifest is not None
            assert active.manifest.presentation_revision == 4
            assert projection.presentation_revision == 4
            assert "Updated native text" in str(
                active.bundles[0].model_dump(mode="json", by_alias=True)
            )
            assert "Old native text" not in str(
                active.bundles[0].model_dump(mode="json", by_alias=True)
            )

            export_attempt_id = uuid.uuid4()
            session.add(
                PresentationExportAttempt(
                    id=export_attempt_id,
                    owner_id=owner_id,
                    owner_scope=str(owner_id),
                    presentation_id=presentation_id,
                    format="pptx",
                    state="pending",
                    source_revision=4,
                    source_snapshot_sha256="3" * 64,
                    request_hash="4" * 64,
                    idempotency_key_hash="5" * 64,
                    requested_adapter="general",
                    provenance_schema_version=1,
                    provenance={"schemaVersion": 1, "source": "canonical-republish-test"},
                )
            )
            await session.flush()
            manifest = active.manifest
            binding = ExportAttemptDigestBindingV1(
                export_attempt_id=export_attempt_id,
                presentation_id=presentation_id,
                presentation_revision=4,
                operation_id=manifest.operation_id,
                generation_attempt_id=manifest.generation_attempt_id,
                slide_origin_generation_attempt_ids=[
                    bundle.origin_generation_attempt_id for bundle in active.bundles
                ],
                canonical_digest=manifest.canonical_digest,
                manifest_digest=manifest.canonical_digest,
                semantic_digests=[
                    bundle.semantic_component.digest for bundle in active.bundles
                ],
                master_contract_digest=manifest.master_contract_digest,
                composition_digest=manifest.composition_digest,
                font_manifest_digest=manifest.font_manifest_digest,
                asset_manifest_digest=manifest.asset_manifest_digest,
                quality_policy_digest=canonical_digest(
                    {"qualityPolicyVersion": manifest.quality_policy_version}
                ),
                mode="hybrid",
                renderer_source_digest="sha256:" + "6" * 64,
                serializer_id="authored-hybrid",
                serializer_version="2",
                receipt_digests=[],
            )
            sealed = await bind_existing_export_attempt(
                session,
                owner_id=owner_id,
                binding=binding,
            )
            assert sealed.binding_digest is not None
            pointer_before_failure = await session.scalar(
                select(AuthoredDeckEmissionActivePointer).where(
                    AuthoredDeckEmissionActivePointer.presentation_id == presentation_id
                )
            )
            assert pointer_before_failure is not None
            pointer_digest_before_failure = pointer_before_failure.canonical_digest
            await session.execute(
                delete(AuthoredSemanticArtifact).where(
                    AuthoredSemanticArtifact.content_digest == font_digest
                )
            )
            await session.commit()

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            failed = await client.post(
                f"/api/v1/ppt/presentation/{presentation_id}/canonical/patches",
                json={
                    "expectedRevision": 4,
                    "expectedDigest": redo.json()["sourceDigest"],
                    "patches": [
                        {
                            "slideId": str(slide_id),
                            "actions": [
                                {
                                    "action": "setText",
                                    "qid": "headline",
                                    "text": "Must roll back",
                                }
                            ],
                        }
                    ],
                },
            )
            assert failed.status_code == 422, failed.text
            assert failed.json()["detail"]["code"] == "authored_canonical_semantic_republish_failed"

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            slide = await session.get(SlideModel, slide_id)
            pointer = await session.scalar(
                select(AuthoredDeckEmissionActivePointer).where(
                    AuthoredDeckEmissionActivePointer.presentation_id == presentation_id
                )
            )
            revision_count = await session.scalar(
                select(func.count())
                .select_from(AuthoredCanonicalRevision)
                .where(AuthoredCanonicalRevision.presentation_id == presentation_id)
            )
            assert presentation is not None and presentation.revision == 4
            assert slide is not None and "Updated native text" in (slide.html_content or "")
            assert pointer is not None and pointer.presentation_revision == 4
            assert pointer.canonical_digest == pointer_digest_before_failure
            assert revision_count == 4
        await engine.dispose()

    asyncio.run(scenario())
