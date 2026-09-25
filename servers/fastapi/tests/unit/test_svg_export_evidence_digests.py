from services.generation_core.export.svg_evidence import build_svg_export_evidence_digests
from services.public_export_identity import ExportEvidenceDigests


def _build(**overrides):
    kwargs = dict(
        svgs=["<svg/>", "<svg/>"],
        notes=["", "speaker note"],
        source_assets=None,
        source_preservation=None,
        quality_receipt={"release_readiness": "ready"},
        converter_version="slide-master/test",
        converter_digest="a" * 64,
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


def test_semantic_digest_currently_equals_scene_digest():
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


def test_different_quality_receipts_change_only_the_quality_digest():
    base = _build()
    changed = _build(quality_receipt={"release_readiness": "blocked"})
    assert changed.quality_digest != base.quality_digest
    assert changed.scene_digest == base.scene_digest


def test_different_converter_identity_changes_only_the_exporter_digest():
    base = _build()
    changed = _build(converter_digest="b" * 64)
    assert changed.exporter_digest != base.exporter_digest
    assert changed.scene_digest == base.scene_digest


def test_svg_export_operation_records_evidence_digests_on_the_receipt():
    import inspect

    import services.svg_export_operation as operation
    source = inspect.getsource(operation.execute_svg_workspace_export)
    assert "build_svg_export_evidence_digests" in source
    assert 'receipt["evidenceDigests"]' in source
