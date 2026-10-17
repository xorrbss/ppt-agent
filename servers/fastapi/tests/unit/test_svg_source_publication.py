import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_document_mutations as mutations
from services.svg_contract import source_digest


@pytest.mark.parametrize("mutation", [None, "reorder", "delete", "content", "marker", "receipt", "unverified"])
def test_source_publication_preserves_verified_svg_and_rejects_mutations(monkeypatch, mutation):
    canvas = SimpleNamespace(width_px=1280, height_px=720)
    marker = {"schemaVersion": "svg-source-preservation/v1", "manifestDigest": "sha256:" + "a" * 64, "pageCount": 2}
    presentation = SimpleNamespace(
        id="presentation", theme={"svg": {"sourcePreservation": marker}}, is_svg=lambda: True
    )
    slides = []
    for index in range(2):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg"><text>Original {index}</text></svg>'
        receipt = {
            "contract_version": "svg-source-preservation/v1",
            "manifest_digest": marker["manifestDigest"],
            "source_page_index": index,
            "source_page_id": f"page-{index}",
            "page_count": 2,
            "svg_digest": source_digest(svg),
            "canvas": {"width": 1280, "height": 720},
            "rendering_verified": True,
            "checks": {
                key: True
                for key in ("source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact")
            },
        }
        slides.append(
            SimpleNamespace(
                index=index,
                svg_content=svg,
                is_svg=lambda: True,
                content={"svgReceipt": {"source_preservation": receipt}},
            )
        )
    originals = [(slide.svg_content, deepcopy(slide.content)) for slide in slides]
    if mutation == "reorder":
        slides.reverse()
        for index, slide in enumerate(slides):
            slide.index = index
    elif mutation == "delete":
        slides.pop()
    elif mutation == "content":
        slides[0].svg_content = slides[0].svg_content.replace("Original", "Changed")
    elif mutation == "marker":
        presentation.theme["svg"].pop("sourcePreservation")
    elif mutation == "receipt":
        slides[0].content = {}
    elif mutation == "unverified":
        slides[0].content["svgReceipt"]["source_preservation"]["rendering_verified"] = False
    session = SimpleNamespace(flush=AsyncMock(), scalars=AsyncMock(return_value=slides))
    monkeypatch.setattr(mutations, "presentation_canvas_contract", lambda _: canvas)

    def forbidden(*args, **kwargs):
        raise AssertionError("A verified source SVG must not be finalized, renumbered or rendered again")

    monkeypatch.setattr(mutations, "finalize_svg", forbidden)
    monkeypatch.setattr(mutations, "read_svg_slide_assets", forbidden)
    if mutation:
        with pytest.raises(ValueError, match="svg_source_publication_binding_invalid"):
            asyncio.run(mutations.refresh_svg_page_numbers(session, presentation))
    else:
        asyncio.run(mutations.refresh_svg_page_numbers(session, presentation))
        assert [(slide.svg_content, slide.content) for slide in slides] == originals
    if mutation is None:
        from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
        from services.svg_common_header import prepare_common_header
        from services.svg_contract import finalize_svg

        outline = PresentationOutlineModel(slides=[
            SlideOutlineModel(content="Source Alpha", title="Alpha"),
            SlideOutlineModel(content="Source Beta", title="Beta", title_source_digest="sha256:" + "b" * 64),
        ])
        header, bindings = prepare_common_header({"tokens": {}}, outline,
                                                 {"width": canvas.width_px, "height": canvas.height_px})
        snapshot = {"tokens": {}, "common_header": header}
        uniform_slides = []
        body = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
                '<text x="64" y="220" font-family="Noto Sans KR" font-size="24" fill="#111">Body content</text></svg>')
        for index, (page, binding) in enumerate(zip(outline.slides, bindings, strict=True)):
            page.common_header = binding
            final = finalize_svg(body, index=index, n=2, style_snapshot=snapshot, header_page=binding)
            uniform_slides.append(SimpleNamespace(index=index, svg_content=final, is_svg=lambda: True,
                properties={"image": f"page-{index}.png"},
                content={"source": page.content, "svgReceipt": {"png_sha256": f"png-{index}", "quality": {
                    "passed": True, "verified": True, "classification": "passed",
                    "common_header": {"passed": True, "header_digest": binding["header_digest"],
                                      "title_digest": binding["title_digest"], "page_digest": binding["digest"]}}}}))
        uniform = SimpleNamespace(id="uniform", theme={"svg": {"styleSnapshot": snapshot}},
                                  outlines=outline.model_dump(mode="json"), is_svg=lambda: True)
        uniform_session = SimpleNamespace(flush=AsyncMock(), scalars=AsyncMock(return_value=uniform_slides),
                                          add=lambda _row: None)
        before = [(slide.svg_content, deepcopy(slide.content), deepcopy(slide.properties)) for slide in uniform_slides]
        frozen_inputs = deepcopy((snapshot, uniform.outlines))
        monkeypatch.setattr(mutations, "finalize_svg", finalize_svg)
        monkeypatch.setattr(mutations, "read_svg_slide_assets", AsyncMock(return_value=None))
        monkeypatch.setattr(mutations, "render_svg_with_metrics", AsyncMock(side_effect=forbidden))
        monkeypatch.setattr(mutations, "_save_slide_pngs", forbidden)
        asyncio.run(mutations.refresh_svg_page_numbers(uniform_session, uniform))
        assert [(slide.svg_content, slide.content, slide.properties) for slide in uniform_slides] == before
        assert (snapshot, uniform.outlines) == frozen_inputs
        mutations.render_svg_with_metrics.assert_not_awaited()
        uniform_slides.reverse()
        for index, slide in enumerate(uniform_slides):
            slide.index = index
        with pytest.raises(ValueError, match="svg_common_header_source_mismatch"):
            asyncio.run(mutations.refresh_svg_page_numbers(uniform_session, uniform))
        uniform_slides.reverse()
        for index, slide in enumerate(uniform_slides):
            slide.index = index
        uniform.outlines["slides"][1]["title_source_digest"] = "changed"
        with pytest.raises(ValueError, match="svg_common_header_source_mismatch"):
            asyncio.run(mutations.refresh_svg_page_numbers(uniform_session, uniform))
        uniform.outlines = deepcopy(frozen_inputs[1])
        uniform.outlines["slides"][1].pop("common_header")
        with pytest.raises(ValueError, match="svg_common_header"):
            asyncio.run(mutations.refresh_svg_page_numbers(uniform_session, uniform))

        async def check_recovery_source_read():
            import uuid
            from datetime import datetime, timedelta, timezone
            from services import svg_generation_assets, svg_document_mutations
            owner = uuid.uuid4()
            presentation = SimpleNamespace(id=uuid.uuid4(), owner_id=owner, revision=0)
            job = SimpleNamespace(workspace_operation_id=uuid.uuid4(), request_payload={"strategy": "svg", "_svg_recovery_lineage": {"child_operation_id": "bound"}}, attempt_number=1, attempt_token="current", state="publishing")
            operation = SimpleNamespace(id=job.workspace_operation_id, presentation_id=presentation.id, owner_id=owner, fencing_token=1, attempt_number=1, attempt_token="current", state="running", lease_expires_at=datetime.now(timezone.utc)+timedelta(minutes=1), expected_revision=0, result_revision=None)
            source = SimpleNamespace(presentation_id=str(uuid.uuid4()))
            pointer = {"digest": "bound"}
            class Session:
                async def scalar(self, statement):
                    return job
                async def get(self, model, identifier):
                    return operation
            async def verify_inherited(session, manifest, value, *, context, presentation_id, operation_id):
                if value != pointer or context.owner_id != owner or context.tenant_id != "tenant" or presentation_id != presentation.id or operation_id != operation.id:
                    raise ValueError("foreign_source")
            monkeypatch.setattr(svg_generation_assets, "_verify_inherited_source_manifest", verify_inherited)
            session = Session()
            read = svg_document_mutations._verified_inherited_slide_source
            assert await read(session, presentation, source, pointer, owner, "tenant")
            job.state = "published"
            job.attempt_token = None
            job.lease_expires_at = None
            job.last_started_at = datetime.now(timezone.utc) - timedelta(seconds=3)
            operation.started_at = job.last_started_at
            job.published_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            assert await read(session, presentation, source, pointer, owner, "tenant")
            job.published_at = job.last_started_at - timedelta(seconds=1)
            assert not await read(session, presentation, source, pointer, owner, "tenant")
            job.state = "publishing"
            job.attempt_token = operation.attempt_token
            job.request_payload = {"strategy": "svg", "_svg_partial_recovery": {"operation_id": str(operation.id)}}
            job.state = "staging"
            assert await read(session, presentation, source, pointer, owner, "tenant")
            assert not await read(session, presentation, source, {"digest":"foreign"}, owner, "tenant")
            assert not await read(session, presentation, source, pointer, owner, "foreign-tenant")
            saved = job.request_payload
            job.request_payload = {}
            assert not await read(session, presentation, source, pointer, owner, "tenant")
            job.request_payload = saved
            operation.attempt_token = "stale"
            assert not await read(session, presentation, source, pointer, owner, "tenant")
            operation.attempt_token = "current"
            operation.lease_expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
            assert not await read(session, presentation, source, pointer, owner, "tenant")
            operation.lease_expires_at = datetime.now(timezone.utc)+timedelta(minutes=1)
            operation.owner_id = uuid.uuid4()
            assert not await read(session, presentation, source, pointer, owner, "tenant")
        asyncio.run(check_recovery_source_read())
        from uuid import uuid4
        from pydantic import ValidationError
        from models.sql.public_artifact import ArtifactRef, artifact_storage_key

        import importlib.util
        from pathlib import Path
        from sqlalchemy import CheckConstraint, Column, Integer, MetaData, String, Table, create_engine, text
        from sqlalchemy.exc import IntegrityError
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from models.sql.public_artifact import PublicArtifact

        migration_path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "f2d4e6a8c0b3_add_svg_export_source_lineage_kind.py"
        spec = importlib.util.spec_from_file_location("svg_export_lineage_kind_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        model_kind_check = next(c for c in PublicArtifact.__table__.constraints if c.name == "ck_public_artifact_kind")
        assert "svg-export-source-lineage" in str(model_kind_check.sqltext)
        engine = create_engine("sqlite://")
        metadata = MetaData()
        Table("public_artifacts", metadata, Column("id", Integer, primary_key=True), Column("kind", String, nullable=False),
              CheckConstraint(migration._OLD, name="ck_public_artifact_kind"))
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(text("INSERT INTO public_artifacts (id, kind) VALUES (1, 'slide-svg'), (2, 'source-manifest')"))
            original_rows = connection.execute(text("SELECT id, kind FROM public_artifacts ORDER BY id")).all()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.execute(text("SELECT id, kind FROM public_artifacts ORDER BY id")).all() == original_rows
            connection.execute(text("INSERT INTO public_artifacts (id, kind) VALUES (3, 'svg-export-source-lineage')"))
            with pytest.raises(IntegrityError):
                connection.execute(text("INSERT INTO public_artifacts (id, kind) VALUES (4, 'unregistered-lineage-kind')"))
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="must be retained"):
                    migration.downgrade()

        lineage_owner = uuid4()
        lineage_ref = ArtifactRef(
            artifactId=uuid4(), ownerId=lineage_owner, tenantId="lineage-unit-tenant",
            kind="svg-export-source-lineage", sha256="a" * 64,
            createdByOperationId=uuid4(), retentionClass="ephemeral", storageKey=artifact_storage_key(
                owner_id=lineage_owner, tenant_id="lineage-unit-tenant", digest="a" * 64
            ),
            sizeBytes=1, mediaType="application/json",
        )
        assert ArtifactRef.model_validate(lineage_ref.model_dump(by_alias=True)).kind == "svg-export-source-lineage"
        with pytest.raises(ValidationError):
            ArtifactRef.model_validate({**lineage_ref.model_dump(by_alias=True), "kind": "unregistered-lineage-kind"})
        from services.svg_export_assets import _ExportSourceLineage, _check_export_source_lineage
        from services.svg_contract import source_digest as export_digest
        export_owner, export_target, export_operation = uuid4(), uuid4(), uuid4()
        export_source = SimpleNamespace(presentation_id=uuid4(), digest="sha256:" + "a" * 64)
        export_placement = SimpleNamespace(digest="sha256:" + "b" * 64, slide_revision=1)
        export_svg = originals[0][0]
        proof = _ExportSourceLineage(source_presentation_id=export_source.presentation_id, target_presentation_id=export_target, operation_id=export_operation, owner_id=export_owner, tenant_id="tenant", attempt_number=1, fencing_token=1, source_manifest_digest=export_source.digest, placement_manifest_digest=export_placement.digest, canonical_svg_digest=export_digest(export_svg), page_index=0, slide_revision=1).model_dump(mode="json")
        checks = dict(presentation_id=str(export_target), svg=export_svg, page_index=0, operation_id=export_operation, owner_id=export_owner, tenant_id="tenant", attempt_number=1, fencing_token=1)
        assert not _check_export_source_lineage(None, export_source, export_placement, **checks)
        assert _check_export_source_lineage(proof, export_source, export_placement, **checks)
        for field, value in [("source_manifest_digest", "sha256:" + "c" * 64), ("placement_manifest_digest", "sha256:" + "c" * 64), ("canonical_svg_digest", "sha256:" + "c" * 64), ("source_presentation_id", str(uuid4())), ("target_presentation_id", str(uuid4())), ("operation_id", str(uuid4())), ("owner_id", str(uuid4())), ("tenant_id", "foreign-tenant"), ("page_index", 1), ("slide_revision", 2), ("attempt_number", 0), ("fencing_token", 0), ("attempt_number", 2), ("fencing_token", 2)]:
            with pytest.raises(ValueError):
                _check_export_source_lineage(dict(proof, **{field: value}), export_source, export_placement, **checks)
        # Exercise the consumer with sealed bytes and the string document ID
        # used by the worker bundle, including exact owner/tenant/fence binding.
        import hashlib
        import json
        from services.svg_asset_manifest import SourceManifest, PlacementManifest
        from services.svg_export_assets import materialize_export_assets
        from services.isolated_worker_contracts import ArtifactRef as WorkerRef
        source = SourceManifest.seal(dict(presentation_id=export_source.presentation_id, source_revision=3,
                                         slots=[], offered_pages=[dict(page_index=0, slot_ids=[])]))
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><text x="40" y="60" font-size="24" font-family="Arial" font-weight="400" text-anchor="start" fill="#111111">Verified source</text></svg>'
        placement = PlacementManifest.seal(dict(canonical_svg_digest=export_digest(svg),
            source_manifest_digest=source.digest, slide_revision=1, purpose="export-pptx",
            placements=[], renderer_version="test"))
        proof.update(source_manifest_digest=source.digest, placement_manifest_digest=placement.digest,
                     canonical_svg_digest=export_digest(svg))
        payloads = {}

        def wire(kind, payload):
            digest = hashlib.sha256(payload).hexdigest()
            ref = WorkerRef(artifact_id=uuid4(), owner_id=export_owner, tenant_id="tenant", kind=kind,
                            sha256=digest, size_bytes=len(payload), media_type="application/json",
                            storage_key=artifact_storage_key(owner_id=export_owner, tenant_id="tenant", digest=digest),
                            created_by_operation_id=export_operation, retention_class="ephemeral")
            payloads[ref.sha256] = payload
            return ref.to_wire()

        binding = dict(sourceManifestRef=wire("source-manifest", source.model_dump_json(by_alias=True).encode()),
                       placementManifestRef=wire("placement-manifest", placement.model_dump_json(by_alias=True).encode()),
                       sourceManifestDigest=source.digest, placementManifestDigest=placement.digest,
                       pageIndex=0, slideRevision=1, derivatives={})
        job = SimpleNamespace(operation_id=export_operation, fencing_token=1,
                              input_artifact=SimpleNamespace(owner_id=export_owner, tenant_id="tenant"))
        store = SimpleNamespace(read=lambda ref: payloads[ref.sha256])

        def materialize(lineage):
            binding["sourceLineageRef"] = wire("svg-export-source-lineage", json.dumps(lineage).encode())
            binding["sourceLineageDigest"] = binding["sourceLineageRef"]["sha256"]
            return materialize_export_assets(binding, svg=svg, job=job, store=store,
                presentation_id=str(export_target), export_format="pptx", width=1280, height=720)

        resolved, contract = materialize(proof)
        assert "Verified source" in resolved and contract == {}
        assert source.presentation_id == export_source.presentation_id
        for field, value in [("owner_id", str(uuid4())), ("tenant_id", "foreign-tenant"),
                             ("operation_id", str(uuid4())), ("fencing_token", 2),
                             ("target_presentation_id", str(uuid4())), ("source_manifest_digest", "sha256:" + "c" * 64)]:
            with pytest.raises(ValueError, match="svg_export_source_lineage_invalid"):
                materialize(dict(proof, **{field: value}))
