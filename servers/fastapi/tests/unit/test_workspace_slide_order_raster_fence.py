"""Regression test for the reorder tenant-context gap D hit on a real
raster-redesign deck (presentation cd8a1348-..., ops e21b3049 and 9a4e092a):
both dead-lettered with slide_order_publish_failed after 3 retries.

Root cause (confirmed via an offline, rolled-back reproduction against the
real production database): WorkspaceSlideOrderOperationHandler.execute calls
publish_presentation_revision -> refresh_svg_page_numbers ->
renumber_redesign_page -> _rebind_screen_placement ->
_fenced_operation (services/svg_raster_renumber.py), which requires
utils.owner_context.get_current_tenant_id() to be set. Tenant context is
only ever established per-HTTP-request (SessionAuthMiddleware,
api/middlewares.py); WorkspaceOperationRunner._execute only establishes
owner context (set_current_owner_id) before calling a handler, never tenant,
because a durable background operation never re-enters a request. So every
reorder of a raster-redesign deck hit "svg_asset_mutation_fence_required"
unconditionally.

test_svg_raster_renumber.py's own coverage of renumber_redesign_page mocks
_fenced_operation entirely (AsyncMock), so it never caught this. This test
does not: it builds one real raster-redesign page with real crops/placements
through the real artifact-persistence path (borrowed from
test_svg_raster_renumber.py / test_raster_redesign_layout_assets.py) and
reorders it via the real create_workspace_command -> WorkspaceOperationRunner
-> WorkspaceSlideOrderOperationHandler -> publish_presentation_revision path,
with no tenant contextvar set anywhere in the test itself -- exactly the
runner's real execution environment. Fixed by recording the submitting
request's tenant in the command payload
(api/v1/ppt/endpoints/presentation_slide_order.py) and having the runner run
every handler as the operation's owner and tenant
(services/workspace_operation_principal.py); no default tenant is invented.
"""
from __future__ import annotations

import asyncio
import uuid

import test_raster_redesign_layout_assets as assets_test
import test_raster_redesign_layout_export_path as export_test
import test_workspace_slide_order_operation as order_test
from sqlalchemy import select

