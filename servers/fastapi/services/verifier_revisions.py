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
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from utils.code_file_digest import CodeFileDigest
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


def _ledger_checkout_converts() -> bool:
    """Whether this checkout converts text files: the probe for anchor files that are gone."""
    return b"\r\n" in Path(__file__).read_bytes()


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

    def source_state(self, sources: Mapping[str, Path], formula: IdentityFormula, *,
                     reviewed_formula: IdentityFormula | None = None) -> SourceState:
        """Read each source once and compute the raw, reviewed and anchor identities."""
        return self.state({key: CodeFileDigest.of(path.read_bytes()) for key, path in sources.items()},
                          formula, reviewed_formula=reviewed_formula)

    def state(self, files: Mapping[str, CodeFileDigest], formula: IdentityFormula, *,
              reviewed_formula: IdentityFormula | None = None) -> SourceState:
        """The raw, reviewed and anchor identities of sources already digested.

        ``reviewed_formula`` is the identity formula with every checkout-dependent
        input other than line endings fixed (for example POSIX path text); it
        defaults to ``formula``.
        """
        anchor = {}
        for key, entry in self.anchor_files.items():
            current = files.get(key)
            converted = entry.crlf is not None and (
                current.crlf if current is not None else _ledger_checkout_converts())
            anchor[key] = entry.crlf if converted else entry.lf
        return SourceState(
            raw=formula({key: digest.raw for key, digest in files.items()}),
            reviewed_key=(reviewed_formula or formula)({key: digest.lf for key, digest in files.items()}),
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
#: converter (manifest-verified on every call) plus the ten export adapter files.
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
        # Cover the already-executed font-subset helper in the current source
        # inventory; historical converter aliases keep their original recipe.
        # No conversion bytes or historical anchor inputs change.
        "ce6b9c85984462c188f37385abcb30f4e8df458a8e986dc172223582045ad591": "svg-pptx-converter/1",
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
        # Cached-author-render deletion: drivers/svg/render.py imports approved_sidecars
        # from svg_cached_author_render_lineage; no header verification changed.
        "sha256:598ff7b694423b562370cb5c50b52acded95917c9b574408cc0242ca098e33cb": "svg-common-header-verifier/1",
        # P8 (decision D1): prepare_common_header gives a new reduced header v1 a
        # divider, a page-position eyebrow and one deck-wide title size from a short
        # ladder only when its snapshot seals ``reduced_header_style``. Those fields
        # already exist in v1 and are verified and rendered by unchanged code; every
        # contract without the seal is built, verified and rendered byte-identically,
        # so stored evidence keeps this revision.
        "sha256:5321677d0117b03a618aee5e15703e59f406f0b1996a47488830c8dbc4565e72": "svg-common-header-verifier/1",
        # P8 merged onto main (#132, #133): the combined source state of the two
        # behaviour-unchanged entries above.
        "sha256:8a7c3138c5515f3423038b1d419a6ed643f6e5769572a1f4765800abd1934ab8": "svg-common-header-verifier/1",
        # Native requirements v4: render's chart repair hint is emitted only for
        # snapshots pinned to v4. Legacy prompts, measurements and header evidence
        # remain byte-identical; stored header verification keeps this revision.
        "sha256:cd24bd647482fcaa18092812c869da0647e6adb6d8a99fded00a5b2607204c8e": "svg-common-header-verifier/1",
        # Template page-context v2 is pinned only by fresh admissions. Render
        # replay re-derives the saved v1 context encoding and still compares every
        # field; header measurement and verification are unchanged.
        "sha256:2d8c55610be61441571256dd905366acfc8491425f89cd600821198e9cf0ba8a": "svg-common-header-verifier/1",
    }),
)

