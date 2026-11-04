# Template component style as author guidance (2026-11-04)

Handoff item "pass the template's component styles as style guidance, never as fixed layouts"
(`docs/svg-template-handoff-20261104.md`, Next 4). Code: `services/svg_templates/component_style.py`,
`SvgTemplatePackage.component_style` (`models/svg_template_frame.py`), `body_author_rules` in
`services/svg_templates/generation.py`. Internal knob `SVG_TEMPLATE_COMPONENT_STYLE` (`on` / `off`),
**off by default**.

## Problem

Template decks keep the frame (header band, fonts, colours), but the body the model designs freely
looked generic next to the source's own body pages. Those pages draw a recognisable vocabulary:
numbered chips, label tiles, a key-message bar under the header, outlined cards, tinted panels, short
heading marks, a table header style, and a narrow ladder of text sizes. Fixed body layouts failed
repeatedly, so only the components' looks are passed, never positions, counts or arrangements.

## Design

**Measured at registration, stored on the package.** `extract_template_package` calls
`extract_component_style(context, all_slides, regions)` after the frames are built. It reads every
source page whose layout has a body frame (all 101 pages of a long deck, not only the representative
pages), and on each the slide's own shapes (groups flattened, title and footer placeholders skipped)
inside that frame's body region (12 px tolerance). Deterministic, no model call, about 5 s on a
101-page deck. Chosen over a generation-time computation because the style resolution it needs (theme
colours, scheme maps, inherited run styles, font substitutions) exists only while the PPTX is open at
registration; the result then travels with the immutable, digest-checked package that every process
already loads, and nothing on the generation path has to fetch the source file. Cost: a template
registered before this change has no `component_style` and must be registered again to get it (the
knob is a no-op for it). The field is omitted when absent, so earlier package digests are unchanged.

**Classification** (`_classify`), per painted shape: `number_chip` (filled, at most 64 px, text a 1-2
digit number, letter or roman numeral), `step` (chevron, pentagon arrow), `bar` (text, at least 55 % of
the body width, at most 110 px high), `label` (text, at most 40 px high), `card` (outlined area),
`panel` (filled area), `accent_bar` (short mark at most 10 px thick), `tile` (square or disc 20-90 px),
`divider` (line or hairline; an arrow is a connector and is left out). A text box drawn over a short
painted shape gives that shape its words (a bar whose text is a separate box). Tables: header fill and
text, body cell fill (second column), alternate row and first column fills, cell rules, cell text.
Unresolvable paint (gradients, colour transforms) is skipped, never guessed.

**Kept**: a variant (kind, geometry, fill, stroke, radius, text style) recurring at least 3 times on at
least 2 pages, ranked by number of pages, then count; at most two variants per kind (the second only on
at least half as many pages). Type ladder: plain unpainted body text, sizes within 8 % merged, levels
with at least 4 % of the characters, at most five, largest first. Sizes are given only where size is
part of the look (chips, tiles, marks, bar and label heights); an area's size is layout.

**Prompt** (`component_style_rules`, appended by `body_author_rules` when the knob is on): one
paragraph, about 2,000 characters (under 500 tokens) for the real deck, deck-invariant (it sits in the
rules, so it is cached with them). It says the layout stays free, to use a component only when the
content calls for it and never to add one to imitate the template, then one line per component
("Number chip: rounded rectangle, corner radius 5px, fill ..., about 36x26px; text 14.5px weight 700
..."). Text the source sets below the page's body text minimum is given at the minimum (rounded up to
0.5 px) and a component around it grows by the same factor; ladder levels that meet at the minimum
merge. Writing policy and SVG contract: the paragraph is instructions only and asks for plain
rectangles, rounded rectangles, circles, lines and polygons.

Also fixed on the way (`272852307`): the raster preservation renderer held every page of both renders
as float arrays at once; registering the 101-page deck peaked at 11 GB and the backend was killed by
the memory limit. Pages are now read in lockstep; the same registration peaks under 1 GB.

## Measurement

Real template deck registered again with this code (analysis 89 s, re-analysis 249 s), the same
fictional structured request (`scripts/svg_template_eval/inputs/structured_proposal_ko.txt`, 10 pages,
`text-heavy`, frame map), content structure policy off, the eval tool with the judge (contact sheet of
8 original body pages, default judge model and effort). Baseline: the three policy-off baseline decks,
re-judged in this session for identical judge fields. Knob on: two batches of three (N = 6).

