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
