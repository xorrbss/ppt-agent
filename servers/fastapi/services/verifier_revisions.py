"""Explicit, reviewed verifier revisions for identities that used to be source-byte digests.

Several verifier identities were SHA-256 digests of their own source files, so a
comment edit or a pure refactor of any of those files silently invalidated every
stored proof that carried the digest. A ledger here replaces such an identity by
an explicit *execution revision*: the identity that evidence carries belongs to
the revision, and the source bytes only decide whether the code that runs is a
reviewed state of that revision.

* ``reviewed`` maps each reviewed source state to the revision it implements.
  The key is the verifier's own identity formula computed over LF-normalized
  bytes, so it is the same on a Linux checkout and on a Windows
  ``core.autocrlf=true`` checkout. A code change produces a key that is not
  listed; ``tests/unit/test_svg_verifier_revisions.py`` then fails and forces a
  decision: re-pin the key under the same revision when behaviour is unchanged
  (stored evidence keeps validating), or bump the revision when behaviour
  changed (stored evidence stops validating, as it must).
* The anchor keeps the migration byte-for-byte. The anchor revision's identity
  is the pre-migration digest: the identity formula over the anchor files
  exactly as this checkout holds them (LF, or CRLF for files that Git converts
  on this checkout), which is the value evidence stored on this machine before
  the migration carries. Later revisions get a derived identity that is the
  same on every checkout and that no source digest can equal.
* An unreviewed source state is never given a revision's identity: ``resolve``
  returns its raw source digest, exactly the pre-migration behaviour, so
  evidence produced by unreviewed code matches only that same code.

This module is deliberately outside every source-byte inventory: editing a ledger
must not change the digests it pins. It must keep Git's default text handling
(no ``-text`` attribute), because its own line endings tell how this checkout
converts files that are no longer on disk. Each entry is a reviewed decision;
add one only with the reason next to it.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from utils.hashing import canonical_json_sha256

LEDGER_SCHEMA = "verifier-revision/v1"

#: An identity formula: ``{source key: bare hex SHA-256 of its bytes} -> identity``.
IdentityFormula = Callable[[Mapping[str, str]], str]


@dataclass(frozen=True)
class AnchorFile:
    """SHA-256 of one anchor source file, as LF bytes and as a CRLF checkout."""

    lf: str
    #: ``None``: the path is checked out byte-exact on every OS (``-text``).
    crlf: str | None = None


@dataclass(frozen=True)
class SourceState:
    """One verifier's source as it is on disk now."""

    #: The identity formula over the bytes on disk: the pre-migration identity.
    raw: str
    #: The same formula over LF-normalized bytes: the ``reviewed`` table key.
    reviewed_key: str
    #: The formula over the anchor files as this checkout holds them.
    anchor: str


def _crlf_checkout(data: bytes | None) -> bool:
    probe = data if data is not None else Path(__file__).read_bytes()
    return b"\r\n" in probe


@dataclass(frozen=True)
class VerifierRevisionLedger:
    """One verifier's current revision, its anchor and its reviewed source states."""

    name: str
    current: str
    anchor_revision: str
    anchor_files: Mapping[str, AnchorFile]
    reviewed: Mapping[str, str]
    #: Prefix of the identity format the evidence field already uses ("" or "sha256:").
    prefix: str = ""

    def derived_identity(self, revision: str) -> str:
        """The checkout-independent identity of a revision after the anchor."""
        return self.prefix + canonical_json_sha256(
            {"schema": LEDGER_SCHEMA, "verifier": self.name, "revision": revision})

    def source_state(self, sources: Mapping[str, Path], formula: IdentityFormula) -> SourceState:
        """Read each source once and compute the raw, reviewed and anchor identities."""
        contents = {key: path.read_bytes() for key, path in sources.items()}
        anchor = {}
        for key, entry in self.anchor_files.items():
            converted = entry.crlf is not None and _crlf_checkout(contents.get(key))
            anchor[key] = entry.crlf if converted else entry.lf
        return SourceState(
            raw=formula({key: sha256(data).hexdigest() for key, data in contents.items()}),
            reviewed_key=formula({key: sha256(data.replace(b"\r\n", b"\n")).hexdigest()
                                  for key, data in contents.items()}),
            anchor=formula(anchor))

    def revision_of(self, state: SourceState) -> str | None:
        """The revision a reviewed source state implements, or ``None`` when unreviewed."""
        return self.reviewed.get(state.reviewed_key)

    def resolve(self, state: SourceState) -> str:
        """The current revision's identity when the source is a reviewed state of it.

        Any other source state keeps its raw source digest as its identity, so it
        is never mistaken for a reviewed revision.
        """
        if self.revision_of(state) != self.current:
            return state.raw
        if self.current == self.anchor_revision:
            return state.anchor
        return self.derived_identity(self.current)


