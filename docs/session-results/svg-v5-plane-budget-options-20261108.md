# V5 native chart paint proof: fitting the default capture budget

Decision memo, 2026-11-08. Read-only analysis of HEAD `66b15ae5d`. Measurements were taken
on this Linux VM (4 vCPU, Chrome for Testing 153.0.8010.12, `--headless=new --disable-gpu`) with a
private profiling harness that monkeypatches the adapter and changes no repo file:
`scratch/profile_v5.py`.
Every number comes from one run per configuration, so allow about ±10% noise. The product
measurements were taken on Windows (column 78–83 s); on this VM column takes 80–92 s.

## 1. Where the time goes (grounded)

**Plane count.** `collect_chart_paint_proof` issues planes as follows:
- 8 per target, in `slide_capture_chart_paint.py:836` (`alpha … current-absent`).
- 4 per demanded grapheme cluster of every glyph-range target, in `:877`.
- 7 per native composition pair, in `:966`.
- 7 per equivalence class, in `:998`.
- At most 2 per stack-boundary pair, in `:1064` and `:1067`.

Clusters dominate. The actual builder emits whole labels as glyph ranges, and
`clustersFor` segments them by grapheme (`slide_capture_chart_text.py:149-152`), so a
5-character label costs 20 planes.

| example | targets | planes | of which cluster planes | baseline (this VM) |
|---|---|---|---|---|
| column | 11 | 232 | 144 (62%) | 80–92 s |
| stacked-column | 19 | 282 | 124 | 105 s |
| fixed-column | 17 | 240 | – | 87 s |
| coincident-line | 13 | 229 | – | 77 s |
| donut | 8 | 184 | – | 63 s |
| separator-pie | 8 | 184 | – | 61 s |
| marker-line | 11 | 153 | – | 55 s |

**Cost per plane: about 0.35–0.38 s.** Each plane runs the full sibling lifecycle
(`slide_capture_chart_adapter.py:318-357`), which does the following:
1. Re-runs the unchanged driver bootstrap: `Page/Runtime/Network.enable`, blocked URLs and
   device metrics (`slide_capture_pipe_render.py:249-264`).
2. `_fresh_document` (`adapter:111-176`). It resets the background override, calls
   `getFrameTree`, creates an isolated world, sets a sentinel, navigates to `data:`, polls
   `getFrameTree` and readiness, creates a second isolated world and checks the blank document.
3. `Page.setDocumentContent` and the metrics/fit evaluation (`pipe_render:280-311`).
4. The `observe` callback (`paint.py:778-822`). It runs setup (`_SETUP`, which re-checks the
   viewport, fonts, images and compositing), the projection-digest checks, the target probe,
   selection all-on painting, the **all-on screenshot compared with the original PNG**
   (`:811`), a geometry check, the mutation (`_PLANE`), another geometry check and the final
   screenshot.

CDP totals for column (232 planes, 92 s run):

| call | count | seconds | notes |
|---|---|---|---|
| `Page.captureScreenshot` | 466 | 22.2 | 2 per plane, about 47 ms each |
| `Page.setDocumentContent` | 233 | 16.4 | about 70 ms each, on a 1.5 KB document |
| `Page.getFrameTree` (first in `_fresh_document`, `adapter:118`) | 233 | 13.2 | about 56 ms; a renderer sync point |
| `Page.getFrameTree` (poll, `adapter:134`) | 232 | 0.1 | |
| metrics evaluation (fonts + double rAF + fit poll) | 233 | 5.7 | |
| all-on double-rAF evaluation (`paint.py:810`) | 232 | 5.7 | |
| `_SETUP` | 232 | 3.8 | |
| `_PLANE` evaluations (contain a double rAF) | 232 | about 4.7 | |
| `Page.navigate` | 232 | 2.3 | |
| everything else | | under 5 | |

About 170–180 ms of each plane is the fresh-document mechanism (`setDocumentContent`, the
first `getFrameTree`, navigate, the metrics evaluation and the bootstrap). Most of the rest
is frame pacing: rAF waits and screenshots that wait for a new compositor frame.

