# Authored in-render card fit (design)

Date: 2026-09-06. Status: design, not implemented. Revision 2: amended after a
review pass the same day (the review's findings are folded in below; the
places they changed are marked "rev 2"). Revision 3: a second review against
main at `934886512` found that the renderer image this design builds on cannot
import its own worker (section 7, "prerequisite"), and added the poll bounds,
the verification rollout and the fixture custody rules (marked "rev 3").
Revision 4: a bench replay of the implemented fitter found it engaging cards
the detector does not flag, and separated the entry test from the fit target
(section 4, "rev 4"; the non-goal it settles is in section 2).
Predecessors: `authored-fit-magnitude-measurement-design-20260904.md` (the A4
fit ladder and the M1 magnitudes), `authored-quality-speed-programme-design-20260905.md`
(section 17.2 open items), `generation-programme-master-design-20260904.md`
(A4 / D7 remake-then-flag policy).

## 1. Problem

The model writes a fixed pixel height on a card and fills it with flow copy
whose wrapped height it cannot measure. Bench run 42 (`306391ea6`, prompt
fixture, 24 slides) published two slides flagged `text_escapes_card`:

| slide | card | declared height | copy below the card's inner edge |
|---|---|---|---|
| 13 | `s12-map` | `height:450px` | 21 px (two leaves) |
| 21 | `s20-map` | `height:448px` | 52 px |

Both slides went through every repair rung the pipeline has and came out
unchanged, for reasons that are structural rather than accidental:

* The deterministic text-scale rung (`services/authored_repair_round_order.py`,
  `text_scale_factor`) shrinks the whole slide once, by `1 / worst_overflow_ratio`.
  The ratio is measured against the *card* height, but only the copy shrinks:
  the 24 px card padding, fixed-height step boxes, chip paddings and margins do
  not. Replayed offline on the stored HTML the rung's own factor left 10.6 px
  (13, factor 0.955) and 20.8 px (21, factor 0.896) of escape, so its candidate
  failed admission and the slide fell through to the remake round. A uniform
  0.80 clears both, but shrinks the title and the page number with the copy.
* The remake produced candidates the gate rejected (`remake_gate_failed`), the
  bounded patch round fixed neither (`fixed=[]`), and the deck's shared remake
  budget (`MAX_TERMINAL_REMAKES_PER_DECK = 8`) was spent by then, so the
  terminal round could not try again. Each remake or correction call in that
  run cost 89 to 155 s of wall clock.
* Growing the card instead (`height` to `min-height`) clears the blocker on 13
  (a 5 px advisory footer intrusion remains) but on 21 pushes the card 36 px
  into the page-number band. Neither grow nor shrink alone is the answer; the
  designer's order is grow into slack first, then shrink the remainder.

An out-of-browser fitter that applies a computed per-card factor and confirms
with a render converges slowly because Korean line wrapping is quantised: a
2 % smaller font seldom removes a line, then one step removes a whole line.
Measured with a residual-driven step on the same two slides: 6 confirm renders
(7.0 s) for slide 13 and 5 (6.9 s) for slide 21, at 0.7 to 1.0 s per render.
Inside the page the same search is a handful of synchronous layouts.

## 2. Goal and non-goals

Goal. Every authored slide leaves its *first* render with no owned text
outside the card that owns it, without a provider call, without a second
render, with the adjustment written into the persisted HTML so the exported
PPTX (which deliberately emits `<a:noAutofit/>`) matches, and with the
adjustment reproducible from a small report so that the persisted document
and the measured document are layout-equivalent, a property that is verified
by a sampled re-render rather than assumed (section 5).

Non-goals. The fitter does not fix overlap, off-canvas, occlusion, clipping
inside `overflow:hidden`, contrast, or footer intrusions that are not caused
by a card escape; those keep their detectors and rungs. It does not touch
infographic figures (`.ig` roots and parts) or SVG text. It does not change
the request or response wire versions of the renderer worker, nor its markup
attribute allowlist. Rev 2: it does not scale paddings, margins or gaps; a
card whose escape is caused by fixed-height rows rather than copy reaches the
font floor and is reported unfixable (section 4, step 5). Extending the lever
to vertical spacing is a v2 item because it widens the reconstruction surface.
Rev 4: it never modifies a card the detector would not flag. A slide with no
`text_escapes_card` defect leaves the fitter byte-identical -- no grow, no
shrink, no inline declaration, and no entry in the report's `cards` or `edits`.

