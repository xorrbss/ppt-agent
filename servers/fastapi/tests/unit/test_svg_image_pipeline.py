from __future__ import annotations

from io import BytesIO
import hashlib
import uuid

from PIL import Image

from services.svg_asset_manifest import SourceManifest
from services.svg_asset_resolver import materialize_svg
from services.svg_contract import validate_svg
from services.svg_image_derivative import build_model_preview


def _large_png() -> bytes:
    noise = Image.effect_noise((2400, 1800), 96).convert("RGB")
    output = BytesIO()
    noise.save(output, format="PNG")
    return output.getvalue()


def test_large_source_uses_bounded_preview_and_slot_materialization() -> None:
    source = _large_png()
    assert len(source) > 1536 * 1024
    preview = build_model_preview(
        source,
        semantic_kind="unknown",
        max_bytes=1536 * 1024,
        max_edge=2048,
    )
    assert len(preview.payload) <= 1536 * 1024
    assert preview.source_sha256 == hashlib.sha256(source).hexdigest()

    source_asset_id = uuid.uuid4()
    occurrence_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    manifest_data = {
        "schemaVersion": "svg-asset-manifest/v1",
        "profileVersion": "svg-image-derivative/v1",
        "presentationId": str(presentation_id),
        "sourceRevision": 0,
        "slots": [{
            "slotId": "source-slot-one",
            "sourceAssetId": str(source_asset_id),
            "sourceSha256": preview.source_sha256,
            "sourceWidth": 2400,
            "sourceHeight": 1800,
            "semanticDescription": "source chart",
            "occurrenceId": str(occurrence_id),
            "sourceLocator": {
                "sourceFormat": "image",
                "pageIndex": None,
                "coordinateSpace": "intrinsic",
            },
            "extractionKind": "direct_image",
            "sourceAppearanceSha256": preview.source_appearance_sha256,
            "preservationClass": "supporting",
            "importanceUncertain": False,
            "policy": {
                "kind": "mandatory",
                "required": True,
                "allowOmit": False,
                "allowExcludeOnFailure": False,
            },
            "selectionReason": "explicit source image",
            "modelDerivative": {
                "ref": str(artifact_id),
                "sha256": preview.sha256,
                "width": preview.width,
                "height": preview.height,
                "mediaType": preview.media_type,
                "byteSize": len(preview.payload),
                "recipeDigest": preview.recipe_digest,
            },
        }],
        "offeredPages": [{"pageIndex": 0, "slotIds": ["source-slot-one"]}],
        "extractionDiagnostics": [],
    }
    manifest = SourceManifest.seal(manifest_data)
    canonical = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<text x="40" y="60" font-family="Arial" font-size="28" fill="#111111">Chart</text>'
        '<image x="40" y="100" width="800" height="500" href="source-slot-one" '
        'preserveAspectRatio="xMidYMid meet"/></svg>'
    )
    contract_manifest = {
        "source-slot-one": {"required": True, "allowOmit": False}
    }
    validate_svg(canonical, source_manifest=contract_manifest)
    materialized = materialize_svg(
        canonical,
        manifest=manifest,
        derivatives={"source-slot-one": manifest.slots[0].model_derivative},
        payloads={"source-slot-one": preview.payload},
        width=1280,
        height=720,
    )
    validate_svg(materialized)
