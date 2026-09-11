# Scope-level reflow contract for the local fit

- Date: 2026-09-12
- Status: **design only.** No code changes accompany this note. Nothing here is
  scheduled; the simulation in §6 decides whether any of it is built.
- Evidence this rests on:
  [`artifacts/local-fit-source-reflow-replay-20260911/report.md`](../artifacts/local-fit-source-reflow-replay-20260911/report.md)
  (the (c) simulation and the two baselines) and
  [`authored-local-fit-overlap-space-design-20260909.md`](authored-local-fit-overlap-space-design-20260909.md) §10.2
  (the two parked structures, (a) and (b)).

## 1. What is being decided

The fitter's preservation contract is expressed **per element**. `_scopeHard`
(`utils/slide_capture_fit_geometry.py:243`) walks every box in the scope and
rejects the whole candidate the moment one preserved box has moved
horizontally past tolerance (`fixed_horizontal`, line 270). The scope report
declares `allowed_axes: ['y']` and `preserved_properties: ['left', 'width']`
(lines 236-240), but those fields only describe; the walk is the contract.

Three facts, all measured, say this shape is the limit and not one document's
habit:

| fact | source |
|---|---|
| `fixed_horizontal` is the largest single rejection on both baselines: 42.7% on the 35-slide verbatim document, 23.9% of judged candidates (27.1% on failing slides) across 60 non-verbatim decks | replay report, "Baseline on non-verbatim decks" |
| Its per-deck share runs 0% to 54.5%, median 15.9%; 9 of 22 decks show none. It concentrates in dense, many-boxed layouts | same |
| Exempting content-sized boxes one at a time buys **0 of 9** slides under five widening definitions; the rejection simply moves to the next sibling (`source-block-f67d…` → `c15e…`, `q-pillar-target` → `q-pillar-product`) | replay report, "(c) simulation" |

The last row is the reason this note exists. A type change ripples across every
box in a scope, and a verdict that is all-or-nothing per candidate cannot be
softened box by box. The only coherent unit for the decision is the **scope**.

The two structures parked in §10.2 of the overlap design are the same decision
seen from other angles: (a) an auto-height flow container cannot be a scope root
because `_parentRootEligible` demands an explicit height and `_scopeHard` pins
the root box with `_sameBox(baseline.rootBox, …)`; (b) `materializeGrowth`
reclaims slack only from **following** siblings with ink under a grid whose
parent is the root. Both are questions about what a scope may do with its own
space. Deciding them separately would be three per-path repairs; this note
folds them into one contract.

## 2. The contract

### 2.1 One decision, taken once, at one place

`_fitScopes` (geometry.py:189) already constructs every scope from the
collision seeds and picks its root (`_slackRoot`). It gains one more output per
scope: a **reflow mode**, decided from the baseline geometry of that scope
alone, before any candidate exists.

```
scope.mode ∈ { vertical, horizontal_within_root, root_growth, horizontal_and_growth }
```

- `vertical` — today's contract, byte for byte. Default.
- `horizontal_within_root` — boxes inside the root may change `left`/`width`;
  the root box itself may not.
- `root_growth` — the root box may grow downward by at most the slack measured
  below it; interior boxes keep their horizontal geometry.
- `horizontal_and_growth` — both.

The mode is chosen by a structural predicate over the scope, not by which box
happens to move in a candidate. Concretely, `horizontal_*` is offered only when
the root's own layout is a horizontal flow — a `display:flex` row or a `grid`
whose column tracks are content-sized (`auto`, `min-content`, `max-content`,
`fit-content`) — so that horizontal motion is what that layout does by nature,
not an accident of a single `<span white-space:nowrap>`. `root_growth` is
offered only when `_availableBottom` (`slide_capture_fit_parent.py:69`) measures
slack for the root, whether the root has an explicit height or not; that single
change is what absorbs (a).

### 2.2 The invariants, unconditional in every mode

`_scopeHard` keeps every check it has today and makes exactly one of them
mode-dependent. Everything else is hard in all modes:

| invariant | today's check | in every mode |
|---|---|---|
| nothing outside the scope changes | `baseline.outside` box comparison | unchanged |
| the root box does not change | `_sameBox(baseline.rootBox)` unless `_rootGrowthHard` | unchanged for `vertical` / `horizontal_within_root`; for `*growth` modes growth is bounded by the slack `_availableBottom` measured at baseline, never by the candidate |
| vertical movement cap | `vertical_movement_cap` (10% of root height) | unchanged |
| protected boxes | `protected_box` | unchanged |
| text stays in its box | `text_escape`, `flow_box_escape`, `nested_escape` | unchanged, hard |
| no overlap remains, none appears | `overlap`, `new_overlap` | unchanged, hard |
| no occlusion, clipping, off-canvas | unchanged | hard |
| font floor and hierarchy | `_fontHard` | unchanged |
| cumulative shrink ≤ 10%, floor 9.0pt | policy | unchanged |

The one mode-dependent check is the horizontal rule at geometry.py:263-270: in a
`horizontal_*` mode a box's `left`/`width` change is not a rejection **provided
the box is inside the root**, which the outside-box invariant already
guarantees. Nothing is exempted per element; the rule is simply not applied in
a scope whose declared mode allows horizontal reflow.

`allowed_axes` and `preserved_properties` in the scope report stop being
literals and become a derivation of `scope.mode`, in one function, so the
report and the verdict cannot disagree. The mode is written into the fit
report and the receipt, so a reviewer can see which contract a slide was fitted
under.

### 2.3 What (b) becomes

