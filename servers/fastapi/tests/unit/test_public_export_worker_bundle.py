"""The export identity must move when the export worker's own code moves.

Reuse of a completed public export identity is unconditional, so a deck built
by a broken worker is served from cache forever unless the identity itself
changes.  These tests hold the binding: identical worker sources reuse, any
edit to an executed source splits, and an unreadable tree degrades to a stable
marker instead of a digest that silently pretends the code is known.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import uuid

import pytest
import services.public_export_worker_bundle as worker_bundle_module

from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.public_export_identity import (
    CanonicalExportIdentity,
    ExportEvidenceDigests,
    SlideCheckpointArtifact,
)

from services.public_export_worker_bundle import (
    BUNDLE_SOURCE_FILES,
    BUNDLE_UNAVAILABLE_MARKER,
    compute_public_export_worker_bundle,
    public_export_worker_bundle,
)

PROTOCOL = "public-worker-protocol/v2"


_SOURCES = (
    ("workers", "public_export_worker.py"),
    ("workers", "public_svg_export_worker.py"),
    ("workers", "public_svg_pptx_convert.py"),
    ("workers", "public_svg_pptx_readback.py"),
    ("services", "svg_contract.py"),
    ("services", "source_content", "raster_redesign_pptx.py"),
    ("services", "generation_core", "drivers", "svg", "export_scene.py"),
    ("services", "generation_core", "drivers", "svg", "export_projection.py"),
    ("services", "generation_core", "drivers", "svg", "export_native_wire.py"),
    ("services", "generation_core", "native_objects", "model.py"),
    ("vendor", "slide_master", "MANIFEST.json"),
    ("vendor", "slide_master", "pptx_to_svg", "convert.py"),
)


def _worker_tree(root: Path) -> Path:
    """Build the minimum on-disk shape the bundle inventory accepts."""

    fastapi = root / "servers" / "fastapi"
    for relative in _SOURCES:
        path = fastapi.joinpath(*relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.name}\n", encoding="utf-8")
    return fastapi


def test_identical_worker_sources_keep_one_identity(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _worker_tree(a)
    _worker_tree(b)

    first = compute_public_export_worker_bundle(PROTOCOL, repository_root=a)
    second = compute_public_export_worker_bundle(PROTOCOL, repository_root=b)

    assert first.source == BUNDLE_SOURCE_FILES
    assert first.file_count == len(_SOURCES)
    assert first.evidence()["schemaVersion"] == "public-export-worker-bundle/v2"
    # Two checkouts of the same code must reuse: the digest is content, not path.
    assert first.digest == second.digest


@pytest.mark.parametrize("relative", _SOURCES)
def test_any_executed_source_change_splits_the_identity(
    tmp_path: Path, relative: tuple[str, ...]
) -> None:
    fastapi = _worker_tree(tmp_path)
    before = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    fastapi.joinpath(*relative).write_text("# patched\n", encoding="utf-8")
    after = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    assert after.source == BUNDLE_SOURCE_FILES
    assert after.digest != before.digest


def test_renaming_an_executed_source_splits_the_identity(tmp_path: Path) -> None:
    fastapi = _worker_tree(tmp_path)
    before = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    source = fastapi / "vendor" / "slide_master" / "pptx_to_svg" / "convert.py"
    source.rename(source.with_name("convert_v2.py"))
    after = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    assert after.digest != before.digest


def test_bytecode_caches_never_invalidate_published_exports(tmp_path: Path) -> None:
    fastapi = _worker_tree(tmp_path)
    before = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    cache = fastapi / "vendor" / "slide_master" / "__pycache__"
    cache.mkdir()
    (cache / "convert.cpython-311.pyc").write_bytes(b"\x00")
    (fastapi / "vendor" / "slide_master" / "stray.pyc").write_bytes(b"\x00")
    native_cache = fastapi / "services" / "generation_core" / "native_objects" / "__pycache__"
    native_cache.mkdir()
    (native_cache / "model.cpython-311.pyc").write_bytes(b"\x00")
    # The native-object directory contributes only Python sources.
    (fastapi / "services" / "generation_core" / "native_objects" / "notes.md").write_text(
        "n\n", encoding="utf-8"
    )
    after = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    assert after.file_count == before.file_count
    assert after.digest == before.digest


def test_a_new_protocol_version_splits_the_identity(tmp_path: Path) -> None:
    _worker_tree(tmp_path)

    first = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)
    second = compute_public_export_worker_bundle(
        "public-worker-protocol/v3", repository_root=tmp_path
    )

    assert first.digest != second.digest


@pytest.mark.parametrize(
    ("relative", "reason"),
    [
        (("workers", "public_export_worker.py"), "worker_entrypoint_missing"),
        (("workers", "public_svg_export_worker.py"), "worker_source_missing"),
        (("services", "svg_contract.py"), "worker_source_missing"),
        (
            ("services", "generation_core", "drivers", "svg", "export_scene.py"),
            "worker_source_missing",
        ),
    ],
)
def test_missing_sources_fall_back_deterministically(
    tmp_path: Path, relative: tuple[str, ...], reason: str
) -> None:
    fastapi = _worker_tree(tmp_path)
    fastapi.joinpath(*relative).unlink()

    first = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)
    second = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    assert first.source == BUNDLE_UNAVAILABLE_MARKER
    assert first.unavailable_reason == reason
    assert first.file_count == 0
    assert first.digest == second.digest
    assert first.evidence()["unavailableReason"] == reason


def test_an_empty_worker_directory_falls_back(tmp_path: Path) -> None:
    fastapi = _worker_tree(tmp_path)
    (fastapi / "services" / "generation_core" / "native_objects" / "model.py").unlink()

    bundle = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    assert bundle.source == BUNDLE_UNAVAILABLE_MARKER
    assert bundle.unavailable_reason == "worker_source_directory_empty"


def test_the_fallback_digest_is_not_a_readable_bundle_digest(tmp_path: Path) -> None:
    _worker_tree(tmp_path)
    readable = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path)

    empty = compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path / "gone")

    assert empty.source == BUNDLE_UNAVAILABLE_MARKER
    assert empty.digest != readable.digest


def test_the_repository_bundle_is_readable() -> None:
    bundle = public_export_worker_bundle(PROTOCOL)

    # A checked-out tree must never export under the unattested marker.
    assert bundle.source == BUNDLE_SOURCE_FILES
    assert bundle.file_count > len(_SOURCES)
    assert len(bundle.digest) == 64
    assert public_export_worker_bundle(PROTOCOL).digest == bundle.digest
    assert bundle.evidence()["digest"] == bundle.digest


def test_live_worker_source_change_invalidates_process_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long-running local controller must not reuse an older source digest."""

    fastapi = _worker_tree(tmp_path)
    monkeypatch.setattr(worker_bundle_module, "_REPOSITORY_ROOT", tmp_path)

    before = public_export_worker_bundle(PROTOCOL)
    fastapi.joinpath("workers", "public_svg_pptx_convert.py").write_text(
        "# converter 2\n", encoding="utf-8"
    )
    after = public_export_worker_bundle(PROTOCOL)

    assert after.digest != before.digest


