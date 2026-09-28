# Canvas expansion — content budget spec (rev 2, 2026-09-29)

Workstream spec under `docs/aspect-ratio-expansion-design-20260928.md`. Scope: how much text an outline slide carries on each canvas, so a smaller page is not filled with the same amount of text as a 16:9 page. Derived from the outline stage's actual knobs. Rev 2 folds in the independent review of rev 1 (52/100): the schema hard cap was missing, rounding was undefined, the `n_auto` rule was circular, and forcing condensation would have removed streaming.

## 1. What the outline stage does today (verified)

| Knob | Where | Value / behaviour |
|---|---|---|
| Prompt target per slide | `utils/llm_calls/generate_presentation_outlines.py:58-66` | "at least about 80 / 150 / 250 words (Korean: about 250 / 500 / 800 characters) in 3–4 / 4–6 / 5–7 bullets" for concise / standard / text-heavy; with the ceiling flag on, "and at most about N characters" is appended (`:77-81`) |
| **Schema hard cap** (deterministic) | `utils/get_dynamic_models.py:24-52` | when `n_slides` is given, each slide's `content` gets `max_length` **400 / 1000 / 2500** (and `min_length=1`); the decoder cannot exceed it. With `n_slides=None` the plain `PresentationOutlineModel` (no cap) is used (`generate_presentation_outlines.py:250-254`) |
| Floor (soft) | `utils/get_env_programme.py:141-143`, `outline_density.py:352-369` | `OUTLINE_DENSITY_FLOOR_CHARS` default **350**, compared with the **deck median**; below it and with a source document, one deck-level LLM expansion of the thin slides; never fails a deck |
| Ceiling (soft, flagged) | `utils/get_env_programme.py:146-148`, `outline_density.py:82-96`, `outline_density_ceiling.py:180-234` | `OUTLINE_DENSITY_CEILING_ENABLED` default **off**; when on, per-slide 400 / 700 / 1100, one deck-level LLM condensation, then `enforce_floor_after_condense` (`:152-177`) |
| Streaming | `generate_presentation_outlines.py:271-277, 324-347` | the outline streams chunk-by-chunk unless expansion or condensation is possible (`density_buffers`), in which case it is emitted as one final chunk |
| Slide count | `generate_presentation_outlines.py:169-172, 191`, `presentation_generate.py:1068-1082`, UI default `useUploadPageState.ts:141` | `n_slides` given → schema `min_length=max_length=n_slides`, endpoint truncates overshoot and fails undershoot; `None` → the prompt says "Number of Slides: auto-detect" and the model picks the count **in the same call** |
| TOC adjustment | `utils/outline_utils.py:68-81`, `presentation_generate.py:988-999` | non-authored templates only; authored decks pass `n_slides` through |
| Receipt | `outline_density.py:99-122, 313-332` | `schemaVersion`, `slideCount`, `slideChars`, `median`, `floor`, `belowFloor`, `ceiling`, `aboveCeiling`, `expanded`, `condensed`; consumers are `_log_receipt` and the two `apply_*` functions only |
| Callers | `presentation_generate.py:1036-1047`, `api/v1/ppt/endpoints/outlines.py:248` | neither passes a canvas today; the second regenerates from a stored `presentation` |

Outline-stage LLM calls today: 1 (stream) + ≤1 (expansion, source only) + ≤1 (condensation, flag only). None per slide.

## 2. Invariants (tests first)

1. `floor < ceiling` for every verbosity on every canvas.
2. Prompt target ≤ ceiling ≤ schema cap for every verbosity on every canvas.
3. Outline-stage LLM calls per deck ≤ 3 (unchanged); per slide 0.
4. `f = 1` canvases produce **exactly** today's numbers and today's streaming/flag behaviour.
5. Streaming behaviour is a function of the flags only, never of the canvas.
6. Pre-existing and **unchanged**: the concise prompt target (250) is below the verbosity-independent floor (350). This spec scales it, it does not fix it; a per-verbosity floor is a separate decision.

## 3. Canvas factor

`ratio = usable_area(canvas) / usable_area(16:9)`, usable area = safe area (presets: 64px per side, `services/authored_canvas.py:28`; A4: 60px, `services/document_canvas.py:115`). `f = min(1, ratio)` — more area buys whitespace, not text. `f` is stored in the registry as `budget.charsFactor` **rounded to two decimals**, and every derived number uses one rule: `scaled(base) = floor(base × f + 0.5)` (round half up on the two-decimal `f`).

