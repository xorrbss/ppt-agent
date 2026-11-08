# Remaining SVG work after the nine-case chart verification — 2026-11-08

The nine-page synthetic chart artifact has completed actual capture and strict
consumer verification, normal publication, ordinary HTTP PPTX export and the
independent native-data/workbook/OOXML audit. These results are bounded to that
artifact and its recorded inputs. They do not complete the original request for
real template decks, product workflows or demonstrated visual improvement.
See the [actual verification report](svg-freeze-final-verification-20261108.md)
for exact results, hashes and retained failed attempts.

Chart V5, template pictures, content sizing and evaluation accounting have
already been implemented and received focused checks. The items below describe
remaining verification and measurement, not instructions to rebuild those
features. Findings from those checks may justify a subsequent scoped fix.

## 1. Finish Office verification of the existing chart artifact

Two pre-existing PowerPoint instances prevented the owned-process renderer from
starting. The user must save and close those documents, or explicitly defer this
step; no existing Office process should be killed or reused to bypass ownership.
With a clean environment, render all nine pages of the exact downloaded PPTX,
retain hash-bound PNGs and renderer/version evidence, and inspect the results.
Separator appearance and the retained `kg` title's position remain unverified.
Completing this step closes the chart artifact's Office gap only.

Authority: [controlling brief](../codex-brief-svg-freeze-quality-20261107.md),
lines 96–100; actual verification report, remaining Office section.

## 2. Verify real decks and the actual product workflow

Use authorized real, source-sized inputs with a framed template through the
actual UI and normal generation path: template selection and template-redesign,
outline, authoring, quality/recovery, persistence and relevant resume/remake,
then editable PPTX and affected PDF export/download. Check the persisted pins,
data representation and representative ordinary, data-rich and dense pages.
The existing synthetic chart replay and health checks do not establish this
provider-backed prompt-to-generation or full UI workflow acceptance. Preserve
normal authentication, ownership and tenant boundaries throughout.

Authority: controlling brief, lines 90–100;
[original goal](../codex-brief-original-level-20261106.md), lines 12–18.

## 3. Verify template pictures in actual generated decks

Picture implementation and fresh/frozen feature pinning have focused unit
acceptance. Still verify the fresh, explicitly pinned template-redesign path
with actual vector scene, spot, pair and pictograph planning/offers, placement
inside the body and final exported output. Check template palette/style,
illustration prohibitions, optional-failure behavior, provider-call budgets and
absence of duplicate cover art. Do not infer scene eligibility from the share
of original pages containing any picture, or introduce a duplicate pipeline.

Authority: controlling brief, lines 72–76; original brief, lines 67–76;
[integration journal](svg-root-integration-20261107.md), picture acceptance.

## 4. Measure content sizing on real template pages

Sizing guidance, admission pinning and the conservative metric are implemented
and unit checked. Verify actual table widths and card dimensions, card-internal
empty space together with body/page whitespace, readability and reading order.
The character-envelope proxy is not measured glyph ink, a density score or
evidence that visual quality improved. Preserve free body composition; do not
add source skeletons, fixed slots or a rejecting sizing validator without the
required evidence.

Authority: controlling brief, line 78; original brief, lines 78–84;
integration journal, accepted content-sizing implementation.

## 5. Run approved quality measurement with complete accounting

The evaluator and recovery accounting are implemented and focused checks passed;
the paired live improvement experiment has not been completed. Start with a small
selection pilot. For surviving changes, use the prescribed four source-sized
inputs, three runs per input and matched control/variant conditions: a full A/B
comparison is 24 decks. Separate experiments or combinations need their own
explicit counts. Before newly paid provider calls, specify code/settings/template
pins, deck count, cost ceiling and stop conditions, and obtain the required spend
approval. Do not treat elapsed time or a pending question as approval.

Retain failures, retries, missing evidence and actual spend. Compare first-draft
acceptance, fallback pages, numeric/data retention, native charts, pictures and
judge axes with real page denominators and matching inputs/pins/judge settings.
Heuristic thresholds are not statistical confidence, and zero leakage candidates
do not prove absence of leakage. Check a second template's count/contract metrics
without claiming its uncalibrated judge scores are comparable. The historical
2.81/zero-native result is not a refreshed baseline; overall 3.8 remains an
intermediate target rather than original-level visual acceptance.

Authority: controlling brief, lines 25–27, 68–70 and 100–102;
original brief, lines 89–96.

## 6. Re-measure the existing connectors policy

Run the existing template-scoped connectors experiment with at least three runs
(`CONTENT_STRUCTURE_POLICY_SCOPE=template`,
`CONTENT_STRUCTURE_POLICY_PARTS=connectors`) under its concrete approved run
specification. This is an experiment on existing behavior; no new flow-diagram
engine is required.

Authority: controlling brief, line 80; original brief, lines 86–87.

## 7. Verify relevant frozen non-SVG behavior without broadening scope

The preservation audit retained dedicated non-SVG tests and all baseline SVG CI
entries, with no deletion or retirement. Relevant unchanged non-SVG contracts
still need existing-fixture or read-only runtime checks where shared code makes
them applicable. Source preservation and scoped checks are not a complete
non-SVG behavioral suite or adversarial tenant/RLS validation. Do not rewrite or
deselect dedicated tests, buy a non-SVG generation merely to prove freezing, or
automatically run a full suite. Preserve existing non-SVG options, stored data,
recovery paths and request/response contracts.

Authority: controlling brief, lines 11–17, 84–88 and 96;
actual verification report, scope and running-stack limitations.

This document records no new run or validation result.
