# Authored semantic fixture set

`canonical-fixture.json` and `canonical-fixture.expected.json` are the
cross-runtime canonical JSON contract. Python and JavaScript normalize strings
to NFC, sort object keys by UTF-16 order, reject non-finite values, and emit
the same normalized number form before hashing UTF-8 bytes.

`vertical-slice-fixture.json` is a fresh deterministic eight-slide semantic
dual-output fixture. Its slides cover a table, native chart, diagram/shapes,
cropped image, long Korean typography, multi-column layout, mixed CSS, and
right/bottom/internal sentinels. Every slide intentionally reuses the same
generic CSS class name under a distinct `data-scope` so class isolation is
observable rather than inferred.

The seed is not product evidence. `scripts/verify-authored-semantic-fixtures.mjs`
derives bundle/manifest digests and verifies fresh mode-scoped artifact hashes.
`scripts/verify-authored-semantic-vertical-slice-evidence.mjs` binds the
eight-slide manifest, revision, operation, generation attempt, export attempt,
PPTX digest, render/report digests, and all eight PNG hashes. It also requires
explicit rejected remake/rollback stale-revision/attempt records. Both tools
always report `not-verified`; they reject reused artifacts, mismatched lineage,
and any true promotion flag. A real UI/PowerPoint evidence bundle must be
supplied by the product verification path. The wrapper
`scripts/run-authored-semantic-dual-output-fixture.mjs` runs both checks and
adds the fixture/evidence identity fence.
