from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from models.public_v2_contracts import QualityReceiptV3
from services.authored_canvas import canvas_metadata_for_aspect_ratio
from services.public_export_errors import PublicExportOperationError
from services.svg_contract import source_digest
from services.svg_contract import SVG_CONTRACT_VERSION
from services.svg_renderer import SVG_RENDERER_VERSION
from services.svg_export_operation import _source_preservation_receipts
from services.svg_export_quality import SOURCE_CHECKS, TEMPLATE_CHECKS, source_svg_public_quality_receipt


def evidence_fixture():
    canvas = canvas_metadata_for_aspect_ratio("16:9")
    svg = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    receipt = dict(
        contract_version="svg-source-preservation/v1",
        manifest_digest="sha256:" + "1" * 64,
        source_digest="sha256:" + "2" * 64,
        source_page_id="page-1",
        source_page_index=0,
        page_count=1,
        page_digest="sha256:" + "3" * 64,
        plan_digest="sha256:" + "4" * 64,
        svg_digest=source_digest(svg),
        style_digest="sha256:" + "5" * 64,
        template_digest="sha256:" + "6" * 64,
        layout_id="content-stack",
        layout_schema_version="svg-source-layout/v2",
        canvas=dict(width=canvas.width_px, height=canvas.height_px),
        rendering_verified=True,
        bindings=[dict(source_id="block-1", kind="text", element_id="text-1", text="원문")],
        checks={c: True for c in SOURCE_CHECKS},
    )
    receipt["template_application"] = dict(
        template_digest=receipt["template_digest"],
        layout_id=receipt["layout_id"],
        constraints_verified=True,
        passed=True,
        checks={c: True for c in TEMPLATE_CHECKS},
        visual_review_verified=False,
    )
    quality = dict(
        contract_version=SVG_CONTRACT_VERSION,
        renderer_version=SVG_RENDERER_VERSION,
        passed=True,
        geometry_passed=True,
        measurement_complete=True,
        source_digest=receipt["svg_digest"],
        png_digest="sha256:" + "a" * 64,
        trusted_font_materialization=dict(worker_materialized=True),
    )
    slide = SimpleNamespace(svg_content=svg, content={"svgReceipt": dict(source_preservation=receipt,
        quality=quality, png_sha256="a" * 64)})
    presentation = SimpleNamespace(
        theme={
            "svg": dict(
                sourcePreservation=dict(
                    schemaVersion=receipt["contract_version"], manifestDigest=receipt["manifest_digest"], pageCount=1
                ),
                styleSnapshot=dict(
                    digest=receipt["style_digest"],
                    source_template=dict(digest=receipt["template_digest"], layouts=[dict(id="content-stack")]),
                ),
            )
        }
    )
    readback = dict(
        schemaVersion="svg-source-preservation-export-readback/v1",
        passed=True,
        manifestDigest=receipt["manifest_digest"],
        sourceDigest=receipt["source_digest"],
        pages=[
            dict(
                sourcePageIndex=0,
                sourcePageId=receipt["source_page_id"],
                svgDigest=receipt["svg_digest"],
                text=dict(matchedBindingCount=1),
                tables=dict(matchedCellValueCount=0),
                images=dict(matchedOccurrenceCount=0),
            )
        ],
    )
    return canvas, presentation, slide, receipt, readback


def public_receipt(slide, receipt, readback):
    return source_svg_public_quality_receipt(
        slides=[slide],
        source_preservation=[receipt],
        worker_metrics=dict(public_export_worker_evidence=dict(structuralReadback=dict(sourcePreservation=readback))),
        quality_receipt_id="quality-test",
        operation_id="operation-test",
        presentation_id="presentation-test",
        artifact_digest="sha256:" + "a" * 64,
        source_revision=1,
        scene_digest="sha256:" + "b" * 64,
        evaluated_at=datetime.now(timezone.utc),
        exporter_version="test-converter/v1",
    )