## 3. Where it runs

The in-page probe (`utils/slide_capture.py`, `_instrument_authored_html`) is
injected by the worker itself before `Page.setDocumentContent`
(`utils/slide_capture_pipe_render.py`), runs `done()` on `DOMContentLoaded`
and again after `document.fonts.ready`, writes its payload into the
`__codex_authored_dom_metrics` meta marker, and only then does the worker
read the marker and take the screenshot. That is the seam: a **fit pass runs
inside the probe, before the measurement pass, on both `done()` invocations**,
so the marker and the PNG both describe the fitted DOM.

```
probe.run()
  ├─ done()                      # DOMContentLoaded: fallback fonts
  │    ├─ fit.reset()            # restore every element the fitter touched
  │    ├─ fit.apply()            # grow / shrink, synchronous layouts only
  │    └─ measure() → marker     # existing metrics + fit report, fit_state
  └─ fonts.ready.then(done)      # final glyph geometry: reset, refit, remeasure
worker: Runtime.evaluate reads marker (waits for fit_state=final) → Page.captureScreenshot
```

Idempotence across the two invocations is mandatory: the fitter records the
original `style` attribute string of every element it edits and restores all
of them before refitting, because the second pass sees real web fonts and
must not compound the first pass's shrink.

Rev 2, robustness. `fit.apply()` is wrapped so that any exception inside it
restores the recorded styles, records `fit.error` (class name and a bounded
message) in the report, and lets `measure()` run over the unfitted DOM. A
fitter defect must never make the metrics unavailable, because unavailable
metrics block publication.

Rev 2, final-pass ordering. The worker's evaluate expression and the probe
both hang off `document.fonts.ready`, and relying on microtask registration
order to decide which runs first is fragile. The report carries
`fit_state: "provisional" | "final"` (`final` is set by the `done()` that runs
after `fonts.ready`, or by the first `done()` when the document has no web
fonts). The evaluate expression in `slide_capture_pipe_render.py` (already an
attested file) re-reads the marker until `fit_state` is `final`. Rev 3, the
bound is a count and an interval, not a deadline: at most 20 polls 50 ms
apart (one second in total, inside the worker's own timeout, which is 1 to
120 s). After the twentieth poll it takes the marker as it is; the report then
carries `fit_state: provisional` and the deck instruments count it.

Rev 2, opt-in signal. The probe fits only when the request HTML carries the
class `codex-fit-v1` on the `<html>` element. `class` is a global attribute
in `utils/renderer_markup_policy.py`, so the marker passes admission without
touching the allowlist; a `data-*` name would be rejected as
`HTML attribute is not allowed`, because that allowlist enumerates attribute
names and has no `data-` prefix rule. The model cannot know the class name,
so no authored stylesheet can target it. This keeps `authored-render-request/v1`
and `authored-render-response/v1` unchanged, needs no addition to the worker's
environment allowlist (`_WORKER_ENV_ALLOWLIST` carries no `AUTHORED_*` key),
and lets the API render the *same* HTML without fitting when it wants a pure
measurement (verification, replay tooling). The class is present only on the
render request; it never reaches the persisted document.

Rev 3, where the probe actually runs. In isolated mode without
`AUTHORED_RENDERER_WORKER_COMMAND_JSON` the worker is
`[sys.executable, "-m", "utils.authored_renderer_worker"]` from the same
virtual environment (`utils/renderer_worker_policy.py`, `worker_command`), so
on the Windows development and bench hosts the fitter is exercised by the
same source tree the API runs, with no container involved. The attested
container image matters under the production tiers that require an external
worker (`renderer_requires_external_worker`). Steps 1 to 3 of the sequencing
in section 11 are therefore verifiable locally; the image work in section 7
gates only the production tier.

## 4. Algorithm (in-page)