#: ``services/svg_export_whole_page_verifier.whole_page_verifier_digest()``: the
#: files of the source-proved whole-page export path.
WHOLE_PAGE_VERIFIER_REVISION = "svg-whole-page-verifier/1"
WHOLE_PAGE_VERIFIER_LEDGER = VerifierRevisionLedger(
    name="svg-whole-page-verifier",
    current=WHOLE_PAGE_VERIFIER_REVISION,
    anchor_revision="svg-whole-page-verifier/1",
    # The sources of origin/main 49e529d6b (2026-11-05), whose digest v4 export
    # bundles, native coverage proofs and svg export identities stored before the
    # migration carry (aa559f55...2469 on an LF checkout).
    anchor_files=_anchor({
        "services/svg_export_whole_page_verifier.py": ("a9a2818e8c110595589e7cdfa95bf952e96ac7433b980ea4dfdd6dae3d02a91a", "1d839010ccd32a68f19cd8249cca98d12c4560c9c78afc66e60db8a9c4cf5b34"),
        "services/svg_pptx_font_embedding.py": ("a6ab3ff15de1c3e84aaf5a1b0ee2a6f4b1cbf3dfab5cbf561a8ae5e57ae95d42", "72eaaef12609be778adcd963b0f107b71f6fd60c74585fc861055e5f51652c12"),
        "services/svg_trusted_fonts.py": ("d909f3a7787b582a92bddfca1c89812ec94d2cbdad0df2845f6c48afd3fba104", "36e0cfd0244e2dfa1d29d6c30ee5554ddbdb2a85fd9275bfdeaa97aacbefc2b0"),
        "services/authored_vnext/semantic/typography.py": ("9960420ffef7a2cf56e741b2680c63937722847302adab388b806c53a701f1b1", "5c697f53b69f08cdc9b8f5a15143fea7a2187ae43dac3425a1ffae5737ef5f61"),
        "utils/svg_template_fonts.py": ("88e19651c40d0e679b9aeae31f52731979337e635e96353b815801e462eac6a1", "020a193e3b66ebf722e8d7e9da815d8480642cf6589427325438856df8e76ee4"),
        "services/svg_text_advance.py": ("d1562ca34dd0fbb93b3996dfd6caa7e0edb7a1fca08a17f0cc798425720e3722", "15964fcb7b699ad3bd974bf569f4e96b643179c28ba92be1a0f8f251a5965c8d"),
        "services/svg_export_whole_page_adapter.py": ("6588054eec4c7baba8a9fd27157a38277929bc3c3f4eacf0a11ac3de11f65d83", "3b26e515fd542e869aac6d823fede6f94e9d09f4e7d998ec02d1e63ba271e86e"),
        "services/svg_export_source_preservation.py": ("ff36d93d15c57874aa9d94c49e83621df6cadccd5a0ea928c7867b554f560f40", "663b659fabd920cd2998664098f12b2a0992bfb552e4eb20534e30cebeeb7bec"),
        "services/svg_export_operation.py": ("7758d41c943ba4deec53da4d121af665f2061404d4ce62c86f20e49f963335f4", "f2d995f05eeef81b6aa4d7d56f750810ceff0e9ad4cb67359dfcd9c6c9e229d6"),
        "services/svg_export_bundle.py": ("6689d4aca72fb1d495563ccc328b782169c862a5b0803db995449d4897138d14", "627fc51996e30e86f89bc27e5262ecb448b9646ef4953518675a32c109c1fd9e"),
        "services/svg_export_identity.py": ("1d29726115e8e7d3f8ffe98e7f8a3a592ca239157f417015f81437ebfde85665", "4208823bfa6544dbf9d8c62ab9e8a08665c9b2343ac23581aeb9c00c5d0d0599"),
        "services/svg_export_quality.py": ("f3c0637c58356a93b0d0e618089671ef0179a3a1b574c6000ff6eb3949181e85", "270c97239727d39c45609d4329324b09b8a097a26f8d72064d014cbbc59df27f"),
        "services/svg_final_pptx_review.py": ("93ca73f4c3ff7761404bc8f2f2a961d3101c4a22b19e021192bd85c400a529e3", "642849282e3f856b91d272c0a803212b87882f3869d3b5ccca4b9e809b4d336a"),
        "services/svg_quality_review_service.py": ("91a447b7d1ff7dc1e0e8109d1b6b0056a722393b25488d83cd2da1f684b24c30", "d130ee22356562193b41a481f4ac1f6e6dceb3c6fadb73ea0fb6e759a1fac84f"),
        "models/svg_quality_review.py": ("6c341dd6919e4d5a14ef9318d64843925a085d8a48da9df7af565be3f9faa293", "48df107d5dc133d551fe295d2e5b4a030f59cfe66b7d67692fb6172533f4ef81"),
        "services/generation_core/quality/vision_advisory.py": ("c888bd6015f006bd24b5d81986c1761dd855f624d7a4ab83a2c8f0abe0c9f9e3", "c3bca0938f0f2c73a57038bc3958327e71636bbf3a918871008b03cc41159a92"),
        "services/generation_core/quality/vision_advisory_prompt.py": ("fce6e11347f660aa0b66a17ee14f531b91a531b59162e741defebb6b791228be", "1f687c9bfdebe54748362b03c7cdbdc25201175d84d2df72e7d6b8204edd1c67"),
        "services/svg_source_layout.py": ("8a6233fdae6a41211fc0c379ab6bfa47100799ee1e455cad9b1ff0ff09c97426", "3dcb417d7cb871e73dd2d8729b22ea937fe51ad75d6457af877476283ec114db"),
        "services/svg_source_charts.py": ("194c2440b0c16f27365e7fa2752825aa46ebe017eb9500ed51fb45a5be0a7c37", "d2d968d09a83b99817a2099f51aaa10c73281c5e1141433460f6e22180ffdc0a"),
        "services/svg_source_native_tables.py": ("713690b1dfe2d6621eaa270720321a1e92f3506272019b89919b9968ffe95ad8", "c83327ca1185909b57b5ee9f8e9a0310003887f0b66d5c9606b2510c47d94cca"),
        "services/generation_core/drivers/svg/native_chart_visual.py": ("ba34b79847c268aa2c73ea928f2bc0906d0a59c0fca928b46196adabb6d7e9ea", "b4f75ae230168399eb4f74d9d5cda3d8d754d3cebfe750fe8a1dc9bea6ea3d44"),
        "services/generation_core/drivers/svg/export_chart_style.py": ("fcb8ed17325952caecefd611bc843e91986b22fe1c044be7a9db58d7596a5061", None),
        "services/generation_core/drivers/svg/native_chart.py": ("5ec4fe703b3d3edba60e8e9811e4f39d541db83dca72e4118c7b1b4f8debfe9e", None),
        "services/generation_core/drivers/svg/native_geometry.py": ("6fae7cbb976f61b8a6cd7fd2c7b40e936c9cd094e62400336d8f759e333ac7f6", "8a4fffc2edb10d2f2f2afa6c0fda573755dec4131238eb9f6e72e3ff58975bfc"),
        "services/svg_contract.py": ("6a446ba978c757cf7e8fa03ec57d2fa45b31d93723299ec8f3041f4633b53029", "502831f32a27cc2bea550f1889af849dee24acb71a91e823951e327597173ff0"),
        "services/source_content/manifest_store.py": ("23af04eb67af4f9dde1760bd15708b58ade17042516e62d906ba6a94f6a23543", "4d8d60c5ae3b8747847d2573e42526e999b24c343d7fd1b25dfbfb525cefd8e5"),
        "services/source_content/contracts.py": ("a4b0f05d5accfb23a9ba282f9bebf5cc0ec9ef7ef7ff5a28c06e54186130a49a", "adbb261ca3adacb35d559ca8c3fb1a12769262530f622a8df5630b66c9cb3c93"),
        "services/source_content/digests.py": ("60cb8e97247e83b25b1adfb5cbc3cbcad40d5abe3f64b2ecbf244337a171b024", "644d51c284417afe8e3aaa720b62ee15cc4a0b11c6a54c324bbbf1c0e531b24e"),
        "services/source_content/pptx_chart_provenance.py": ("b1542725f5e4bcb727f914fc1425480827196351c686039b64c2023d83a5a048", "7e46c88f0f7ea7fd341ad62b52ee2b75900ea3c681560ffd956d3fe11630bb6d"),
        "services/source_content/pptx_package_parts.py": ("d683f8ccd868243ae2580eac1e507bb1606debbe83a0e7463d0d8e58de1d4503", "2ef954b13e42a977bef5306004c502d4fc3842a4484f7b98025d32022f456d19"),
        "services/source_content/pptx_extraction_constants.py": ("dd1582dc0fa247f9cf97ca3ea625ed0a06274b1f13657bcc8baeb19ad4dbcf72", "e500786ca0e4eff12205cbe8c5b65cfec1bc78fa3b8d0388f870dce012e1a09a"),
        "services/source_content/security.py": ("f16960aa59e71f62db5a5bb85290c3b2a70d594dbbb9cf8743cfc1cd67b3b02b", "c31e499119a00571155a07b91df6ebb8b3da08032b5b48c3d9eab1ae5768b335"),
        "workers/public_svg_export_worker.py": ("758f6a553079e3ef5b982111af43f6b37be62c4edc263c436e0aae98467d3bb7", "bb1d7a7441e8929ad77349e09802bb7dbeb4df066f1ca2621b7975b92482b138"),
        "workers/public_svg_whole_page_convert.py": ("b7e26aed3cac6b9c711cb811bb84b296a03be89d8eedab86d06c1de205bd29ac", "6effcc440c2b2e9ee961fbc08df2580ab7f26bad13ea215bc0994043d0ddbf3c"),
        "workers/public_svg_pptx_readback.py": ("76a4d36a384b77f9423ccf6f366adfffef1c26f604650369570527a06e4a8de6", "a3068edf9ede4c1d8c9b773f7bfe927a73ecda93ff4df92b36eeb947ca8f4e2e"),
        "workers/public_svg_pptx_render.py": ("f3431c93330bc418cf67df1a8b2bf603272cc450c409f250f47d3cef548039c4", "7e3210fcac8c6c7eb5c39ee0e1fca86786d05ce3d017e818965cfaec7f646651"),
    }),
    reviewed=MappingProxyType({
        # 49e529d6b: the anchor state itself.
        "aa559f555120a1600b4a011ec2e0e00b013074649adad43703d5d649e2cb2469": "svg-whole-page-verifier/1",
        # Migration commit: the digest now resolves through this ledger; no export
        # verification behaviour changed.
        "1f50d4b5f2c023e84264e04ec18f58ca094fbb2c7677330ee90a341ed11e2bc4": "svg-whole-page-verifier/1",
        # Template A visuals (svg_contract.py): finalize_svg gives the page number a
        # readable ink (white or near-black when the style primary fails 4.5:1) only
        # when the snapshot pins ``page_number_ink`` (set by fresh admissions); every
        # stored snapshot lacks it and finalizes byte-identically. validate_svg and
        # every export verification input are unchanged.
        "8416e5b6508fd714bee8039acd9355438341379b368175789e1cbbb8d273d2cd": "svg-whole-page-verifier/1",
        # Native requirements v4: single-series title matching and publication
        # remeasurement receive v4 only from its snapshot pin. Legacy chart
        # projection and export evidence verification remain byte-identical.
        "f672a0bb8d8761a363db6f5dad0b9ac355fee1cffdd0111c158479aefd5b2d4e": "svg-whole-page-verifier/1",
        # Cover the already-executed font-subset helper in the current source
        # inventory without changing export verification or historical anchors.
        "67d5215fb1386032c3eeafda62ba95c9325b45abe79033813c68282042d14b3b": "svg-whole-page-verifier/1",
    }),
)

