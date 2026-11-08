# Codex brief: template-redesign decks at the template's own level (2026-11-06)

> Current execution authority (owner instruction, 2026-11-07): `docs/codex-brief-svg-freeze-quality-20261107.md`. Preserve and freeze all existing non-SVG modes and their dedicated tests; no retirement or deletion. Native requirements v4 is already implemented: verify remaining chart-integrity risks instead of repeating item 1. The newer brief also controls verification scope and requires managed-stack runtime validation after development.

You continue work started in another tool. Read, in this order, before changing anything:
`AGENTS.md`, `CLAUDE.md` (especially "Never build generation on reusing original template pages as skeletons",
"Template body composition", "SVG deck pictures and icons come from the asset offer", "Judge svg template
generation changes on several runs"), `docs/original-level-handoff-20261106.md` (loss map, baseline, plan),
`servers/fastapi/scripts/svg_template_eval/README.md`. The handoff note is the source of the file:line evidence
below; re-check it against the current code before editing.

## Goal

A framed SVG user template (`svg_template_mode: template-redesign`) should produce decks whose body pages reach the
template's own level. Measured on the real-deck template A with source-sized inputs: judge overall 2.81 (density
2.73, visuals 1.89), first drafts ~71 %, 0 native charts; the template's own pages score ~4.45.
Targets: overall >= 3.8, density and visuals >= 3.5, first-draft acceptance and numbers kept not lower, no
template wording in generated decks.

## Hard rules

1. No source-skeleton reuse (user decision, `.spec-memory/decisions/generation.no-source-skeleton-reuse.1.json`):
   never copy a template page's body shapes/layout and swap text, table values or chart data; no fixed-slot or
   Deck IR slot schemas; no Design Pack source frames under any name. Page bodies are composed by the author.
2. Pictures are LLM-drawn vector SVG (`SVG_ILLUSTRATION_MODE=vector` machinery), not an image provider.
3. Every behaviour change sits behind an internal knob (default off) or a revision pinned at admission, so stored
   decks replay byte-identically. Register knobs like `SVG_TEMPLATE_OUTLINE_SECTIONS`: accessor in
   `servers/fastapi/utils/get_env_concurrency.py`, entry in `servers/fastapi/tests/support/internal_env_names.py`,
   one CLAUDE.md sentence. Freeze a knob that changes authoring or validation into the style snapshot in
   `services/svg_generation_admission.py` `pin_svg_style` (see `page_number_ink`, `native_requirements_revision`,
   `template_body_renders`).
4. Verifier revision ledgers (`services/verifier_revisions.py`): if `tests/unit/test_svg_verifier_revisions.py`
   fails after your change, add the new source state under the CURRENT revision with a precise reason only when
   stored decks replay byte-identically. Never bump a revision; if a stored-evidence behaviour change is
   unavoidable, stop and report.
5. Never print, log or commit API keys. The template A deck and its renders are customer material: never commit
   them or text derived from them.
6. Code, comments, commit messages and docs in English. One topic per PR.
7. Tests: the narrowest check first (AGENTS.md). Before a PR also run what CI runs for the backend:
   `cd servers/fastapi && uv run ruff check .`, `uv run python scripts/svg_unit_subset.py --check` (use `--write`
   when you add an svg test), the subset `uv run pytest $(cat tests/svg_unit_subset.txt) -n 4 --dist loadfile -q`,
   `node scripts/check-structural-layering.mjs`, `node --test scripts/required-ci-contract.test.mjs`.
   About 285 full-suite failures are pre-existing (`docs/test-failure-baseline-20261105.md`); do not chase them.
   The subset is green on `main`.

## Work items, in order (one PR each)

