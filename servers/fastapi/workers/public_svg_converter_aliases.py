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

Raw aliases hash the current adapter bytes. A reviewed execution state of the
anchor revision also accepts the historical alias reconstructed from the
ledger's anchor files, using this checkout's LF/CRLF conventions. An unreviewed
adapter change or a later execution revision cannot inherit that alias. A
vendored code change makes the ``files`` maps differ, so the alias stops
applying by itself.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: superseded vendor MANIFEST.json SHA-256 -> reason it was superseded.
#: Empty since 2026-11-08: the E3 manifest ``9ab2ba71...`` was voided when
#: ``b392c58f7`` patched the executed vendored ``chart_data.py`` (see the README in
#: ``svg_converter_superseded_manifests``).
SUPERSEDED_VENDOR_MANIFESTS: dict[str, str] = {}
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


def _historical_converter_identity_digest(vendor_manifest_sha256: str) -> str | None:
    """Reconstruct an anchor alias only for that reviewed execution revision.

    Source inventories may grow after the anchor: the ledger keeps the original
    anchor preimage while requiring the complete current inventory to be reviewed.
    Its source-state builder also retains each anchor file's checkout convention.
    """
    from services.verifier_revisions import CONVERTER_LEDGER
    from workers.public_svg_pptx_convert import (
        _PROJECTION_ADAPTER_FILES, _VENDOR_MANIFEST_KEY, VENDOR_ROOT,
        converter_identity, converter_source_state,
    )

    if (CONVERTER_LEDGER.current != CONVERTER_LEDGER.anchor_revision
            or CONVERTER_LEDGER.revision_of(converter_source_state()) != CONVERTER_LEDGER.current):
        return None
    root = Path(__file__).resolve().parents[1]
    sources = {_VENDOR_MANIFEST_KEY: VENDOR_ROOT / "MANIFEST.json",
               **{path.relative_to(root).as_posix(): path for path in _PROJECTION_ADAPTER_FILES}}
    return CONVERTER_LEDGER.source_state(
        sources, lambda hashes: converter_identity({**hashes, _VENDOR_MANIFEST_KEY: vendor_manifest_sha256}),
    ).anchor


def accepted_converter_digests() -> frozenset[str]:
    """The current converter digest plus every live, identity-backed alias."""
    from workers.public_svg_pptx_convert import VENDOR_ROOT, converter_digest

    accepted = {converter_digest()}  # verifies the vendored bytes first
    current = json.loads((VENDOR_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    for sha256 in SUPERSEDED_VENDOR_MANIFESTS:
        if executed_identity_equal(superseded_manifest(sha256), current):
            accepted.add(converter_identity_digest(sha256))
            historical = _historical_converter_identity_digest(sha256)
            if historical is not None:
                accepted.add(historical)
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
