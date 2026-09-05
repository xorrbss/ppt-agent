# Authored generation: free composition is the default again (2026-09-05)

Decision D16 of the generation programme
(`docs/generation-programme-master-design-20260904.md`). Amends D9/D14: the
archetype library stays, but it is no longer on the ordinary generation path.

## What changed

Three defaults for the ordinary authored deck (`CompositionProfile` id
`ordinary`; source-verbatim and source-outline are untouched):

| behaviour | before this change | now | restore with |
|---|---|---|---|
| deck-wide infographic plan + archetype component library (`7c95334e1`) | always on | off | `AUTHORED_INFOGRAPHIC_PLAN_ENABLED=true` |
| server-frozen composition envelope (text <= 460 chars, qids <= 40, 15px floor, `PRE-AUTHOR TEXT PLAN`) | always on | off; 12px floor (`AUTHORED_FONT_FLOOR_PX`) | `AUTHORED_COMPOSITION_ENVELOPE_ENABLED=true` |
| slides per authoring call, token profile | 3 | 1 | `AUTHORED_BATCH_SLIDES_PER_CALL=3` |

Seams, one each:

- `services/authored_deck_plans.py:_infographic_plan_applies` — the single
  decision for both `resolve_deck_plans` and
  `resolve_final_outline_infographic_plan`. Off means no plan call, no figure
  brief, no library CSS, and `derive_illustration_plan(None)`, so each slide
  decides its own picture as before 2026-08-22. The 08-22 deck-level
  illustration budget was deleted with `7c95334e1` and is not restored.
- `services/authored_composition_policy.py` — `SlideCapacity.enforced`.
  `build_authored_capacity_plan` marks ordinary slides relaxed unless the knob
  is set; a relaxed capacity states no `prompt_brief` and
  `capacity_violations` reports nothing, so the preflight cannot classify an
  ordinary slide as `authored_slide_capacity_exceeded`. Role, image ceiling
  and font floor stay readable for the receipts and the D7 fallback.
- `utils/llm_calls/author_deck.py` — no text plan and no capacity brief when
  the plan is relaxed (`_capacity_brief`).
- `services/authored_provider_rate_profile.py` — `_TOKEN_SLIDES_PER_CALL = 1`.

The DOM safety gate (`strict_dom_gate`, off-canvas / overlap inspection) and
D7 remake-then-flag stay on: they protect the free-composition path and do
not reduce density.

## Why

The user judged the 2026-09-05 fixture deck
(`artifacts/authored-fixture-run3-20260905/`) worse than the August decks on
every slide but the cover and slide 3. Three read-only analyses found:

- The envelope forbids August content outright. August medians per slide:
  417-748 visible chars, 29-48 text-bearing elements, 12.5-16.5px median font
  (`aug-2c89713d`, `aug-941c17d6`), against 460 chars / 40 qids / 15px.
  Replaying the August 24-slide input with the library on died on
  `authored_slide_capacity_exceeded` (`artifacts/aug-replay-libon-20260905/`).
- The figure contract replaces free composition with one server-drawn figure
  of 3-6 nodes (label <= 10, desc <= 18, takeaway <= 12 chars, no chart
  archetype, "no other diagram, card grid or chart may compete", hero pages
  with no side text, "quiet" pages). Stretching that figure to the bottom
  content line inflates thin content instead of adding any.
- Batching three slides per call cut per-slide markup to about a quarter.
- Neither the plan nor the envelope had an off switch.

Confounds in that fixture, fixed or noted separately: the outline padded four
slides with zero-width spaces (fixed in the outline model by the other
session), the fixture gave 161 usable chars per slide against 605 for the
comparable August deck, and the bench ran with images off.

## Evidence and follow-up

- Analysis memory: `~/.claude/projects/.../memory/density-regression-root-causes-20260905.md`.
- Bench run 4 of the 10-slide fixture with both knobs unset is the first
  measurement of this default; compare against `authored-fixture-run3` and
  the August reference before touching either knob's default again.
- Known library bugs left in place for the opt-in path: `before_after`
  skeleton falls back to `node.desc` for the BEFORE cell; `LOW_HEIGHT_MAX_PX`
  lacks layers / funnel / pyramid / roadmap / swimlane / process.
