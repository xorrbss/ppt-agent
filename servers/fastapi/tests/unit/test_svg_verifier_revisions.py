"""Explicit verifier revisions (docs/explicit-verifier-revisions-design-20261105.md).

A source change of a pinned verifier fails the first test until someone decides:
re-pin the new source state under the current revision when the change does not
alter behaviour, or bump the revision when it does.
"""
import dataclasses
import hashlib
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Callable

import pytest

import services.svg_export_whole_page_verifier as whole_page
import services.svg_partial_recovery as recovery
import services.verifier_revisions as revisions
import workers.public_svg_pptx_convert as convert
from services import svg_common_header_export_evidence as header_evidence
from utils.code_file_digest import clear_code_file_digests
from workers.public_svg_converter_aliases import converter_digest_accepted

FASTAPI_ROOT = Path(__file__).resolve().parents[2]

_DECISION = (
    "{name}: source state {key} is not a reviewed state of {revision}. Decide in "
    "services/verifier_revisions.py: if behaviour is unchanged, add this key to the "
    "ledger's `reviewed` table under {revision} (stored evidence keeps validating); if "
    "behaviour changed, bump `current` and list the key under the new revision (stored "
    "evidence stops validating)."
)


@dataclasses.dataclass(frozen=True)
class Verifier:
    ledger_attribute: str
    state: Callable
    #: The resolved identity that evidence carries.
    identity: Callable
    #: The raw identity formula over ``{source key: SHA-256}`` on this checkout.
    formula: Callable
    #: The identity evidence stored on an LF checkout before the migration carries.
    stored: str
    anchor_commit: str
    #: ``{source key: path}`` of every file the identity reads.
    inventory: Callable
    #: The formula as the reviewed key computes it (checkout-independent path text).
    reviewed_formula: Callable | None = None
    #: The formula as a Windows checkout computes it.
    windows_formula: Callable | None = None
    #: Simulates a Windows checkout's path text for the duration of a test.
    windows_paths: Callable | None = None

    @property
    def ledger(self):
        return getattr(revisions, self.ledger_attribute)


def _converter_inventory():
    return {"vendor/slide_master/MANIFEST.json": convert.VENDOR_ROOT / "MANIFEST.json",
            **{path.relative_to(FASTAPI_ROOT).as_posix(): path for path in convert._PROJECTION_ADAPTER_FILES}}


def _named(names):
    return lambda: {name: FASTAPI_ROOT / name for name in names}


def _windows_path_text(monkeypatch):
    monkeypatch.setattr(recovery, "_native_path_text", lambda name: str(PureWindowsPath(name)))


VERIFIERS = {
    "converter": Verifier(
        "CONVERTER_LEDGER", convert.converter_source_state, convert.converter_digest,
        convert.converter_identity, "52b87dbae3b651a8a2eba3b1bceac779f375d4ab94ed8f030af8adde933b88a8",
        "9d389ba80", _converter_inventory),
    "common-header": Verifier(
        "COMMON_HEADER_VERIFIER_LEDGER", header_evidence.common_header_verifier_source_state,
        header_evidence.common_header_verifier_digest, header_evidence.digest,
        "sha256:86232705ab2c98b9e19f18193e5ce4fadafe969f6d7de892cbc956899be263e0",
        "9d389ba80", _named(header_evidence.COMMON_HEADER_VERIFIER_FILES)),
    "whole-page": Verifier(
        "WHOLE_PAGE_VERIFIER_LEDGER", whole_page.whole_page_verifier_source_state,
        whole_page.whole_page_verifier_digest, whole_page.whole_page_verifier_identity,
        "aa559f555120a1600b4a011ec2e0e00b013074649adad43703d5d649e2cb2469",
        "49e529d6b", _named(whole_page._FILES)),
    "partial-recovery-code": Verifier(
        "PARTIAL_RECOVERY_CODE_LEDGER", recovery.code_source_state, recovery._code_digest,
        recovery.code_identity, "91280a73f656aa9b0d49d8701f159aa8474d6228d4fa63e40171a7b6fe5ade1a",
        "49e529d6b", _named(recovery.CODE_IDENTITY_FILES),
        reviewed_formula=recovery._reviewed_code_identity,
        windows_formula=lambda hashes: recovery.code_identity(
            hashes, path_text=lambda name: str(PureWindowsPath(name))),
        windows_paths=_windows_path_text),
    "page-render-code": Verifier(
        "PAGE_RENDER_CODE_LEDGER", recovery.page_render_source_state, recovery._page_render_code_digest,
        recovery.page_render_identity, "80cbd150c5d4f35086749c0faefadcd15181f3a74019c5cf43247bbb70b8ce36",
        "49e529d6b", _named(recovery.PAGE_RENDER_CODE_FILES)),
}
NAMES = sorted(VERIFIERS)


