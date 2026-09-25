# T6 — one bounded repair loop behind a knob

Design: `docs/authored-verbatim-placeholder-authoring-design-20260911.md` rev 3, sections 2.5, 4 (step 5)
and 7. Spec decision: `.spec-memory/decisions/authored.verbatim-placeholder-authoring.1.json`
("After authoring, exactly one repair loop produces HTML; every candidate is admitted through the same
gate", "No new judge or verify-repair loop is added").

Offline only: no provider call, no browser, no stack restart, no database access, no secret read.
The provider account is rate-limited through 2026-09-15, so nothing here is live evidence.

## What the loop is

`services/authored_repair_loop.py:246` `run_repair_loop(slides, deps)` runs each flagged slide through
one fixed, bounded order and returns the existing `SlideRepairResult` shape, so the generation stage's
admission, persistence and terminal handling downstream are untouched:

1. **Deterministic** (`services/authored_repair_loop_deterministic.py:26`) — the already-composed
   deterministic candidate (`_deterministic_repair_candidate`: normalization rewrites, paint-order and
   overlap repairs, off-canvas and blank-field reducers, header-eyebrow repair, alignment) plus the
   local fit the capture callback applies. One capture, one assessment.
2. **Model**, at most `N` calls (`services/authored_repair_loop.py:110` `_model_cycle`,
   `services/authored_repair_loop_model.py:74` `build_repair_author`) — one prompt family: the single
   authoring prompt `author_slide_html`, with the gate's bounded evidence as the correction hint. The
   placeholder block table and the token rules are appended to that same prompt by T5's path
   (`utils/llm_calls/author_slide_prompt.py:460`), so the repair is bound by the identical source
   contract as authoring: it may move, resize, restyle, regroup and restructure, and it may not write
   source text (`services/authored_repair_loop_evidence.py:70` `repair_correction_hint`).
3. **Terminal policy, unchanged** — the loop returns `final_status="unresolved"` and the existing call
   site declares `declare_unadmitted_gate_repair` and hands the slide to
   `resolve_terminal_gate_slides`, which on the verbatim path flags without remaking
   (`services/authored_terminal_slide_fallback.py:394`). No code of that policy changed.

Every candidate is assessed by the same per-slide evaluator the stage builds
(`make_source_repair_evaluator`), whose composition validator is the gate call `source_candidate` →
`admit_slide_html`; the result is bound with the real `bind_repair_candidate` evidence, so
`validate_repair_result` at the checkpoint keeps its full force. A rejected candidate is never
selected: the loop's selection only moves onto an assessed pair of bytes and metrics, so the frozen
previous admission stays. A candidate that raises (capture, evaluator, provider) is recorded and
dropped — one unusable candidate may not end a deck (validation run-14).

`N` is `AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS`, default 1, clamped 0..3. A slide with no durable
provider claim (`provider_recovery_allowed`) spends no model call at all.

## Switch points

`services/authored_presentation_generation.py:1845` computes
`single_loop = single_repair_loop_enabled(source_reflow=source_reflow)` once for the attempt. The knob
owns the **verbatim (source-reflow) path only**; an ordinary authored deck is untouched whatever the
knob says.

| Site | Knob off | Knob on |
| --- | --- | --- |
| `authored_presentation_generation.py:2196` bounded gate repair | `_revise_gate_repair_candidates` → `_revise_source_gate_repair_candidates` → `revise_structural_candidates` (patch proposals, `apply_authored_patch`, `setText`-class contracts), `max_cycles=2` per slide | `run_verbatim_repair_loop` (`services/authored_repair_loop_stage.py:27`) fills the same `structural_results` sink |
| `authored_presentation_generation.py:2189` vision review | not called here | `vision_evidence_for_flagged` runs the review over the flagged slides, **evidence only** |
| `authored_presentation_generation.py:2418` VQA repair block | runs as today (critique → local patch or full re-author → capacity admission → HTML replacement) | skipped; `visual_review_evidence_only` writes the receipt evidence for the review that already ran |

`utils/llm_calls/author_vision_qa.py:391` adds `review_slides_for_evidence` — the existing
`critique_authored` call, returned per absolute slide index, and nothing else done with it. It is the
only new entry point in that module; `revise_authored_deck` is unchanged and still owns the knob-off
path.

## Paths removed or bypassed when the knob is on (verbatim path only)

- **Gate repair via the structural patch loop** — `utils/llm_calls/author_structural_repair_loop.py`
  is not reached: no `propose_authored_slide_patch`, no `apply_authored_patch`, therefore no
  `setText`-class operation from `author_slide_patch_contracts.py`. The structural/patch *contracts*
  survive only as the deterministic, text-free repairs in step 1.
- **VQA repair** — `revise_authored_deck` is not called at all on this path; the vision review remains
  as evidence for step 2's prompt. It can no longer mutate a slide, which is where run-17 lost a deck.
- **Per-slide extra model attempts** — `max_cycles=2` plus a VQA cycle becomes `N=1` by default.
- **The separate rescue readback** — already removed by T2 (`authored_author_stage_rescue.py:100` →
  the common gate, which returns `None` under enforce) and the rescue itself already folded into the
  author stage as ordinal three by T4. **I made no edit for this**: adding a knob-conditioned bypass on
  top would take effect only in gate `shadow` mode, where it would delete a check with nothing
  enforcing in its place. That would relax integrity, so `services/authored_presentation_pipeline.py`
  is unchanged (it is still listed in my scope; the correct action there was none). The rollout order
  in section 4 already requires the gate to be enforcing before this knob is turned on.
- Not removed, and deliberately: the deck-level deterministic reducers after the repair stage
  (header-eyebrow fallback, closing capacity/title reducers) and the terminal policy. Neither calls a
  model.

With the knob on, the verbatim path has exactly two HTML producers: the author stage (three ordinals,
the third being the folded rescue) and this loop.

## Registry and accessors

`constants/settings_registry.json` — appended two entries only, after re-reading the file immediately
before the edit: `AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP` (boolean, default false) and
`AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS` (integer, default 1).
`utils/get_env_programme.py:265` adds `is_authored_verbatim_single_repair_loop_enabled` and
`get_authored_verbatim_repair_loop_model_calls` (clamped 0..3) and exports both.
`npm run test:settings-registry`: **passed, 163 keys, 2 tests**.

## Assumption recorded

The loop's model step emits **token-state** HTML and relies on the gate's placeholder static checks to
refuse retyped source text. With `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` off, the prompt carries no
block table; the call still runs and the legacy binder still binds its output, exactly as the author
stage behaves under the same combination. The design's rollout order (token mode before the repair
loop) is what makes the token contract present in practice; the loop adds no separate text-emitting
producer either way.