## 2. Options

### Option 1: chart-route Chrome launch without frame-rate limiting

Launch the chart route's Chrome with `--disable-frame-rate-limit --disable-gpu-vsync`. The
flags are currently fixed in `slide_capture_pipe.py:144-160`. `chrome_pipe` is already
injected into the adapter (`authored_renderer_session.py:48-65` → `adapter:300-305`), so a
chart-only launcher can be supplied without changing the launch of the absent/default route.

- **Measured.** column 92 → 70 s, coincident-line 77 → 68 s, donut 63 → 52 s, separator-pie
  61 → 56 s, marker-line 55 → 49 s, fixed-column 87 → 81 s.
- **Proof output.** Byte-identical to the baseline for all 7 examples, ignoring only the
  per-run nonce field `admittedRenderDigest`.
- **Soundness impact.** None. Raster content does not change, only when frames are produced.
  The original PNG and every plane come from the same flagged browser, and every check stays.
  An incomplete frame would fail the all-on equality check (fail-closed), not pass.
- **Size.** Small: one optional flag set on `_ChromePipe`, or a chart-only factory. The
  absent-route launch argv must stay byte-identical.
- **Risk.** Low to medium. These flags still need verifying on the Windows product Chrome.
  Savings are 7–25%, which is **not enough on its own** (stacked-column would stay at about
  85–95 s).

### Option 2: per-document plane batching with full rebinding and fresh-document fallback

Keep one disposable sibling document across consecutive planes. The primary page stays
immutable, so this is not the rejected mutable-primary restore. Before each reused plane:
1. Reset the background override through CDP.
2. Call the existing `state.clearTextPaint()` (`chart_text.py:284`), which clears the
   Selection and the adopted sheet.
3. Replay the setup snapshot with `state.restore()` (`paint.py:462-463`, present today and
   unused). The prototype found this replay incomplete, so the implementation must replay a
   full attribute snapshot, not only `style`.
4. Remove the adopted sheet, delete `window.__chartPaint` and remove the font-evidence marker
   attribute (`paint.py:355-356` sets `data-chart-paint-selected` and never removes it).
5. Require a **DOM-state digest** to equal the pristine post-load digest. The digest covers
   `outerHTML`, the adopted-sheet rules, the Selection range count and the computed
   fill/stroke/opacity/visibility/display/colour/font/transform of every element.
6. Run the unchanged `observe` callback: setup, viewport/font/image/compositing checks,
   projection digests, the target probe, the all-on raster equality with the original,
   geometry equality, the mutation, geometry equality and the final screenshot.

If the digest differs, or the reused document fails the all-on raster check, rerun that plane
in a fresh document with the current lifecycle. Do not mark the target incomplete.

**Prototype results**, run through the real adapter with that fallback:

| example | baseline | batching | batching + Option 1 | reuse fallbacks | proof identical to baseline |
|---|---|---|---|---|---|
| column | 82–92 s | 45 s | **29.6 s** | 0 | yes |
| stacked-column | 105 s | 57 s | **40.2 s** | 0 | yes |
| fixed-column | 87 s | 48 s | **32–43 s** | 0 | yes |
| coincident-line | 77 s | 47 s | **33 s** | 0 | yes |
| donut | 63 s | – | **29.8 s** | 21 raster | yes |
| separator-pie | 61 s | – | (not rerun with fallback) | raster | – |
| marker-line | 55 s | – | **30.3 s** | 23 raster | yes |

That is about 0.12–0.15 s per plane, against 0.35–0.38 s today.

**Findings that matter for the soundness case.** These are prototype observations, not
assumptions:
- **The naive `restore()` leaves `<html style="">` after alpha planes, and font-evidence planes
  leave `data-chart-paint-selected`.** The digest caught both (58 and 8 mismatches on column)
  before the attribute replay was hardened. Hidden DOM residue is real, and fresh documents
  currently mask it.
