import pytest

from templates.v2.slide_variants import (
    TemplateV2VariantError,
    VariantPatch,
    VariantRequest,
    apply_slide_variant,
    cancel_slide_variants,
    preview_slide_variants,
    restore_slide_variant_from_journal,
)


def _layouts() -> dict:
    return {
        "layouts": [
            {
                "id": "variant-slide",
                "description": "A slide-scoped visual variant fixture",
                "server_owned_metadata": {"keep": "stable"},
                "components": [
                    {
                        "id": "content",
                        "description": "Variant candidate content component",
                        "position": {"x": 0, "y": 0},
                        "elements": [
                            {
                                "type": "text",
                                "name": "title",
                                "decorative": False,
                                "font": {"size": 24, "bold": False},
                                "runs": [{"text": "Quarterly results"}],
                                "max_length": 100,
                                "min_length": 1,
                            },
                            {
                                "type": "image",
                                "name": "hero",
                                "decorative": False,
                                "data": "/app_data/images/hero.png",
                                "fit": "contain",
                                "is_icon": False,
                                "unknown_image_metadata": {"keep": True},
                            },
                            {
                                "type": "chart",
                                "name": "trend",
                                "decorative": False,
                                "chart_type": "line",
                                "categories": ["Q1", "Q2"],
                                "series": [
                                    {"name": "Revenue", "values": [10, 12]},
                                    {"name": "Plan", "values": [9, 11]},
                                ],
                                "legend": False,
                            },
                        ],
                    }
                ],
            }
        ],
        "unknown_envelope_metadata": {"keep": [1, 2, 3]},
    }


def _requests() -> tuple[VariantRequest, ...]:
    return (
        VariantRequest(
            kind="data_focused",
            label="Data focused",
            patches=(
                VariantPatch(
                    path=(
                        "components",
                        0,
                        "elements",
                        2,
                        "legend",
                    ),
                    after=True,
                ),
            ),
        ),
        VariantRequest(
            kind="image_focused",
            label="Image focused",
            patches=(
                VariantPatch(
                    path=("components", 0, "elements", 1, "fit"),
                    after="cover",
                ),
            ),
        ),
        VariantRequest(
            kind="executive_summary",
            label="Executive summary",
            patches=(
                VariantPatch(
                    path=(
                        "components",
                        0,
                        "elements",
                        0,
                        "font",
                        "bold",
                    ),
                    after=True,
                ),
            ),
        ),
    )


def test_slide_variants_preview_two_or_three_scoped_candidates_and_cancel():
    source = _layouts()
    preview = preview_slide_variants(
        source,
        layout_index=0,
        source_revision=11,
        requests=_requests(),
    )

    assert [candidate.kind for candidate in preview.candidates] == [
        "data_focused",
        "image_focused",
        "executive_summary",
    ]
    assert len({candidate.render_digest for candidate in preview.candidates}) == 3
    assert len({candidate.semantic_digest for candidate in preview.candidates}) == 1
    assert source == _layouts()
    assert cancel_slide_variants(preview) == {
        "preview_id": preview.preview_id,
        "status": "cancelled",
    }
    assert source == _layouts()


def test_slide_variant_apply_is_patch_only_cas_guarded_and_journal_restorable():
    source = _layouts()
    preview = preview_slide_variants(
        source,
        layout_index=0,
        source_revision=11,
        requests=_requests(),
    )
    applied = apply_slide_variant(
        source,
        preview,
        selected_kind="executive_summary",
        expected_revision=11,
        current_revision=11,
    )
    elements = applied.layouts["layouts"][0]["components"][0]["elements"]

    assert applied.revision == 12
    assert elements[0]["font"]["bold"] is True
    assert elements[0]["runs"] == [{"text": "Quarterly results"}]
    assert elements[1]["fit"] == "contain"
    assert elements[1]["unknown_image_metadata"] == {"keep": True}
    assert applied.layouts["layouts"][0]["server_owned_metadata"] == {
        "keep": "stable"
    }
    assert applied.layouts["unknown_envelope_metadata"] == {"keep": [1, 2, 3]}
    assert applied.journal_entry["reason"] == (
        "slide-variant:executive_summary"
    )
    assert applied.journal_entry["layout_index"] == 0

    restored = restore_slide_variant_from_journal(
        applied.layouts,
        applied.journal_entry,
        expected_revision=12,
        current_revision=12,
    )
    assert restored.revision == 13
    assert restored.restored_from_revision == 11
    assert restored.layouts == source

    with pytest.raises(
        TemplateV2VariantError,
        match="template_v2_variant_stale_revision",
    ):
        apply_slide_variant(
            source,
            preview,
            selected_kind="executive_summary",
            expected_revision=10,
            current_revision=11,
        )


def test_slide_variant_rejects_content_or_server_metadata_patches():
    source = _layouts()
    bad_request = VariantRequest(
        kind="executive_summary",
        label="Unsafe content rewrite",
        patches=(
            VariantPatch(
                path=(
                    "components",
                    0,
                    "elements",
                    0,
                    "runs",
                    0,
                    "text",
                ),
                after="Replace the entire semantic content",
            ),
        ),
    )

    with pytest.raises(
        TemplateV2VariantError,
        match="template_v2_variant_patch_not_visual",
    ):
        preview_slide_variants(
            source,
            layout_index=0,
            source_revision=11,
            requests=(_requests()[0], bad_request),
        )