### 1. Native charts survive validation (highest value)
Evidence: the author declares charts with the requested data (5/5 pages) but projection accepts 0/10
(`native_chart_label_not_drawn`), and the page then falls back to text; 21 self-declared charts, 0 accepted.
Introduce native requirements revision v4, pinned for fresh decks (follow how v3 is added in
`services/generation_core/drivers/svg/native_requirements.py`: `REQUIREMENTS_REVISIONS`,
`ADMITTED_REQUIREMENTS_REVISION`, `SNAPSHOT_REVISION_KEY`). Under v4:
- a single-series chart group whose one drawn title names the series is accepted (`drivers/svg/native_chart.py`,
  `_drawn_series_name`, ~269-290, 577); all other label/value/geometry checks stay;
- the retry hint carries the drop reason and the labels/values that were not drawn (`drivers/svg/render.py` ~1044,
  `native_declarations.py`);
- a page whose declared data equals the requirement and whose required values are drawn is published as drawn
  without the native object instead of the text fallback (`quality/page_failure_kind.py:43-44`,
  `native_requirements.py`); content-integrity checks stay strict;
- prose derivation accepts a unit given in the sub-heading/parenthesis/label header and joins one series split
  over consecutive lines (`drivers/svg/prose_chart_literal.py` ~130-144); values are never computed; grounding
  (`services/outline_chart_grounding.py`) stays;
- author guidance prefers donut/pie for composition (parts of a whole) and bar/line for trends/comparisons.
Unit tests: each rule, v3 snapshots byte-identical, v4 pinned on fresh admissions.

### 2. Pictures for template decks (LLM-drawn SVG), knob `SVG_TEMPLATE_PICTURES`
Evidence: template decks use the `default` style without an illustration prompt, so `deck_has_picture_source`
(`assets/infographic_plan.py:36-47`) plans no pictures; scene share is capped at 25 %
(`models/authored_infographic_plan.py:121,265`); template A has page-specific pictures on 47 of 100 body pages.
With the knob on, for template-redesign decks: plan pictures from an illustration prompt derived from the
template's palette/style; align the scene definition and share with the template's picture share; draw scene,
spots and pictograph parts in vector mode (no cover picture, the template frame owns the cover); add sizing and
placement rules for pictures inside the body region (`services/svg_templates/generation.py`). Picture failures
stay optional and never fail a page. Files: `drivers/svg/asset_slots.py`, `assets/offer.py`,
`assets/vector_offer.py`, `assets/infographic_plan.py`, `utils/llm_calls/author_infographic_plan.py`.

### 3. Content-sized bodies, knob `SVG_TEMPLATE_CONTENT_SIZING`
Evidence: text ink 13.7 % while content reaches the body bottom; `FILL_BY_ENLARGING` in
`services/svg_templates/body_composition.py` (~759) asks to enlarge components; 54 of 82 tables >= 1,000 px wide;
cards ~20 % empty below their last line. With the knob on (frozen like `snapshot_has_visual_guidance`): replace the
"enlarge" ending with content-sized components and one block of remaining space; table width follows its columns;
cards sized to their content. Add a card-empty-below-text metric to `scripts/svg_template_eval/metrics.py`. Do not
add a rejecting validator yet. Knob off: prompt bytes identical (pin the off paragraph literally in a test).

### 4. Flow diagrams (no code)
Re-measure `CONTENT_STRUCTURE_POLICY_SCOPE=template` with `CONTENT_STRUCTURE_POLICY_PARTS=connectors` (N >= 3).

## Measurement (needs a live stack and a provider key; may run outside your environment)
Per item: paired matrix on template A against the sized baseline,
`run_eval.py matrix --inputs structured_proposal_ko_sized,data_report_ko_sized,unstructured_report_ko_sized,dense_proposal_ko_sized --runs 3 --pptx`
for control and variant, then `run_eval.py decide`. A verdict counts only beyond the judge's noise. Report
first drafts, fallback pages, numbers kept, native charts, pictures, judge axes. Live stack, judge references and
the Linux venv workaround: `docs/original-level-handoff-20261106.md` "How the measurements were run".
If your environment cannot run the live stack, finish the code with unit tests and state in the PR the exact env
values for the measurement run.

## Each PR states
What changed (file:line), the knob/revision and its env value, tests run with results, verifier ledger entries
added and why, risks to first-draft acceptance, and the measurement still owed.
