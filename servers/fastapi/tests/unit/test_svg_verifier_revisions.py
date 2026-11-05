"""Explicit verifier revisions (docs/explicit-verifier-revisions-design-20261105.md).

A source change of a pinned verifier fails the first two tests until someone
decides: re-pin the new source state under the current revision when the change
does not alter behaviour, or bump the revision when it does.
"""
import dataclasses
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

import services.verifier_revisions as revisions
import workers.public_svg_pptx_convert as convert
from services import svg_common_header_export_evidence as header_evidence
from workers.public_svg_converter_aliases import converter_digest_accepted

FASTAPI_ROOT = Path(__file__).resolve().parents[2]
ANCHOR_COMMIT = "9d389ba80"
#: The identities that evidence stored on an LF checkout before the migration carries.
STORED_CONVERTER_DIGEST = "52b87dbae3b651a8a2eba3b1bceac779f375d4ab94ed8f030af8adde933b88a8"
STORED_COMMON_HEADER_VERIFIER_DIGEST = "sha256:86232705ab2c98b9e19f18193e5ce4fadafe969f6d7de892cbc956899be263e0"

_DECISION = (
    "{name}: source state {key} is not a reviewed state of {revision}. Decide in "
    "services/verifier_revisions.py: if behaviour is unchanged, add this key to the "
    "ledger's `reviewed` table under {revision} (stored evidence keeps validating); if "
    "behaviour changed, bump `current` and list the key under the new revision (stored "
    "evidence stops validating)."
)

VERIFIERS = {
    "converter": (lambda: revisions.CONVERTER_LEDGER, convert.converter_source_state,
                  convert.converter_digest, convert.converter_identity, STORED_CONVERTER_DIGEST),
    "common-header": (lambda: revisions.COMMON_HEADER_VERIFIER_LEDGER,
                      header_evidence.common_header_verifier_source_state,
                      header_evidence.common_header_verifier_digest, header_evidence.digest,
                      STORED_COMMON_HEADER_VERIFIER_DIGEST),
}


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
def test_source_is_a_reviewed_state_of_the_current_revision(verifier):
    ledger, state, *_ = VERIFIERS[verifier]
    ledger = ledger()
    key = state().reviewed_key
    assert ledger.revision_of(state()) == ledger.current, _DECISION.format(
        name=ledger.name, key=key, revision=ledger.current)


def test_migration_keeps_the_stored_identities_byte_for_byte():
    assert revisions.CONVERTER_EXECUTION_REVISION == "svg-pptx-converter/1"
    assert revisions.COMMON_HEADER_VERIFIER_REVISION == "svg-common-header-verifier/1"
    assert convert.converter_digest() == STORED_CONVERTER_DIGEST
    assert header_evidence.common_header_verifier_digest() == STORED_COMMON_HEADER_VERIFIER_DIGEST
    assert converter_digest_accepted(STORED_CONVERTER_DIGEST)


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
def test_lf_checkout_reviewed_key_is_the_raw_digest(verifier):
    _ledger, state, *_ = VERIFIERS[verifier]
    current = state()
    if any(b"\r\n" in path.read_bytes() for path in _inventory(verifier).values()):
        pytest.skip("CRLF checkout")
    assert current.reviewed_key == current.raw


def _inventory(verifier):
    if verifier == "converter":
        return {"vendor/slide_master/MANIFEST.json": convert.VENDOR_ROOT / "MANIFEST.json",
                **{path.relative_to(FASTAPI_ROOT).as_posix(): path for path in convert._PROJECTION_ADAPTER_FILES}}
    return {name: FASTAPI_ROOT / name for name in header_evidence.COMMON_HEADER_VERIFIER_FILES}


def _with_changed_bytes(monkeypatch, target):
    original = Path.read_bytes

    def changed(path):
        data = original(path)
        return data + b"\n# unreviewed edit\n" if Path(path).resolve() == target.resolve() else data

    monkeypatch.setattr(Path, "read_bytes", changed)


def test_unreviewed_converter_source_keeps_its_raw_digest(monkeypatch):
    _with_changed_bytes(monkeypatch, convert._PROJECTION_ADAPTER_FILES[0])
    state = convert.converter_source_state()
    assert revisions.CONVERTER_LEDGER.revision_of(state) is None
    assert convert.converter_digest() == state.raw == convert.converter_source_digest()
    assert state.raw != STORED_CONVERTER_DIGEST
    assert not converter_digest_accepted(STORED_CONVERTER_DIGEST)


def test_unreviewed_common_header_verifier_source_keeps_its_raw_digest(monkeypatch):
    _with_changed_bytes(monkeypatch, Path(header_evidence.__file__))
    state = header_evidence.common_header_verifier_source_state()
    assert revisions.COMMON_HEADER_VERIFIER_LEDGER.revision_of(state) is None
    assert header_evidence.common_header_verifier_digest() == state.raw != STORED_COMMON_HEADER_VERIFIER_DIGEST