- **On donut, separator-pie and marker-line, reused documents sometimes failed the all-on raster
  check even when the DOM digest matched.** This happened after white/black recolour planes
  (`_PLANE` lines 559-566) and once after `reference-original`. That is stale paint
  invalidation, not DOM state. It is the same class as the historical "pie restore was still
  different" failure (`svg-chart-render-proof-20261108.md:68`). Without the fallback, these
  targets came back `glyph_original_highlight_raster_changed`. The check failed closed and
  never accepted an artifact.

**Soundness impact.** The proof would no longer establish that every counterfactual raster
comes from a document freshly parsed from the admitted bytes. It would establish instead:
- the document derives from that fresh document;
- its observable DOM/CSSOM/Selection state digest equals the pristine state;
- viewport/DPR, fonts, images, compositing and projection digests are re-proven by `_SETUP`;
- the all-on raster is byte-equal to the immutable original;
- geometry is equal before and after the mutation, as today.

The residual gap is renderer state outside the digest and outside the all-on raster, such as a
paint-invalidation bug that affects only the mutated state. The current design already relies
on the same invalidation between its all-on screenshot and its mutation in one document.
Batching adds a mutation→restore→mutation sequence, and the stale-paint observation above shows
the restore direction is the weaker one. The all-on gate catches it there.

To narrow the gap further, either option is possible:
- (a) Use a fresh document for the first plane of every target, reusing only within a target.
  Savings are nearly identical: about 11–19 extra fresh documents.
- (b) Pin each recolour mode family to fresh documents. This costs more.

The proof JSON schema does not need to change. `probeVersion` (`paint.py:28`, pinned by the
consumer at `chart_content_paint.py:1395`) should move to a v2 so the changed acquisition method
is explicit. No accepted v5 proofs exist yet, so this has no compatibility cost.

- **Size.** Medium, about 150–250 lines in `slide_capture_chart_adapter.py` and
  `slide_capture_chart_paint.py`:
  - a reuse lifecycle beside `begin_plane`/`end_plane`, keeping the sibling identity checks;
  - the reset script and the digest;
  - fallback plumbing in `plane()`;
  - Chromium tests asserting proof identity with the fresh-only mode on all native examples.
- **Risk.** Medium. The owner previously rejected the restore architecture, so the argument
  must stand on "the primary stays immutable, the gate is unchanged, and the system falls back
  rather than tolerates". Fallback rates on recolour-heavy charts (11–15%) erode the savings,
  but the measured totals still fit with margin.

### Option 3: skip redundant per-plane checks

The obvious candidates are the all-on screenshot for planes 2..n of the same target and the
first `getFrameTree`.
- **Planes.** No change.
- **Savings.** About 7/8 of the target planes plus 3/4 of the cluster planes skip one
  screenshot. That is about 185 × 47 ms ≈ 9 s on column, plus the trailing all-on double rAF,
  about 4 s. The first `getFrameTree` (13 s) is a renderer synchronisation point, not a
  redundant check. Removing it would probably move the wait elsewhere; this is unverified.
- **Soundness impact.** Real. The all-on equality at `paint.py:811` is the only binding between
  a given document's raster and the original. Option 2 shows that documents with equal DOM can
  still differ in raster. Observation equality (`:843-844`) and geometry equality do not
  substitute for it. Sol already removed the only genuinely duplicate screenshot
  (`svg-chart-render-proof-20261108.md:128`).
- **Size.** Small.
- **Risk.** High in soundness terms, for about 15% savings. **Not recommended.**

### Option 4: bounded pool of parallel diagnostic tabs or contexts

- **Planes.** No change. The wall-clock estimate is 1.8–2.5× faster on the browser-bound part
  with K=2–3, because per-plane time is mostly idle frame-pacing waits. Column would land at
  roughly 35–45 s; combined with Option 1, perhaps 30 s. This is not prototyped.
- **Soundness impact.** None if every plane keeps its own fresh document and identity checks.
- **Size.** Large:
  - `_ChromePipe.command` is strictly synchronous and drops non-matching replies
    (`slide_capture_pipe.py:226-268`), so it needs a demultiplexing reader;
  - `_SharedChartPipe` has a single `plane_active` and one cached sibling identity triple
    (`adapter:61-109`, `:197-206`);
  - `collect_chart_paint_proof` is a sequential loop. Cluster planes depend on target
    observations, and pair/equivalence planes depend on `support_masks`, so it needs a staged
    scheduler.

  Separate Chrome processes per worker thread avoid the multiplexing work, but that departs from
  the documented "same browser transport" property.
