from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    AssetManifestV1,
    AuthoredEmissionBundleV1,
    AuthoredSemanticSourceV1,
    ElementIdentityRegistryV1,
    FontManifestV1,
    ShapeElementV1,
    ShapePayloadV1,
    TablePayloadV1,
)
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    bytes_digest,
    canonical_json,
    seal_asset_manifest,
    seal_font_manifest,
    seal_identity_registry,
    validate_and_seal_candidate,
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


def test_native_table_seed_contract_is_first_class_and_strict() -> None:
    base = {
        "elementId": "s01-table",
        "kind": "table",
        "semanticRole": "nativeTable",
        "parentId": None,
        "childIds": [],
        "intendedOrder": 0,
        "requiredFeatures": ["table", "table-v1", "cell-text", "cell-fill"],
        "payload": {
            "schemaVersion": "authored-native-table/v1",
            "headerRows": 1,
            "fontFamily": "Malgun Gothic",
            "fontSizePt": 18.0,
            "columnWidths": [1.0, 1.0, 1.0],
            "rows": [
                [
                    {"text": "분기", "fill": "#2563EB", "textColor": "#FFFFFF", "bold": True},
                    {"text": "정확도", "fill": "#2563EB", "textColor": "#FFFFFF", "bold": True},
                    {"text": "처리량", "fill": "#2563EB", "textColor": "#FFFFFF", "bold": True},
                ],
                [{"text": "1분기"}, {"text": "92%"}, {"text": "1,240"}],
                [{"text": "2분기"}, {"text": "96%"}, {"text": "1,680"}],
            ],
        },
    }
    source = AuthoredSemanticSourceV1.model_validate(
        {
            "presentationId": uuid.uuid4(),
            "slideId": uuid.uuid4(),
            "slideRevision": 1,
            "operationId": uuid.uuid4(),
            "authoredHtmlDigest": _digest("table-seed"),
            "elements": [base],
            "identityRegistry": {"activeQids": ["s01-table"]},
        }
    )
    assert source.elements[0].payload.schema_version == "authored-native-table/v1"
    assert len(source.elements[0].payload.rows[0]) == 3

    for invalid_payload, code in [
        ({**base["payload"], "rows": [base["payload"]["rows"][0], [{"text": "short"}]]}, "authored_table_row_column_mismatch"),
        ({**base["payload"], "headerRows": 4}, "authored_table_header_rows_invalid"),
        ({**base["payload"], "columnWidths": [1.0, 1.0]}, "authored_table_column_widths_mismatch"),
    ]:
        with pytest.raises(ValidationError, match=code):
            TablePayloadV1.model_validate(invalid_payload)

    legacy = {key: value for key, value in base.items() if key != "payload"}
    legacy.update(sourceRef=_digest("legacy-table"), rasterReason="table_serializer_unavailable")
    legacy_source = AuthoredSemanticSourceV1.model_validate(
        {
            "presentationId": uuid.uuid4(),
            "slideId": uuid.uuid4(),
            "slideRevision": 1,
            "operationId": uuid.uuid4(),
            "authoredHtmlDigest": _digest("legacy-table-seed"),
            "elements": [legacy],
            "identityRegistry": {"activeQids": ["s01-table"]},
        }
    )
    assert legacy_source.elements[0].source_ref == _digest("legacy-table")