Constants are interpolated from the Python detectors, never retyped in JS
(section 7): `ESCAPE_TOLERANCE_PX` (2), the card predicate of
`utils/authored_dom_text_escape.py` (`_is_card_like`: painted tag, non-transparent
background, background image, any `overflow` other than `visible`, any radius,
a shadow, or four borders), the ink-extent rule (em box of the glyph range
rects, not line boxes), `NUMBERED_CONTENT_BOTTOM_PX` (648) /
`UNNUMBERED_CONTENT_BOTTOM_PX` (688) and the page-number geometry rule from
`utils/authored_dom_footer_zone.py`, and `AUTHORED_FONT_FLOOR_PX` (12) from
`services/authored_vnext/capability_registry.py`.

Rev 2, target margin. The fitter does not fit to the detector's tolerance. It
fits to `FIT_MARGIN_PX = 6` *inside* the card's inner edge, so a slide that
passes here still passes when the exported deck is measured by a different
Chromium on a different OS with a different Korean fallback font. The
detector's 2 px tolerance is the floor of acceptance, not the target.

Rev 4, entry is not the target. Rev 2 used one test for both questions: a card
was engaged whenever its ink came within `FIT_MARGIN_PX` of the inner edge,
which is 4 px stricter than the detector that defines the defect, so the
fitter changed slides that have no defect. Replaying both bench decks (44
slides of published HTML, real Chromium) measured the collateral: run 42 slide
7 (`s7-test`) had no escape before or after and was shrunk to factor 0.9016,
dropping 6 text elements a mean of 2.10 px (22 -> 19.8, 20 -> 18, 18 -> 16.2);
run 41 slide 15 had no escape and was shrunk to 0.9401 across 4 elements; run
42 slide 2 grew a card by 7 px and slide 15 by 3 px, both with no escape and no
font change. The harm is not smaller type as such -- it is that one card ends up at
15.4 px while visually identical cards on other slides stay at 18 px.

Entry is therefore the detector's question and only the target is the
fitter's. A card is engaged only when its ink passes the card's inner edge
(padding box, margin 0) by more than `ESCAPE_TOLERANCE_PX` -- exactly when
`text_escapes_card` would fire for it. "Would fire" is the *reported*
category, so entry also mirrors `reconcile_footer_zone_reports`: a leaf whose
ink passes the footer content line while the card itself hangs below that line
is reported as `text_intrudes_footer_zone`, not as an escape, and it does not
engage the card. That reconciliation, not the target margin, is what run 42
slide 7 (card bottom 686, escape 8 px, content line 648) and run 41 slide 15
(card bottom 651, escape 4 px) tripped: both escapes are real and both are the
footer line's business, because returning that copy to its card would still
leave it printed under the page number. Once engaged nothing else changes: it is
still fitted to `FIT_MARGIN_PX` inside that edge (step 2), so the export
renderer's different font metrics keep their headroom, and `escape_px` in the
report stays the margin-0 escape over the leaves the detector counts (22 px on
run 42 slide 12, unchanged by this revision). A card within
tolerance is left completely untouched: no grow, no shrink, no inline style, no
`edits` entry, no `cards` entry. The tradeoff accepted: a card that passes the
detector by 1 px now gets no export headroom at all. That is the deliberate
price of never touching a slide the detector passes.

0. **Snapshot.** Before any edit, record for every candidate element its
   `style` attribute string and its computed `font-size` and `line-height`.
   All factors are applied to the snapshot values, never to values read after
   an earlier edit, so a parent's shrink cannot be compounded into a child's.
1. **Cards.** All `[data-qid]` elements satisfying the card predicate, excluding
   `html`, `body`, `.ig` roots and their parts, and elements inside `<svg>`.
   Process **innermost first** (post-order) so an inner card is settled before
   its container is measured. An outer card's leaf set includes the leaves of
   inner cards; those are scaled again relative to their snapshot size times
   the inner factor, and the floor applies to the product.
