# Editable export page recovery follow-up

The prior 100-page export replaced source pages 9, 45, 65 and 89 with slide images. Its saved reasons were Chrome teardown timeout (45/65) and raster fragment matching failure (9/89). Those codes alone did not prove a teardown-only failure or an actually absent PNG.

## Changes

- For the Chrome runner's capture-and-cleanup aggregate, classify the original capture error before its cleanup errors. Successful captures already survive cleanup warnings.
- Retry only the failed page, once, for transient measurement timeout or closed/crashed browser session errors. Admission uses the existing deck deadline and reserves at least 120 seconds; it does not abandon an in-flight Chrome process.
- Distinguish packaged fragment absence, digest mismatch and placement mismatch without widening the existing one-EMU plus floating-point allowance.
- Keep bounded hashes, placements and candidate counts for fragment failures, and bounded retry outcomes in per-page timing. Durable/cache projection strips arbitrary diagnostics and limits candidate samples to four.
- Advance the Python editable policy to v3. The existing Node worker source digest also separates requests made with changed worker code from older cached artifacts.

## Recovery limits

The Python readback sidecar contains fragment identities and placements, not the source crop bytes and complete reconstruction state. It cannot safely reconstruct a damaged fragment by guessing. It continues to replace only the rejected page with the identity-verified original image, preserving healthy editable pages. Region recovery is allowed only with verified content and stacking evidence.

This change does not establish that the four original pages are repaired, that all 100 pages are fully editable, or that export speed and visual fidelity are restored. The previous downloaded file is unchanged.

## Focused verification

- Primary classification/retry cases passed; a follow-up isolated browser-closure retry case passed with exit code 0. The earlier worker test command's complete terminal summary was not captured, so no full-suite pass is claimed.
- Existing fragment readback test: 1 passed, including coordinate-boundary, missing-image, digest-mismatch and bounded-candidate cases.
- Existing durable delivery/cache contract test: 1 passed, including retry evidence retention and diagnostic redaction/candidate limits.
- Four-page real Python worker + Node export: exit 0, 166.492 seconds, editable-hybrid delivery. Source pages 9/45/65/89 contain 35/30/26/27 native text shapes respectively; no whole-page image fallback and no local-fit page rejection. Four completed-capture cleanup warnings did not discard output. The saved evidence reports 16 raster fallback elements, quality `partially-editable`, and editability verdict `unknown`; native text presence is not a full editability/visual acceptance pass.
- Artifact: `artifacts/editable-export-four-page-repro-20260914/focused-four-pages.pptx`; SHA-256 `785aae4e89b8443b632fb8b4e26ab7484803cc7a78a0af762e2436731aa9e85a`. Evidence: adjacent `run-result.json`. This is a focused worker reproduction, not a new 100-page web export or interactive PowerPoint edit. Per-page timing was not retained in this run; no rerun was made solely to collect it.
- API and export controller restarted through the managed stack after an initial ownership-change guard stopped the combined restart. Unaffected services were preserved.
- The presentation workspace export card now distinguishes full, partial, image-only and unknown completion using durable evidence. Partial completion does not imply whole-page images; the page list appears only when replacement pages are known. Fragment failure descriptions use Korean product wording.
- Card-only Cypress verification: 8/8 passed. The initial literal parenthesized spec path found no tests; after correcting the glob, one text assertion needed adjustment before the final passing run. No full frontend suite or build was run.