# The sources of origin/main 49e529d6b (2026-11-05) that the two partial recovery
# code identities read (``services/svg_partial_recovery.CODE_IDENTITY_FILES``).
# Each identity formula reads only its own files from this table.
_PARTIAL_RECOVERY_ANCHOR = _anchor({
    "services/svg_partial_recovery.py": ("81d828822625276bee148cc55c9cfeb58fecfb2cc9ecfc4023e4ca439bb25169", "e377e636e99e857ca93ea017e5c959d6b5ae55efdc3249e8845c32e246254346"),
    "services/svg_source_local_review_continuation.py": ("aaf0e03d6f75345681f96c9a2e34b273ccf6ba851e3e2f5fd7c31aa3cd90a0f4", "32080441572bc291d82f9ace2b058c6415290134bb9b04007d6906aa6705b65b"),
    "services/generation_core/drivers/svg/collect.py": ("ed79dc3ea9d092466a64c9b71d84170a016337ac3295a10764c60232a15f7d94", "f6f97f130ac6b7ce710eba1c093273d80e758c814fe786bbf6cce4139789d92b"),
    "services/generation_core/drivers/svg/author.py": ("7252c6fd9ce29409cb1bd8fc4ef257e5a46cb79f12f9908a8d63070f5e5447a4", "65cfde8f6d1f1a8d7e2e4d9161fc19327fd94f0ea258d9ada8de23e407cb7ec4"),
    "services/generation_core/drivers/svg/attempt_cause.py": ("8fd66314acd1c7a27ea7753f38256a9ca1e1b8e55e52a4ef88b9a344c5116739", "9e3796ac2aa9074a62e3b05c1ea5b8f0e9d9285a1e9cf0880434ec53f9228b72"),
    "services/generation_core/drivers/svg/render.py": ("f0b119fe0f750c3fdce3c3757af5fa488cd52e60f163c9e2e0067760de70a115", "d627225b6351cc100d9975591374492fea4424f9ec9c3d163e6f1135e91fde9f"),
    "services/generation_core/drivers/svg/quality.py": ("1eaeee8a3f2699f4e6b297b9db50e627d944f498276e325949cac3b8e1311c45", "4a8130017a0ab3fa487cffe1bce0000849b9ccf1ebaeff3f3d8f5ce1b9312514"),
    "services/generation_core/drivers/svg/quality_recovery.py": ("026b375376bd1aef392f273181800ad7d954121bdb308deb1738b6c347f54e55", "75c94e44f0b670fc4621b209007ab8163c0fcbbc43c59fd20a44bccd29f86466"),
    "services/generation_core/quality/vision_advisory.py": ("c888bd6015f006bd24b5d81986c1761dd855f624d7a4ab83a2c8f0abe0c9f9e3", "c3bca0938f0f2c73a57038bc3958327e71636bbf3a918871008b03cc41159a92"),
    "services/generation_core/quality/vision_advisory_prompt.py": ("fce6e11347f660aa0b66a17ee14f531b91a531b59162e741defebb6b791228be", "1f687c9bfdebe54748362b03c7cdbdc25201175d84d2df72e7d6b8204edd1c67"),
    "services/generation_core/quality/review_coverage.py": ("833f85e7a78df0c1b1cdd5752768923c385072cac3893d8dcc3f70a1aaa088ea", "6a19d370f6065f8830484b6025e2866e4d79eae9e820745bca7203385c064152"),
    "services/svg_source_layout.py": ("8a6233fdae6a41211fc0c379ab6bfa47100799ee1e455cad9b1ff0ff09c97426", "3dcb417d7cb871e73dd2d8729b22ea937fe51ad75d6457af877476283ec114db"),
    "services/svg_source_native_tables.py": ("713690b1dfe2d6621eaa270720321a1e92f3506272019b89919b9968ffe95ad8", "c83327ca1185909b57b5ee9f8e9a0310003887f0b66d5c9606b2510c47d94cca"),
    "services/document_canvas_generation.py": ("07315aab2d1dd4a9a46eeedcc983fe6895d1069cd8cf19544f2769dabc27e288", "f98df79de82b7c8d388985ca2f7aeb3cdf14d739bc2b9c598ba2f3714e470364"),
    "services/presentation_generation_staging.py": ("91803bb564886db687021dc19ecc33bec8b8814bd1fdff0a5d8dabfd14909293", "e68f706e3da73e3a545d04e1178b48bcfb112501d216e307f2352c7295034418"),
    "services/presentation_generation_pipeline.py": ("ab751cdfb8f5e28e78b9fac2dfb5defc81e5e2c9bd5aec700cdc858fdb5ee075", "4a695fac500a6bb25e78681db7a0abb5a6372545bbf5234ae2454ea12c7ab360"),
    "services/workspace_service.py": ("019511c6098823c51a1ed271e02e20e7d6b69128c939c8b0cc7abbc91ab380c2", "f995b9d33ed7d9539996c9a773f2b0fef06c6242c96d62ce9932b913e1d894a0"),
    "services/svg_authoring_profile.py": ("451b5f65fcd5e435741c22a0f796fb0c6cc9e60e27cf400f7069cf0f3224e492", "b4d528c036c288e5ccf5837d70734ee5aa086f485d620fde254b1b4ace940c6b"),
    "services/provider_durable_admission.py": ("017d3bc087199f1a56a79a43071218645b2dfc758e801a04886ce464566977e2", "31dfebac921a7634d3224e01687976f63577b7e74cd3996160c062bbe5edb083"),
    "services/provider_usage_instrumentation.py": ("f02f6be4a3be27cbab7848b5ab8c89018805b3c3ca02bd08d155f56fca7f2241", "705b35fb322a23d8735a937bb925de1d48ffaced2255dd30fa2cc7747634da77"),
    "services/provider_runtime_job_registration.py": ("70e96662a291e8951855ac9d1a5213ce35fd9eac99a5f254e0d1f4001125e864", "b6c15e8c4b84f07571f6fb1a73d6633cb1816b511f190032d0507d74968fe75e"),
    "services/generation_core/drivers/svg/partial_quality_recovery.py": ("380674244954bece21cd8e0a995d662dd13fcdaf861293112619468d9a4f4cae", "e1f46a18fa5ba9d0c34c1d5eb6333fef16486e0bac5550888ad1bde52154d69e"),
    "services/svg_presentation_prepare.py": ("128f235168b56782caad538abfe68a4805271e7e0fd7b10a79c635320daa1ab9", "50d58326517471f0876fcffd166dc602e83d65eff8d3f37415ad39f843c8cabe"),
    "services/svg_common_header.py": ("60fb031d7c366518278a6098878699c1644092640d2333c622d856e025c1b110", "b91d70ca13d9e90b90ea0490b01f95f95f94c7fc626ec0db3799611f2cd3c825"),
    "services/svg_common_header_generation.py": ("b07052fe1c6ab5130d09235a44e2c51193d85551511affbb004f2230299a17f2", "77858b919be10eb5cdd24128d5e0510b319117e495de45c6a2538351a96db256"),
    "services/svg_common_header_design_checkpoint.py": ("7664ab95f54988deebf3bdaa6a1a62c54e5ff36fa737d6f730107a0aa5479ae0", "83ff700ec76609af85188a340a9d77e2013110b0b44c0addfdf0d9e3d6856028"),
    "services/svg_common_header_titles.py": ("8291849dc51fd8f8fa993bcbd055943e63a915365b517ac4fcb1a2d95d579970", "c42b84596db6a7bf1c58405ec2055d377ea9b5ed74fa8dbb3599e31cdbb6553c"),
    "services/svg_common_header_title_budget.py": ("8fc63d4a064df6eef41ff5dcee2f0bde0cad75a246d00fb38940a24bbc8e477c", "29d9b85cc5188b6d47cc4836cb7986172bbb6def84c7abdcd9b9cdd8d206c36a"),
    "services/svg_generation_inputs.py": ("a907286b10f99c5e05f099a4e03e4c5707673b7d39abd6ab38ded42632284fac", "c78934761ee886d8b7af9e167967315c1ad03c05a1565cf72364cb1b13642ab8"),
    "services/svg_presentation_checkpoint.py": ("0effe00507b954a5310d677263d08a62f6e6a73704297b6c1ad6709e8d4a8527", "55e550999b587eab2993f84eb4488c3274b9c72958d8f5353619dfa5bb5e1adf"),
    "services/svg_generation_assets.py": ("5747e380fce3183b0b064ea4088bc14b96fff3f1d6aec536f1e0e83e19380c5d", "97bbd795ae5767b5722eb01b1755793388346acd8add85b30befc07f52dd2ecd"),
    "services/svg_default_fonts.py": ("a8ebecbba96e6c6355d301851e402c915b00b3240ac838c738cb9d20e753e4c3", "4cc78a7165d9c3199aef749d559c5f69b7f5a0ba340b0b6ed22627fc3da5f688"),
    "services/svg_contract.py": ("6a446ba978c757cf7e8fa03ec57d2fa45b31d93723299ec8f3041f4633b53029", "502831f32a27cc2bea550f1889af849dee24acb71a91e823951e327597173ff0"),
    "utils/svg_validation.py": ("bd57ffb5a282108d23a722c6baa09ac61f49d1e6c11179200b7098d2ab82815a", "b11454c979b937126a1941e6558051b98f276f8e3fcbae5862aeae0a7e507ba9"),
    "services/svg_renderer.py": ("95d3e8ee93a4c96d083ee25642e60a8b7427b6373a607426db9dfe0cc8e7ab0c", "863a16f1c36148838d7595dfae51c087921b8deeca91f395b5d5f72dbab5598b"),
    "utils/svg_template_fonts.py": ("88e19651c40d0e679b9aeae31f52731979337e635e96353b815801e462eac6a1", "020a193e3b66ebf722e8d7e9da815d8480642cf6589427325438856df8e76ee4"),
    "services/authored_vnext/semantic/typography.py": ("9960420ffef7a2cf56e741b2680c63937722847302adab388b806c53a701f1b1", "5c697f53b69f08cdc9b8f5a15143fea7a2187ae43dac3425a1ffae5737ef5f61"),
    "services/svg_text_advance.py": ("d1562ca34dd0fbb93b3996dfd6caa7e0edb7a1fca08a17f0cc798425720e3722", "15964fcb7b699ad3bd974bf569f4e96b643179c28ba92be1a0f8f251a5965c8d"),
    "services/svg_text_overlap.py": ("6900f9117c7d25df77bff3ad1b739e988178d9387d1d947f4938392c2ecf5943", "9b0e03d483fec21480ceea03374f396ea8bd61740d1286627330e210e4689bd3"),
    "services/svg_master_furniture.py": ("2561a15cc5156ffe5f4ecbdd260888770499461fe9c83bba0444bdd076d26c08", "6e623a7caed650d70525301fd2d42927be1935b5490748e26c5b75b6170570a2"),
    "services/generation_core/drivers/svg/native_declarations.py": ("e463913d4010e9f215df6d21d09c99e2ec0dcc66260b9ea872072ee24cf13996", None),
    "services/generation_core/drivers/svg/asset_slots.py": ("15103b625be7f49a613cb1163a531d3824a2c0058bfeb0dac029db008942c618", "861354fa7e989fee66ce42b854b2b3273bda9f6f3cd85202fbe5f0bb16ca1fd7"),
    "services/generation_core/drivers/svg/embed_assets.py": ("2d81e9c51ada08aa548385836cc973bfd83e2c83b1a650d67165862f27a0df97", "32113f90a85eac87f25f1afccbdca5b1d1246284fd569758c88cb5ff6fa33472"),
    "services/generation_core/drivers/svg/icon_flatten.py": ("ad73474b5f518fab0abfa724d40ed4efecbbe6d47e650ec22134d4bd727156e8", "5948a7437e382b8cf407d20276ca5a3ffc70523f1f8ba96a00bbe3a5667db33a"),
    "services/generation_core/drivers/svg/icon_geometry.py": ("1836ecbbd9684af1b05cdea06f479634ef0560e039a6f3f61e49097ceb73642c", "cae5e33c95d0fbc5b8281a33785929a2a6980ab2939c39fab789577aeaf611ba"),
    "services/generation_core/assets/offer.py": ("7dea2104d6396c9f0c11d34695d6323a7a130422e70da1d8c898fad596f0677a", "7b4ce036248706699396a3e7793620d8dedb6a7ffa527761f2cb9b35392248cd"),
    "utils/authored_image_slots.py": ("315de39121eec548e3e7bf8dc78193fb6bb247599abf7bc304ed3e920a5205ab", "b735af79ba2a7178d8ffaf287833bad889f27692134c4399edff9d1d686b4467"),
    "utils/codex_stream_adapter.py": ("644b72952d731ad83a1c7a57d0453d263f34120d0d6ae50ecafe24c0e9b85784", "94da540d3144d8787f45e552e1b391a199c9ca5bc1ab88c5f5c8db5651a526c0"),
    "utils/llm_calls/prepare_svg_common_header_titles.py": ("215d1fb7c93593b56689ee9345a956c7f2436148312c45dfb3336bb39f71d50d", "6b56ec7371d36138f1543010e33fe9ed771ba5ecf6978014ef6b4e8d124fe586"),
    "utils/ppt_writing_prompt.py": ("b8c97f381b58f89900ab011adc6f7560c60c9338ab13a67f3b84c04c432bd63c", "75fcebba7dc093827e9e890daf10df726d137bdcb7bc4d0ad8deae240d782da1"),
    "services/ppt_writing_policy.py": ("69831c4dc5886aa26e099c948bf31adb0a1b3b880d2104dd368e9eb7357353a0", "23350b241a8b6069c6b44e6d8bc4d77b7c0b7d4eb0b1688543649b77468da2e7"),
    "services/ppt_writing_visible.py": ("9f62050df418788910e3f810b5ee5f01af57b6409ad365adcfb547224e489c1f", "3b6e1784c52fa07bc271edeb6d0fd8737436a1653d0ff554a3f923092c46e156"),
    "services/svg_recovery_source_evidence.py": ("c97aec2706cf573c6b5da0c01379fe8bc54bd7b3e3448b8e2a9e3123f43fc50d", "b0d787bc4d109e1914007d144d1756c9f3570360d3425b7c6e509c23fac15237"),
    "services/generation_core/authoring/author_slide_svg.py": ("58119c91f9b2f012ef1729c63e7a3a1f4c7aa6a9c155452ef816a1b27c6320a8", "37ba84560da0166d4092e2d0365d85de6f4ec2af478e72b16dfed05ac13e258b"),
    "services/svg_author_output_view.py": ("a61dc9e5b6ec8509fabe27825312775d5603ed6a6ae0bb4d183efa3ddbc1c92d", "cc68459698455d624bd99ebfa1ac39737126bb325cf42c927e0c39f21949c6e7"),
    "services/generation_core/authoring/source_id_alias.py": ("71d98e39e8298d85bafdb09302c03df92e8344eeeb5eb91feb34b75d1e4851c5", "fdfc57caf4d2a8bca963c817285cb83c438205719dcf5208246cbcdb3f545fa3"),
    "services/svg_text_defaults.py": ("8a4107f3a098d9428fad97f5873a85cb94dc2d608e2143579bdc3a1b7844efbe", "47919c31a11ae271a42f1a1773747d09dc5feff8c8578f8a6a09935d988e8e26"),
    "services/generation_core/authoring/author_source_svg_layout.py": ("3a9f7d9270709ffdca499fb6ed2b10be84f4dfdd6cfe341737137cf747475a3a", "5b91061977700113cfb48aa442275eb624257d91ba9422c4e0d638587536ca85"),
    "models/presentation_outline_model.py": ("98bfe0ada17614ae087dadae55249ddfafb1bb7e452bf84b397bfb670477bf49", "912401ffce905ca8b9e83c18f56eb79ce787c75bd128120fa0c0d724f58eb24e"),
    "utils/slide_capture_result.py": ("bc45be1a82e7ff86e9957fd15ab3d90442279bf3d512fa2e63a76edc3862cb61", "c30dc38acc97423ce2c3015e5e03e7fc46a7a4d9240cbe092b336e9165aa93d0"),
    "utils/slide_capture.py": ("1bbbd4b65634ed914388ee93819ce60f20144ce836c8c1d5bea53dbb089bfa29", "de04c954d627a998080cd0015b84135f8587d10f71f8e0b40984b9eb5ebfd986"),
    "utils/slide_capture_text_ink.py": ("2b590d909d7d0023cfb9198609519dbb8ef40a6de92fc96e01ab37b9faa79e2e", "82bf39b0a2406ec7fbe5fec733e9064ba995a9c844b6149411e097d8310e6a94"),
    "services/svg_final_selected_continuation.py": ("51bf49f356b6120a2f5f700f0e71401d7d66b9816e93582bb53eeb0d9d73f29e", "19adf1796274bf4a4df2075552b6f7b5b670b2fc5313f68ae50b9d40ed1722b4"),
})

