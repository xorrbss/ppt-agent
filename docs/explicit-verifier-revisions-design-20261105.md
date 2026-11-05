# Explicit verifier revisions (2026-11-05)

Design for replacing verifier identities that are SHA-256 digests of source
files with explicit, reviewed revision constants. It follows
`docs/svg-mode-quality-performance-review-20261105.md` §4.12 ("Code-file
digests act as authority") and the follow-ups noted in PRs #109 (E3, converter
manifest alias), #121 (E6, per-process digest cache; header digest left
uncached) and #122 (S4/S5, `CONVERTER_VERSION` could not be bumped).

Status in this change:

| Identity | Status |
|---|---|
| `converter_digest()` | **implemented** (§5) |
| `common_header_verifier_digest()` | **implemented** (§5) |
| every other identity in §2 | design only (§6) |

Baseline: `origin/main` at `9d389ba80`.

## 1. Problem

A verifier identity built from the bytes of its source files changes on any
edit of those files, including a comment, a rename or a pure refactor. Every
stored proof, export identity or approval that carries the old digest then
stops validating, although nothing the verifier checks has changed. The edit
is silent: no test fails and no reviewer is asked. The cost only shows up later,
as a re-export, a re-approval or a deck that cannot be exported until it is
measured again.

The opposite failure is also possible. An identity that is supposed to change
with behaviour is pinned by hand (`APPROVED_CONVERTER_CODE_DIGEST`). Nobody
notices when it goes stale, and the feature it guards stays disabled.

What is wanted:

- The identity changes when **behaviour** changes, not when bytes change.
- A source change **forces a decision**: it is either behaviour-preserving or
  it is not.
- That decision is recorded and reviewed.

## 2. Inventory of source-byte-keyed identities

Paths are relative to `servers/fastapi/`. In the "Breaks when" column, "any
edit" means an edit of any listed file, even one that changes no behaviour.