| canvas | safe area | ratio | f |
|---|---|---|---|
| 16:9 (1280×720) | 1152×592 | 1.000 | 1.00 |
| A4 landscape (1188×840) | 1068×720 | 1.128 | 1.00 |
| A4 portrait (840×1188) | 720×1068 | 1.128 | 1.00 |
| 9:16 (720×1280) | 592×1152 | 1.000 | 1.00 |
| 9:13 (720×1040) | 592×912 | 0.792 | 0.79 |
| 1:1 (720×720) | 592×592 | 0.514 | 0.51 |
| 21:9 (1680×720) | 1552×592 | 1.347 | 1.00 |

Invariant 1 under this rounding: the 16:9 gap is 50 chars (350 → 400), so the scaled gap is ≥ 50·f − 1 ≥ 24 for every f ≥ 0.5; the two independent roundings move each number by at most 0.5, so the floor stays below the ceiling by construction as long as `f ≥ 0.5`. The registry generator refuses an entry with `f < 0.5` (no such canvas exists; a future one needs its own budget).

## 4. Rule

For `f < 1`, every text knob scales by the same rule; for `f = 1` nothing changes:

| knob | 16:9 | scaled | 1:1 (f = 0.51) | 9:13 (f = 0.79) |
|---|---|---|---|---|
| prompt target chars (c/s/t) | 250 / 500 / 800 | `scaled` | 128 / 255 / 408 | 198 / 395 / 632 |
| prompt target words | 80 / 150 / 250 | `scaled` | 41 / 77 / 128 | 63 / 119 / 198 |
| bullets lo–hi | 3–4 / 4–6 / 5–7 | `max(2, scaled(lo))`–`max(3, scaled(hi))` | 2–3 / 2–3 / 3–4 | 2–3 / 3–5 / 4–6 |
| floor (deck median) | 350 | `scaled` | 179 | 277 |
| ceiling (per slide) | 400 / 700 / 1100 | `scaled` | 204 / 357 / 561 | 316 / 553 / 869 |
| **schema max_length** | 400 / 1000 / 2500 | `scaled` | 204 / 510 / 1275 | 316 / 790 / 1975 |

- The **schema cap is the enforcement** for `f < 1`: it is deterministic, costs nothing, and applies whenever `n_slides` is given (the UI always gives one). For `n_slides=None`, `get_presentation_outline_model_with_n_slides` is generalised to `outline_model(n_slides: int | None, verbosity, max_length)` so the cap applies there too (slide count unconstrained, content capped).
- The prompt phrase for `f < 1` is the existing "and at most about N characters" (`:77-81`), emitted regardless of the ceiling flag; the "at least" target is scaled. Words and characters scale together.
- Floor and ceiling are scaled but their **mechanisms are unchanged**: expansion still needs a source and the floor knob; condensation still needs the flag. No forced condensation → streaming and call count are unchanged (invariants 3, 5).
- Slide count: `n_slides` given → unchanged (the user chose it; each slide says less). `n_slides=None` → the prompt adds one sentence: "Choose the slide count so that each slide fits its budget; prefer more slides over denser slides." No pre-call, no post-hoc count change. The earlier `ceil(n_auto / f)` rule is withdrawn (circular).
- Receipt: `schemaVersion` → `outline-density/v2`, adds `canvas` (id) and `factor`; existing keys unchanged (`_log_receipt` reads by fixed key).

## 5. Wiring

- `generate_ppt_outline(..., canvas_budget: CanvasBudget | None = None)`; `get_system_prompt`/`get_user_prompt` take the scaled targets; `outline_model(...)` takes the scaled `max_length`; floor/ceiling values come from `canvas_budget` (or today's constants when `None`).
- `CanvasBudget` is built by `utils/llm_calls/outline_canvas_budget.py::budget_for_canvas(canvas_id)` from the registry entry.
- Caller 1 (`presentation_generate.py:1036`): canvas id from `request_canvas_contract(request)` (already resolved by admission).
- Caller 2 (`outlines.py:248`, regeneration): canvas id from `presentation_canvas_metadata(presentation.theme)` (`services/authored_canvas.py:199-217`).
- No other consumer of `outline_density_receipt` outside tests.

## 6. Tests

- `tests/unit/test_outline_canvas_budget.py`: the §4 table for every registry canvas × verbosity (exact values above); invariants 1–2 for all; `f = 1` equals today's constants and today's `max_length`; the rounding rule; the generator's `f ≥ 0.5` refusal.
- `tests/unit/test_generate_presentation_outlines.py`: existing cases unchanged (the `f = 1` path); new case asserting `density_buffers` is unaffected by `canvas_budget`.
- Bench (bench spec): for a fixture manuscript at 1:1 with `n_slides=8`, every slide's usable chars ≤ 510 (schema) and the median ≤ 357 (ceiling) without the ceiling flag; expansion/condensation call counts equal the 16:9 run.