| Metric (mean, per deck in brackets) | Baseline (N=3) | Component style (N=6) |
|---|---|---|
| Judge tone | 4.20 (4.2, 4.0, 4.4) | **4.52** (4.3, 4.8, 4.5, 4.6, 4.5, 4.4) |
| Judge visuals | 3.03 (3.0, 3.0, 3.1) | 3.08 (3.1, 3.2, 3.0, 3.1, 3.1, 3.0) |
| Judge structure | 3.40 | 3.63 |
| Judge density | 2.87 | 2.95 |
| Judge hierarchy | 3.93 | 4.00 |
| Judge overall | 3.49 (3.54, 3.40, 3.54) | 3.64 (3.58, 3.78, 3.62, 3.64, 3.62, 3.58) |
| Fallback pages | 0 | 0 |
| First draft accepted | 9.33 (9, 9, 10) | **8.0** (10, 7, 7, 8, 8, 8) |
| Gate passed / flagged | 9 / 1 | 10/0, 0/10, 8/2, 10/0, 8/2, 9/1 |
| Words kept / numbers kept | 75 % / 100 % | 80 % / 100 % |
| Seconds per deck | 420 | 471 |

* Tone rose by 0.32; five of the six decks scored at or above the best baseline deck (4.4).
  Visuals rose by 0.05 (noise). The judge's per-axis repeatability is about 0.2.
* The 0/10 gate is the deck review pipeline, not the pages: the local vision review of pages 1-5
  came back incomplete (`local_review_coverage_incomplete`), so the deck flow was sealed unreviewed
  for all pages, the failure mode already described in `docs/content-structure-policy-20261104.md`.
* First drafts accepted fell by about 1.3 pages per deck. The 12 repaired pages (6 decks) were all
  repaired, none fell back: vision design review `information_structure` 3 and `density_balance` 2
  (new; the baseline's repairs were contract ones only), contract 3, symbols 1, overlap 1, font 1,
  outside region 1.

**What visibly changed** (about ten renders looked at side by side with the same baseline pages; counts
from the body SVGs of all pages, per deck):

* Signature components appear: number chips 0 -> 9 (rounded, accent fill, white number), short
  heading marks 0.3 -> 9, rectangles in the source's key-message bar tint 0 -> 9 (the bar under the
  header, often with a left accent edge),
  navy label tiles and title strips on cards (navy fills 9 -> 22), five-step and four-layer pages with
  chips and step shapes like the source.
* Outlines follow the template: accent-blue card outlines 5.7 -> 0.3, the source's grey rule colour
  on almost all cards; the off-palette near-black body text (46 texts per deck) is gone, body text uses
  the source's grey and navy.
* Text sizes follow the ladder: 66 % of all texts are at the two ladder sizes at the body minimum
  (14.5 and 17 px); the baseline spread its text over 15-110 px. Pages read denser and calmer, closer
  to the source; the cost is that some cards are half empty (the source fills them with more text),
  which is the likely reason for the new `density_balance` repairs.
* Polygons 0 -> 4.7 per deck (step chevrons, downward triangles between layers). Direction was right
  on every page checked. One five-step sequence was drawn as one long bar with a single arrow end
  instead of separate chevrons, and one page had a stray 5 px mark under a heading.

## Decision

The default stays **off**: tone met the bar (+0.32) but visuals did not move, and first drafts fell
by more than the baseline's spread (the repairs cost about 50 s per deck, with no fallback). It is
the closest change so far to the template's look, so the next step is to keep it and fix the cause of
the extra repairs, then measure again with `SVG_TEMPLATE_COMPONENT_STYLE=on` (N >= 3), with a
same-session knob-off control on the same registration:

1. The ladder pushes every small source size to the body minimum, so body text is uniform and cards
   look empty at this content amount. Try giving the ladder as two levels above the minimum (body and
   sub-heading) and leaving caption sizes out.
2. Name a step sequence's shapes as separate, gapped chevrons (or leave `step` out) so a sequence is
   not drawn as one bar.
3. The second variant per kind (two label looks, two panel looks) may be noise to the model; try one.
