from services.generation_core.export.svg_evidence import (
    build_svg_export_evidence_digests,
    svg_quality_inputs,
)
from services.public_export_identity import ExportEvidenceDigests


def _build(**overrides):
    kwargs = dict(
        svgs=["<svg/>", "<svg/>"],
        notes=["", "speaker note"],
        canvas={"widthPx": 1280, "heightPx": 720},
        source_assets=None,
        source_preservation=None,
        quality_inputs={"policy": None, "advisory": None, "slides": [None, None]},
        converter_version="slide-master/test",
        converter_digest="a" * 64,
        contract_version="svg-static/v1",
    )
    kwargs.update(overrides)
    return build_svg_export_evidence_digests(**kwargs)


def test_returns_a_valid_export_evidence_digests_instance():
    digests = _build()
    assert isinstance(digests, ExportEvidenceDigests)
    for value in (
        digests.scene_digest, digests.semantic_digest, digests.quality_digest,
        digests.exporter_digest, digests.font_manifest_digest,
    ):
        assert len(value) == 64


def test_semantic_digest_equals_scene_digest_without_native_objects():
    digests = _build()
    assert digests.semantic_digest == digests.scene_digest


def test_is_deterministic_for_identical_inputs():
    assert _build() == _build()


def test_different_svgs_change_the_scene_digest_only():
    base = _build()
    changed = _build(svgs=["<svg/>", "<svg>different</svg>"])
    assert changed.scene_digest != base.scene_digest
    assert changed.exporter_digest == base.exporter_digest
    assert changed.font_manifest_digest == base.font_manifest_digest


def test_canvas_is_part_of_the_scene():
    assert _build(canvas={"widthPx": 1920, "heightPx": 1080}).scene_digest != _build().scene_digest


def test_different_quality_inputs_change_only_the_quality_digest():
    base = _build()
    changed = _build(quality_inputs={"policy": "release-v1", "advisory": None, "slides": [None, None]})
    assert changed.quality_digest != base.quality_digest
    assert changed.scene_digest == base.scene_digest


def test_different_converter_identity_changes_only_the_exporter_digest():
    base = _build()
    changed = _build(converter_digest="b" * 64)
    assert changed.exporter_digest != base.exporter_digest
    assert changed.scene_digest == base.scene_digest


def test_font_embedding_changes_only_the_font_manifest_digest():
    base = _build()
    embedded = _build(font_embedding=True)
    assert embedded.font_manifest_digest != base.font_manifest_digest
    assert embedded.exporter_digest == base.exporter_digest


def test_quality_inputs_read_the_stored_slide_quality_and_advisory():
    class _Slide:
        def __init__(self, quality):
            self.content = {"svgReceipt": {"quality": quality}}

    inputs = svg_quality_inputs(slides=[_Slide({"passed": True}), _Slide(None)],
                                advisory={"digest": "x"}, quality_policy="release-v1")
    assert inputs == {"policy": "release-v1", "advisory": {"digest": "x"}, "slides": [{"passed": True}, None]}


def test_evidence_digests_are_identity_inputs_not_public_receipt_extras():
    import inspect

    import services.svg_export_identity as identity
    import services.svg_export_operation as operation
    assert "build_svg_export_evidence_digests" in inspect.getsource(identity.build_svg_export_identity)
    source = inspect.getsource(operation.execute_svg_workspace_export)
    assert 'receipt["evidenceDigests"]' not in source
