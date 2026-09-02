# Authored infographic plan (2026-09-03)

Stage 1 of the generation-quality programme recorded in
`output/infographic-experiment-20260902/NEXT-SESSION-PROMPT.md`.

## What changed

The deck-scoped illustration plan (one model call that ranked which slides
earned a decorative picture, clamped to a floor of three and 15% of the deck)
is replaced by a deck-scoped **infographic plan**: one model call that decides,
for every slide of the final outline, which explanatory figure the slide is
drawn as and how much of the canvas it claims.

| Module | Role |
|---|---|
| `models/authored_infographic_plan.py` | Vocabulary (20 archetypes + `none`), per-slide entry (`archetype`, `weight`, `takeaway`, `nodes`, `emphasis`, `density`), deck plan, draft repair (`coerce_infographic_plan`), scene ceiling, `without_scenes`. |
| `utils/llm_calls/author_infographic_plan.py` | The single deck call. Sends every slide's full content and role so labels can be copied verbatim. Fails open. |
| `services/authored_deck_infographic_plan.py` | Resolves the plan on the final outline, demotes scenes when the deck cannot illustrate, derives the illustration plan, loads the persisted plan. |
| `utils/llm_calls/author_infographic_marker.py` | Renders one slide's figure brief into the authoring prompt. Stage 2 replaces its free-form "draw …" text with the archetype slot contract. |
| `models/authored_illustration_plan.py` | Kept as the persisted picture-slot shape (`theme.illustrationPlan`); the budget and the appetite reading are gone. Only the outright-prohibition reading survives. |

## Decisions

- **Naming.** `AuthoredVisualPlan` already names the deterministic
  story/silhouette plan, so the LLM-decided plan is `AuthoredInfographicPlan`.
- **Density** is how much supporting text each node carries (`low` labels
  only, `medium` label + phrase, `high` label + phrase + figure). The node
  count is `len(nodes)` (3–6). Both are the knobs the best-of-N stage varies
  without another model call.
- **Rhythm** is per-slide `weight` (`hero` / `standard` / `quiet`) plus one
  line of `art_direction`; `none` forces `quiet`. Cover, section, agenda and
  closing roles are forced to `none` in code.
- **Labels are verbatim.** A node whose label the slide content does not
  contain is dropped; a figure left with fewer than three nodes becomes a quiet
  page. Takeaway and labels more than twice their budget are dropped, not
  clipped.
- **Pictures.** A slide carries a generated picture exactly when its archetype
  is `scene`. The illustration plan is derived (`derive_illustration_plan`),
  never resolved separately, so the two cannot disagree. One guard survives
  from the old budget: scenes above a quarter of the figure pages are demoted
  to `icon_list`, hero-weighted scenes kept first. A style without an
  illustration prompt, or a request that refuses pictures, demotes every scene
  before the plan is frozen.
- **Scope.** The plan runs for every authored deck of any length and any style
  (the old plan skipped the `default` style and decks under eight slides). It
  does not run for exact reconstruction or for a source-content restyle.
- **Checkpoint and persistence.** The resumable checkpoint stage keeps the
  name `illustration` but stores `infographicPlan`. The published theme carries
  both `infographicPlan` and the derived `illustrationPlan`; page remake and
  quality review replay both.
- **Story plan coherence.** When a figure is planned, the deterministic story
  kind follows the archetype for the process, architecture and metric
  families; other archetypes keep the deterministic story, and the figure
  brief states that it wins on the figure's shape.

## Known baseline failures (not introduced here)

On the HEAD this was built on, `tests/unit/test_authored_illustration_plan_wiring.py::test_the_plan_round_trips_through_the_persisted_theme`
fails because publication now requires an owner the fake session does not
provide, and two provider-bridge tests in
`test_authored_generation_speed_cache_contract.py` fail independently. Whole-tree
Ruff and the file-size ratchet also fail on HEAD; the new modules are clean and
under the size limit.
