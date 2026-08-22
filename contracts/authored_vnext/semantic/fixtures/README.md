# Authored semantic fixture set

`canonical-fixture.json` and `canonical-fixture.expected.json` are the
cross-runtime canonical JSON contract. Python and JavaScript normalize strings
to NFC, sort object keys by UTF-16 order, reject non-finite values, and emit
the same normalized number form before hashing UTF-8 bytes.

`vertical-slice-fixture.json` is a deterministic two-slide seed. Slide A
covers mixed CJK/Latin/number text, an IBM Plex Mono run, an image, three
simple shapes, and an intentionally raster chart island. Slide B reuses the
generic CSS class name while changing its content/style and includes right,
bottom, and internal sentinels.

The seed is not product evidence. `scripts/verify-authored-semantic-fixtures.mjs`
derives bundle/manifest digests and verifies fresh mode-scoped artifact hashes
when passed an evidence sidecar. It always reports `not-verified` and rejects
reused artifacts, mismatched attempt/revision/canonical digests, or any true
promotion flag. A real UI/PowerPoint evidence bundle must be supplied by the
product verification path.
