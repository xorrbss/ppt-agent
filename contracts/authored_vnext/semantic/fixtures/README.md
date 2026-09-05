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

The seed is not product evidence. The verification tools that once consumed it
(`verify-authored-semantic-fixtures.mjs`,
`verify-authored-semantic-vertical-slice-evidence.mjs`, and the wrapper
`run-authored-semantic-dual-output-fixture.mjs`) were removed as unreferenced
code on 2026-09-05 (commit 64b7d646e); recover them from that commit's parent
if the checks are needed again. A real UI/PowerPoint evidence bundle must be
supplied by the product verification path.