def test_vendored_converter_bytes_are_still_verified_on_every_call(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    shutil.copytree(convert.VENDOR_ROOT, vendor, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    changed = vendor / "svg_to_pptx" / "conversion_context.py"
    changed.write_bytes(changed.read_bytes() + b"\n# tampered\n")
    monkeypatch.setattr(convert, "VENDOR_ROOT", vendor)
    with pytest.raises(ValueError, match="svg_converter_integrity_failed"):
        convert.converter_digest()


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
def test_a_revision_bump_retires_stored_evidence(monkeypatch, verifier):
    ledger, state, identity_of, _formula, stored = VERIFIERS[verifier]
    ledger = ledger()
    bumped_revision = ledger.name + "/2"
    bumped = dataclasses.replace(ledger, current=bumped_revision,
                                 reviewed={**ledger.reviewed, state().reviewed_key: bumped_revision})
    attribute = next(name for name in ("CONVERTER_LEDGER", "COMMON_HEADER_VERIFIER_LEDGER")
                     if getattr(revisions, name) is ledger)
    monkeypatch.setattr(revisions, attribute, bumped)
    identity = identity_of()
    assert identity == bumped.derived_identity(bumped_revision) != stored
    assert identity.startswith(ledger.prefix) and len(identity) == len(ledger.prefix) + 64
    if verifier == "converter":
        assert converter_digest_accepted(identity) and not converter_digest_accepted(stored)


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
def test_ledger_is_well_formed(verifier):
    ledger, _state, _identity, formula, stored = VERIFIERS[verifier]
    ledger = ledger()
    named = {ledger.current, ledger.anchor_revision, *ledger.reviewed.values()}
    assert all(revision.startswith(ledger.name + "/") for revision in named)
    assert ledger.current in ledger.reviewed.values()
    for key in ledger.reviewed:
        assert key.startswith(ledger.prefix) and len(key) == len(ledger.prefix) + 64
        assert all(char in "0123456789abcdef" for char in key[len(ledger.prefix):])
    # The anchor's LF form is the stored identity and a reviewed state of its own revision.
    lf_anchor = formula({key: entry.lf for key, entry in ledger.anchor_files.items()})
    assert lf_anchor == stored and ledger.reviewed[lf_anchor] == ledger.anchor_revision
    # A derived identity can never collide with a source state or the anchor.
    derived = ledger.derived_identity(ledger.name + "/999")
    assert derived not in ledger.reviewed and derived != lf_anchor


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
def test_anchor_crlf_hashes_match_the_unchanged_files(verifier):
    ledger = VERIFIERS[verifier][0]()
    for key, path in _inventory(verifier).items():
        entry = ledger.anchor_files.get(key)
        data = path.read_bytes().replace(b"\r\n", b"\n")
        if entry is None or entry.crlf is None or hashlib.sha256(data).hexdigest() != entry.lf:
            continue
        assert entry.crlf == hashlib.sha256(data.replace(b"\n", b"\r\n")).hexdigest(), key


def _git(*args):
    try:
        result = subprocess.run(["git", *args], cwd=FASTAPI_ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git or the anchor commit is not available")
    return result.stdout


def _unconverted_paths(paths):
    """Paths Git checks out byte-exact (``-text``)."""
    output = _git("check-attr", "text", "--", *paths).decode()
    return {line.split(": ")[0] for line in output.splitlines() if line.endswith(": unset")}


@pytest.mark.parametrize("verifier", sorted(VERIFIERS))
def test_windows_autocrlf_checkout_keeps_its_stored_identity(verifier, monkeypatch):
    """Evidence stored on a ``core.autocrlf=true`` checkout carries the CRLF digest; it keeps validating."""
    ledger, _state, identity_of, formula, _stored = VERIFIERS[verifier]
    ledger = ledger()
    inventory = _inventory(verifier)
    relative = {key: path.resolve().relative_to(FASTAPI_ROOT).as_posix() for key, path in inventory.items()}
    ledger_file = Path(revisions.__file__).resolve().relative_to(FASTAPI_ROOT).as_posix()
    exact = _unconverted_paths([*relative.values(), ledger_file])
    assert ledger_file not in exact
    assert {key for key, entry in ledger.anchor_files.items() if entry.crlf is None} == {
        key for key, path in relative.items() if path in exact}

    def checkout(path, data):
        return data if path in exact else data.replace(b"\n", b"\r\n")

    # The pre-migration identity of the anchor commit, as such a checkout computed it.
    windows_stored = formula({key: hashlib.sha256(checkout(path, _git("show", f"{ANCHOR_COMMIT}:./{path}"))).hexdigest()
                              for key, path in relative.items()})
    assert windows_stored != VERIFIERS[verifier][4]

    original = Path.read_bytes
    converted = {FASTAPI_ROOT / path for path in [*relative.values(), ledger_file] if path not in exact}

    def crlf_checkout(path):
        data = original(path)
        return data.replace(b"\n", b"\r\n") if Path(path).resolve() in converted else data

    monkeypatch.setattr(Path, "read_bytes", crlf_checkout)
    assert identity_of() == windows_stored