def _anchor(table: Mapping[str, tuple[str, str | None]]) -> Mapping[str, AnchorFile]:
    return MappingProxyType({key: AnchorFile(lf, crlf) for key, (lf, crlf) in table.items()})


#: ``workers/public_svg_pptx_convert.converter_digest()``: the vendored slide-master
#: converter (manifest-verified on every call) plus the nine export adapter files.
CONVERTER_EXECUTION_REVISION = "svg-pptx-converter/1"
CONVERTER_LEDGER = VerifierRevisionLedger(
    name="svg-pptx-converter",
    current=CONVERTER_EXECUTION_REVISION,
    anchor_revision="svg-pptx-converter/1",
    # The sources of origin/main 9d389ba80 (2026-11-05), whose digest every export
    # bundle, proof and svg export identity stored before the migration carries
    # (52b87dba...88a8 on an LF checkout). The CRLF hash is the Git autocrlf
    # checkout of the same blob; ``-text`` paths have none.
    anchor_files=_anchor({
        "vendor/slide_master/MANIFEST.json": ("ec2502a9a97c48d1fad9724022cc8a150bdcb8513c988538e43e4e696ae4baf6", None),
        "services/generation_core/drivers/svg/export_projection.py": ("a744aaf2d6875d1aa262eda8f681879b2e09104a8f36a67e4611e6776c505914", None),
        "services/generation_core/drivers/svg/export_scene.py": ("75e0dfed23fea5c6c6be50ce1ba4b26a011b453d5a14b66a8ebdc183fb183a4a", None),
        "services/generation_core/drivers/svg/export_chart_style.py": ("fcb8ed17325952caecefd611bc843e91986b22fe1c044be7a9db58d7596a5061", None),
        "workers/public_svg_pptx_convert.py": ("04d590ea4fc5ca0f0acf0fa67c0965dd754e1f8fcc2f43d84e6813b4b538b279", None),
        "workers/svg_common_header_export.py": ("e6cef710e72e13d44af2b62c2ace733950ea28417918c1e2cc4afab84c61dde6", "7c8ad98b16dfa1ae9d2fd29dbf681df4cbad63aeae58ae55461e2fbf33a74ae9"),
        "workers/svg_common_header_export_v2.py": ("cd4f2162bfeb59ba3976dd295d4d20511deef4c12d6bb72b78860e4abb7338c3", "e67b58dd5de1cda16f8660e4df5c2b7dcd51f62ddc67f5e0518a18d6286cc15e"),
        "services/svg_common_header_export_evidence.py": ("bc66a167eae6e03515179aa3df31b234b4f6cd25b1afbeb44e41a437012aa321", "3d229da664f1d52643e7497f4679cf2521386febc108b39484dad638ae1a8184"),
        "services/svg_pptx_font_embedding.py": ("a6ab3ff15de1c3e84aaf5a1b0ee2a6f4b1cbf3dfab5cbf561a8ae5e57ae95d42", "72eaaef12609be778adcd963b0f107b71f6fd60c74585fc861055e5f51652c12"),
        "services/svg_templates/frame_native_text.py": ("b57abe55abdbca5f630fa6b02c764495526368ab2ca987de854e25544f24e390", "60ba8d5262f13abb7df3d8f198121218478b1497b6d35318595cbb138a124ddf"),
    }),
    reviewed=MappingProxyType({
        # 9d389ba80: the anchor state itself.
        "52b87dbae3b651a8a2eba3b1bceac779f375d4ab94ed8f030af8adde933b88a8": "svg-pptx-converter/1",
        # Migration commit: converter_digest() and common_header_verifier_digest()
        # resolve through this ledger; no conversion or verification behaviour changed.
        "e1fb878c24a10098e81cea86b14422b80ba33dffe8e2c7a51249b397266a9fcf": "svg-pptx-converter/1",
    }),
)