@pytest.fixture(autouse=True)
def _fresh_code_file_digests():
    """Partial recovery caches file digests per process; simulated bytes need a fresh read."""
    clear_code_file_digests()
    yield
    clear_code_file_digests()


@pytest.mark.parametrize("name", NAMES)
def test_source_is_a_reviewed_state_of_the_current_revision(name):
    verifier = VERIFIERS[name]
    ledger, state = verifier.ledger, verifier.state()
    assert ledger.revision_of(state) == ledger.current, _DECISION.format(
        name=ledger.name, key=state.reviewed_key, revision=ledger.current)


@pytest.mark.parametrize("name", NAMES)
def test_migration_keeps_the_stored_identities_byte_for_byte(name):
    verifier = VERIFIERS[name]
    assert verifier.ledger.current == verifier.ledger.name + "/1"
    assert verifier.identity() == verifier.stored
    if name == "converter":
        assert converter_digest_accepted(verifier.stored)


def test_partial_recovery_code_identity_is_the_published_recipe():
    """Revision 1 is the pre-migration formula: the base list digest plus the final-selected sources."""
    root = FASTAPI_ROOT / "services"
    sources = ["svg_final_selected_continuation.py", "svg_authoring_profile.py",
               "generation_core/drivers/svg/quality.py", "generation_core/drivers/svg/quality_recovery.py",
               "generation_core/drivers/svg/collect.py"]
    expected = recovery.digest({"base": recovery._base_code_digest(), "final_selected_sources": {
        str(Path(name)): hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}})
    assert recovery.code_source_state().raw == expected
    assert recovery.page_render_source_state().raw == recovery._base_code_digest(recovery.PAGE_RENDER_CODE_FILES)


@pytest.mark.parametrize("name", NAMES)
def test_lf_checkout_reviewed_key_is_the_raw_digest(name):
    verifier = VERIFIERS[name]
    if any(b"\r\n" in path.read_bytes() for path in verifier.inventory().values()):
        pytest.skip("CRLF checkout")
    if verifier.windows_paths is not None and str(Path("a/b")) != "a/b":
        pytest.skip("Windows path text")
    current = verifier.state()
    assert current.reviewed_key == current.raw


def test_partial_recovery_reviewed_key_does_not_depend_on_path_text(monkeypatch):
    posix = recovery.code_source_state()
    _windows_path_text(monkeypatch)
    windows = recovery.code_source_state()
    assert windows.reviewed_key == posix.reviewed_key
    assert windows.raw != posix.raw and windows.anchor != posix.anchor


def _with_changed_bytes(monkeypatch, target):
    original = Path.read_bytes

    def changed(path):
        data = original(path)
        return data + b"\n# unreviewed edit\n" if Path(path).resolve() == target.resolve() else data

    monkeypatch.setattr(Path, "read_bytes", changed)


@pytest.mark.parametrize("name", NAMES)
def test_unreviewed_source_keeps_its_raw_digest(monkeypatch, name):
    verifier = VERIFIERS[name]
    _with_changed_bytes(monkeypatch, next(iter(verifier.inventory().values())))
    state = verifier.state()
    assert verifier.ledger.revision_of(state) is None
    assert verifier.identity() == state.raw != verifier.stored
    if name == "converter":
        assert state.raw == convert.converter_source_digest()
        assert not converter_digest_accepted(verifier.stored)
    if name == "whole-page":
        assert state.raw == whole_page.whole_page_verifier_source_digest()


def test_partial_recovery_authority_edit_keeps_the_page_render_identity(monkeypatch):
    """Authority files are outside the page-render identity: editing one re-pins only the code ledger."""
    assert "services/svg_partial_recovery.py" not in recovery.PAGE_RENDER_CODE_FILES
    _with_changed_bytes(monkeypatch, Path(recovery.__file__))
    assert revisions.PARTIAL_RECOVERY_CODE_LEDGER.revision_of(recovery.code_source_state()) is None
    assert recovery._page_render_code_digest() == VERIFIERS["page-render-code"].stored