## Verification

- `tests/unit/test_authored_repair_loop.py` (new, 24 tests): **24 passed**. Covers the fixed order
  (deterministic before any model call, and the model step only after a deterministic rejection), the
  `N` bound for N ∈ {0,1,2,3}, a rejected candidate leaving the frozen admission (bytes, PNG,
  `changed=False`, `final_status="unresolved"`), a rejected model candidate keeping an accepted
  deterministic selection, a slide without a provider claim spending no call, a raising capture not
  ending the deck, `loop_slides` ordering, the knob defaulting off and covering the verbatim path only,
  the budget default and clamp, the correction hint's permissions and its one prohibition, that the
  loop's proposer is the one authoring prompt (`author_slide_html`), that this prompt carries T5's
  `placeholder_prompt_contract`, that **a candidate which retypes source text instead of placing its
  tokens is rejected by the gate with `source_placeholder_missing` while the tokenized candidate is
  admitted and expands to the source text**, that the vision review is evidence-only, and the
  evidence-only receipt's residual accounting (it reports the slides that actually entered the review,
  never the whole deck).
- Regression, gate-adjacent: `test_authored_admission.py`, `test_authored_admission_integration.py`,
  `test_source_placeholder.py`, `test_source_placeholder_prompt.py`, `test_authored_vqa_reachability.py`,
  `test_authored_visual_review_evidence.py` — **147 passed**.
- Knob off = unchanged behaviour, measured rather than asserted, twice:
  - `test_authored_pipeline.py` + `test_authored_generation_authority.py`: **130 passed / 9 failed**
    with my tree, and the identical 130/9 with `authored_presentation_generation.py` restored from
    HEAD — same nine names. (The brief named six in the pipeline file; HEAD `537822b1f` has seven.)
  - the whole `-k authored` unit sweep: **2,525 passed / 32 failed** (`test_authored_pipeline.py`,
    `test_authored_generation_authority.py` and `test_authored_terminal_slide_fallback.py` excluded).
    The 18 files holding 24 of those failures were re-run with both
    `authored_presentation_generation.py` and `author_vision_qa.py` restored from HEAD: the failure
    list is **byte-identical** (24 failed / 172 passed, only the timing line differs). With the nine
    above, 33 of the failures are directly shown to be pre-existing; the remaining eight are
    generated-contract drift and unrelated subsystems (`test_authored_vnext_*` ×6,
    `test_slidespec_authored_importer`, `test_workspace_artifact_publication_faults`).
  - `test_authored_terminal_slide_fallback.py` was not run or touched — another agent owns it.
- Ruff: new modules, the accessor module and the new test — **all checks passed**. The two shared files
  I touched (`authored_presentation_generation.py`, `author_vision_qa.py`) report the same **56
  pre-existing F401** with my changes and with the HEAD versions in place: **zero introduced findings**.
- `npm run test:file-size-ratchet`: fails only on the ten documented pre-existing offenders
  (`authored_local_fit_evidence.py`, `authored_presentation_publication.py`,
  `presentation_export_direct_attempt_service.py`, `public_export_operation.py`,
  `workspace_artifact_publication_service.py`, `WorkspaceOperationCard.tsx`,
  `presentation-generation.ts`, `PresentonMode.tsx`, `authored-hybrid/export.ts`,
  `authored-hybrid/slide-layer.ts`). **No T6 file is reported**: generation 3432 ≤ 3473 (cap),
  pipeline unchanged at 1496, `author_vision_qa.py` 1226 ≤ 1273, every new module ≤ 308 lines.

## What still needs live verification

Everything about the loop's *effect*. This change is structural and gated; no deck was generated.
After the 2026-09-15 quota reset the coordinator should run experiment (c) from section 4: the same
input with the knob off and on (N=1, then N=2), comparing publish rate, flagged-slide rate and
classification, provider calls per deck, Chromium captures per slide and wall time. Specifically
unverified offline:

- whether a token-mode re-author repair actually fixes the measured defect classes the patch loop used
  to address, and whether it keeps the slide's bound source images (the candidate is refused when it
  does not, so the risk is yield, not integrity);
- the interaction with gate `enforce` mode and the placeholder knob, which the rollout requires before
  this knob is enabled;
- that the vision review as evidence changes the model's repair for the better rather than only
  lengthening the prompt.