def _identity(exporter_digest: str) -> CanonicalExportIdentity:
    """One export identity that differs only in the worker bundle digest."""

    owner_id = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
    tenant_id = "tenant-a"
    png_digest = sha256(b"png").hexdigest()
    checkpoint = SlideCheckpointArtifact(
        slide_index=0,
        artifact=ArtifactRef(
            artifact_id=uuid.UUID("00000000-0000-4000-8000-0000000000a2"),
            owner_id=owner_id,
            tenant_id=tenant_id,
            kind="slide-png",
            sha256=png_digest,
            size_bytes=64,
            media_type="image/png",
            storage_key=artifact_storage_key(
                owner_id=owner_id, tenant_id=tenant_id, digest=png_digest
            ),
            created_by_operation_id=uuid.UUID("00000000-0000-4000-8000-0000000000a3"),
            retention_class="checkpoint",
        ),
        validation_digest=sha256(b"validation").hexdigest(),
    )
    return CanonicalExportIdentity.build(
        owner_id=owner_id,
        tenant_id=tenant_id,
        presentation_id=uuid.UUID("00000000-0000-4000-8000-0000000000a4"),
        source_revision=7,
        export_format="pptx",
        export_mode="editable-hybrid",
        render_adapter="public-isolated-hybrid",
        exporter_version=PROTOCOL,
        evidence=ExportEvidenceDigests(
            scene_digest=sha256(b"scene").hexdigest(),
            semantic_digest=sha256(b"semantic").hexdigest(),
            quality_digest=sha256(b"quality").hexdigest(),
            exporter_digest=exporter_digest,
            font_manifest_digest=sha256(b"fonts").hexdigest(),
        ),
        slides=(checkpoint,),
    )


def test_a_worker_code_change_yields_a_re_exportable_identity(tmp_path: Path) -> None:
    """A fixed worker must not be locked out by a cached successful identity."""

    fastapi = _worker_tree(tmp_path)
    before = _identity(
        compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path).digest
    )
    fastapi.joinpath("workers", "public_svg_export_worker.py").write_text(
        "# worker 2\n", encoding="utf-8"
    )
    after = _identity(
        compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path).digest
    )

    assert after.canonical_digest != before.canonical_digest
    # Every column of `uq_public_export_identity` except the exporter digest is
    # unchanged, so the digest alone has to carry the split.
    assert after.evidence.exporter_digest != before.evidence.exporter_digest


def test_unchanged_worker_code_keeps_the_reusable_identity(tmp_path: Path) -> None:
    _worker_tree(tmp_path)
    first = _identity(
        compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path).digest
    )
    second = _identity(
        compute_public_export_worker_bundle(PROTOCOL, repository_root=tmp_path).digest
    )

    assert first.canonical_digest == second.canonical_digest
