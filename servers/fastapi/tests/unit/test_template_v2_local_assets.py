from io import BytesIO

from PIL import Image
import pytest

from templates.v2.local_assets import (
    TemplateV2LocalAssetError,
    apply_crop_candidate,
    apply_local_image_replacement,
    preview_deterministic_crop_candidates,
    preview_local_image_replacement,
    validate_local_image,
)


IMAGE_PATH = (
    "layouts",
    0,
    "components",
    0,
    "elements",
    0,
)


def _png(width: int = 32, height: int = 20) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color=(12, 34, 56)).save(
        output,
        format="PNG",
    )
    return output.getvalue()


def _layouts() -> dict:
    return {
        "layouts": [
            {
                "id": "image-slide",
                "description": "A local image replacement fixture",
                "components": [
                    {
                        "id": "hero",
                        "description": "A bounded hero image component",
                        "position": {"x": 0, "y": 0},
                        "elements": [
                            {
                                "type": "image",
                                "name": "hero-image",
                                "decorative": False,
                                "data": "/app_data/images/original.png",
                                "fit": "cover",
                                "focus_x": 25.0,
                                "focus_y": 75.0,
                                "crop_scale": 1.4,
                                "is_icon": False,
                                "vendor_image_metadata": {
                                    "retention_class": "source"
                                },
                            }
                        ],
                    }
                ],
            }
        ],
        "vendor_envelope_metadata": {"keep": True},
    }


def test_local_image_validation_checks_magic_type_dimensions_and_provenance():
    payload = _png()
    asset = validate_local_image(
        filename=r"C:\fakepath\hero.png",
        declared_media_type="image/png",
        payload=payload,
    )

    assert asset.filename == "hero.png"
    assert (asset.width, asset.height) == (32, 20)
    assert asset.size_bytes == len(payload)
    assert asset.asset_id.startswith("local-")
    assert asset.provenance()["source"] == "local-upload"

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_magic_mismatch",
    ):
        validate_local_image(
            filename="spoof.png",
            declared_media_type="image/jpeg",
            payload=payload,
        )

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_filename_invalid",
    ):
        validate_local_image(
            filename="https://example.test/image.png",
            declared_media_type="image/png",
            payload=payload,
        )

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_dimension_exceeded",
    ):
        validate_local_image(
            filename="too-wide.png",
            declared_media_type="image/png",
            payload=_png(width=8193, height=1),
        )


def test_local_image_replace_is_previewed_cas_guarded_and_retention_safe():
    source = _layouts()
    asset = validate_local_image(
        filename="hero.png",
        declared_media_type="image/png",
        payload=_png(),
    )
    preview = preview_local_image_replacement(
        source,
        element_path=IMAGE_PATH,
        asset=asset,
        previous_asset_record={
            "id": "old",
            "provenance": {
                "source": "pptx-import",
                "sha256": "old",
            },
        },
    )

    assert source == _layouts()
    assert preview.before_reference == "/app_data/images/original.png"

    result = apply_local_image_replacement(
        source,
        preview,
        expected_revision=4,
        current_revision=4,
    )
    image = result.layouts["layouts"][0]["components"][0]["elements"][0]

    assert result.revision == 5
    assert image["data"] == preview.after_reference
    assert "asset_provenance" not in image
    assert result.asset_record["provenance"]["sha256"] == asset.sha256
    assert image["fit"] == "cover"
    assert image["focus_x"] == 25.0
    assert image["focus_y"] == 75.0
    assert image["crop_scale"] == 1.4
    assert image["vendor_image_metadata"] == {"retention_class": "source"}
    assert result.retention.defer_orphan_cleanup is True
    assert result.retention.delete_immediately is False
    assert result.retention.previous_reference == (
        "/app_data/images/original.png"
    )
    assert result.retention.previous_asset_record == {
        "id": "old",
        "provenance": {
            "source": "pptx-import",
            "sha256": "old",
        },
    }

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_stale_revision",
    ):
        apply_local_image_replacement(
            source,
            preview,
            expected_revision=3,
            current_revision=4,
        )


def test_local_image_preview_rejects_non_image_target():
    source = _layouts()
    source["layouts"][0]["components"][0]["elements"][0] = {
        "type": "text",
        "name": "title",
        "decorative": False,
        "runs": [{"text": "Title"}],
        "max_length": 100,
        "min_length": 1,
    }
    asset = validate_local_image(
        filename="hero.png",
        declared_media_type="image/png",
        payload=_png(),
    )

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_target_not_image",
    ):
        preview_local_image_replacement(
            source,
            element_path=IMAGE_PATH,
            asset=asset,
        )


def test_crop_candidates_are_bounded_deterministic_and_cas_applied():
    source = _layouts()
    asset = validate_local_image(
        filename="wide.png",
        declared_media_type="image/png",
        payload=_png(width=60, height=20),
    )
    first = preview_deterministic_crop_candidates(
        source,
        element_path=IMAGE_PATH,
        asset=asset,
    )
    second = preview_deterministic_crop_candidates(
        source,
        element_path=IMAGE_PATH,
        asset=asset,
    )

    assert first == second
    assert len(first.candidates) == 3
    assert {candidate.strategy for candidate in first.candidates} == {
        "center",
        "adaptive_focus",
        "rule_of_thirds",
    }
    assert all(
        0 <= candidate.focus_x <= 100
        and 0 <= candidate.focus_y <= 100
        and 1 <= candidate.crop_scale <= 6
        for candidate in first.candidates
    )

    selected = first.candidates[1]
    result = apply_crop_candidate(
        source,
        first,
        candidate_id=selected.candidate_id,
        expected_revision=8,
        current_revision=8,
    )
    image = result.layouts["layouts"][0]["components"][0]["elements"][0]

    assert result.revision == 9
    assert image["data"] == "/app_data/images/original.png"
    assert image["focus_x"] == selected.focus_x
    assert image["focus_y"] == selected.focus_y
    assert image["crop_scale"] == selected.crop_scale
    assert image["vendor_image_metadata"] == {"retention_class": "source"}

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_crop_candidate_unknown",
    ):
        apply_crop_candidate(
            source,
            first,
            candidate_id="not-previewed",
            expected_revision=8,
            current_revision=8,
        )


def test_crop_candidates_fail_closed_for_icons():
    source = _layouts()
    image = source["layouts"][0]["components"][0]["elements"][0]
    image["is_icon"] = True
    asset = validate_local_image(
        filename="icon.png",
        declared_media_type="image/png",
        payload=_png(),
    )

    with pytest.raises(
        TemplateV2LocalAssetError,
        match="template_v2_local_image_crop_unsupported_icon",
    ):
        preview_deterministic_crop_candidates(
            source,
            element_path=IMAGE_PATH,
            asset=asset,
        )