#: ``services/svg_partial_recovery._code_digest()``: ``CODE_FILES`` plus the five
#: final-selected sources. Approval manifests (``code_digest``), runtime episode
#: digests and ``svg-runtime-provenance/v1.codeFingerprint`` carry it.
PARTIAL_RECOVERY_CODE_REVISION = "svg-partial-recovery-code/1"
PARTIAL_RECOVERY_CODE_LEDGER = VerifierRevisionLedger(
    name="svg-partial-recovery-code",
    current=PARTIAL_RECOVERY_CODE_REVISION,
    anchor_revision="svg-partial-recovery-code/1",
    # Stored before the migration as 91280a73...de1a on an LF checkout.
    anchor_files=_PARTIAL_RECOVERY_ANCHOR,
    reviewed=MappingProxyType({
        # 49e529d6b: the anchor state itself.
        "91280a73f656aa9b0d49d8701f159aa8474d6228d4fa63e40171a7b6fe5ade1a": "svg-partial-recovery-code/1",
        # Migration commit: _code_digest() and _page_render_code_digest() resolve
        # through these ledgers; no recovery behaviour changed.
        "e4232fcc984e8b08e9427b7b106e7c91c496b9c9a2b6008698e9b8f884424abe": "svg-partial-recovery-code/1",
        # Cached-author-render deletion: the shared witnesses (digest, author_witness,
        # unknown_provider) moved verbatim to svg_recovery_witness; collect's cached
        # page check, the workspace admission and the pipeline's zero-provider flag
        # were reduced to the rejection each one already always reached. No partial
        # recovery behaviour changed.
        "651b68aa3866cf5987ce233b5e8ebc5098c9b29b00fdda4cf8700b8ece5f8cdf": "svg-partial-recovery-code/1",
        # S3 authoring profile v10: new rules apply only to decks pinned to v10
        # (admissions still pin v9 by default); v9 and earlier prompts are
        # byte-identical (pinned digests), so recovery of stored decks is unchanged.
        "64ad5a76691fb115fbe7932497489ed2a53cb5fd48e4a11f3c96d55fcb09584a": "svg-partial-recovery-code/1",
        # P8 (decision D1): the reduced header default sits behind the registered
        # SVG_COMMON_HEADER_DEFAULT setting (default "designed") and a pinned
        # reduced_header_style; decks without them recover byte-identically.
        "7492d3fbc8b5bb316e1cf62db8fbe2f05a816a460ef859e74e956a7f3c4ae894": "svg-partial-recovery-code/1",
        # Template A visuals: (1) finalize_svg's readable page-number ink applies only to
        # snapshots pinning ``page_number_ink`` (fresh admissions), so replaying a stored
        # candidate yields its stored bytes; (2) SVG_TEMPLATE_OUTLINE_SECTIONS is read only
        # while a fresh outline is requested, and recovery reuses the job's pinned
        # authored_outline; (3) SVG_TEMPLATE_STEP_PICTURES and the vector scene demotion in
        # offer.py act only when an asset offer is built, while recovery replays the
        # assetOfferPlan sealed in the page-0 checkpoint. Stored decks recover byte-identically.
        "83057000cd28d3e85f6cd5751d1870a91bd49c2e6bd070226d7feab818675921": "svg-partial-recovery-code/1",
        # Template body renders (author_slide_svg.py): the render images, their prompt block and
        # the ``template_body_renders`` receipt exist only for snapshots that pin
        # ``template_body_renders`` (fresh admissions with SVG_TEMPLATE_BODY_RENDERS on); the
        # design view drops that key, which no stored snapshot holds. Stored decks recover
        # byte-identically.
        "25e50dffd2ee74f3b2d8afe433314e7e860ce9b654aeb608bb397d1b9e568200": "svg-partial-recovery-code/1",
        # Template body renders review fixes: the render block is left out when no render was sent
        # (same pin condition as above). Stored decks recover byte-identically.
        "6d2edfe0a0038e4acc657fefe9239f254159fa0e2ef68eb2d1e5651afb7f15ad": "svg-partial-recovery-code/1",
        # Native requirements v4: author guidance, declaration diagnostics and
        # chart repair hints require the snapshot's v4 pin. Frozen legacy prompt
        # hashes and snapshot identity checks preserve stored recovery bytes.
        "db03f40f285488381ebac8e8ffb6d47c49929cd7a64946a892e162b15ddb495d": "svg-partial-recovery-code/1",
        # Asset projection v2 requires a fresh immutable snapshot pin and a
        # matching sealed offer receipt. Unpinned snapshots keep the legacy
        # opacity, raster coverage and provider-drawing normalization, including
        # seedless offers; saved v1 template contexts replay unchanged. Track
        # both drawing helpers now while preserving the historical preimage.
        "57aa07966d950e5dc470f9eba5b1ced173b6f5c4bde43332a4f9b88c6fbe5b49": "svg-partial-recovery-code/1",
    }),
)