def test_vendored_converter_bytes_are_still_verified_on_every_call(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor"
    shutil.copytree(convert.VENDOR_ROOT, vendor, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    changed = vendor / "svg_to_pptx" / "conversion_context.py"
    changed.write_bytes(changed.read_bytes() + b"\n# tampered\n")
    monkeypatch.setattr(convert, "VENDOR_ROOT", vendor)
    with pytest.raises(ValueError, match="svg_converter_integrity_failed"):
        convert.converter_digest()


@pytest.mark.parametrize("name", NAMES)
def test_a_revision_bump_retires_stored_evidence(monkeypatch, name):
    verifier = VERIFIERS[name]
    ledger = verifier.ledger
    bumped_revision = ledger.name + "/2"
    bumped = dataclasses.replace(ledger, current=bumped_revision,
                                 reviewed={**ledger.reviewed, verifier.state().reviewed_key: bumped_revision})
    monkeypatch.setattr(revisions, verifier.ledger_attribute, bumped)
    identity = verifier.identity()
    assert identity == bumped.derived_identity(bumped_revision) != verifier.stored
    assert identity.startswith(ledger.prefix) and len(identity) == len(ledger.prefix) + 64
    if name == "converter":
        assert converter_digest_accepted(identity) and not converter_digest_accepted(verifier.stored)


@pytest.mark.parametrize("name", NAMES)
def test_ledger_is_well_formed(name):
    verifier = VERIFIERS[name]
    ledger = verifier.ledger
    named = {ledger.current, ledger.anchor_revision, *ledger.reviewed.values()}
    assert all(revision.startswith(ledger.name + "/") for revision in named)
    assert ledger.current in ledger.reviewed.values()
    for key in ledger.reviewed:
        assert key.startswith(ledger.prefix) and len(key) == len(ledger.prefix) + 64
        assert all(char in "0123456789abcdef" for char in key[len(ledger.prefix):])
    assert set(verifier.inventory()) <= set(ledger.anchor_files)
    # The anchor's LF form is the stored identity and a reviewed state of its own revision.
    lf_anchor = (verifier.reviewed_formula or verifier.formula)(
        {key: entry.lf for key, entry in ledger.anchor_files.items()})
    assert lf_anchor == verifier.stored and ledger.reviewed[lf_anchor] == ledger.anchor_revision
    # A derived identity can never collide with a source state or the anchor.
    derived = ledger.derived_identity(ledger.name + "/999")
    assert derived not in ledger.reviewed and derived != lf_anchor


def test_ledger_names_are_unique():
    assert len({ledger.name for ledger in revisions.LEDGERS}) == len(revisions.LEDGERS)


@pytest.mark.parametrize("name", NAMES)
def test_anchor_crlf_hashes_match_the_unchanged_files(name):
    verifier = VERIFIERS[name]
    for key, path in verifier.inventory().items():
        entry = verifier.ledger.anchor_files.get(key)
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


@pytest.mark.parametrize("name", NAMES)
def test_windows_autocrlf_checkout_keeps_its_stored_identity(name, monkeypatch):
    """Evidence stored on a ``core.autocrlf=true`` checkout carries the CRLF digest; it keeps validating."""
    verifier = VERIFIERS[name]
    ledger = verifier.ledger
    inventory = verifier.inventory()
    relative = {key: path.resolve().relative_to(FASTAPI_ROOT).as_posix() for key, path in inventory.items()}
    ledger_file = Path(revisions.__file__).resolve().relative_to(FASTAPI_ROOT).as_posix()
    exact = _unconverted_paths([*relative.values(), ledger_file])
    assert ledger_file not in exact
    assert {key for key in inventory if ledger.anchor_files[key].crlf is None} == {
        key for key, path in relative.items() if path in exact}

    def checkout(path, data):
        return data if path in exact else data.replace(b"\n", b"\r\n")

    # The pre-migration identity of the anchor commit, as such a checkout computed it.
    formula = verifier.windows_formula or verifier.formula
    windows_stored = formula({key: hashlib.sha256(checkout(path, _git("show", f"{verifier.anchor_commit}:./{path}"))).hexdigest()
                              for key, path in relative.items()})
    assert windows_stored != verifier.stored

    original = Path.read_bytes
    converted = {FASTAPI_ROOT / path for path in [*relative.values(), ledger_file] if path not in exact}

    def crlf_checkout(path):
        data = original(path)
        return data.replace(b"\n", b"\r\n") if Path(path).resolve() in converted else data

    monkeypatch.setattr(Path, "read_bytes", crlf_checkout)
    if verifier.windows_paths is not None:
        verifier.windows_paths(monkeypatch)
    clear_code_file_digests()
    assert verifier.identity() == windows_stored
    assert ledger.revision_of(verifier.state()) == ledger.current