def test_persisted_domains_are_connected_without_claiming_visual_or_native_acceptance():
    canvas, presentation, slide, receipt, readback = evidence_fixture()
    assert _source_preservation_receipts(presentation, [slide], canvas) == [receipt]
    result = public_receipt(slide, receipt, readback)
    validated = QualityReceiptV3.model_validate(result)
    assert validated.authoring.status == "passed"
    assert validated.geometry_font.status == "passed"
    assert validated.image_content_integrity.status == "passed"
    assert validated.visual_review.required and validated.visual_review.status == "failed"
    assert validated.performance.status == "not_assessed"
    assert validated.native_coverage_ooxml.status == 'not_required'
    assert validated.native_coverage_ooxml.required is False
    assert validated.release_readiness == "blocked"
    assert validated.authoring.evidence.input_digest.startswith("sha256:")

    # Current review acceptance is a read projection; the earlier receipt stays historical.
    import asyncio
    import uuid
    from copy import deepcopy
    from pathlib import Path
    from types import SimpleNamespace
    from models.sql.presentation import PresentationModel
    from models.sql.slide import SlideModel
    from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
    from services.generation_core.quality.advisory_record import content_digest
    from services.svg_export_operation import current_svg_artifact_fidelity, svg_source_review_context
    from services.svg_export_visual_review import split_page_advisories, assemble_page_advisory
    from services.svg_final_pptx_review import seal_final_review
    from services.workspace_operation_principal import operation_principal
    from utils.hashing import canonical_json_digest, sha256_hex
    from workers import public_svg_pptx_render
    from workers.public_svg_pptx_convert import converter_digest

    owner, presentation_id, review_id, export_id, public_id = [uuid.uuid4() for _ in range(5)]
    tenant = "synthetic-review-tenant"
    svg = slide.svg_content
    svg_digest, png_digest, artifact_digest = sha256_hex(svg.encode()), "2" * 64, "3" * 64
    source_content = deepcopy(slide.content)
    source_content["svgReceipt"].update(png_sha256=png_digest)
    source_content["svgReceipt"]["quality"] = dict(passed=True, verified=True, measurement_complete=True,
        source_digest="sha256:" + svg_digest, png_digest="sha256:" + png_digest)
    current_slide = SlideModel(id=uuid.uuid4(), presentation=presentation_id, owner_id=owner, index=0,
                              source_format="svg", svg_content=svg, content=source_content)
    source_vision = dict(promptVersion="svg-required-visual-review/v1", slideCount=1, model="synthetic-model",
                         reviewedPageIndices=[0], reviewedPngDigests=[png_digest], slides=[dict(index=0, issues=[])])
    page_records = split_page_advisories(source_vision, [current_slide], [0])
    advisory = assemble_page_advisory([current_slide], page_records)
    identity = dict(presentationId=str(presentation_id), ownerId=str(owner), ownerScope="synthetic-owner", sourceRevision=1)
    binding_body = dict(identity, schemaVersion="svg-visual-review-operation-binding/v1", operationId=str(uuid.uuid4()),
                        tenantId=tenant, sourceDigests=[svg_digest], pngDigests=[png_digest],
                        pageRecordDigests=[item["digest"] for item in page_records])
    advisory["operationBinding"] = dict(binding_body, digest=content_digest(binding_body))
    snapshot = dict(authoring_profile="art-direction-v1@8")
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    current = PresentationModel(id=presentation_id, owner_id=owner, n_slides=1, mode="svg", revision=1,
                                theme=dict(svg=dict(styleSnapshot=snapshot, advisory=advisory)))
    proof = dict(schemaVersion=public_svg_pptx_render.SCHEMA, status="measured", artifactDigest="sha256:" + artifact_digest,
                 artifactDigestAfter="sha256:" + artifact_digest, hashInvariant=True, openReadOnly=True, openWithWindow=False,
                 operationId=str(export_id), presentationId=str(presentation_id), sourceRevision=1,
                 sourceDigests=[svg_digest], ownerId=str(owner), tenantId=tenant, pageCount=1,
                 converterDigest=converter_digest(), checkerCodeDigest=sha256_hex(Path(public_svg_pptx_render.__file__).read_bytes()),
                 availablePhysicalMemoryBytes=2**40, pages=[dict(index=0, pngSha256=png_digest)])
    proof["evidenceDigest"] = canonical_json_digest(proof)
    producer = seal_final_review(SimpleNamespace(operation_id=review_id, owner_id=owner, owner_scope="synthetic-owner",
        presentation_id=presentation_id, expected_revision=1), dict(proof=proof, artifactId=str(public_id),
        artifactDigest="sha256:" + artifact_digest, parentExportOperationId=str(export_id)), source_vision)
    current.theme["svg"]["finalPptxReviews"] = {artifact_digest: producer}
    old_receipt = validated.model_dump(mode="json", by_alias=True)
    export = SimpleNamespace(id=export_id, kind="export.pptx", state="succeeded", owner_id=owner, owner_scope="synthetic-owner",
        presentation_id=presentation_id, expected_revision=1, result_revision=1,
        result=dict(quality_receipt=deepcopy(old_receipt), public_export_worker_evidence=dict(structuralReadback=dict(finalPptxRender=proof))))
    review_operation = SimpleNamespace(id=review_id, kind="quality.review", state="succeeded", owner_id=owner,
        owner_scope="synthetic-owner", presentation_id=presentation_id, result_revision=1)
    public = SimpleNamespace(id=public_id, kind="presentation-pptx", owner_id=owner, tenant_id=tenant, sha256=artifact_digest)
    binding = SimpleNamespace(public_artifact_id=public_id, operation_id=export_id, owner_id=owner,
                              presentation_id=presentation_id, source_revision=1)
    review_request = dict(principal_tenant_id=tenant, context=dict(scope="all", slide_indices=[], mode="analyze_only",
                          target="final_pptx", artifact_id=str(public_id)))
    export_request = dict(principal_tenant_id=tenant)

    class ReadSession:
        async def get(self, model, key):
            return {public_id: public, export_id: export}.get(key)
        async def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            if entity is WorkspaceArtifact:
                return binding
            values = statement.compile().params.values()
            return review_request if review_id in values else export_request
        async def scalars(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            # This fixture has no completed header edit. Header authority queries
            # operations independently of the presentation's current slide list.
            assert entity in {WorkspaceOperation, SlideModel}
            return SimpleNamespace(all=lambda: [] if entity is WorkspaceOperation else [current_slide])

    async def consume():
        return await current_svg_artifact_fidelity(ReadSession(), current, review_operation)

    before_current, before_export = deepcopy(current.theme), deepcopy(export.result)
    with operation_principal(owner, tenant):
        assert asyncio.run(svg_source_review_context(ReadSession(), current))["snapshot"] == snapshot
        accepted = asyncio.run(consume())
        assert accepted["status"] == "passed"
        assert accepted["artifactId"] == str(public_id) and accepted["reviewOperationId"] == str(review_id)
        assert current.theme == before_current and export.result == before_export
        assert export.result["quality_receipt"] == old_receipt
        # Source authority remains available to its own checker, while final
        # artifact review compares only its actual owner/revision identity.
        from services.svg_export_quality import svg_public_quality_receipt
        full_binding = dict(identity, tenantId=tenant,
            _generationSourceReview=asyncio.run(svg_source_review_context(ReadSession(), current)))
        metrics = deepcopy(export.result)
        metrics["final_pptx_visual_review"] = deepcopy(producer)
        metrics["public_export_worker_evidence"]["structuralReadback"]["sourcePreservation"] = readback
        receipt_args = dict(slides=[current_slide], worker_metrics=metrics, advisory=advisory,
            require_native_coverage=True, review_binding=full_binding,
            quality_receipt_id="synthetic-final-review", operation_id=str(export_id),
            presentation_id=str(presentation_id), artifact_digest="sha256:" + artifact_digest,
            source_revision=1, scene_digest="sha256:" + "b" * 64,
            evaluated_at=datetime.now(timezone.utc), exporter_version="test-converter/v1")
        for builder, preservation in ((source_svg_public_quality_receipt, [receipt]),
                                      (svg_public_quality_receipt, None)):
            complete = builder(**receipt_args, source_preservation=preservation)
            assert complete["visualReview"]["status"] == "passed"
            for key, wrong in (("ownerId", str(uuid.uuid4())), ("ownerScope", "different-owner-scope"),
                               ("tenantId", "different-tenant"), ("sourceRevision", 2)):
                changed_metrics = deepcopy(metrics)
                changed = changed_metrics["final_pptx_visual_review"]
                changed[key] = wrong
                changed["digest"] = content_digest({name: value for name, value in changed.items() if name != "digest"})
                rejected = builder(**{**receipt_args, "worker_metrics": changed_metrics},
                                   source_preservation=preservation)
                assert rejected["visualReview"]["status"] == "failed"
        assert current.theme == before_current and export.result == before_export
        for violation in ("review_unknown", "owner", "tenant", "review_tenant", "export_tenant", "target", "artifact", "revision", "count", "slide_owner", "index", "format", "svg", "png", "parent"):
            if violation == "review_unknown":
                review_operation.state = "running"
            elif violation == "owner":
                public.owner_id = uuid.uuid4()
            elif violation == "tenant":
                public.tenant_id = "different-tenant"
            elif violation == "review_tenant":
                review_request["principal_tenant_id"] = "different-tenant"
            elif violation == "export_tenant":
                export_request["principal_tenant_id"] = "different-tenant"
            elif violation == "target":
                review_request["context"]["target"] = "source_svg"
            elif violation == "artifact":
                binding.public_artifact_id = uuid.uuid4()
            elif violation == "revision":
                current.revision = 2
            elif violation == "count":
                current.n_slides = 2
            elif violation == "slide_owner":
                current_slide.owner_id = uuid.uuid4()
            elif violation == "index":
                current_slide.index = 1
            elif violation == "format":
                current_slide.source_format = "html"
            elif violation == "svg":
                current_slide.svg_content = svg + " "
            elif violation == "png":
                current_slide.content["svgReceipt"]["png_sha256"] = "4" * 64
            else:
                binding.operation_id = uuid.uuid4()
            assert asyncio.run(consume())["status"] == "failed"
            review_operation.state = "succeeded"
            public.owner_id, public.tenant_id = owner, tenant
            review_request["principal_tenant_id"] = export_request["principal_tenant_id"] = tenant
            review_request["context"]["target"] = "final_pptx"
            binding.public_artifact_id, binding.operation_id = public_id, export_id
            current.revision, current_slide.index, current_slide.source_format, current_slide.svg_content = 1, 0, "svg", svg
            current.n_slides, current_slide.owner_id = 1, owner
            current_slide.content["svgReceipt"]["png_sha256"] = png_digest
        assert current.theme == before_current and export.result == before_export


    _assert_cached_native_coverage_promotion_guards()


def _assert_cached_native_coverage_promotion_guards():
    import uuid
    from copy import deepcopy

    from services.svg_export_quality import _generic_native_ooxml_domain
    from services.svg_export_whole_page_verifier import whole_page_verifier_digest
    from utils.hashing import canonical_json_sha256
    from workers.public_svg_pptx_convert import converter_digest

    producer, consumer, owner, presentation = (uuid.uuid4() for _ in range(4))
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"/>'
    slide = SimpleNamespace(svg_content=svg, content={})
    source = source_digest(svg)
    artifact_digest = "sha256:" + "a" * 64
    identity = "sha256:" + "b" * 64
    tenant, scope = "fixture-tenant", "fixture-scope"

    def ref(artifact_id, operation_id, kind, sha256, retention):
        return dict(schemaVersion="artifact-ref/v1", artifactId=str(artifact_id),
            ownerId=str(owner), tenantId=tenant, kind=kind, sha256=sha256,
            sizeBytes=1234, mediaType=("application/vnd.openxmlformats-officedocument.presentationml.presentation"
                if kind == "presentation-pptx" else "image/svg+xml"),
            storageKey=f"sha256/{sha256}", createdByOperationId=str(operation_id),
            retentionClass=retention)

    worker_ref = ref(uuid.uuid4(), producer, "presentation-pptx", artifact_digest[7:], "ephemeral")
    canonical_ref = ref(uuid.uuid4(), producer, "presentation-pptx", artifact_digest[7:], "published")
    consumer_ref = ref(uuid.uuid4(), consumer, "presentation-pptx", artifact_digest[7:], "published")
    source_ref = ref(uuid.uuid4(), producer, "slide-svg", source[7:], "ephemeral")
    native = {"fellBack": 0, "reasons": []}
    page = dict(pageIndex=0, sourcePageId="slide-1", svgDigest=source,
        shapeCount=1, nativePrimitiveCount=1, promotedBackgroundCount=0,
        shapeInventoryDigest="sha256:" + "c" * 64, traceDigest="sha256:" + "d" * 64)
    measurements = dict(verifiedPages=1, verifiedShapes=1, nativePrimitives=1,
        verifiedRelationships=1, unboundShapes=0, unboundPrimitives=0,
        geometryMismatches=0, unboundRelationships=0)
    proof = dict(schemaVersion="svg-generic-native-ooxml-coverage/v1", passed=True,
        artifactDigest=artifact_digest, operationId=str(producer), presentationId=str(presentation),
        sourceRevision=1, ownerId=str(owner), tenantId=tenant, sourceDigests=[source],
        verifierCodeDigest=whole_page_verifier_digest(), converterDigest=converter_digest(),
        nativeObjectsDigest="sha256:" + canonical_json_sha256(native),
        officeRenderMeasured=False, packagePartsDigest="sha256:" + "e" * 64,
        pages=[page], measurements=measurements, conversionSourceDigests=[source],
        projectedSourceDigests=[source], artifactRef=worker_ref, sourceArtifactRefs=[source_ref])
    proof["coverageDigest"] = "sha256:" + canonical_json_sha256(proof)
    structural = dict(genericNativeCoverageOoxml=proof, nativeObjects=native, slideCount=1)
    original = dict(structuralReadback=structural, sourceDigests=[source[7:]], fallbackReason=None)
    binding = dict(schemaVersion="svg-export-reuse-producer-binding/v1",
        producerOperationId=str(producer), consumerOperationId=str(consumer),
        canonicalIdentityDigest=identity, ownerId=str(owner), ownerScope=scope,
        tenantId=tenant, presentationId=str(presentation), sourceRevision=1,
        artifactDigest=artifact_digest,
        producerWorkerEvidenceDigest="sha256:" + canonical_json_sha256(original),
        producerArtifactRef=canonical_ref, consumerArtifactRef=consumer_ref)
    binding["digest"] = "sha256:" + canonical_json_sha256(binding)
    worker = {**deepcopy(original), "reusedCompletedIdentity": True,
        "reuseProducerWorkerEvidence": deepcopy(original), "reuseProducerBinding": binding}
    arguments = dict(required=True, slides=[slide], artifact_digest=artifact_digest,
        operation_id=str(consumer), presentation_id=str(presentation), source_revision=1,
        review_binding=dict(ownerId=str(owner), ownerScope=scope, tenantId=tenant),
        export_identity_digest=identity, export_tenant_id=tenant)

    def checked(candidate):
        return _generic_native_ooxml_domain(**arguments,
            worker_metrics=dict(public_export_worker_evidence=candidate))

    assert checked(worker)["status"] == "passed"
    assert worker["reuseProducerWorkerEvidence"] == original
    for field, changed in (("storageKey", "different/key"), ("ownerId", str(uuid.uuid4())),
                           ("tenantId", "other-tenant"), ("createdByOperationId", str(uuid.uuid4())),
                           ("sha256", "f" * 64), ("retentionClass", "ephemeral")):
        altered = deepcopy(worker)
        altered_binding = altered["reuseProducerBinding"]
        altered_binding["producerArtifactRef"][field] = changed
        altered_binding["digest"] = "sha256:" + canonical_json_sha256({
            key: value for key, value in altered_binding.items() if key != "digest"})
        assert checked(altered)["status"] == "failed", field

@pytest.mark.parametrize("broken", ["template", "render", "readback"])
def test_missing_evidence_is_never_promoted(broken):
    _, _, slide, receipt, readback = evidence_fixture()
    if broken == "template":
        receipt["template_application"]["checks"]["slots"] = False
    elif broken == "render":
        slide.content["svgReceipt"]["quality"]["measurement_complete"] = False
    else:
        readback["pages"][0]["svgDigest"] = "sha256:" + "f" * 64
    result = public_receipt(slide, receipt, readback)
    key = dict(template="authoring", render="geometryFont", readback="imageContentIntegrity")[broken]
    assert result[key]["status"] == "failed"
    assert result[key]["evidence"] is None


def test_v2_template_mismatch_rejected_and_legacy_source_receipt_unchanged():
    canvas, presentation, slide, receipt, _ = evidence_fixture()
    presentation.theme["svg"]["styleSnapshot"]["source_template"]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(PublicExportOperationError, match="template_evidence_invalid"):
        _source_preservation_receipts(presentation, [slide], canvas)
    legacy = deepcopy(receipt)
    legacy.pop("layout_schema_version")
    slide.content["svgReceipt"] = dict(source_preservation=legacy)
    with pytest.raises(PublicExportOperationError, match="template_contract_required"):
        _source_preservation_receipts(presentation, [slide], canvas)
    presentation.theme["svg"]["styleSnapshot"].pop("source_template")
    assert _source_preservation_receipts(presentation, [slide], canvas) == [legacy]