import services.artifact_store as artifact_store_module
import services.svg_asset_persistence as svg_asset_persistence
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from models.workspace import WorkspaceCommandRequest
from services import raster_source_assets as assets
from services.document_canvas import document_canvas_for_orientation
from services.source_content.raster_contracts import RasterApprovalPin, seal_approval
from services.source_content.raster_svg_layout import render_raster_page_svg
from services.svg_asset_manifest import PlacementManifest, SourceManifest
from services.svg_asset_persistence import SvgPublicationContext
from services.svg_contract import source_digest
from services.workspace_operation_handler_registry import WorkspaceOperationHandlerRegistry
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_service import create_workspace_command
from services.workspace_slide_order_operation_handler import (
    OPERATION_KIND,
    register_workspace_slide_order_operation_handler,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def test_reorder_of_a_raster_redesign_deck_succeeds_without_ambient_tenant_context(
    monkeypatch, tmp_path,
):
    asyncio.run(_reorder_raster_redesign_deck(monkeypatch, tmp_path))


async def _reorder_raster_redesign_deck(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.asset_directory_utils.get_app_data_directory_env", lambda: str(tmp_path))
    # -- Build one real raster-redesign page (real crops/placements), the
    # same setup test_svg_raster_renumber.py uses. --
    store = export_test.ExportArtifacts()
    analysis = assets_test._dense_page_analysis(store, brand=True, noise=True)
    assets_test._install_fakes(monkeypatch, store, analysis)
    export_test.install_export_fakes(monkeypatch, store, tmp_path)
    monkeypatch.setattr(artifact_store_module, "configured_public_artifact_service", lambda: store)
    approved = tuple(r.region_id for r in analysis.pages[0].regions)
    svg = render_raster_page_svg(analysis, 1, approved_region_ids=approved)
    pin = seal_approval(RasterApprovalPin(
        operation_id=assets_test.OPERATION, analysis_id=analysis.analysis_id,
        analysis_revision=analysis.revision, analysis_digest=analysis.digest,
        source_sha256=assets_test.SHA, approved_region_ids=approved, automatic_redesign_pages=(1,),
    ))
    publication_context = SvgPublicationContext(
        operation_id=assets_test.OPERATION, owner_id=assets_test.OWNER,
        tenant_id=assets_test.TENANT, attempt_number=1, attempt_token="t", fencing_token=1,
    )
    presentation_id = uuid.uuid4()
    prepared = await assets.prepare_raster_page_assets(
        None, store, approval_pin=pin, owner_id=assets_test.OWNER, tenant_id=assets_test.TENANT,
        publication_context=publication_context, presentation_id=presentation_id,
        source_revision=1, slide_revision=1, page_number=1, output_page_index=0,
        canonical_svg=svg, width=1188, height=840,
    )

    async def load(model, session, service, *, pointer, owner_id, tenant_id):
        return model.model_validate_json(store.blobs[uuid.UUID(str(pointer["ref"]))])

    monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer",
                        lambda *a, **k: load(SourceManifest, *a, **k))
    monkeypatch.setattr(svg_asset_persistence, "load_placement_manifest_pointer",
                        lambda *a, **k: load(PlacementManifest, *a, **k))

    # -- Persist it as slide 0 of a 2-slide SVG deck, plain slide at 1. --
    engine, maker = await order_test._database(tmp_path / "raster-reorder.db")
    try:
        style_snapshot = {"tokens": {"body_font": "Noto Sans KR", "primary": "#2563EB"}}
        # 1188x840 (A4 landscape) matches the raster fixture's real canvas,
        # not the generic 16:9 default presentation_canvas_contract falls
        # back to -- refresh_svg_page_numbers validates every slide's SVG
        # against this one deck-level canvas.
        document_canvas = document_canvas_for_orientation("landscape").model_dump(
            mode="json", by_alias=True,
        )
        # Owner scoping (services/database.py's do_orm_execute hook) filters
        # by the owner contextvar at query time, and SlideModel/PresentationModel
        # default owner_id from that same contextvar at construction time, so
        # seeding must happen under the same owner context the reorder itself
        # runs under.
        owner_token = set_current_owner_id(assets_test.OWNER)
        try:
            async with maker() as session, session.begin():
                session.add(User(id=assets_test.OWNER, username="raster-reorder-owner", hashed_password="x"))
                presentation = PresentationModel(
                    id=presentation_id, content="source", n_slides=2, language="ko", title="deck",
                    mode="svg", version="v1-standard", lifecycle_status="published", revision=1,
                    theme={
                        "mode": "svg", "style": "test",
                        "documentCanvas": document_canvas,
                        "svg": {"styleSnapshot": style_snapshot},
                    },
                    owner_id=assets_test.OWNER,
                )
                session.add(presentation)
                raster_slide = SlideModel(
                    presentation=presentation_id, layout_group="custom", layout="content", index=0,
                    content={
                        "svgAssetManifest": prepared.screen_envelope,
                        "svgReceipt": {
                            "rasterApprovalPin": pin.model_dump(mode="json"),
                            "rasterSourcePageNumber": 1,
                            "rasterAnalysisDigest": pin.analysis_digest,
                            "source_digest": "old",
                            "quality": {"source_digest": source_digest(svg)},
                        },
                    },
                    svg_content=svg, source_format="svg",
                )
                plain_slide = SlideModel(
                    presentation=presentation_id, layout_group="custom", layout="content", index=1,
                    content={"title": "slide-1"},
                    svg_content=(
                        '<svg xmlns="http://www.w3.org/2000/svg" width="1188" height="840" '
                        'viewBox="0 0 1188 840"><text x="10" y="20" font-family="Noto Sans KR" '
                        'font-size="16" fill="#000000">plain</text></svg>'
                    ),
                    source_format="svg",
                )
                session.add(raster_slide)
                session.add(plain_slide)
                await session.flush()
                raster_slide_id, plain_slide_id = raster_slide.id, plain_slide.id

            # -- Reorder (swap positions) through the real command/runner/handler
            # path, exactly as the endpoint submits it -- deliberately with no
            # tenant contextvar set anywhere in this test. --
            registry = WorkspaceOperationHandlerRegistry()
            register_workspace_slide_order_operation_handler(registry=registry, session_factory=maker, replace=True)
            runner = WorkspaceOperationRunner(
                session_factory=maker, registry=registry, max_concurrency=1,
                heartbeat_seconds=60, poll_seconds=0.01,
            )
            new_order = [plain_slide_id, raster_slide_id]
            command = WorkspaceCommandRequest(
                command="Reorder slides", command_type=OPERATION_KIND,
                idempotency_key="raster-reorder-1", expected_revision=1,
                context={
                    "slide_ids": [str(x) for x in new_order],
                    "tenant_id": assets_test.TENANT,
                },
            )
            async with maker() as session:
                operation, _duplicate = await create_workspace_command(
                    session, presentation_id=presentation_id, request=command)
                operation_id = operation.id
            dispatched = await runner.dispatch_once()
            tasks = tuple(runner._active.values())
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            reset_current_owner_id(owner_token)

        assert dispatched == 1
        async with maker() as session:
            from models.sql.workspace import WorkspaceOperation
            row = await session.get(WorkspaceOperation, operation_id, populate_existing=True)
            slides = (await session.scalars(
                select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
            )).all()

        assert row.state == "succeeded", (row.state, row.error_code, row.error_detail)
        assert [s.id for s in slides] == new_order
        moved_raster_slide = next(s for s in slides if s.id == raster_slide_id)
        assert moved_raster_slide.index == 1
        assert ">02<" in moved_raster_slide.svg_content
        assert moved_raster_slide.content["svgReceipt"]["rasterDisplayPageNumber"] == 2
        # Provenance (source page, crops, offered page) is untouched by the move.
        assert moved_raster_slide.content["svgReceipt"]["rasterSourcePageNumber"] == 1
    finally:
        await engine.dispose()