| # | Identity (computed in) | Inputs | Stored in / validated by | Consequence of an edit |
|---|---|---|---|---|
| 1 | `converter_digest()` (`workers/public_svg_pptx_convert.py`) | Bytes of the vendor `MANIFEST.json`, after every vendored file is verified against it. Bytes of the nine `_PROJECTION_ADAPTER_FILES`. | Export bundle `converterDigest` (`services/svg_export_bundle.py`), checked by the worker against `converter_digest_accepted`. Native OOXML coverage proofs and generic native coverage proofs (`services/svg_export_quality.py`). Final PPTX render proofs (`services/svg_final_pptx_review.py`). The svg export canonical identity: `converter_digest` → `exporter_digest` (`services/generation_core/export/svg_evidence.py`, `services/svg_export_identity.py`). | Any edit of one of the nine adapters, or a manifest rebuild, rejects in-flight bundles (`svg_export_version_mismatch`). Completed export identities stop being reused, so the deck is re-exported. Stored final render proofs stop verifying, so the final review has to run again. The #109 alias covers manifest provenance only. |
| 2 | `common_header_verifier_digest()` (`services/svg_common_header_export_evidence.py`) | 12 files: header v2 build/geometry, `svg_common_header.py`, `svg_renderer.py`, `svg_text_overlap.py`, both header export workers, `svg_export_page_evidence.py`, `svg_export_quality.py`, `drivers/svg/render.py`, `svg_master_furniture.py`. | Generation-time slide evidence `quality.common_header.verifier_digest`, checked by `common_header_v2_current`. OOXML header readback v2 `verifierDigest` (`workers/svg_common_header_export_v2.py`, `common_header_v2_ooxml_current`). Export identity `commonHeaderVerifierDigest`. Remeasurement witnesses (`services/workspace_svg_header_body_remeasurement.py`). | **Highest impact.** Every page with a generated v2 common header becomes "stale verifier" and cannot be exported (`svg_export_operation.py:288`) until a header body remeasurement operation runs. Export identities and readbacks are invalidated as well. Two of the twelve files (`svg_export_quality.py`, `render.py`) are among the most frequently edited svg files: 15 commits since 2026-10-29. Its file is also adapter #1, so it could not even be cached (#121). |
| 3 | `whole_page_verifier_digest()` (`services/svg_export_whole_page_verifier.py`) | 37 files, including the export bundle, identity, quality and final-review modules, the source-content modules and four workers. | Bundle `wholePageVerifierDigest` (rejected by the worker on mismatch). Coverage proofs `verifierCodeDigest` (`svg_export_quality.py`). Export identity `wholePageVerifierDigest`. | Any edit rejects in-flight v4 bundles, retires completed-identity reuse and invalidates native coverage proofs. Final render proofs do not bind it. |
| 4 | `_code_digest()` and `_base_code_digest()` (`services/svg_partial_recovery.py`) | `CODE_FILES` (62 files, about 2.2 MB) plus five final-selected sources. Cached per process since #121. | Partial recovery approval manifest `code_digest`, checked by `verify_episode(require_current_code=True)`. Runtime operation digest `"code"`. `svg-runtime-provenance/v1.codeFingerprint` (diagnostic only). | Any edit of 67 files makes in-flight partial recovery approvals unusable until they are re-approved (#121 notes). This is the widest inventory: it includes `workspace_service.py`, `presentation_generation_pipeline.py` and `svg_common_header.py`. |
| 5 | `_page_render_code_digest()` (same module) | `CODE_FILES` minus 10 authority files, plus `svg_final_selected_continuation.py`. | Quality recovery page checkpoint reuse key (`drivers/svg/collect.py`, `render_code`). | Any edit misses the checkpoint reuse key, so pages are rendered again. This costs work and gives no wrong answer. |
| 6 | `materializer_code_digest()` (`utils/svg_template_glyph_materialization.py`; mirrored and cached in `services/svg_templates/frame_glyphs.py`) | Bytes of the materializer file. | Registered svg template frame v3 `fixed_glyph_preview.materializer_code_digest`, checked by `validate_frame_glyph_authority`. Glyph markers (`svg_templates/generation.py`, `svg_asset_resolver.py`). Renderer handshake (`utils/slide_capture_worker_client.py`). | Any edit invalidates every registered v3 (fixed-glyph) template, which then has to be registered again. |
| 7 | `private_diagnostic_pipe_code_digest()` (`utils/slide_capture_pipe_render.py`) | Bytes of that file. | Echoed by the capture worker in `privateDiagnosticRaster.pipeCodeDigest` and compared by the client in the same call. Not stored. | Only fails when the worker and the client run different bytes. Low impact. It is listed because it is a renderer contract keyed by source bytes. |
| 8 | `APPROVED_CONVERTER_CODE_DIGEST` and `converter_code_digest()` (`services/svg_cached_author_render_recovery.py`) | 12 files, including two vendored chart files, the vendor manifest and `workers/public_svg_pptx_convert.py`. | Checked against the approval manifest and the constant (`drivers/svg/collect.py`). | Already stale. It was pinned 2026-10-07, and on `9d389ba80` the files hash to `aa4f99ec…` instead of `52b0a07b…`. The cached-author-render recovery therefore always rejects. Every edit of those files moves it further away. |
| 9 | `template_export_verifier_digest()` (`services/svg_templates/export_evidence.py`) | 15 files. | Export identity `svgTemplateVerifierDigest` for template decks. | Any edit retires reuse of template exports, which are then exported again. |
| 10 | `compute_public_export_worker_bundle()` (`services/public_export_worker_bundle.py`) | Node worker sources, svg worker/contract/convert/readback, `native_objects/*.py`, export scene/projection/wire, the whole vendor tree. | Authored export canonical identity `exporter_digest`. Receipt `exporterWorkerBundle`. | Any edit means completed authored exports are no longer reused (one re-export each). This is intentional: a worker fix must not be masked by the cache. Nothing re-validates stored receipts against it. |
| 11 | `checker_digest()` (`workers/public_svg_pptx_render.py`) | The renderer file and `utils/powerpoint_ownership.py`. | Final PPTX render proofs `checkerCodeDigest` (`final_render_current`). | Any edit makes stored final render proofs stop verifying, so the final review runs again. |
| 12 | Renderer and reviewer digests (`services/svg_quality_recovery_review.py`, `drivers/svg/quality_recovery.py`) | `svg_renderer.py`, `vision_advisory*.py`. | Sealed review runtime identity and the quality recovery validation context. | Any edit invalidates saved review runtimes and recovery validation contexts. |
| 13 | Template source capture and review code digests (`services/svg_templates/source_pdf_worker.py`, `source_review.py`) | Their own files, the renderer and ownership files, and third-party library files (pdfium, Pillow). | Template source review evidence, used during registration. | An edit invalidates a pending template source review. The third-party digests are runtime-integrity evidence, not code identity, and should stay. |

## 3. Design

### 3.1 Ledger

A ledger (`services/verifier_revisions.py`) records three things for each
verifier:

- **`current`**: the explicit execution revision, for example
  `CONVERTER_EXECUTION_REVISION = "svg-pptx-converter/1"`.