Slack reclaim (`materializeGrowth`, `_parentPlan`) is a growth-budget
computation. Under this contract it is the `*growth` modes' budget, computed
once per scope from the baseline: the slack below the root, in either sibling
direction, with or without ink, up to the parent's content edge. Direction and
ink are inputs to one measurement, not separate support-table rows. That is the
whole of (b).

### 2.4 What this deliberately does not do

- No element-level exemption of any kind; the (c) result closes that door.
- No widening of `GLOBAL_ATTRIBUTES`; no new attribute carries the mode.
- No change to the font floor, the 10% cumulative cap, the candidate tiers, or
  the measurement budget.
- No new mode is offered to a scope whose root is `protected`, a header/footer
  slot, or a source-locked block; those scopes stay `vertical`.

## 3. Choke point and bypass-proof tests

The choke point is the pair `_fitScopes` (decides) and `_scopeHard` (enforces).
Any code that wants a different geometry contract has to go through the mode
table; there is no second predicate to add a special case to.

Tests that make a bypass fail rather than pass silently:

1. **Invariant suite, parametrised over every mode.** Every fixture under
   `tests/fixtures/local_fit_v14/` is fitted under each mode in the table, and
   for every accepted candidate the suite asserts the §2.2 invariants directly
   from the browser metrics: outside boxes identical, root box identical or
   grown by no more than the measured slack, zero collisions, zero escapes, no
   clipping, floor respected. A mode that lets any of these slip fails here,
   whatever `_scopeHard` says.
2. **The report derives from the mode.** For every scope, `allowed_axes` and
   `preserved_properties` in the report must equal the derivation of
   `scope.mode`; the test enumerates the mode table so a mode added to the
   verdict but not to the derivation fails.
3. **The mode table is closed.** The set of modes the invariant suite covers
   must equal the set the runtime can emit. Adding a mode string anywhere else
   fails this test until the suite covers it.
4. **Default is byte-identical.** Under `vertical` every existing v1.4/v1.5
   fixture must produce the same report as today; the existing determinism test
   (`test_the_same_fixture_rendered_twice_yields_the_same_report`) is extended
   to compare against a stored golden report per fixture.
5. **The nine real failing slides as fixtures.** The (c) slides
   (`artifacts/verbatim-design-measurements-20260911/fixtures/`) are pinned
   with their expected outcome per mode from the simulation in §6, so a later
   change that quietly regresses one of them is caught.

## 4. Expected yield, stated before measuring

The (c) simulation is the strongest available prior: when the width rule was
cleared on two slides, they failed next on `reject.text_escape` ×38 and
`reject.flow_box_escape` ×16 / `vertical_movement_cap` ×14. Those checks stay
hard here. So the contract change is expected to convert **some** `fixed_horizontal`
rejections into accepted candidates and **some** into escape rejections; the
split is unknown and is what §6 measures. On the non-verbatim baseline the rule
is 27.1% of rejections on failing slides, with four other rules above 11%, so
even a perfect conversion cannot resolve most failing slides. This is a
contract cleanup with a bounded upside, not a fix for the failing set.

## 5. Order of work, if the simulation passes

1. Mode table and derivation of the report fields (no behaviour change; the
   only mode emitted is `vertical`). Tests 2, 3, 4.
2. `_scopeHard` reads the mode for the horizontal rule; `_fitScopes` can emit
   `horizontal_within_root`. Test 1 for that mode; the nine fixtures.
3. `root_growth` via `_availableBottom` without the explicit-height condition,
   absorbing (a); then the budget generalisation absorbing (b). Test 1 for
   those modes.

Each step ships behind the existing policy revision mechanism
(`authored-local-fit/v1.6` when a mode other than `vertical` can be emitted),
so a deck's receipt names the contract it was fitted under.

## 6. Simulation plan on the two baselines

Nothing above is built until this measurement is read. The harness already
exists: `simulate_content_width_exemption.py` patches the geometry fragment in
process and re-renders stored HTML; it is extended with one arm per mode, each
arm implementing the mode's `_scopeHard` rule and `_fitScopes` predicate
in-process. No code under `servers/` changes for the measurement.

| corpus | slides | why |
|---|---|---|
| 35-slide verbatim document (`sha256:7746aaed…`), runs 11/15/16 | the 9 slides the replay left unresolved | the dense, one-document case where the rule dominated |
| 60 non-verbatim decks (2026-08-19 .. 09-09) | the 177 slides where the search ran and ended `candidates_exhausted` | the document-independent basis; the 414 zero-candidate image slides are excluded because they never reach the search |

Arms: `vertical` (control, must reproduce today's report byte for byte),
`horizontal_within_root`, `root_growth`, `horizontal_and_growth`.

Per slide and per arm, the same verdict the replay used: the fitted HTML is
re-rendered `fit=False` and judged by `inspect_authored_dom` +
`blocking_safety_issues`, giving `resolved` / `improved` / `no_change` /
`worse`, plus the rejection-rule mix, worst shrink and minimum type size.

Decision rule, fixed now so the result cannot be argued into a different
conclusion later:

- any `worse` on either corpus blocks the arm outright;
- an arm proceeds only if it resolves or improves at least 10% of the 177
  non-verbatim slides (≥ 18) **and** at least one of the 9 verbatim slides;
- an arm that clears the width rule only to fail on `text_escape` /
  `flow_box_escape` on most slides is reported as such and not built;
- measurement counts per slide must stay within today's `max_measurements`
  (160); an arm that needs more is reported as needing a budget decision, not
  built.

Read-only as before: stored HTML in, Chromium render, JSON out; no generation,
no provider call, no database write, no stack contact. Output goes under
`artifacts/local-fit-source-reflow-replay-20260911/` as a `mode-simulation`
section and JSON, with per-slide records for both corpora.
