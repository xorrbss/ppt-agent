# Authored generation on gpt-5.6-sol: quality first, speed second (2026-09-08)

## Premise

- The default Codex model is `gpt-5.6-sol` because `gpt-6-astra` costs too much.
  Astra remains the quality and speed reference the Sol path is measured against,
  not an option for the default.
- Priority order: PPT quality first, generation speed second. Cost is the reason for
  the model choice and is tracked per deck, but no change below trades quality for cost.
- Everything here is measured on the same 24-slide Korean proposal fixture
  (`fixture-doc-hyundai.json`, verbosity `standard`), the same code path and a
  PostgreSQL store, one variable per bench run.

## What the measurements say

Visible text, structure and type on the published decks (style/script bodies
excluded; `scratchpad/visible_text.py`, `fragmentation_measure.py`,
`type_floor_calibrate.py`):

| | Astra run 47 | Sol run 48 | Sol run 49 |
|---|---|---|---|
| brief chars (median) | 431 | 371 | 324 |
| visible chars (median) | 423 | 450 | 457 |
| visible / brief | 1.0x | 1.2x | 1.4x |
| text owners per slide (median / max) | 26 / 37 | 45 / 69 | 46 / 96 qids |
| label runs below 15px per deck | 13 (13px) | 591 (12-14px) | similar |
| body copy size | 18px | 17-18px | 17-18px |
| CSS chars per slide (median) | 2,221 | 5,371 | 5,096 |
| escaping cards before repair | 2 | 17 | 9 |

Both models put the same amount of text on a slide. Sol splits it into twice the
boxes, labels every box with 12-14px chips, and writes 2.3x the CSS. The live
type-scale prompt already says "labels 18px or larger, nothing below 13px"; Sol
ignores it, Astra follows it. A character budget in the prompt (run 49) changed
nothing. Prompt text is not the lever for this model; deterministic code is.

Provider receipts per deck (`scratchpad/receipts_by_stage.py`):

| | Astra run 47 | Sol run 48 | Sol run 49 |
|---|---|---|---|
| author calls / p50 latency | 25 / 66 s | 25 / 132 s | 25 / 129 s |
| author stage wall | 1,570 s | ~3,000 s | 3,092 s |
| repair stage wall | 820 s | 2,607 s | 2,970 s |
| output tokens per deck | 65k | 110k | 108k |
| input tokens per deck | 306k | 325k | 323k |
| deck wall | ~48 min | 72 min | 75 min |

The repair stage is the largest single cost on Sol: run 48 flagged 8 slides,
remade 6 of them and each remake is one 130 s author call executed **serially**
(`services/authored_repair_round_order.py::remake_first_flagged_slides`,
`for index in flagged: await remake_slide_once(...)`). The authoring reasoning
effort is already `low` (`utils/llm_calls/author_slide_effort.py`,
`DEFAULT_REASONING_EFFORT`; fast roles ask for `minimal`, which Sol rejects and
normalises to `low`), so there is no cheaper effort to move to. The token rate
profile runs 2 authoring calls per wave (`AUTHORED_BATCH_WAVE_CONCURRENCY`
unset -> 2) on a pool of 5.

## What "quality" means here, in numbers

Publish-blocking defects are already zero on both models after the ladder. The
quality gap the owner sees is fragmentation and tiny labels. The measures, in the
order they decide:

1. Blind paired rating by the owner on the same fixture, sides randomised per
   slide (harness: `scratchpad/rate/blind-rating.html`, reused from the
   2026-09-06 prompt-bundle rating). This is the only measure that can say a deck
   got better rather than merely less defective.
