# Would local fit repair the slides a verbatim deck publishes flagged?

Date: 2026-09-11. Paired offline replay, no generation, no provider call, no database write.

## Method

The three published runs of the verbatim 35-slide fixture (`repeatability-20260911.md`
runs 11, 15 and the later run 16 — same input PPTX `7746aaed…`, 289 source text blocks,
`hyundai-culture-center`, `content_policy.text=verbatim`) shipped 7, 17 and 11 slides with a
review flag. For each of those **35 flagged slides** the stored `slides.html_content` was
rendered twice in real headless Chromium with the production policy `authored-local-fit/v1.5`:

1. as it shipped (`fit=False`) — judged by the pipeline's own `inspect_authored_dom` +
   `blocking_safety_issues`;
2. through the fitter (`fit=True`), whose output was re-rendered `fit=False` and judged again.

Script: `replay_fit_on_flagged.py`; per-slide records in `fit-replay-run11.json` and
`fit-replay-run15-16.json`. Deck-level baseline reader: `compare_fit_runs.py`.

**Parity check.** The offline `before` verdicts match the stored receipts: run-11 slide 26
`overlap ×1`, slide 29 `overlap ×3 + text_escapes_card ×3` — exactly the deck's recorded
`residualIssues`. The gate used here is therefore the gate that actually ran.

## Result

| verdict | slides |
|---|---|
| `unchanged` — the fitter made no edit at all | 23 |
| `no_change` — the fitter edited, blocking count unmoved | 10 |
| **`improved`** | **1** (run-16 slide 6: `text_escapes_card` cleared, `empty_media_panel` left) |
| **`resolved`** | **1** (run-15 slide 22: `text_escapes_card ×1` → none) |
| `worse` | **0** |

Only **13 of the 35 flagged slides carried a blocking defect on the published bytes**; the other
22 were flagged by `composition_safety:gate_repair_not_admitted` during generation, so no
geometry repair could have changed what shipped. Defect instances over all 35 slides:

| category | before | after |
|---|---|---|
| `overlap` | 9 | 9 |
| `text_escapes_card` | 7 | 5 |
| `empty_media_panel` | 6 | 6 |
| `clipping` | 2 | 2 |
| `off_canvas` | 1 | 1 |

The fitter reported `unresolved` on 8 slides (run-11 16/29, run-15 21/24, run-16 9/19/25/26) —
it tried and its bounded candidate search found nothing admissible.

## The verbatim objection is not supported

Turning the fit on was expected to risk shrinking verbatim source text. Measured: it shrank text
on **2 of 35 slides** (run-15 slides 20 and 22), **worst 5.00%**, and the smallest resulting type
over every slide was **11.25pt** — inside the 10% cumulative cap and well above the 9.0pt floor.
No slide got worse on any axis.

## Conclusions

- Enabling `AUTHORED_LOCAL_FIT_SOURCE_REFLOW` is **safe** on this evidence: zero regressions,
  shrink bounded at 5%, floor never approached.
- Its **yield on the residual set is low**: 1 resolved + 1 improved out of 35 flagged, 2 of the 13
  slides that actually carried a defect.