- **`reviewed`**: a reviewed table `source key → revision`. The source key
  is the verifier's own identity formula, unchanged, computed over
  **LF-normalized** file bytes. The current verifier code therefore stays the
  definition of "source state", and the key is the same on a Linux checkout and
  on a Windows `core.autocrlf=true` checkout.
- **`anchor_revision`** and **`anchor_files`**: the revision that existed
  before the migration, and for each anchor source file the SHA-256 of its LF
  bytes and of its Git CRLF checkout. The CRLF hash is `None` for `-text`
  paths, which Git never converts.

Each owner module reads its files once and builds a `SourceState`:

| Field | Meaning |
|---|---|
| `raw` | The formula over the bytes on disk, i.e. the pre-migration identity. |
| `reviewed_key` | The formula over the LF-normalized bytes. |
| `anchor` | The formula over the anchor files as this checkout holds them: the CRLF hash of a file Git converts, if this checkout converts files; otherwise the LF hash. |

The identity that evidence carries is `ledger.resolve(state)`:

```
reviewed[reviewed_key] != current   ->  raw                  (unreviewed state)
current == anchor_revision          ->  anchor               (pre-migration value on this checkout)
otherwise                           ->  derived(current)
```

The derived identity is `prefix + sha256(canonical {"schema":
"verifier-revision/v1", "verifier": name, "revision": revision})`. It has the
same format as the field it replaces, so it fits every existing format check.
No source digest can collide with it, and it is the same on every checkout.

Whether a checkout converts a file is read from that file's own bytes on disk
(it contains `\r\n`). For an anchor path that no longer exists, it is read from
the ledger module's bytes. The anchor blobs contain no `\r`, so Git's
conversion is exactly `\n → \r\n`.

### 3.2 Why runtime checking is not weakened

- Everything the verifiers check at run time is unchanged. In particular,
  `converter_source_digest()` still verifies every vendored file against
  `MANIFEST.json` on every call, and `svg_converter_integrity_failed` is still
  raised.
- If a deployment runs **unreviewed** bytes, the identity is the raw source
  digest. That is exactly the pre-migration behaviour: evidence produced by
  those bytes matches only those bytes, and nothing stored is accepted by
  mistake.
- The only new trust point is the reviewed table: a reviewer declares that
  source digest X behaves as revision R. That declaration is a code change in
  the ledger, with a reason comment next to each entry, so it is reviewed like
  any other code.

### 3.3 The forcing test

`tests/unit/test_svg_verifier_revisions.py` asserts that the source digest of
the current checkout is a reviewed state of the current revision. The test is
in the svg unit subset (`tests/svg_unit_subset.txt`), so Bitbucket runs it on
every pull request. When it fails, its message names the new digest and the
two options:

1. **Behaviour unchanged** (refactor, comment, rename, extracted helper): add
   the new digest under the **same** revision. Stored evidence keeps
   validating.
2. **Behaviour changed** (what the verifier measures, how it projects, what it
   accepts): bump `current` and list the digest under the new revision.
   Evidence of the old revision stops validating, which is what a behaviour
   change requires. The new identity is derived, never typed by hand.

The test also covers:

- the stored identities, byte for byte;
- fallback to the raw digest for an unreviewed edit;
- vendor tamper detection;
- that a revision bump retires stored evidence;
- the ledger invariants: well-formed digests, an anchor whose LF form is the
  stored value and a reviewed state of its own revision, and derived identities
  that collide with nothing;
- that the recorded CRLF anchor hashes match the files unchanged since the
  anchor;
- that a simulated Windows `autocrlf` checkout reproduces its own
  pre-migration digests (§4).

### 3.4 Where the ledger lives

The ledger module is deliberately outside every source-byte inventory in §2.
If editing a ledger changed the digests it pins, a re-pin would never
converge. The resolver is small and fully covered by the forcing test. The
ledger module must keep Git's default text handling (no `-text` attribute),
because it is the line-ending probe for anchor files that are gone. The
Windows test asserts this.

## 4. Migration

- **Byte-for-byte.** For each migrated verifier, revision `/1` is anchored to
  the digest computed on `origin/main` `9d389ba80`. Both that digest and the
  digest of the migration commit are recorded as reviewed states of `/1`. The
  migration commit only routes the identity through the ledger, so no
  verification behaviour changes. `converter_digest()` and
  `common_header_verifier_digest()` return exactly the values that evidence
  stored before the change carries:

  | Identity | Value |
  |---|---|
  | converter | `52b87dba…88a8` |
  | header | `sha256:86232705…63e0` |

  As a result:
  - bundles, proofs, export identities, slide header evidence and OOXML header
    readbacks keep validating;
  - completed-identity reuse keeps working.