2. Type: no visible text below 15px (the design contract's 11pt floor), body copy
   at the ramp (18px standard).
3. Structure: text owners per slide at Astra's level (median ~30, hard cap 50).
4. Content fidelity: visible / brief between 1.0x and 1.4x; no slide loses a
   figure, date or name the brief carries.
5. Defects: escaping cards after the ladder 0, footer intrusions 0, page number
   preserved on every numbered slide.

Speed is measured as deck wall time and provider seconds by stage; cost as
output and input tokens per deck. Both are reported beside quality, never
instead of it.

## Design

### Q1. Fix type size before the first render, deterministically (done)

`enforce_authored_font_floor` already raised explicit CSS sizes below 12px at
author time. With `AUTHORED_FRAGMENTATION_GATE_ENABLED` on it now raises them
to the gate floor (`AUTHORED_TYPE_FLOOR_PX`, default 15) at both author call
sites and in the pipeline, the in-render fitter stops shrinking at the same
floor, and the prompt exemplar is clamped so the example does not contradict
the rule. Page-number detection follows the floor so the 12px page number
raised to 15px is still furniture (footer line stays at 648px).

Cost: zero provider calls. Risk: raised labels overflow their boxes on the
worst slides; those slides go to Q3.

### Q2. Measured clamp: a second deterministic pass on computed sizes (proposed)

The regex clamp misses `em`, `rem`, `var()`, `calc()` and the `font` shorthand.
The first render's DOM metrics carry the computed size of every text owner, so
the pipeline can set an inline `font-size` on each sub-floor qid and render
once more (seconds, not a 130 s author call). Only when the second render still
reports sub-floor text does the slide enter the repair ladder.

Cost: one extra render on affected slides. Gain: no author call is ever spent
on type size.

### Q3. Fragmentation repair that the model can act on (partly done)

The post-render gate counts text owners and emits `text_owners:N>50`. Because
merging boxes is structural, only a full re-author can do it: the local patch
round returns `full_reauthor` for structural change. The repair instruction now
travels as the issue's `detail`, which the remake brief quotes, and a slide
whose only defects are fragmentation gets the merge-boxes hint instead of the
generic "fewer bullets" hint.

Proposed on top:

- A separate remake budget for fragmentation-only slides (6 per deck) so they
  cannot starve real overflow slides of the shared 8.
- Remake order by DOM severity, not slide index.
- Skip the local patch round for fragmentation-only slides (it cannot help),
  bounding the extra provider cost of the gate to one call per slide.

### Q4. Reasoning effort as a quality lever (to measure)

Sol authors at `low`. The 2026-09-05 Astra series found `medium` the best
defect profile (0 defects, 0 flags) at +50% call time. Quality first means the
same question must be asked of Sol: one run at `medium`, judged by the blind
rating and the fragmentation numbers, accepting slower calls if the deck is
better. If `medium` yields fewer repairs, part of its cost comes back in the
repair stage.

### S1. Concurrent remakes (proposed; the largest speed lever)

Remakes run one after another. Running them the way the authoring wave runs
(2 concurrent under the token profile, honouring the same pool and 429
back-off) turns run 48's 2,607 s repair stage into roughly 900-1,200 s with
no change to what any slide is told. This is pure scheduling; admission,
budgets and ordering stay as they are.

### S2. Wave concurrency 2 -> 4 (to measure)

`AUTHORED_BATCH_WAVE_CONCURRENCY=4` halves the authoring stage wall if the
provider allows it. The owner accepts the 429 risk on this personal-plan token;
the run reports 429s and the batch policy's serial downgrade if it triggers.

### C. Cost per deck

Sol emits 1.7x Astra's output tokens for the same text because of CSS volume.
Fewer, larger boxes (Q1/Q3) reduce CSS and therefore output tokens; each run
reports tokens per deck beside quality so the cost story is measured rather
than assumed.

### Out of scope for this series

- The outline density ceiling (`OUTLINE_DENSITY_CEILING_ENABLED`) stays off:
  it is calibrated in characters for Korean and contradicts the English word
  floors; it also buffers the outline stream and condenses at the main model.
  Make it language-aware and light-model before enabling.
- The content-lock gate was reverted: both models paraphrase the brief, so a
  verbatim detector flags 22 of 24 Astra slides. Content fidelity is measured
  as a volume ratio instead.

## Bench series (one variable per run)

| run | change on top of the previous | decides |
|---|---|---|
| 50 | fragmentation gate on (Q1 + Q3 as merged) | owners, sub-15px labels, escaping cards, repairs, content loss vs brief |
| 51 | + concurrent remakes (S1) | repair stage wall; identical slide outcomes expected |
| 52 | + wave concurrency 4 (S2) | author stage wall, 429 count |
| 53 | Sol at `medium` effort (Q4) | fragmentation numbers, repairs, call latency |
| rating | 48 vs the best of 50-53, blind, sides randomised | whether the owner prefers it |

Acceptance per run: no publish-blocking defect, page number on every numbered
slide, visible / brief within 1.0-1.4x, and the run's own target metric moved
in the expected direction. A run that improves speed but loses the rating is
rejected.

## Implementation order and estimates

1. S1 concurrent remakes: half a day (`authored_repair_round_order.py`, a
   bounded gather with the wave's concurrency; tests on ordering and budget).
2. Q3 budget split, severity order, patch skip: half a day (new module beside
   `authored_terminal_slide_fallback.py`, which is at 499 lines).
3. Q2 measured clamp: one day (pipeline re-render seam, cache identity carries
   the floor, tests on `em`/`var()` cases).
4. Q4 and S2 are configuration runs, no code.

Runs 50-52 can proceed as each step lands; the rating waits for the best deck.