- What remains is dominated by **`overlap` (9 instances, unmoved)** and `text_escapes_card`, and the
  fitter says `unresolved` on most of them. That is the same wall as the two capabilities parked
  as strict xfail in `tests/integration/test_authored_infographic_fit_chrome.py`
  (auto-height scope root; slack outside the grid's parent) — see
  `docs/authored-local-fit-overlap-space-design-20260909.md` section 10.
- `empty_media_panel` (6 instances) is out of scope for any geometry fitter; it needs content.

## Limitation, stated plainly

This replays the fitter on **published** bytes, i.e. after the bounded LLM repair round already
ran. In a real ON generation the fitter runs *before* that, on pre-repair HTML, where more would
be available to fix. **These numbers are a lower bound on the effect, not the effect.** A single
end-to-end ON run would measure the full effect, but it cannot establish it either: the flagged
count across the three OFF runs is 7 / 17 / 11, so one ON number would fall inside the control
spread. Deciding the knob on end-to-end counts needs several runs per arm.

---

# Why the fitter cannot repair them (follow-up, same day)

Nine slides were re-rendered with every candidate's rejection recorded:
the eight the replay left `unresolved`, plus run-11 slide 26. Script
`diagnose_unresolved.py`, per-candidate records `unresolved-diagnosis.json`.
347 non-duplicate candidate rejections.

## The largest blocker is not either parked capability

| rejection rule | count | share | addressed by the parked (a)/(b)? |
|---|---|---|---|
| `fixed_horizontal` | 148 | **42.7%** | **no** |
| candidate did not fix or broke something (`flow_box_escape` 39, `overlap` 33, `text_escape` 10, `nested_escape` 8) | 90 | 25.9% | no |
| `parent_space_unavailable` | 78 | 22.5% | yes |
| `parent_growth_cap` | 18 | 5.2% | partly (a cap, not the root choice) |
| `vertical_movement_cap` | 13 | 3.7% | partly |

**(a) auto-height scope root and (b) slack outside the grid's parent together
address at most 27.7% of what actually rejects these candidates.**
`fixed_boundary_unavailable` does appear in `parent_exclusions` on four slides,
so (a) is real — it is simply not the main cost.

## A third defect: vertical-only fit versus content-sized text

`_scopeHard` rejects a candidate when any element's `left` or `width` moves more
than the tolerance (`preserved_properties: ['left', 'width']`). On 5 of the 8
slides that rule is the dominant rejection (24-35 of each slide's 35-63), and each
slide has one element that drifts:

| run / slide | drifting element | its box |
|---|---|---|
| run-16 s9 | `q-span-8e4418d14ca4` | `<span class="check-option">`, `white-space:nowrap` |
| run-15 s24 | `q-principle-emphasis-24` | bare `<strong>` |
| run-11 s29 | `q-pillar-target` | `.pillar{height:58px;display:flex;…}`, no width |
| run-11 s16 | `source-block-f67d8c2c0cf8f503` | — |
| run-16 s25 | `q-span-5fba4a1b38e4` | — |

All are **auto-width, content-sized text boxes**: their width is a function of the
type size the fitter is allowed to change. Any candidate that shrinks type
therefore moves their `left`/`width`, and the whole candidate is rejected as
horizontal drift — although the fit was vertical in intent. Spacing-only
candidates do not reflow text and are unaffected, which is why the two slides the
replay did repair used `spacing_adjusted` / `combined`.

This is not (a) or (b). It is the vertical-only preservation contract being
applied to boxes whose width cannot be held while their type changes.

## Overlaps are genuine

All nine `overlap` instances were checked against the intentional-overlap marker.
`utils/slide_capture.py` emits `intentional` from `data-allow-overlap` and
`utils/authored_dom_quality.py` honours it, so the gate side of that guard was
always live (only the fitter read a dead name, fixed in `453bece2d`).
**None of the nine elements is marked intentional, in the metrics or in the HTML.**
They are real defects, not mis-flagged decoration.

## (c) simulation — exempting content-sized boxes from the width verdict

The section above named the blocker but not its size, so the exemption was
simulated directly. `simulate_content_width_exemption.py` patches the geometry
fragment **in this process only** — no file in `servers/` changes — and re-runs
the nine failing slides under five definitions of "content-sized". `left` stays
preserved throughout; only the width half of the verdict is relaxed.

| arm | what is forgiven a width change |
|---|---|
| `baseline` | nothing (production `authored-local-fit/v1.5`) |
| `inline` | inline-family text leaves |
| `shrink` | also shrink-to-fit flex/grid items |
| `measured` | any **text leaf** measured as wide as its own content and narrower than its parent offers |
| `box` | the same measurement, container or leaf — the widest reading of (c) |

### Result: no slide changes verdict, in any arm

| arm | verdicts over the 9 slides | `fixed_horizontal` rejections |
|---|---|---|
| `baseline` | 9 × `unchanged` | 148 |
| `inline` | 9 × `unchanged` | 116 |
| `shrink` | 9 × `unchanged` | 92 |
| `measured` | 9 × `unchanged` | 92 |
| `box` | 9 × `unchanged` | 92 |

The patch demonstrably ran — the rejection count falls — but no candidate ever
survives to produce an edit, so every arm ships the same bytes. Worst shrink stays
0.000 and the minimum type stays 11.25pt in all five arms.

### Why: the rejection moves to the next box

Two slides clear the rule outright (run-15 s24 at `inline`, run-16 s25 at
`shrink`) and immediately fail on a different one — s24 on `reject.text_escape`
×38, because the narrowed box now spills its card; s25 on `reject.flow_box_escape`
×16 and `vertical_movement_cap` ×14.

On the other three the count does not move at all, and the reason is sharper. The
rejecting **element** changes while the rule does not:

| run / slide | rejecting element, `baseline` | rejecting element, `box` |
|---|---|---|
| run-11 s16 | `source-block-f67d8c2c0cf8f503` ×27 | `source-block-c15e81177b2c6741` ×27 |
| run-11 s29 | `q-pillar-target` ×30 | `q-pillar-product` ×18, `q-pillar-target` ×12 |
| run-16 s9 | `q-span-8e4418d14ca4` ×35 | `q-span-fa338f43ced6` ×35 |

Forgiving one content-sized box hands the veto to its sibling. A type change
ripples across every box in the scope, and the contract is **all-or-nothing per
candidate**: one preserved box that genuinely must hold its width rejects the
whole candidate no matter how many others were exempted.

Measured mechanism of the three, from the fitter's own probe: `.goal-label` in a
`display:flex;flex-direction:row;align-items:center` parent, `.pillar` in a
`display:grid` parent whose tracks are `244.578px 244.578px`, and
`span.check-option` (`display:block;white-space:nowrap`) in a flex row. In each
case the box after the candidate is **exactly** as wide as its own content
(69.47, 244.58, 129.19 px), so these are content-sized in fact, not by guess.

### What this settles

- **(c) as an element-level exemption is dead.** It buys 0 of 9 slides under the
  most permissive reading available. Do not implement it.
- The decision is therefore not "which boxes may change width" but **whether a
  scope may reflow horizontally at all** — a scope-level contract change, with
  `reject.text_escape` and `reject.flow_box_escape` waiting behind it on the two
  slides that do get past the width rule.
- The residual mix at `box` is `fixed_horizontal` 26.5%, `parent_space_unavailable`
  22.5%, `reject.flow_box_escape` 15.3%, `reject.text_escape` 12.4%,
  `reject.overlap` 9.5%. No single remaining rule dominates.

### Scope of the evidence

Nine slides, three runs, **one source document** (`sha256:7746aaed…`, 35 slides,
Korean PPTX, `content_policy.text=verbatim`). Every number above comes from that
document. It is the same basis as the rest of this report and is **not** a
document-independent measurement.

Reproduce: `python simulate_content_width_exemption.py <validation-dir> <out.json>`
from `servers/fastapi`, with `LOCAL_FIT_DB_URL_FILE` pointing at a file holding the
database URL and `SIM_ARMS` selecting the arms. Per-slide records in
`content-width-simulation.json`. No generation, no provider call, no database
write, no stack contact.

## Baseline on non-verbatim decks — is `fixed_horizontal` the document or the contract?

Everything above came from one source document, so the rejection mix could be
that document's habits. `baseline_non_verbatim.py` re-runs the bounded search
over the **60 most recent non-verbatim authored decks** (2026-08-19 .. 09-09,
1,252 slides, many different prompts) by rendering stored `slides.html_content`
as it sits. Only the `fit=True` render is needed. No code changed, nothing was
generated, no provider was called, no row was written.

A second document *was* attempted first and is not available: the 43-page
contract PPTX has three decks, but all three hold 43 empty slide rows, binding
state `admitted`, never completed. Of 188 decks that do hold authored HTML, only
17 are source-bound and all 17 are the same 35-slide document.

### The fitter never runs on a slide that carries a raster image

The dominant outcome was not a rejection at all:

| slide outcome | slides | share |
|---|---|---|
| `unresolved`, reason `font_unverifiable`, **zero candidates produced** | 414 | 33.1% |
| `unchanged` | 483 | 38.6% |
| `unresolved`, reason `candidates_exhausted` | 177 | 14.1% |
| `spacing_adjusted` | 162 | 12.9% |
| `combined` / `text_scaled` / render error | 16 | 1.3% |

`font_unverifiable` tracks one thing almost perfectly: **401 of those 414 slides
carry a raster element** (`<img>`, `background-image: url(...)`, `<video>`,
`<canvas>`), while only 4 of the other 838 slides do.

The cause is a field with two readers and no writer. `slide_capture_fit_runner.py`
resolves image-text proof from `this.P.image_text_evidence`, and
`authored_local_fit_evidence.py::_validate_image_text_scope` fails closed on a
visible raster without `image_text_evidence` in the metrics. **No code in the
repository ever populates either one.** `local_fit_policy()` does not set it, and
production calls that function with the same arguments this measurement does
(`authored_presentation_generation.py:645`). So every image asset resolves as
unverifiable, the runner records `font_unverifiable`, and the search stops before
proposing a single candidate.

Open question this measurement cannot settle: whether production inserts images
before or after the fit pass. If images are present at fit time, the deterministic
fitter is disabled on a third of all slides. That belongs to whoever owns the
image stage, and it is worth answering before any further work on the fit search.

### With those slides set aside, the rule mix

On the 834 slides where the search actually ran:

| rule | share of 9,046 candidates | share on `unresolved` slides only |
|---|---|---|
| `fixed_horizontal` | 23.9% | 27.1% |
| `reject.dominated` | 15.9% | 0% |
| `parent_space_unavailable` | 15.4% | 20.1% |
| `reject.flow_box_escape` | 14.6% | 14.8% |
| `reject.text_escape` | 11.3% | 11.0% |
| `vertical_movement_cap` | 11.0% | 16.7% |

### Answer

`fixed_horizontal` is **a property of the contract, not of one document** — it is
the largest single rejection rule here too. But 42.7% overstated it. Across many
documents it is 23.9% of all candidates and 27.1% on the slides that fail, and it
is not dominant: four other rules each take 11% or more.

Its spread across decks is wide. Of 22 decks whose search rejected anything,
**9 show 0%** and the maximum is 54.5%, median 15.9%. It concentrates in
dense, many-boxed layouts and is absent from simple ones.

Consequences for the proposed scope-level reflow contract:

- The contract change stays justified, because the rule is real across documents.
- Its expected yield is **lower** than the one-document figure implied, and the
  `(c)` simulation already showed the rejection moves to a neighbouring rule.
- It is **not the first thing to fix.** A third of all slides never reach the
  search at all, for a reason unrelated to geometry.

Reproduce: `python baseline_non_verbatim.py <decks.json> <out.json>` from
`servers/fastapi`, with `LOCAL_FIT_DB_URL_FILE` pointing at a file holding the
database URL. Per-deck and per-slide records in `baseline-non-verbatim.json`.

## After the evidence producer — re-measurement of the 414 zero-candidate slides

The producer/transport bridge (`image_text_evidence_for_html` +
`capture_with_policy`) was implemented on 2026-09-12 and the 414 slides that
produced zero candidates were rendered again through the same path
(`remeasure_font_unverifiable.py`, records in `remeasure-font-unverifiable.json`).

| result | slides |
|---|---|
| still `font_unverifiable`, zero candidates | **414** |
| search now runs | **0** |
| image-text records issued by the runner | 721, all `unverifiable` |

**This is the expected and correct outcome for this corpus**, not a failure of
the bridge:

- The only provenance the producer restates is the icon-pack index, and this
  `app_data` has **no icon pack at all** (`app_data/authored-icon-packs` does not
  exist). No slide in the database carries an icon-pack placement either
  (`class="ig-ic"` with a data-URI `<img>`: 0 rows).
- Every one of the 401 image-bearing slides holds **generated illustrations**
  (data-URI `<img>` written by the illustration stage from a style prompt).
  Those may carry labels by design (`utils/authored_illustrations.py:682-683`),
  so they have no text-free record and must stay fail-closed until a text-size
  measurement exists for them.

The bridge itself is proven end to end in real Chrome by
`tests/integration/test_local_fit_image_text_evidence_chrome.py`: a slide whose
raster is an OCR-verified pack icon fits (`image_text_evidence.status == "no_text"`
lands on the element metrics), a heuristic-only icon and an unrecorded icon both
still fail closed.

What would move the 401: a durable, unforgeable text-size record for generated
illustrations (the illustration stage knows whether it asked a text-capable
provider for labels; recording that decision is the natural next producer), or
running the icon-pack infographic path so slides carry pack icons instead of
free illustrations. Neither is part of this change.

### Second re-measurement, with the illustration review producer (2026-09-12)

The same 414 slides, rendered again after `services/authored_illustration_reviews.py`
and its write point in `_generate_slot_image` landed: **still 414 `font_unverifiable`,
0 now run**, 721 image records all `unverifiable`. Expected: the review store only
holds entries for illustrations generated under this code, and no stored deck was.
The producer restates recorded reviews; it does not inspect pictures at fit time.
The mechanism is covered by the real-Chrome cases in
`tests/integration/test_local_fit_image_text_evidence_chrome.py`. Per-review OCR
cost, measured on 20 stored illustrations: median 1.39 s, p90 2.25 s
(design note §5.1).