2. **Escape.** For a card, its owned-text leaves are the `[data-qid]`
   descendants whose owned text nodes belong to them (same definition as the
   metrics' `owned_text`). `escape = max(ink_bottom − (card_inner_bottom −
   FIT_MARGIN_PX), ink_right − (card_inner_right − FIT_MARGIN_PX), 0)` over
   those leaves, inner edges being the padding box. Leaves that are
   `position:absolute|fixed` make the card *unfixable* (a fit cannot reason
   about them); skip and report.
3. **Slack.** `slack = min(content_line, ancestor_line) − card_outer_bottom`,
   where `content_line` is 648 when the slide has a page-number element by the
   footer detector's rule, else 688, and rev 2 `ancestor_line` is the inner
   bottom of the nearest ancestor whose computed height comes from an explicit
   `height` or whose `overflow` is not `visible` (a grown card must not be
   clipped by its own container, or the fit merely converts an escape into a
   clipping defect). Slack is further reduced so the grown card stays above the
   top of any `[data-qid]` element that starts below the card and overlaps it
   horizontally. Slack applies only to a card whose computed height came from
   an explicit `height`; a card already sized by content is not grown.
4. **Grow.** `grow = min(escape, max(slack, 0))`; if `grow > 0`, set inline
   `height: <outer height + grow>px` on the card and re-measure escape.
5. **Shrink.** If escape is still positive, bisect a factor `f` in
   `[f_floor, 1]` applied to every owned-text leaf of the card: inline
   `font-size: round(font × f, 1)px`, and `line-height: round(lh × f, 1)px`
   when the snapshot line height is a pixel value (a `normal` line height is
   left alone). `f_floor = max(12 / leaf_font)` over the leaves, so no leaf
   goes under the floor. Seven bisection steps give a 1 % resolution; each step
   is one synchronous layout. Keep the largest `f` whose escape is zero. If
   even `f_floor` escapes, revert the card entirely and report
   `unfixable:floor`; the gate then routes the slide to the existing remake.
6. **Budgets.** At most 16 cards per slide and 160 layouts per slide (both
   constants in the fragment module, not knobs); a 10 % time budget of the
   worker timeout. Past the budget the fitter stops, reports `partial: true`,
   and the remaining cards stay with the gate.
7. **Style application.** Rev 2: edits are written with
   `setAttribute('style', original + ';' + declarations)`, a plain string
   append, never through CSSOM property assignment. CSSOM assignment
   re-serialises the whole attribute (colour formats, shorthand order, dropped
   invalid declarations), which would make the in-page attribute differ from
   the string the API reconstructs. With the append, the attribute the browser
   measured and the attribute the API persists are the same bytes.
8. **Report.** Written into the metrics payload as `fit`:

```json
{
  "version": "authored-in-render-fit/v1",
  "requested": true,
  "fit_state": "final",
  "partial": false,
  "error": null,
  "cards": [
    {"qid": "s20-map", "escape_px": 52.0, "grow_px": 0, "factor": 0.833,
     "outcome": "shrunk", "layouts": 8},
    {"qid": "s12-map", "escape_px": 21.0, "grow_px": 21, "factor": 1.0,
     "outcome": "grown", "layouts": 2}
  ],
  "edits": [
    {"qid": "s12-map", "declarations": "height:471px"},
    {"qid": "s20-tier1-copy", "declarations": "font-size:15px;line-height:22.5px"}
  ]
}
```

`edits` is the complete, ordered list of declaration strings the fitter
appended, verbatim. Everything in the element metrics (`bbox`,
`scrollHeight`, ink rects, contrast) describes the DOM *after* these edits.

## 5. Making the fitted HTML the HTML of record

The API must never persist metrics for one document and HTML for another.
The ledger, the metrics cache (`_metrics_cache_key(html)`), the checkpoint
(`illustrated_html`) and the scene envelope (`source_digest`) are all keyed
by the exact HTML string, so the fitted document has to be reconstructed on
the API side and become the string every one of them sees.

* Rev 2, precondition. `render_html_to_png_with_metrics(html, *, fit=False)`
  gains the flag. With `fit=True` the caller must pass qid-complete HTML: the
  function asserts `ensure_authored_qids(html) == html` and raises otherwise.
  The worker stamps missing qids itself before measuring, so a report's qids
  refer to the stamped document; requiring the caller to have stamped first
  is what makes the qids in the report resolvable on the API side. Every seam
  in this design already canonicalises before rendering
  (`canonicalize_authored_roles`, `ensure_authored_qids`).
* With `fit=True` the function sends `html` with the `codex-fit-v1` class on
  `<html>`, and on return, if `metrics["fit"]["edits"]` is non-empty, computes
  `fitted_html = apply_fit_edits(html, edits)` and returns a `RenderCapture`
  whose `html_digest` is the digest of `fitted_html`, with two new fields
  `fitted_html` and `fit_report`.
* Rev 2, metrics identity. The gate
  (`services/authored_composition_policy.py`) accepts metrics only when
  `metrics["source_digest"]` equals the digest of the HTML it is handed and
  `render_digest` is well-formed. `_cache_browser_metrics(fitted_html,
  metrics)` rewrites `source_digest` to the digest of its key, so the fitted
  metrics are cached under `fitted_html` and under nothing else. The
  `render_digest` and `metrics_cache_key` the probe embedded describe the
  directive-bearing instrumented input and stay as evidence; the gate only
  checks their shape and their internal consistency with the occlusion
  evidence, which the fit does not touch.
* Rev 2, no redirect. There is no cache entry under the pre-fit string. A
  redirect entry would be evicted independently of the fitted entry by the
  LRU and reproduce the run-24 failure class (`capacity.browser_observation_missing`
  after eviction). Callers hold `capture.fitted_html` and use it for every
  later cache read.
* `apply_fit_edits` is a string transform over start tags, reusing the tag
  scanner of `utils/authored_qid_namespace.py`: for each edit, find the element
  with that `data-qid`, append `;<declarations>` to its `style` attribute (or
  add `style="<declarations>"`), in report order, using exactly the append the
  probe used. It refuses (raises) on a qid that is missing or duplicated, and
  is idempotent because a second application finds the declarations already
  present verbatim. Because the in-page fitter appended the same bytes to the
  same attributes, the reconstructed document and the measured document are
  the same document as far as the parser and the cascade are concerned.
* Callers that persist HTML take `capture.fitted_html`:
  * the draft render in `_author_illustrate_render_pipeline`
    (`services/authored_presentation_pipeline.py`, the block that calls
    `render_html_to_png` then `ensure_browser_metrics(illustrated_html)`)
    replaces `illustrated_html` with the fitted string before
    `validate_visible_source_images`, the `render` stage completion and
    `evidence_ledger.record_slide`, so the `render` checkpoint stores the
    fitted document and a resume that re-reads the checkpoint sees it;
  * `_admit_remade_slide` and `_apply_text_scale_rung`
    (`services/authored_terminal_slide_fallback.py`, `authored_repair_round_order.py`)
    render candidates with `fit=True`, so a remade slide is fitted before it is
    admitted.
  * Rev 2, excluded in v1: `_bind_bounded_repair_evidence`
    (`services/authored_presentation_generation.py`). That seam binds the
    reducer's exact document and is annotated that it must not re-normalise
    anything; a closed-operation repair result must stay the document the
    repair plan was proven against. Bounded-repair outputs are therefore
    measured without fitting in v1; if they escape, the existing rungs apply.
* Verification render. `AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE` (0 to 1, default
  0 in production, 1 in the bench) re-renders `fitted_html` *without* the
  class and asserts: zero `text_escapes_card`, and every element `bbox`
  within 0.5 px of the fitted metrics. A mismatch is logged as
  `authored_fit_reconstruction_mismatch` with both digests, counted in the deck
  instruments, and the slide's evidence is replaced by the verification
  capture's metrics (the fitted HTML stays). This is how the layout-equivalence
  claim is checked rather than assumed.

The DOM metrics version string stays `authored-dom-metrics-v16-owned-text`:
`fit` is an additional key and the per-element schema is unchanged.
`services/authored_composition_policy.py` compares that string for equality,
so a bump would be a breaking change for no benefit.

## 6. What changes for the rest of the pipeline

* Detectors are unchanged. `text_escapes_card` still runs on the fitted DOM and
  still blocks; it simply fires only for cards the fitter reported unfixable,
  skipped, or left partial.
* The text-scale rung keeps its job for `overflow` and `clipping`. Its
  shrinkable set (`TEXT_SCALE_SHRINKABLE_CATEGORIES`) keeps `text_escapes_card`
  for the case where the fitter was off, errored or partial.
* Remake budgets, the terminal round and D7 flagging are untouched.
* Receipts and instruments: the quality receipt gains `fittedSlideIndices`
  and `fitEditCount`; `instruments.deck` gains `fit_cards_grown`,
  `fit_cards_shrunk`, `fit_cards_unfixable`, `fit_errors`,
  `fit_reconstruction_mismatches`, `fit_factor_min` and the sorted
  `fit_escape_px` distribution, next to the footer-intrusion instruments.
* Export: the authored-hybrid browser source measures the persisted HTML, so
  the inline sizes carry into the native OOXML text runs; `noAutofit` stays.
  The 6 px target margin (section 4) is what absorbs font-metric differences
  between the worker's Chromium and the export renderer.
* The `AUTHORED_FONT_FLOOR_PX` floor is the same 12 px the relaxed envelope
  enforces, so the fitter can never produce a font the gate would reject.

## 7. Isolation, attestation and the contract

The probe runs inside the attested worker image, so every byte of the fitter
is part of the security boundary.

* New module `servers/fastapi/utils/slide_capture_fit_script.py` (≤ 350 lines)
  builds the JS fragment as an f-string with the constants imported from
  `utils/authored_dom_text_escape.py` and `utils/authored_dom_footer_zone.py`.
  `slide_capture.py` is at the ratchet baseline and only gains the call site
  and the `fit=` plumbing; the fragment lives in the new module. A unit test
  renders the fragment and asserts the interpolated numbers equal the Python
  constants, so the two detectors and the fitter cannot drift.
* Rev 3, prerequisite: the image cannot import the worker today. A static
  import walk from `utils/authored_renderer_worker.py` over the project
  packages reaches 21 modules; the `authored-renderer-runtime` `COPY` list in
  `Dockerfile` names 14, and `IMAGE_SOURCES` in
  `contracts/authored-renderer/attestation.py` (21 entries, several of them
  Next.js files) has the same gap. Missing, with top-level imports among them:
  `utils/renderer_markup_policy.py` and `utils/renderer_css_policy.py`
  (imported at the top of `renderer_isolation.py`, both created 2026-08-19,
  the `COPY` block last edited 2026-08-14), `services/authored_vnext/capability_registry.py`
  and `utils/renderer_frame_codec.py` (top of the worker module),
  `utils/slide_capture_glyph_probe.py` (top of `slide_capture_pipe_render.py`),
  `utils/authored_renderer_session.py`, `utils/authored_qid_namespace.py`,
  `utils/authored_dom_tags.py`, `utils/authored_dom_text_escape.py`,
  `utils/authored_dom_footer_zone.py`, `utils/authored_dom_categories.py`,
  `utils/authored_dom_fit_magnitude.py`, `models/authored_vnext/deck/scene.py`
  and `utils/__init__.py`. Nobody saw it because the local isolated worker
  runs from the venv (section 3) and the CI workflow accepts a
  `docker-unavailable-observation.json` as the dynamic gate's input
  (`.github/workflows/authored-renderer-security.yml`), so the image is built
  nowhere. This is a pre-existing defect, not one this design introduces, and
  it is repaired **first, in its own commit, with no fit code in it**:
  1. `verify_contract.py` gains a check that computes the worker's static
     import closure and asserts every module in it is named by both the
     Dockerfile stage and `IMAGE_SOURCES`; the closure is derived, never
     listed by hand again, so the list cannot fall behind a third time.
  2. The `COPY` list and `IMAGE_SOURCES` are brought up to the closure. The
     `services` package enters the image through the one module the worker
     already imports (`capability_registry.py`), so the earlier plan to keep
     `services` out of the image by re-exporting the font floor is dropped;
     the constant is imported from where it lives.
  3. The dynamic gate is run against the rebuilt image, and the image must
     answer one render request end to end before the fit work starts.
  Another session has announced a change to the same workflow and contract
  directory (reducing the security gate to container-only); that change
  surfaces this defect on its own, so the repair is coordinated with it and
  whichever lands first carries the closure check.
* With the closure repaired, this design adds exactly one module to it,
  `utils/slide_capture_fit_script.py`; the detector modules it imports are
  already in the closure by then.
* `contracts/authored-renderer/attestation.py` `IMAGE_SOURCES` gains the three
  files. The source-tree digest, the contract digest and the deployment
  attestation are regenerated per the README procedure, and
  `run_dynamic_gate.py` is re-run against the rebuilt image.
* `contracts/authored-renderer/verify_contract.py` gains a check that the
  fit module is listed in both the Dockerfile stage and `IMAGE_SOURCES`, and
  that the probe grants the fit pass only behind the `codex-fit-v1` class. The
  wire checks (`authored-render-request/v1`, `authored-render-response/v1`)
  are unchanged and must stay green. The markup attribute allowlist is not
  edited.
* The evaluate expression in `slide_capture_pipe_render.py` changes to poll
  for `fit_state` (section 3, 20 polls at 50 ms); that file is already in
  `IMAGE_SOURCES`.
* CSP: the fitter is part of the existing nonce-bearing probe script; no new
  script element, no inline event handlers, no network.
* Response size: a report is a few kilobytes against `MAX_RESPONSE_BYTES`
  (32 MiB); no new limit.

## 8. Knobs (registered in `constants/settings_registry.json`, read through `utils/get_env_programme.py`)

| key | default | meaning |
|---|---|---|
| `AUTHORED_IN_RENDER_FIT_ENABLED` | `false` until the bench passes, then `true` | request the fit pass on draft and remake renders |
| `AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE` | `0.05` at first production enablement, `0` after two weeks | fraction of fitted slides re-rendered for the reconstruction check |

Rev 2: the card and layout budgets, the margin, the floor and the tolerances
are constants, not knobs. The registry already carries more than 130 keys and
none of these has a per-deployment reason to differ.

Rev 3, verification rollout. The bench runs with the sample at 1. Production
turns the fit on with the sample at 0.05, so roughly one fitted slide in
twenty pays one extra render, and `fit_reconstruction_mismatches` in the deck
instruments is watched for two weeks of real decks. It goes to 0 only when
that counter has stayed at zero across at least 200 fitted slides; a non-zero
count keeps the sample where it is and opens a defect. Without this step the
layout-equivalence claim would be verified on 44 bench slides and never
again.

## 9. Verification plan

Unit (no browser): fragment/constant parity (including the re-exported font
floor); `apply_fit_edits` determinism, idempotence and refusal on a missing or
duplicated qid; the qid-complete precondition; report parsing; the
`fit_state` poll in the evaluate expression against a stubbed marker.

Integration (Chrome, `tests/integration/test_authored_in_render_fit.py`): the
two run-42 slides are checked in as fixtures (`s12.html`, `s20.html`, taken
from the bench database of `3232141a-45bc-4aec-965b-39a5ec39fd44`). Rev 3,
custody: that database lives in another session's scratch directory and can
vanish with it, so the two files are copied to
`servers/fastapi/tests/fixtures/authored_fit/run42-s12.html` and
`run42-s20.html` **now**, with this design, not with the implementation; the
matching browser metrics from `artifacts/authored-fixture-run42-20260905/slides/`
are copied beside them so the unit tests can replay the detector without a
browser. Expected:
slide 13 grown (about 27 px with the margin) with no font change and no
blocking issue; slide 21 shrunk to a factor near 0.82 with every leaf at or
above 12 px and no blocking issue; the reconstruction render matches within
0.5 px; a slide with an absolutely positioned leaf is reported `unfixable`
and left byte-identical; a fitter exception (injected) yields metrics with
`fit.error` set and no other difference from an unfitted render; a card
inside a fixed-height `overflow:hidden` container is shrunk, not grown.

Contract: `verify_contract.py`, the attestation digests, the framing tests
(`tests/unit/test_authored_renderer_worker_framing.py`) and the session-pool
tests stay green with the rebuilt image.

Bench: runs 43 (prompt fixture) and 44 (document fixture) at the shipping
configuration plus `AUTHORED_IN_RENDER_FIT_ENABLED=true` and verify sample 1,
compared with 42 and 41. Acceptance:

| measure | 41 / 42 today | acceptance |
|---|---|---|
| `text_escapes_card` at the first gate | 2 (prompt), 1 (doc) | 0, or only `unfixable` cards |
| published flagged | 2 / 0 | 0 / 0 |
| remake calls spent (receipt `remakeCount`) | 8 / – | fewer than today on the prompt fixture |
| fit report present on fitted slides | – | 100 % |
| reconstruction mismatches | – | 0 over the 44 slides of both fixtures |
| `fit.error` | – | 0 |
| median body font on body slides | 16 px | ≥ 15 px (no deck-wide shrink) |
| authoring + repair wall | 1979 s / 2099 s | not slower |

## 10. Risks and limits

* Two-pass fonts. The first `done()` runs on fallback fonts; the fit there is
  provisional and is redone after `fonts.ready`. If web fonts never settle
  within the worker's virtual-time budget, the provisional fit is what gets
  measured and screenshotted, the report says `fit_state: provisional`, and
  that is the same exposure the metrics have today.
* Reconstruction drift. With string-append edits, a qid-complete precondition
  and refusal on unresolvable qids, the remaining ways the API's string and the
  worker's DOM can diverge are parser-level (an invalid declaration the
  browser dropped in-page but the string still carries; harmless, dropped
  again on re-parse). The verification render measures the residual.
* Cross-renderer fonts. The export path measures the persisted HTML in a
  different Chromium with different fallback fonts. The 6 px target margin is
  the mitigation; it is a judgement, and the bench's export QA on runs 43/44
  is where it is checked.
* Layout side effects. Shrinking one card in a flex or grid row can reflow its
  siblings; the fitter measures live, so escapes it creates in a sibling are
  caught when that sibling is processed, and a card processed earlier is
  re-checked once at the end. The fitter does not undo a sibling's fit to
  favour another; it reports both.
* Lever limit. Escapes caused by fixed-height rows inside the card reach the
  floor and are reported unfixable (section 2); those slides take the remake
  path as they do today.
* Semantic cost. Growing a card consumes bottom slack that the footer detector
  treats as advisory; shrinking reduces type in one card only. The paired
  ratings of 2026-09-06 showed the owner prefers the parent's 12 to 16 px copy,
  so a card at 14 to 15 px is inside the accepted range, and the floor keeps it
  there.
* Not addressed here: the `contrast` category firing on every slide, which
  makes the vision-QA model call unreachable (programme design section 17.2);
  it is unrelated to fitting and stays open.

## 11. Work breakdown (rev 2 estimates)

| item | files | size |
|---|---|---|
| rev 3, prerequisite: image import-closure check and repair | `contracts/authored-renderer/verify_contract.py`, `attestation.py`, `Dockerfile` renderer stage | ~80 lines + rebuild + dynamic gate; its own commit |
| fit fragment + constants parity test | `utils/slide_capture_fit_script.py`, `tests/unit/test_slide_capture_fit_script.py` | new, ~350 + ~150 lines |
| probe integration behind the class, robustness wrapper, `fit_state` | `utils/slide_capture.py` (`_instrument_authored_html`, `run()` sequencing) | ~60 lines |
| evaluate poll for `fit_state` | `utils/slide_capture_pipe_render.py`, `slide_capture_reusable_render.py` | ~40 lines |
| API reconstruction, precondition, capture fields, cache under fitted key | `utils/slide_capture.py` (`render_html_to_png_with_metrics`), new `utils/authored_fit_edits.py` | ~80 + ~180 lines |
| callers persist the fitted string (draft, remake, rung) | `services/authored_presentation_pipeline.py`, `authored_terminal_slide_fallback.py`, `authored_repair_round_order.py` | ~60 lines |
| verification render and mismatch handling | `utils/slide_capture.py` or new `utils/authored_fit_verify.py` | ~120 lines |
| receipts and instruments | `services/authored_presentation_generation.py`, `utils/authored_deck_instruments.py` | ~50 lines |
| knobs | `constants/settings_registry.json`, `utils/get_env_programme.py`, generated compose/docs surfaces | ~25 lines |
| image and contract | `Dockerfile`, `contracts/authored-renderer/attestation.py`, `verify_contract.py`, README attestation refresh, dynamic gate | ~30 lines + rebuild |
| integration fixtures and test | `tests/integration/test_authored_in_render_fit.py`, `tests/fixtures/authored_fit/` | new |
| bench runs 43 / 44 and the comparison note | `artifacts/`, `docs/` | – |

The sequencing that keeps main green at every commit: image closure check
and repair (prerequisite, coordinated with the renderer-security gate change
another session has announced for `contracts/authored-renderer/`; whichever
lands first carries the check); fixtures; fragment + unit tests; probe + API
reconstruction behind the knob (off); callers; add the one new module to the
image; integration test; bench at verify sample 1; production at 0.05; flip
the default; sample to 0 after two weeks at zero mismatches.