#: ``services/svg_partial_recovery._page_render_code_digest()``: ``CODE_FILES``
#: minus the recovery/operation/provider authority files, plus the final-selected
#: continuation. The quality recovery page checkpoint reuse key carries it.
PAGE_RENDER_CODE_REVISION = "svg-page-render-code/1"
PAGE_RENDER_CODE_LEDGER = VerifierRevisionLedger(
    name="svg-page-render-code",
    current=PAGE_RENDER_CODE_REVISION,
    anchor_revision="svg-page-render-code/1",
    # Stored before the migration as 80cbd150...ce36 on an LF checkout.
    anchor_files=_PARTIAL_RECOVERY_ANCHOR,
    reviewed=MappingProxyType({
        # 49e529d6b: the anchor state itself. The migration commit edits only
        # svg_partial_recovery.py, which this identity excludes, so it keeps this key.
        "80cbd150c5d4f35086749c0faefadcd15181f3a74019c5cf43247bbb70b8ce36": "svg-page-render-code/1",
        # Cached-author-render deletion (same edit as the code ledger entry): no page
        # rendering or measurement behaviour changed.
        "8e9cd3ad649183f3635bf02e53d7f2efa86646b5823729d3511b82491ea4e921": "svg-page-render-code/1",
        # S3 authoring profile v10 (same edit as the code ledger entry): pages of
        # v9-and-earlier decks render from byte-identical prompts.
        "b12bbf894bf36535708a8ffea10ce5f14f46a0a45f46482afb3d468745e4be48": "svg-page-render-code/1",
        # P8 (same edit as the code ledger entry): pages without the pinned
        # reduced_header_style render byte-identically.
        "97655caf4437ec7e8aff84152784b96d1c8dad102a0ac9119ed25d7052ed9931": "svg-page-render-code/1",
        # Template A visuals (same edit as the code ledger entry): pages of snapshots
        # without the ``page_number_ink`` pin finalize byte-identically, and pinned
        # outlines and sealed asset offers are replayed, not rebuilt.
        "1eb28c7bb992e469a3f880719a08f22f318179e583291cc34191a39732dfec1f": "svg-page-render-code/1",
        # Template body renders (same edit as the code ledger entry): pages of snapshots
        # without the ``template_body_renders`` pin are authored from byte-identical prompts.
        "216b7a9c6d1657134ab86a340a1248080d2e3b42894baf23fb80cad6d225de73": "svg-page-render-code/1",
        # Template body renders review fixes (same edit as the code ledger entry).
        "e5321183bd24087425804c01919016eae2e35fe58835c6de7b0b6c72674de7bd": "svg-page-render-code/1",
        # Native requirements v4 (same edit as the recovery code entry): only
        # snapshots pinned to v4 receive new guidance, diagnostics and repair
        # hints; legacy page prompts and rendering inputs stay byte-identical.
        "65fa2d2cd24e5e022c392582b9e4741a674cddf876b9cee289d7f474b4605586": "svg-page-render-code/1",
        # The same snapshot-bound asset projection and template-context replay
        # as the recovery entry: unpinned pages retain their historical bytes.
        # Both drawing helper files are now covered by the current inventory.
        "f5f97281424d071632bc81efa13c00abca135a19a75765b64687a04b9eb85bd5": "svg-page-render-code/1",
    }),
)

LEDGERS = (CONVERTER_LEDGER, COMMON_HEADER_VERIFIER_LEDGER, WHOLE_PAGE_VERIFIER_LEDGER,
           PARTIAL_RECOVERY_CODE_LEDGER, PAGE_RENDER_CODE_LEDGER)

__all__ = [
    "COMMON_HEADER_VERIFIER_LEDGER",
    "COMMON_HEADER_VERIFIER_REVISION",
    "CONVERTER_EXECUTION_REVISION",
    "CONVERTER_LEDGER",
    "LEDGERS",
    "LEDGER_SCHEMA",
    "PAGE_RENDER_CODE_LEDGER",
    "PAGE_RENDER_CODE_REVISION",
    "PARTIAL_RECOVERY_CODE_LEDGER",
    "PARTIAL_RECOVERY_CODE_REVISION",
    "WHOLE_PAGE_VERIFIER_LEDGER",
    "WHOLE_PAGE_VERIFIER_REVISION",
    "AnchorFile",
    "IdentityFormula",
    "SourceState",
    "VerifierRevisionLedger",
]
