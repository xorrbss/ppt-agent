# Composer phase 1: the archetype library (design, 2026-09-04)

Design for phase 1 of the migration plan in
`docs/generation-quality-root-cause-and-composer-architecture-20260904.md`
section 6: *"port `utils/authored_infographic/` archetypes into Composer
components with density variants; template tokens from authored styles;
furniture bands"*.

Phase 0 is implemented and measured (section 6a of that document). This
document is the plan for the work that follows it, written after reading the
code on both sides. It changes the phase's shape in three ways the migration
table did not anticipate, each stated with the measurement that forced it.

Scope decision (decision 2 of section 7, restated): **phase 1 is verbatim
restyle only.** The figure registry it builds is engine-neutral and phase 2
inherits it, but nothing in this phase wires figures into ordinary authored
generation.

## 0. Amendment (2026-09-04, later the same day)

Read with `docs/generation-programme-master-design-20260904.md`, which
supersedes this document's place in the programme:

1. **Scope.** This library serves verbatim restyle only, as workstream C2
   (the guaranteed path). Composer phases 2-3 are withdrawn; nothing here
   feeds ordinary generation, which stays on the authored engine.
2. **Sequencing.** A fixture of the alternative C1 (the authored designer
   composing a page around server-rendered, bound source fragments) runs
   *before* further figure work; C3 in the master design decides which path
   ships. The figure kinds, elastic slots and `render_export_policy.py` of
   this document are needed by both paths and land first either way.
3. **Selection (decision 6 reversed).** Direct viewing of the phase-0 output
   (master design 1.5) showed three of four defects are grouping/role errors
   a deterministic classifier cannot fix. The LLM plan overlay described in
   the master design C2 (batched, typed closed operations on plan v0,
   validated against the partition invariants) is the **default**; the
   zero-call deterministic mode remains as the flag-off fallback. Rendered
   K-candidates and a vision critic stay off for attribution reasons.
4. **Defects to fix in scope**, observed on `r12-43` pages 6, 11, 13: orphaned
   number blocks not attached to their cards; a running header classified as
   body; a chapter divider not recognised as a cover; rung-5 title/footer
   overlap (A4). Section 2 lists only the `|` defect; these four are added.
5. **Module plan (section 5) superseded by master design D9.** There is one
   archetype library, `utils/authored_infographic/`, extended with elastic
   slots, a pluggable node-text renderer (plan label or bound source fragment)
   and per-archetype height models. The `services/composer/figures/*` modules
   listed in section 5 shrink to adapters from `ComposerComponent` to node
   references; `figures/css.py`, `flow.py`, `stack.py`, `data.py` are not
   built as a second implementation.

---

## 1. What phase 1 is not

The migration table reads as a CSS port. It is not one. Three findings from
the code:

**1.1 The archetype library's information model is incompatible with
verbatim.** `models/authored_infographic_plan.py` budgets a node at
`MAX_NODE_LABEL_CHARS = 10`, `MAX_NODE_DESC_CHARS = 18`,
`MAX_NODE_FIGURE_CHARS = 12`, `MAX_TAKEAWAY_CHARS = 12`, and
`coerce_infographic_plan` *clips* anything longer. Verbatim restyle may not
drop a character: `contracts.TEXT_RULES` requires "exact run characters per
bound element", and A2 requires 100% of source blocks present in readback.

Measured on the two decks phase 0 evaluates (78 slides,
`artifacts/composer-offline-eval/r12-43` and `r12-35`, 151 rendered process
steps):