- **Windows checkouts.** This fork's Windows checkouts use
  `core.autocrlf=true` (`docs/authored-style-samples-design-20260928.md` D6,
  `compatibility/slide-master/README.md`). Five of the nine converter adapters
  and all twelve header verifier files are not `-text`. On such a checkout the
  stored digests are therefore different values, computed over CRLF bytes. An
  anchor fixed to the LF value alone would have retired all Windows-stored
  evidence, which is why the anchor is computed per checkout (§3.1).
  `test_windows_autocrlf_checkout_keeps_its_stored_identity` rebuilds the
  pre-migration CRLF digest from the `9d389ba80` blobs and Git's attributes. It
  then simulates a CRLF checkout of the current tree and asserts that both
  identities equal that digest. The test skips only when Git or the anchor
  commit is unavailable.
- **Untouched.** No file of the whole-page verifier inventory (#3), the
  partial recovery inventory (#4), the final-render checker (#11) or the
  template verifier (#9) was edited, so those identities are unchanged
  (`whole_page_verifier_digest()` stays `c0fa3807…`).
- **One-time side effects.** Both are caused by editing
  `workers/public_svg_pptx_convert.py`, which cannot be avoided because every
  producer imports `converter_digest()` from it:
  - The authored export worker-bundle digest (#10) changes once, so completed
    authored exports are re-exported once on their next request. No stored
    receipt is re-validated against it.
  - The cached-author-render converter digest (#8) changes again. It was
    already stale, so its behaviour does not change: it rejects.
- **#109 aliases.** The converter manifest aliases keep working, and they are
  now a special case of the ledger. A superseded manifest's raw digest is not a
  reviewed state, so `converter_digest()` returns it unchanged for that
  manifest. `accepted_converter_digests()` still adds it. A future
  provenance-only manifest rebuild should be handled by re-pinning (option 1)
  instead of adding an alias.

## 5. Implemented

| File | Change |
|---|---|
| `services/verifier_revisions.py` | New: `VerifierRevisionLedger`, `SourceState`, `AnchorFile`, `CONVERTER_LEDGER` / `CONVERTER_EXECUTION_REVISION`, `COMMON_HEADER_VERIFIER_LEDGER` / `COMMON_HEADER_VERIFIER_REVISION`. |
| `workers/public_svg_pptx_convert.py` | `converter_source_state()` keeps the vendor integrity check and reads the sources. `converter_identity()` is the unchanged `svg-export-adapter-projection/v1` formula. `converter_source_digest()` is the raw (pre-migration) value. `converter_digest()` resolves the state through the ledger. |
| `services/svg_common_header_export_evidence.py` | Same split: `COMMON_HEADER_VERIFIER_FILES`, `common_header_verifier_source_state()` / `_source_digest()`, `common_header_verifier_digest()`. |
| `workers/public_svg_converter_aliases.py` | Docstring only: the mirror is of `converter_source_digest()`. |
| `tests/unit/test_svg_verifier_revisions.py` | New forcing test (§3.3). |
| `tests/unit/test_svg_converter_digest_aliases.py` | The mirror assertion compares with `converter_source_digest()`. |

**Merge-order caution.** Files of the header inventory (#2), in particular
`services/svg_common_header.py`, are being edited in parallel. If such an edit
reaches `main` **before** this change, main's raw header digest moves, and
evidence stored from then on carries the new value. The anchor of
`svg-common-header-verifier/1` must then be re-derived from main at merge time,
not kept at `9d389ba80`'s value. That means the `anchor_files` hashes (LF and
CRLF) of the changed files, the anchor's reviewed key, and the anchor commit
named in the forcing test. If it reaches main **after** this change, the
forcing test fails on that branch, and its author re-pins or bumps. The same
applies to the converter adapters (#1).

## 6. Design only: remaining identities

Each one follows the same pattern: split `*_source_digest()` from the identity,
add a ledger anchored to the current digest, and pin the source digest in the
forcing test. Specific notes:

- **#3 `whole_page_verifier_digest()`.** It is the next most valuable target,
  and it would also have removed the one-time re-pin that #109 caused. Its
  definition lives in a file of its own inventory, so the migration commit must
  record two reviewed digests, as in §4. It was not done here because the task
  limited implementation to #1 and #2, and because its inventory overlaps the
  files that callers of #1 import (`svg_export_bundle.py`,
  `svg_export_identity.py`, `svg_export_quality.py`), so migrating both in one
  commit widens the review.
- **#4 and #5, partial recovery.** Use one ledger for `code_digest`.
  `_page_render_code_digest` is a reuse key and can stay byte-keyed or follow
  the same ledger. In-flight approvals carry the current `_code_digest()`, so an
  anchor keeps them valid. The 67-file inventory is far wider than what
  determines recovery behaviour. A ledger turns each unrelated edit (for
  example in `workspace_service.py`) into a one-line re-pin instead of a
  re-approval.
- **#6, materializer.** It is stored in registered templates, so the anchor
  approach avoids re-registration on refactors. The value format is
  `sha256:<file hash>`. `frame_glyphs.materializer_code_digest()` and the
  utils function must resolve through the same ledger.
- **#7, pipe digest.** Replace it with a protocol revision constant shared by
  the worker and the client. Nothing stored depends on it, so no migration is
  needed. It is deferred because `utils/slide_capture_pipe*.py` is being edited
  in parallel.
- **#8, cached-author-render.** It is already stale, so the feature is
  disabled. This needs an owner decision: re-review the fixed-axis converter
  path and express the approval as "converter revision R plus fixed-axis
  revision F" (the approval would survive refactors), or delete the staged
  module.
- **#9 and #12.** These are reuse and sealed-runtime keys. The ledger is
  optional, and their invalidation costs work, not correctness.
- **#10, worker bundle.** Invalidating reuse on worker code change is the
  purpose of this digest. A ledger would keep authored reuse across refactors,
  but it would need a revision per Node and Python worker source set. Separately
  from that, its inventory includes the svg converter and the vendor tree. That
  should be confirmed, because the authored hybrid worker does not appear to
  execute them.
- **#11, checker.** Use a ledger anchored to the current checker digest, so
  that stored final render proofs survive refactors of
  `public_svg_pptx_render.py`.
- **#13.** Use a ledger for the code digests. Keep the third-party library
  digests byte-keyed: they are integrity evidence about the runtime, not
  identities of reviewed code.

## 7. `exporter_version` 128-character cap (#122)

Facts on `9d389ba80`:

- `CONVERTER_VERSION` is the svg `exporter_version` of the export identity
  (`svg_export_identity.py`) and the bundle `converterVersion`. Bundles must
  match it exactly.
- It is 125 characters long:
  `slide-master/<40-hex commit>+document-canvas.2+text-advance.1+chart-furniture.16+native-fixed-text.2`.
- The cap is 128 characters, enforced in three places:
  - `public_artifact.exporter_version` is `String(128)` (`models/sql/public_artifact.py`);
  - `ExportIdentityKey.normalized()` rejects values over 128
    (`services/artifact_store.py`);
  - `CanonicalExportIdentity` normalizes `exporter_version` with
    `maximum=128` (`services/public_export_identity.py`).
- `compatibility/slide-master/README.md` called this "a 160-character identity
  field". 160 is only the default `maximum` of `_normalized_text`, so that
  statement was inaccurate. The README now says 128.

Every patch suffix makes the string longer, so the scheme cannot grow at all.
Proposal (design only, not implemented):

1. Keep the current 125-character `CONVERTER_VERSION` as the frozen
   `exporter_version` of `svg-pptx-converter/1`, so that stored identities and
   bundles keep matching.
2. Stop appending patch suffixes. A behaviour change of the vendored converter
   or of an adapter bumps `CONVERTER_EXECUTION_REVISION` (§3.3, option 2). For
   revisions after `/1`, `CONVERTER_VERSION` becomes the short revision string
   (for example `svg-pptx-converter/2`, 20 characters), which can never reach
   the cap.
3. The patch chain is already recorded in the vendor manifest (`version`,
   `presentonPatches`, `patches`) and covered by `converter_source_digest()`.
   The ledger entry for a new revision names the patch that motivated it.

Widening the column instead (Alembic `String(256)` plus both normalizers) is
possible, but it would only postpone the overflow, and it would need a
migration on SQLite and PostgreSQL. With explicit revisions it is unnecessary.

## 8. Risks and decisions

- **Re-pin judgement.** Re-pinning a change that does alter behaviour would let
  stale evidence validate. Mitigations:
  - one reason comment per ledger entry;
  - the forcing test message spells out the two options;
  - reviewers should treat a ledger diff like a verifier change.
- **Merge order** with parallel edits of the header and adapter inventories
  (§5).
- **Checkout assumption.** The per-checkout anchor assumes that one checkout
  applies one line-ending policy. A Windows tree whose `autocrlf` setting
  changed between checkouts can hold mixed files, and its pre-migration
  evidence may then not be reproduced. Before this change, such a tree's
  evidence was equally fragile.
- **Owner decisions needed:**
  - #8: re-review or delete;
  - whether to migrate #3 and #4 next (recommended order: #3, #4, #11, #6);
  - adopting §7 for the next converter behaviour change.
