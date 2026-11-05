"""The converter digest alias is valid only because the executed converter bytes are identical (D5 C)."""
import hashlib
import json
import shutil
import uuid

import pytest

import workers.public_svg_converter_aliases as aliases
import workers.public_svg_pptx_convert as convert
from services.svg_final_pptx_review import final_render_current
from utils.hashing import canonical_json_sha256
from workers import public_svg_pptx_render


def _vendor_files_on_disk(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
            and path.name != "MANIFEST.json"}


def _digest_over_manifest(tmp_path, monkeypatch, manifest_bytes):
    """Run the unchanged ``converter_digest()`` over the vendored tree with other manifest bytes."""
    vendor = tmp_path / "vendor"
    shutil.copytree(convert.VENDOR_ROOT, vendor, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (vendor / "MANIFEST.json").write_bytes(manifest_bytes)
    with monkeypatch.context() as patch:
        patch.setattr(convert, "VENDOR_ROOT", vendor)
        return convert.converter_digest()


def test_identity_mirror_matches_converter_source_digest():
    current = hashlib.sha256((convert.VENDOR_ROOT / "MANIFEST.json").read_bytes()).hexdigest()
    assert aliases.converter_identity_digest(current) == convert.converter_source_digest()


@pytest.mark.parametrize("sha256", sorted(aliases.SUPERSEDED_VENDOR_MANIFESTS))
def test_superseded_manifest_describes_byte_identical_executed_converter(sha256, tmp_path, monkeypatch):
    kept = (aliases.SUPERSEDED_MANIFEST_ROOT / f"{sha256}.json").read_bytes()
    assert hashlib.sha256(kept).hexdigest() == sha256
    superseded = aliases.superseded_manifest(sha256)
    current = json.loads((convert.VENDOR_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    # Every file the converter can execute is covered by both manifests ...
    on_disk = _vendor_files_on_disk(convert.VENDOR_ROOT)
    assert set(superseded["files"]) == set(current["files"]) == on_disk
    # ... and both name the bytes that are on disk now.
    for name in sorted(on_disk):
        actual = hashlib.sha256((convert.VENDOR_ROOT / name).read_bytes()).hexdigest()
        assert superseded["files"][name] == current["files"][name] == actual, name
    # Only provenance metadata differs.
    differing = {key for key in set(superseded) | set(current) if superseded.get(key) != current.get(key)}
    assert differing <= aliases.PROVENANCE_KEYS
    assert aliases.executed_identity_equal(superseded, current)
    # The alias accepts exactly what the unchanged converter_digest() produced over the old manifest.
    old_digest = _digest_over_manifest(tmp_path, monkeypatch, kept)
    assert old_digest == aliases.converter_identity_digest(sha256)
    assert old_digest != convert.converter_digest()
    assert aliases.accepted_converter_digests() == {convert.converter_digest(), old_digest}


def test_alias_stops_applying_when_executed_vendor_bytes_change(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    shutil.copytree(convert.VENDOR_ROOT, vendor, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    changed = vendor / "svg_to_pptx" / "conversion_context.py"
    changed.write_bytes(changed.read_bytes() + b"\n# changed\n")
    manifest = json.loads((vendor / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest["files"]["svg_to_pptx/conversion_context.py"] = hashlib.sha256(changed.read_bytes()).hexdigest()
    (vendor / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(convert, "VENDOR_ROOT", vendor)
    old_digests = {aliases.converter_identity_digest(sha256) for sha256 in aliases.SUPERSEDED_VENDOR_MANIFESTS}
    assert aliases.accepted_converter_digests() == {convert.converter_digest()}
    assert not any(aliases.converter_digest_accepted(value) for value in old_digests)


def test_alias_refuses_a_tampered_superseded_manifest(tmp_path, monkeypatch):
    sha256 = next(iter(aliases.SUPERSEDED_VENDOR_MANIFESTS))
    kept = (aliases.SUPERSEDED_MANIFEST_ROOT / f"{sha256}.json").read_bytes()
    (tmp_path / f"{sha256}.json").write_bytes(kept.replace(b"slide-master-svg-converter", b"slide-master-svg-convertor"))
    monkeypatch.setattr(aliases, "SUPERSEDED_MANIFEST_ROOT", tmp_path)
    with pytest.raises(ValueError, match="svg_converter_alias_manifest_integrity_failed"):
        aliases.accepted_converter_digests()


def test_unknown_digests_are_not_accepted():
    assert not aliases.converter_digest_accepted("0" * 64)
    assert not aliases.converter_digest_accepted(None)
    assert aliases.converter_digest_accepted(convert.converter_digest())


def _final_render_proof(converter_digest_value):
    proof = dict(
        schemaVersion=public_svg_pptx_render.SCHEMA, status="measured",
        artifactDigest="sha256:" + "a" * 64, artifactDigestAfter="sha256:" + "a" * 64,
        hashInvariant=True, openReadOnly=True, openWithWindow=False,
        operationId=str(uuid.UUID(int=1)), presentationId=str(uuid.UUID(int=2)), sourceRevision=3,
        sourceDigests=["b" * 64], ownerId=str(uuid.UUID(int=4)), tenantId="tenant",
        converterDigest=converter_digest_value, checkerCodeDigest=public_svg_pptx_render.checker_digest(),
        availablePhysicalMemoryBytes=public_svg_pptx_render.MINIMUM_AVAILABLE_MEMORY,
        pageCount=1, pages=[dict(index=0, pngSha256="c" * 64)])
    proof["evidenceDigest"] = "sha256:" + canonical_json_sha256(proof)
    return proof


def _verifies(proof):
    return final_render_current(proof, artifact_digest="sha256:" + "a" * 64, operation_id=uuid.UUID(int=1),
                                presentation_id=uuid.UUID(int=2), source_revision=3, source_digests=["b" * 64],
                                owner_id=uuid.UUID(int=4), tenant_id="tenant")


def test_stored_final_render_proof_with_the_superseded_digest_still_verifies(tmp_path, monkeypatch):
    sha256 = next(iter(aliases.SUPERSEDED_VENDOR_MANIFESTS))
    kept = (aliases.SUPERSEDED_MANIFEST_ROOT / f"{sha256}.json").read_bytes()
    old_digest = _digest_over_manifest(tmp_path, monkeypatch, kept)
    assert _verifies(_final_render_proof(convert.converter_digest()))
    assert _verifies(_final_render_proof(old_digest))
    assert not _verifies(_final_render_proof("0" * 64))