| step text length | steps |
|---|---|
| <= 10 chars (the library's label budget) | 82 / 151 |
| <= 18 | 105 / 151 |
| <= 40 | 118 / 151 |
| <= 100 | 143 / 151 |
| max | 244 chars |

Median 9, p75 35, p90 72. Just over half of real steps fit the library's node
budget and the tail runs to 244 characters. A figure whose slots are sized for
a 10-character label cannot render this content, so the port cannot carry the
node model across. What ports is the **visual grammar** — structure, ornament
elements, emphasis, token derivation — re-expressed over elastic slots.

**1.2 The skeletons write their own identity namespace.** `skeleton_parts.Ctx`
stamps `data-qid="ig.s3.n2.label"` and escapes plan text with `esc()`. The
Composer's renderer must stamp `data-source-block-id` from
`SourceRunRef.binding_id` and derive `data-qid` with
`source_stamp_dom.deterministic_source_qid`, via `render_text.render_bound_text`
and the per-slide `BindingRegistry`. The skeleton builders therefore cannot be
called; their *shapes* are re-implemented against `ComposerComponent`.

**1.3 The library CSS has no scale hook.** `css_base.py` sets literal pixel
sizes (`.ig-label{font-size:22px}`) with three coarse overrides keyed by node
count (`.ig.n3`, `.ig.n5`, `.ig.n6`). The Composer's ladder needs a continuous
type scale (rung 2), a source-relative size per paragraph (rung 4) and a
per-box shrink (rung 5). Sizes must come from `ctx.size(...)` inline per
element, as every existing `cx-` component already does; the figure CSS
carries structure, colour and spacing only.

**Conclusion.** Phase 1 builds a *figure library* in the Composer's own
vocabulary, using `utils/authored_infographic/` as the design reference — the
one artefact the team has that reached "approaches image-model quality"
(section 4.7). The `ig-` package stays where it is, unchanged, serving the
authored path until phase 3 retires it.

---

## 2. Measured demand: which figures are worth building

Slide-level archetype distribution over the same 78 slides:

| archetype | deck-43 | deck-35 | total |
|---|---|---|---|
| comparison | 12 | 19 | 31 |
| process | 17 | 5 | 22 |
| mixed | 10 | 0 | 10 |
| text | 2 | 8 | 10 |
| table | 2 | 0 | 2 |
| list | 0 | 2 | 2 |
| cover | 0 | 1 | 1 |

Structure inside those components, counted from the rendered HTML:

- process step counts: 2 (x1), 3 (x5), 4 (x10), 5 (x3), 6 (x1), 7 (x1),
  9 (x1), 11 (x1), 15 (x2), 16 (x1). **19 of 26 fit within the library's
  `MAX_NODES = 6`; 7 do not, and reach 16 steps.**
- comparison columns: 2 columns on 18 slides, 3 on 13.
- images: **0 on all 78 slides.** Both evaluation decks are text-only, so
  image-bearing figures cannot be validated on them and phase 1 must not be
  scored on image work.

Short step labels are real and common (`영업 → 상담 → 계약 → 납품`), and bare
numeric steps appear (`7`, `14`, `12`, `42`, `39주`, `24주`) — the timeline
signal exists in this corpus. One defect surfaced while counting: a literal
`|` is rendered as a process step on three slides, i.e. the `order_free`
separator leaks into content (`classify_groups.flow_step_groups`). Fix it in
this phase.

### 2.1 The vocabulary, tiered

Twenty archetypes exist in `skeleton._BUILDERS`. For verbatim restyle they do
not all make sense:

**Tier 1 — build in this phase (covers the measured mass).**

| figure | why | signal |
|---|---|---|
| `process` (redesigned) | 22 slides; today a flat card grid | ordinals, arrow glyphs, arrow shapes (exists) |
| `comparison` (redesigned, + native-table variant) | 31 slides | column clusters (exists) |
| `milestones` (timeline) | numeric/date steps measured in this corpus | date/period tokens, monotonic ordinals (new) |
| `phases` (roadmap) | schedule pages in SI decks | duration bar shapes + period tokens (new) |
| `cards` (kpi / icon_list, one builder) | numbers with units | number+unit runs at top size rank (new) |

**Tier 2 — build if tier 1 lands inside the phase.**
`bands` (layers / funnel / pyramid, one builder; monotonic width ramp),
`hierarchy` and `branch` (geometric tree), `before_after` (two-value rows as a
native table).

**Tier 3 — named and deferred, with the reason.**
`cycle`, `hub_spoke` — absolute ring geometry breaks on long labels and they
are the hardest to make elastic. `swimlane` — its chips clip with
`text-overflow: ellipsis`, which hides text and violates verbatim; needs a
wrapping redesign. `matrix`, `checklist`, `gauge`, `venn` — each needs a claim
the source does not state (quadrant membership, done/open state, a share of a
whole, set overlap); deriving it would be an inference, not a restyle.

**Struck for verbatim.** `scene` requires a generated illustration: a provider
call and invented content. It cannot appear on the verbatim route.

Nothing here forbids the tier-3 figures later; they are simply not what the
measured decks need, and phase 1 is scored on those decks.

---

## 3. Design

### 3.1 A figure is a component variant, not a page archetype

`ComposerSlidePlan.archetype` stays what it is: the page-layout selector used
by `render_chrome` / `render_flow`. Ten of 78 slides are `mixed` — a process
next to paragraphs — so the drawing decision belongs to the block, not the
page.

Add to `ComposerComponent`:

```python
figure: FigureKind | None = None          # None -> render exactly as today
figure_scores: dict[str, float] = {}      # what chose it, for the receipt
```

`FigureKind` is a `Literal` in a new `services/composer/figures/kinds.py`.

### 3.2 Elastic slots: three shape variants, chosen by measurement

Each figure implements up to three variants. The variant is chosen by a single
shared function from the *actual* available box and the *actual* text lengths —
never by node count alone:

| variant | when | shape |
|---|---|---|
| `chip` | every node <= ~12 chars | badge + label card, ornament allowed |
| `card` | every node <= ~80 chars | header line + body paragraph in a card |
| `row` | anything longer, or too many nodes for the box | full-width row: fixed left header column, body flowing right |

`figure_variant(component, params, deck, *, width_px, height_px) -> Variant`
lives in `figures/base.py` and is called by **both** the renderer and the
estimator, exactly as `estimate_components.process_per_row` is today. That is
the invariant that keeps the ladder honest: the estimate and the browser can
never disagree about which shape was drawn.

This is where the library's `density` (`low` / `medium` / `high`) lands. It
does **not** become a new fit parameter and the ladder gains no rung: the
ladder's existing `density` 0/1 and the measured box already determine the
variant, and rung semantics are frozen by the A1-A7 gate and the receipt.

### 3.3 Every figure ships a height model

The registry entry for a figure is:

```python
@dataclass(frozen=True)
class FigureSpec:
    kind: FigureKind
    render:   Callable[..., str]        # -> markup, bound text, qids by construction
    estimate: Callable[..., float]      # -> height px at these params and width
    css:      Callable[[FigurePalette, DensitySpec], str]
    eligible: Callable[..., bool]       # hard predicates, checked before scoring
    signals:  Callable[..., float]      # 0..1 score from the manifest
```

A unit test iterates the registry and fails if any kind is missing any of the
six. A figure with no height model cannot be registered, so the ladder can
never be forced to discover a figure's size by rendering it — which is exactly
the failure mode that would push slides to rung 5.

Height models reuse `estimate_components` primitives (`wrapped_line_count`,
`LINE_HEIGHT`, `GAP_PX`, `GROUP_CHROME_PX`, `paragraph_font_px`). No new
metrics engine.

### 3.4 Figures and the fit ladder

| rung | figures |
|---|---|
| 0 | richest eligible variant |
| 1 | dense: `chip`/`card` may drop to `card`/`row`, ornament panels drop |
| 2 | same structure, type scaled down; variant re-evaluated at the new size |
| 3 | same, in the larger content area chrome compaction buys |
| 4 | figures stay on, forced to `row`: paragraph sizes come from the source |
| 5 | **figures off.** Source geometry places each block in its own rectangle; there is no template figure to apply |

A figure that cannot fit its minimum box at a rung is not eligible at that
rung, and the component renders as it does today. Falling back to today's
markup is always available and always correct.

### 3.5 Deck consistency

The infographic plan is deck-scoped for a reason recorded in its own
docstring: per-page figure choice produced "forty near-identical card grids".
The Composer's risk is the mirror image — 43 pages each picking a different
figure.

`figures/normalize.py` runs one deterministic pass over `ComposerDeckPlan`
between classify and fit:

1. bucket components by shape class (kind, group count band, variant);
2. within a bucket, if a plurality chose figure X, every component whose
   runner-up was X within a score margin switches to X;
3. a figure used on fewer than two pages of a deck of >= 10 pages reverts to
   the bucket's plurality choice.

Cost: one pass, no renders, no provider calls. The receipt records the
per-component figure and the score that chose it, so a rating round can be
traced back to a decision.

### 3.6 Selection: deterministic, single candidate (decision (1))

**Decision: phase 1 selects one figure per component, deterministically. No
rendered K-candidates, no vision critic.** Rendered candidates and a critic
are designed for, flag-gated, and off.

Three reasons, in order of weight:

1. *Budget.* A5 allows 5 minutes for 50 pages with zero provider calls. The
   43-page deck already spends 223 s and 150 renders. K=3 multiplies the
   render axis by three on top of a six-rung ladder; a per-slide vision call
   adds seconds each and reintroduces the provider dependency phase 0 removed.
2. *Confounding.* Phase 1's acceptance is a **human** blind rating (7b).
   Introducing a model critic in the same phase makes the rating measure the
   critic as much as the library.
3. *Sufficiency is testable.* If deterministic selection is good enough, K is
   waste; if it is not, the blind rating says so per slide and K becomes a
   phase-1.5 with a stated target. Building it first assumes the answer.

Selection is three stages:

- **eligibility** — hard predicates (node count in range, every node's text
  under the variant ceiling, minimum box available). Ineligible figures are
  removed, never scored.
- **score** — each eligible figure sums its weighted signals; highest wins;
  exact ties break on a fixed priority order so the choice is stable across
  runs.
- **normalize** — the deck pass of 3.5.

Designed-for extension, both default off:
`COMPOSER_FIGURE_CANDIDATES` (int, default 1) renders the top-K figures at
rung 0 only, on slides where the top two scores are within a margin, capped at
+1 render per slide; `COMPOSER_FIGURE_VISION_CRITIC` (bool, default false)
chooses among them. Both reuse the ladder's existing render loop and neither
is on the phase-1 acceptance path.

### 3.7 Tokens

`ComposerTokens` already carries `background`, `surface`, `text_primary`,
`text_secondary`, `accent`, `accent_text`, `accent_soft`, `rule` and the font
families — derived from the authored style in phase 0. `InfographicTokens`
carries a different set (`primary_deep`, `primary_tint`, `primary_soft`,
`primary_glow`, `on_primary`, ...).

`figures/palette.py` derives a `FigurePalette` from `ComposerTokens` using the
shade/mix helpers promoted out of `utils/authored_infographic/tokens.py`.
`tokens_for_brand` itself is **not** reused: it hardcodes `ink`, `muted`,
`line`, `surface` and would override the deck's own tokens.

`primary_glow` is a shadow. Per 3.8 it may only be painted on an empty
sibling; on text-bearing elements it is dropped.

### 3.8 Export safety is an assertion, not a hope (A6)

`docs/authored-native-figure-contract-20260903.md` lists exactly what keeps an
element on the raster backplate. New figure markup must obey it or A6 gets
worse. The rules that bind this phase:

- **class names**: never a whole-word `timeline`, `chart`, `diagram`,
  `flowchart`, `mindmap` token — the matcher splits on whitespace, `_` and
  `-`. Hence `cx-milestones` and `cx-phases`, not `cx-timeline` /
  `cx-roadmap`. (The `ig-` library already learned this: its timeline root is
  `.ig-milestones`.)
- no `::before` / `::after`, `text-shadow`, `filter`, `mask`, `clip-path`,
  CSS `columns`, `mix-blend-mode`;
- no `transform` other than a pure rotation; no `overflow: hidden` that
  actually clips (this is what rules out the swimlane chips);
- uniform pixel `border-radius` only, never a percentage, on any text
  container;
- paint (background, border, shadow) on an **empty sibling** element when a
  text-bearing element would otherwise need it — the `ig-node-bg` pattern;
- no text box overlapping another text box.

New module `services/composer/render_export_policy.py` asserts these on every
rendered document inside `render.validate_rendered_html`, alongside the
existing admission and QID gates. A violation raises `ComposerRenderError` —
a renderer bug with a stack trace, per principle 4. This makes phase 1's
markup A6-ready by construction and turns a class of export regressions into
a unit test.

### 3.9 The one seam phase 2 depends on

Phase 2 (ordinary authored generation on Deck IR) is not designed here and
does not need to be: its gate is a different one (a matched A/B against the
authored engine, section 6 of the architecture doc), its content comes from an
LLM plan rather than a manifest, and whether it is worth extending figures at
all is exactly what phase 1's blind rating answers.

One coupling is real and is settled now, because getting it wrong would make
every figure verbatim-only:

**Figures never read `Paragraph.source`.** They pass a `Paragraph` to
`render_text.render_bound_text` / `render_flow_paragraph` and let those own the
binding. This is already how `render_components` behaves; the registry
inherits it as a rule, enforced by a grep-level test.

That matters because `Paragraph.source: SourceRunRef` is currently **required**
and `render_bound_text` calls `registry.bind(paragraph.source.binding_id)`
unconditionally (`render_text.py:227`). LLM-authored content has no source run,
so phase 2 must make the binding optional in exactly those two places. If the
figures stay off `paragraph.source`, that is a two-file change and every figure
built in phase 1 keeps working. If they reach into it, it is a rewrite of the
library.

The selection seam is `ComposerComponent.figure`: phase 1 fills it from the
deterministic signals of section 4, phase 2 fills it from the LLM plan. The
signals themselves are manifest-only and phase 2 does not reuse them — it does
not need to, because the plan states the figure directly. Nothing else about
phase 2 constrains this design.

---

## 4. Classifier extension

This is the hard half of the phase. Every signal is deterministic, reads only
the enriched manifest, and returns 0..1.

Already available: ordinals (`ordinal_start`), arrow glyphs and arrow shapes
(`is_arrow_flow`, `is_arrow_shape`), column and row clusters, bullet metadata,
run sizes and weights, block rectangles, placeholder types, table and image
blocks, empty-shape presets.

New, in `services/composer/classify_signals.py`:

| signal | figure | rule |
|---|---|---|
| period tokens | `milestones`, `phases` | `YYYY`, `YYYY.MM`, `N년`, `N월`, `NQ`/`QN`, `N주`, `상/하반기`, `N차`, or bare 1-2 digit numbers **when monotonic across the groups** — monotonicity is what separates a timeline from a plain numbered process |
| duration bars | `phases` | a wide, short empty shape row-aligned with each step, with monotonically increasing left offsets |
| number + unit | `cards` | a run matching a number with `%`, `억`, `만`, `건`, `시간`, `배`, `x` whose size rank is top-2 on the page, in a cluster of 2-5 similar blocks |
| width ramp | `bands` | >= 3 vertically stacked blocks whose widths decrease (funnel), increase (pyramid) or match (layers) monotonically |
| tree topology | `hierarchy`, `branch` | one block centred above a row of >= 2, with a connector block whose rectangle spans the gap |
| parallel columns | `comparison` as native table | existing column clusters where every column has equal member count and every member is short |
| two-value rows | `before_after` | exactly two numeric runs per row with a delta glyph between |

Two manifest gaps this exposes, both real:

- **`prstGeom` is only captured for empty shapes.** `pptx.py:525` reads the
  preset in the `else` branch — a *text-bearing* trapezoid or chevron records
  no preset. Funnel/pyramid/chevron-process detection therefore has geometry
  only. Fix: capture `preset` on the text-block payload too. This changes the
  manifest digest, so it must ride with the extractor-revision fix already
  listed as a phase-0 follow-up (re-analysing an old snapshot 409s otherwise).
- **Connectors carry no endpoints.** `pptx.py:541` stores a `cxnSp` with
  geometry only; `stCxn` / `endCxn` are dropped. Tree topology must be
  inferred from rectangles. Capturing the endpoints is a small, separate
  change; phase 1 does not depend on it and tier 2's `hierarchy` is weaker
  without it. Named, not scheduled.

**Default-safe.** Below the score threshold, or with no signal, a component
keeps exactly today's rendering. This is what lets the phase merge
incrementally with A1-A3 staying green.

---

## 5. Module plan

New, under `services/composer/figures/`:

| module | contents | est. lines |
|---|---|---|
| `__init__.py` | registry, `choose_figure` facade | 90 |
| `kinds.py` | `FigureKind`, `Variant`, `FigureSpec` | 80 |
| `base.py` | chip/card/row primitives, `figure_variant` | 200 |
| `flow.py` | `process`, `milestones`, `phases` | 300 |
| `stack.py` | `bands`, `hierarchy`, `branch` (tier 2) | 300 |
| `data.py` | `cards`, `comparison`, `before_after` | 300 |
| `css.py` | per-figure CSS fragments | 320 |
| `palette.py` | `FigurePalette` from `ComposerTokens` | 120 |
| `estimate.py` | per-figure height models | 280 |
| `normalize.py` | deck-level consistency pass | 120 |

New, elsewhere:

| module | contents | est. lines |
|---|---|---|
| `services/composer/classify_signals.py` | the signals of section 4 | 330 |
| `services/composer/classify_figure.py` | eligibility, scoring, threshold | 220 |
| `services/composer/render_export_policy.py` | the A6 assertions of 3.8 | 180 |

Changed:

- `plan.py` (299) — `FigureKind` import, two fields on `ComposerComponent`.
  This lands near 340; if it crosses, split the pixel/type constants into
  `plan_constants.py` rather than growing the ratchet baseline.
- `render_components.py` (322) — `render_component` delegates to the registry
  when `component.figure` is set; `render_process` / `render_comparison` move
  into `figures/`. Net shrink.
- `render_css.py` (371) — process/comparison rules move to `figures/css.py`;
  `stylesheet()` gains a `figures: frozenset[FigureKind]` argument and emits
  **only the fragments the slide actually uses**. Today the whole stylesheet
  is inlined into every slide document; adding a figure library to all 43
  would bloat every document and every measurement. Net shrink.
- `estimate_components.py` (346) — `process_per_row` generalises into
  `figures/estimate.py`. Net shrink.
- `classify.py` (328) — one call to `assign_figures` after components are
  built. +10.
- `pipeline.py` (295) — the deck normalisation pass between classify and fit.
  +15. (`pipeline.py` was already flagged for one more split in phase 0.)
- `receipt.py` (194) — figure and score per component. +20.

Tests, mirroring the existing `tests/unit/test_composer_*.py` set:
`test_composer_figures.py` (registry completeness: every kind has all six
callables), `test_composer_classify_signals.py`, `test_composer_figure_estimate.py`
(estimate vs. rendered height within tolerance on fixtures),
`test_composer_figure_normalize.py`, `test_composer_export_policy.py`.

---

## 6. Acceptance

**The phase gate is 7b, unchanged**: blind rating of the 43-page deck via
`scripts/composer_blind_rating_pack.py` — at or above the floor tier on every
rated slide, at or above parity on legibility and consistency, at least 80% of
the target deck's median craft score.

Regression gates that must stay green on both decks, all read from
`report.json`:

| gate | threshold |
|---|---|
| A2 source readback | 100% of blocks, both decks (289/289, 965/965) |
| A3 blocking DOM defects, rungs 0-4 | 0 |
| A5 wall time / provider calls | <= 5 min per 50 pages, 0 calls |
| rung histogram | must not worsen — no slide moves to a higher rung than its phase-0 baseline |
| export policy | 0 violations of section 3.8 |

The rung histogram is the main regression risk and the reason every figure
needs a height model: a figure that renders taller than its estimate pushes
slides down the ladder, and a page that lands at rung 5 loses the template
entirely. Phase-0 baselines to hold against: deck-43 `0:2 1:10 2:12 3:13 5:6`,
deck-35 `0:31 1:4`.

New metric, recorded in the receipt: **figure coverage** — the share of
process and comparison components rendered as a designed figure rather than a
text stack. Target >= 60% across the two decks. Coverage without a rating
improvement is not success, and a rating improvement at 10% coverage is noise;
reporting both keeps the phase honest.

Out of scope for scoring: image-bearing figures. Both evaluation decks carry
zero images, so nothing about image layout can be validated in this phase.

---

## 7. Sequence

Week 1 — the seam and the first figure:
`kinds.py`, `__init__.py`, `base.py`, `palette.py`, `render_export_policy.py`,
`figures/css.py` scaffolding, `stylesheet(figures=...)`; move `process` and
`comparison` into the registry **unchanged** and prove the offline eval is
byte-stable. Then redesign `process` with its three variants and its height
model. Fix the `|`-as-a-step defect. Run the blind pack once for an early
read on a single figure.

Week 2 — `comparison` redesign incl. the native-table variant, `cards`,
`milestones`, `phases`; `classify_signals.py` and `classify_figure.py`;
`normalize.py`. Manifest `prstGeom` capture, riding with the extractor
revision fix.

Week 3 — tier 2 (`bands`, `hierarchy`, `branch`, `before_after`) as far as it
goes; full blind rating round; receipt and coverage reporting.

The phase is mergeable at the end of any week: below the score threshold every
component renders as it does today.

---

## 8. Open decision

**Sequencing against A6.** A6 (hybrid PPTX export as native shapes) is FAIL:
unreachable on the product path, and 0 of 642 text elements native when
forced (7d). It is the blocking criterion of the A1-A7 gate, and phase 1 does
not fix it.

Recommendation: **close A6 first as a separate, short track**, and start phase
1 in parallel with `render_export_policy.py` landed in its first days so the
new markup cannot regress export while A6 is being fixed. Reasons: A6 is the
one gate that decides whether the Composer ships at all, it is export-reader
work that does not compete for the same files as the figure library, and
phase 1 adding markup to a route whose export is unproven raises the cost of
every later A6 finding.

This is a scheduling decision, not a design one; the design above is unchanged
either way.

---

## 9. Decisions recorded here

1. Phase 1 builds a Composer-native figure library using
   `utils/authored_infographic/` as the design reference; the `ig-` package is
   not called and is not modified. (Section 1.)
2. A figure is a component-level variant, not a page archetype. (3.1.)
3. Variants are chosen from measured text length and available box by one
   function shared by the renderer and the estimator; no new fit rung and no
   new fit parameter. (3.2.)
4. Every figure ships a height model, enforced by a registry test. (3.3.)
5. Figures are off at rung 5. (3.4.)
6. Selection is deterministic and single-candidate; rendered K-candidates and
   a vision critic are designed for, flag-gated and off. (3.6.)
7. Export safety is asserted in the renderer. (3.8.)
8. Figures never read `Paragraph.source`; the binding stays owned by
   `render_text`. This is what lets phase 2 reuse the library. (3.9.)
9. `scene` is struck from the verbatim route; `cycle`, `hub_spoke`,
   `swimlane`, `matrix`, `checklist`, `gauge`, `venn` are deferred with
   reasons. (2.1.)
