# Authored editable export recovery implementation

Status: implementation integrated; real export completed with partial editability. Full visual/selection recovery is not accepted.

Design: `authored-editable-export-recovery-design-20260914.md`.

## Delivered changes

- Editable requests cannot silently publish a whole-deck raster fallback. Partial, unknown and not-applicable evidence remain separate from operation completion.
- Current-policy canonical identities preserve completed-result reuse while bypassing historical evidence-less results.
- Python preflights the actual serialized UTF-8 request at 8 MiB; Node enforces the matching boundary.
- Worker receipts distinguish assembler process time, conversion wall time, per-slide conversion, font preparation, assembly and write/readback. Unmeasured stages are absent; no synthetic live page progress is emitted.
- Validated attempt deadlines protect staged inputs and aggregate outputs from TTL/LRU deletion. Admission reserves staged inputs, output budget and temporary budget; ordinary completion/error/cancel/timeout releases staging.
- Raster band v2 retains source digest and ownership once per band, with bounded disjoint PNG fragments and validated pixel composition, digest, dimensions and placement. Only proven bottom backgrounds are promoted to slide background.
- Local-fit readback checks packaged fragment bytes and physical placement against the new evidence rather than comparing a crop to the full source PNG digest.
- Body/title/table correspondence, missing/rasterized/obstructed/unknown targets and photo exemptions are recorded per page. Hidden ownership and page numbers do not prove editable body content.
- The download card shows a separate editability verdict, affected pages and concise Korean reasons.

## Focused verification

- Backend delivery contract: 3 tests passed (agent verification).
- Attempt lifetime/capacity: 3 focused tests passed (agent verification).
- Editability evidence: 4 tests passed (agent verification).
- Worker integration/evidence/fragment readback: 5 tests passed.
- Node worker and assembler: 28 tests passed.
- Download card: focused TSX transpilation check passed. An earlier ESLint invocation did not complete and was stopped.

No full test suite, full build or full lint run was performed. Existing unrelated worktree changes were preserved.

## Actual product acceptance

The managed API and export controller were restarted; unrelated services were left running.
The 100-slide presentation `ba5db838-2471-4591-8167-050be683932c`, revision 1, was submitted through the same-origin workspace command endpoint as editable hybrid with font embedding requested.
Operation: `d6273f3b-f464-488b-81a0-2e735080985a`.
Evidence directory: `artifacts/editable-export-recovery-implementation-20260914/`.

The real same-origin operation completed without retry. The downloaded file SHA-256 matches the durable artifact receipt: `d5273f9ac3d16516faf33780e213790d07657aab2e8743d98585e0ebc632052d`.

- 100 slides; 96 contain native text. Image-only pages: 9, 45, 65, 89.
- Full-canvas picture objects decreased from 499 in the previous export to 31. This is not proof that every remaining object is directly selectable.
- Required targets: 2,916; native correspondence 2,206; rasterized 105; missing 0; obstructed 403; unknown 209. Unknown-role targets can be outside the required-target denominator, so these disposition counts must not be interpreted as a disjoint percentage of 2,916.
- Geometric selection-obstruction evidence remains on pages 2, 5, 6, 10, 14, 18, 20, 24, 37, 46, 55, 61, 66, 82, 87, 91.
- PowerPoint selected and edited a representative text object on each of pages 1, 14, 50, 88, 98, 99, 100; all seven changes survived saving a separate copy and reopening it. Programmatic object selection does not prove unobstructed mouse selection for every object.
- Representative PowerPoint renders preserve the main layout, but native text differs from raster rendering and right-edge clipping remains visible on page 98. Page 1 has an existing white-on-white appearance problem also present in the previous export. The first transient blank PowerPoint screenshot of page 1 was re-rendered successfully; it is not an artifact-content finding.
- Operation wall time: 826,045 ms (13 min 46 sec), export worker: 802,191 ms. Node conversion wall time: 780,927 ms; assembly: 2,423 ms; writing/readback: 3,549 ms. Conversion dominates this run. A controlled performance improvement is not established.

Pages 45 and 65 fell back after Chrome teardown timeouts. Pages 9 and 89 fell back with `raster_fragment_missing`. A valid one-EMU shared-edge rounding case was reproduced and the validator now permits an additional 1e-9 pt floating-point allowance, while still rejecting two-EMU errors and byte tampering (focused test passed). The failed attempt's intermediate PPTX was cleaned up, so that numerical defect is not proven to be the exact cause on pages 9 and 89; the downloaded file predates this correction.

Completed-result reuse was verified by operation `2152db88-f2e7-40b9-b020-bd36019bad3a`: same artifact hash, zero worker executions, and preserved image-page reasons and producer timing. Its operation wall time was 54,623 ms, including 35,245 ms staging. This is reuse, not a new conversion speed measurement.

After the numerical validator correction, the editable policy is bumped to v2 so future requests do not reuse pre-correction v1 files. The completed 100-page file and reuse proof above are v1 evidence; v2 has the focused numerical regression check but no second full-deck acceptance run. This document does not claim restored visual quality or export speed.

## Remaining limits

- Windows staging reservation is admission accounting; a hard per-process disk quota is not proven. Container temporary storage uses the existing bounded tmpfs.
- Child process crash/cancel/timeout cleanup is covered; an API-host hard kill can leave orphan staging directories. No existing startup scavenger was found.
- Fragment-count or byte limits preserve the original raster and report remaining selection obstruction.
- Actual PowerPoint interaction is independently verified; a worker correspondence verdict alone does not certify an interactive edit.
