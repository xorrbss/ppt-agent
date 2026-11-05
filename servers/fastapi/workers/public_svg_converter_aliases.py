"""Reviewed converter digest aliases (owner decision D5, option C, 2026-11-05).

``converter_digest()`` (``workers/public_svg_pptx_convert.py``) hashes the
exact bytes of the vendored ``MANIFEST.json`` together with the adapter files.
A manifest rebuild that changes only provenance metadata -- patch notes, patch
file hashes, key order, line endings -- therefore changes the digest although
the converter code that executes is byte-identical, and every stored proof that
pins ``converterDigest`` would stop verifying.

``SUPERSEDED_VENDOR_MANIFESTS`` lists each reviewed superseded manifest by the
SHA-256 of its exact bytes; the bytes are kept in
``svg_converter_superseded_manifests/<sha256>.json``. A proof carrying the
digest that ``converter_digest()`` computed over a superseded manifest is
accepted only while:

* the kept file still hashes to its registered SHA-256;
* its ``files`` map (every vendored file and its SHA-256) equals the current
  manifest's, and ``converter_digest()`` has just verified the vendored bytes
  against the current manifest -- so the superseded manifest describes exactly
  the bytes that execute now;
* the identity fields that are not provenance metadata (name, version,
  commit, source, license) are equal.

The adapter files are hashed with their current bytes on both sides and are
never aliased: an adapter change still invalidates older proofs. A vendored
code change makes the ``files`` maps differ, so the alias stops applying by
itself; ``tests/unit/test_svg_converter_digest_aliases.py`` then fails until the
inert entry is removed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: superseded vendor MANIFEST.json SHA-256 -> reason it was superseded.
SUPERSEDED_VENDOR_MANIFESTS = {
    "9ab2ba71a6386d6d52b80fb11a53c3af3d9654df74fa30e5a1f19192975c9a16": (
        "E3 2026-11-05: rebuilt by compatibility/slide-master/rebuild.py after the "
        "chart-furniture.16 and native-fixed-text.1 patch files were recorded; "
        "only presentonPatches, patches and the serialization changed"
    ),
}
SUPERSEDED_MANIFEST_ROOT = Path(__file__).resolve().parent / "svg_converter_superseded_manifests"
#: Manifest keys that execute nothing and may differ between aliased manifests.
PROVENANCE_KEYS = frozenset({"presentonPatches", "patches"})


def converter_identity_digest(vendor_manifest_sha256: str) -> str:
    """``converter_source_digest()`` over a vendor manifest with the given SHA-256.

    It mirrors the source identity built by ``converter_source_digest()`` (pinned
    by a test) and reads the adapter files' current bytes. Such a raw digest is
    not a reviewed revision state (``services/verifier_revisions``), so
    ``converter_digest()`` itself returns it unchanged for that manifest.
    """
    from workers.public_svg_pptx_convert import _PROJECTION_ADAPTER_FILES

    root = Path(__file__).resolve().parents[1]
    identity = {
        "schema": "svg-export-adapter-projection/v1",
        "vendorManifestSha256": vendor_manifest_sha256,
        "adapterSha256": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in _PROJECTION_ADAPTER_FILES
        },
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def superseded_manifest(sha256: str) -> dict:
    """The kept superseded manifest, refused unless its bytes match the registry."""
    if sha256 not in SUPERSEDED_VENDOR_MANIFESTS:
        raise ValueError("svg_converter_alias_unregistered")
    payload = (SUPERSEDED_MANIFEST_ROOT / f"{sha256}.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise ValueError("svg_converter_alias_manifest_integrity_failed")
    return json.loads(payload)


def executed_identity_equal(superseded: dict, current: dict) -> bool:
    """Two manifests describe the same executed vendor bytes and identity fields."""
    keys = (set(superseded) | set(current)) - PROVENANCE_KEYS
    return isinstance(current.get("files"), dict) and all(
        superseded.get(key) == current.get(key) for key in keys)


def accepted_converter_digests() -> frozenset[str]:
    """The current converter digest plus every live, identity-backed alias."""
    from workers.public_svg_pptx_convert import VENDOR_ROOT, converter_digest

    accepted = {converter_digest()}  # verifies the vendored bytes first
    current = json.loads((VENDOR_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    for sha256 in SUPERSEDED_VENDOR_MANIFESTS:
        if executed_identity_equal(superseded_manifest(sha256), current):
            accepted.add(converter_identity_digest(sha256))
    return frozenset(accepted)


def converter_digest_accepted(value) -> bool:
    """Whether a stored ``converterDigest`` identifies the converter bytes that run now."""
    return isinstance(value, str) and value in accepted_converter_digests()


__all__ = [
    "PROVENANCE_KEYS",
    "SUPERSEDED_MANIFEST_ROOT",
    "SUPERSEDED_VENDOR_MANIFESTS",
    "accepted_converter_digests",
    "converter_digest_accepted",
    "converter_identity_digest",
    "executed_identity_equal",
    "superseded_manifest",
]
