"""The converter digest alias is valid only because the executed converter bytes are identical (D5 C)."""
import dataclasses
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

import workers.public_svg_converter_aliases as aliases
import workers.public_svg_pptx_convert as convert
import services.verifier_revisions as revisions
from services.svg_final_pptx_review import final_render_current
from utils.hashing import canonical_json_sha256
from workers import public_svg_pptx_render


# Git 9d389ba80's adapters with the retained superseded manifest. These fixed
# fixtures predate the revision migration; never generate them from current code.
_HISTORICAL_ALIASES = {
    False: "3c40f9dc2b4816796bfc21a016347c2bb352763efcf31616563ad56259d591e0",
    True: "cc4a2f5bc4dfb256e14bd63ca143424d578b43ca3668ae051021bbf0e1b4a00b",
}


def _reviewed_checkout(monkeypatch, crlf):
    """Review this test's source state, independently of pending ledger integration."""
    original = Path.read_bytes
    ledger = revisions.CONVERTER_LEDGER
    root = Path(convert.__file__).resolve().parents[1]
    adapters = {path.resolve(): ledger.anchor_files.get(path.relative_to(root).as_posix())
                for path in convert._PROJECTION_ADAPTER_FILES}

    def checkout(path):
        data = original(path)
        if Path(path).resolve() not in adapters:
            return data
        data = data.replace(b"\r\n", b"\n")
        entry = adapters[Path(path).resolve()]
        return data.replace(b"\n", b"\r\n") if crlf and entry is not None and entry.crlf else data

    monkeypatch.setattr(Path, "read_bytes", checkout)
    state = convert.converter_source_state()
    monkeypatch.setattr(revisions, "CONVERTER_LEDGER", dataclasses.replace(
        ledger, reviewed={**ledger.reviewed, state.reviewed_key: ledger.current}))


def test_voided_historical_aliases_are_refused():
    """b392c58f7 patched an executed vendored file, so the E3 alias stopped applying (D5)."""
    assert aliases.SUPERSEDED_VENDOR_MANIFESTS == {}
    for historical in _HISTORICAL_ALIASES.values():
        assert not aliases.converter_digest_accepted(historical)
        assert not _verifies(_final_render_proof(historical))


@pytest.fixture
def provenance_only_manifest(tmp_path, monkeypatch):
    """Register a superseded manifest that differs from the current one only in provenance."""
    current = json.loads((convert.VENDOR_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    superseded = {**current, "presentonPatches": [*current.get("presentonPatches", []), "provenance-only"]}
    kept = (json.dumps(superseded, indent=2) + "\n").encode("utf-8")
    sha256 = hashlib.sha256(kept).hexdigest()
    root = tmp_path / "superseded"
    root.mkdir()
    (root / f"{sha256}.json").write_bytes(kept)
    monkeypatch.setattr(aliases, "SUPERSEDED_MANIFEST_ROOT", root)
    monkeypatch.setattr(aliases, "SUPERSEDED_VENDOR_MANIFESTS", {sha256: "test: provenance-only rebuild"})
    return sha256, kept


@pytest.mark.parametrize("crlf", [False, True])
def test_historical_alias_survives_only_the_reviewed_anchor_execution(monkeypatch, crlf, provenance_only_manifest):
    sha256, _ = provenance_only_manifest
    _reviewed_checkout(monkeypatch, crlf)
    historical = aliases._historical_converter_identity_digest(sha256)
    assert historical is not None
    assert aliases.converter_digest_accepted(historical)
    assert _verifies(_final_render_proof(historical))

    ledger = revisions.CONVERTER_LEDGER
    state = convert.converter_source_state()
    bumped_revision = ledger.name + "/2"
    monkeypatch.setattr(revisions, "CONVERTER_LEDGER", dataclasses.replace(
        ledger, current=bumped_revision, reviewed={**ledger.reviewed, state.reviewed_key: bumped_revision}))
    assert not aliases.converter_digest_accepted(historical)


def test_unreviewed_subset_change_cannot_inherit_a_historical_alias(monkeypatch, provenance_only_manifest):
    sha256, _ = provenance_only_manifest
    _reviewed_checkout(monkeypatch, False)
    historical = aliases._historical_converter_identity_digest(sha256)
    assert aliases.converter_digest_accepted(historical)
    original = Path.read_bytes
    target = Path(convert.__file__).resolve().parents[1] / "services/svg_pptx_font_subset.py"

    def changed(path):
        data = original(path)
        return data + b"\n# unreviewed subset change\n" if Path(path).resolve() == target else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert not aliases.converter_digest_accepted(historical)


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


def test_superseded_manifest_describes_byte_identical_executed_converter(provenance_only_manifest, tmp_path,
                                                                         monkeypatch):
    sha256, kept = provenance_only_manifest
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
    expected = {convert.converter_digest(), old_digest}
    historical = aliases._historical_converter_identity_digest(sha256)
    if historical is not None:
        expected.add(historical)
    assert aliases.accepted_converter_digests() == expected


def test_alias_stops_applying_when_executed_vendor_bytes_change(provenance_only_manifest, tmp_path, monkeypatch):
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


def test_alias_refuses_a_tampered_superseded_manifest(provenance_only_manifest, tmp_path, monkeypatch):
    sha256, kept = provenance_only_manifest
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


def test_stored_final_render_proof_with_the_superseded_digest_still_verifies(provenance_only_manifest, tmp_path,
                                                                          monkeypatch):
    sha256, kept = provenance_only_manifest
    old_digest = _digest_over_manifest(tmp_path, monkeypatch, kept)
    assert _verifies(_final_render_proof(convert.converter_digest()))
    assert _verifies(_final_render_proof(old_digest))
    assert not _verifies(_final_render_proof("0" * 64))