#: ``services/svg_common_header_export_evidence.common_header_verifier_digest()``:
#: the twelve files that build and verify generated common header v2 evidence.
COMMON_HEADER_VERIFIER_REVISION = "svg-common-header-verifier/1"
COMMON_HEADER_VERIFIER_LEDGER = VerifierRevisionLedger(
    name="svg-common-header-verifier",
    current=COMMON_HEADER_VERIFIER_REVISION,
    anchor_revision="svg-common-header-verifier/1",
    prefix="sha256:",
    # The sources of origin/main 9d389ba80 (2026-11-05), whose digest stored slide
    # header evidence, OOXML header readbacks and svg export identities carry
    # (sha256:86232705...63e0 on an LF checkout).
    anchor_files=_anchor({
        "services/svg_common_header_export_evidence.py": ("bc66a167eae6e03515179aa3df31b234b4f6cd25b1afbeb44e41a437012aa321", "3d229da664f1d52643e7497f4679cf2521386febc108b39484dad638ae1a8184"),
        "services/svg_common_header_v2.py": ("4ce5c16bce7ff0ad7ae8c1d6d1f4de185ca43a50d5043e6299979f1e2b7b67dd", "14009c780c7f14a54ccf1056aeb83a05690e80460f376686294e3ea88704fdf7"),
        "services/svg_common_header_v2_geometry.py": ("6b021f4454958fd7788e1eb1847b7495d00bdd7eea8354500c5f2a3bd25613e5", "4742aa9788113fcba05be1429bf8bb00afd7dfa39bd46e8460a851398dd0b28a"),
        "services/svg_common_header.py": ("5bb02479e841b4417520fb343b37628aa1f381a6ae3c7246fd10d14d699d77c7", "b971216bec5840e6eba9b955f2f4afb65e7e066dad12e92661678f50e0d81b83"),
        "services/svg_renderer.py": ("95d3e8ee93a4c96d083ee25642e60a8b7427b6373a607426db9dfe0cc8e7ab0c", "863a16f1c36148838d7595dfae51c087921b8deeca91f395b5d5f72dbab5598b"),
        "services/svg_text_overlap.py": ("6900f9117c7d25df77bff3ad1b739e988178d9387d1d947f4938392c2ecf5943", "9b0e03d483fec21480ceea03374f396ea8bd61740d1286627330e210e4689bd3"),
        "workers/svg_common_header_export_v2.py": ("cd4f2162bfeb59ba3976dd295d4d20511deef4c12d6bb72b78860e4abb7338c3", "e67b58dd5de1cda16f8660e4df5c2b7dcd51f62ddc67f5e0518a18d6286cc15e"),
        "workers/svg_common_header_export.py": ("e6cef710e72e13d44af2b62c2ace733950ea28417918c1e2cc4afab84c61dde6", "7c8ad98b16dfa1ae9d2fd29dbf681df4cbad63aeae58ae55461e2fbf33a74ae9"),
        "services/svg_export_page_evidence.py": ("5a3a12c3e9095387e1933a3aaf82119f2cd8b7bf2bb5703c4990c6e4833064a3", "e214c7bf0c5e3aaf2df27128e193ed746df0394cb37ec4a578ca88cb70aa9f2d"),
        "services/svg_export_quality.py": ("f3c0637c58356a93b0d0e618089671ef0179a3a1b574c6000ff6eb3949181e85", "270c97239727d39c45609d4329324b09b8a097a26f8d72064d014cbbc59df27f"),
        "services/generation_core/drivers/svg/render.py": ("f0b119fe0f750c3fdce3c3757af5fa488cd52e60f163c9e2e0067760de70a115", "d627225b6351cc100d9975591374492fea4424f9ec9c3d163e6f1135e91fde9f"),
        "services/svg_master_furniture.py": ("2561a15cc5156ffe5f4ecbdd260888770499461fe9c83bba0444bdd076d26c08", "6e623a7caed650d70525301fd2d42927be1935b5490748e26c5b75b6170570a2"),
    }),
    reviewed=MappingProxyType({
        # 9d389ba80: the anchor state itself.
        "sha256:86232705ab2c98b9e19f18193e5ce4fadafe969f6d7de892cbc956899be263e0": "svg-common-header-verifier/1",
        # Migration commit: the digest now resolves through this ledger; no header
        # measurement or verification behaviour changed.
        "sha256:2e52a0e56674690a607fc214c78206e9425c54bb293929f6f5babef6a688d700": "svg-common-header-verifier/1",
        # #126 measured header space advance: adds measurement versions glyph/v3 and
        # paint/v4 (self-describing; older code rejects them by version) and seals a
        # fallback record on glyph/v2 / paint/v3 when the probe cannot measure. The
        # verification of every earlier version is byte-identical, so stored
        # evidence keeps this revision.
        "sha256:c58474633c70f87addab8165499a0b3ecb577badd57b461534796976f5518638": "svg-common-header-verifier/1",
    }),
)

LEDGERS = (CONVERTER_LEDGER, COMMON_HEADER_VERIFIER_LEDGER)

__all__ = [
    "COMMON_HEADER_VERIFIER_LEDGER",
    "COMMON_HEADER_VERIFIER_REVISION",
    "CONVERTER_EXECUTION_REVISION",
    "CONVERTER_LEDGER",
    "LEDGERS",
    "LEDGER_SCHEMA",
    "AnchorFile",
    "IdentityFormula",
    "SourceState",
    "VerifierRevisionLedger",
]