- **Risk.** Medium to high:
  - CPU contention with software raster (`--disable-gpu`) and with concurrent capture workers;
  - more memory;
  - a much larger lifecycle and cleanup proof for reviewers.

### Option 5: target-independent planes and disjoint-footprint batching (analysed, rejected)

- **No mode is target-independent.** In `_PLANE` (`paint.py:518-571`), `belongs`, `owned`,
  `strokeOnly` and `later` all depend on the target, and so does `state.paints`, which is
  computed from the selected subtree (`:501-503`). `current-white` and `current-black` recolour
  only the target, and `current-absent` hides only the target. The reference planes hide leaves
  that come later in document order than the target.
- **The only exact deduplication** applies to a glyph target whose single cluster equals its
  whole range. Its 4 cluster planes repeat the target's `alpha`, `reference-white`,
  `reference-black` and `current-absent` planes. That saves 12 of 282 planes on stacked-column
  and 0 on column. Reusing target-level planes for multi-cluster labels would let a neighbouring
  glyph supply response pixels, which contradicts "a neighboring visible digit cannot satisfy
  missing demanded ink".
- **Disjoint batching is blocked for glyph clusters.** Native Selection holds one contiguous
  range, and `render()` throws `chart_text_simultaneous_disjoint_ranges_unproven`
  (`chart_text.py:247-277`).
- **Disjoint batching is unsound for primitives.** The occlusion context ("later" and "owned")
  differs per target. Assigning combined alpha to targets would need masks derived from
  geometry, and the proof explicitly refuses that ("No character rectangle … defines ink").
- **Verdict.** A 0–4% gain is not worth the code.

### Option 6: raise the budget only for manifest-bearing (V5) captures

- **Planes.** No change; 55–105 s on this VM, and 78–95 s measured on Windows.
- **Change.** A V5-only timeout, for example worker 150 s and client 175 s. Today callers omit
  the timeout (default 60 s, `slide_capture_worker_client.py:350`; client
  `max(25, timeout+25)` at `:251`).
- **Soundness impact.** None.
- **Size.** Small, but the timeout reaches shared carrier signatures and needs owner approval,
  since "no caps were increased" was a stated constraint.
- **Risk.**
  - It holds a worker slot for 1.5–2 min per chart slide and multiplies across chart-heavy decks.
  - Plane counts grow linearly with label characters, so it does not scale.
  - It is a reasonable temporary bridge, not a fix.

## 3. Recommendation

**Adopt Option 2 (document reuse with full rebinding, a DOM-state digest and fresh-document
fallback), shipped together with Option 1 (chart-only frame-pacing launch flags).**

- **Budget.** It is the only measured path that brings every real-builder example well inside
  60 s: 30–43 s on this VM, against 55–105 s.
- **Proof output.** Byte-identical to the current proof on every example tested with the
  fallback (column, stacked-column, fixed-column, coincident-line, donut, marker-line).
- **Verification.** Every existing check stays per plane. The change detects residue and falls
  back instead of tolerating it, so no artifact can be accepted that the current design would
  reject.
- **Coverage.** No chart type or configuration loses support.

Implementation conditions:
1. Reuse at most within one target: start a fresh document at each target, pair or class.
2. Replay a full attribute snapshot, then gate on the digest. Treat any digest or all-on
   mismatch as "fall back to fresh", never as a pass or a failure.
3. Bump `probeVersion` and accept only v2 in the consumer.
4. Add a Chromium test that runs every native example in both fresh-only and reuse modes and
   asserts identical proofs, apart from nonce-derived digests.
5. Re-measure once on the Windows product path at the omitted default timeout before claiming
   readiness.

Keep Option 6 as the fallback only if Option 2 is rejected on principle. Do not pursue
Option 3 or Option 5.
