# Verbatim decks: text by construction, one admission gate — design

- Date: 2026-09-11
- Scope: the document-verbatim generation path (`source_content_restyle`, `source_reflow=True`) of the
  authored pipeline. Ordinary prompt-driven authoring and the Hybrid export lane are not changed.
- Status: proposal awaiting the user's decision. Nothing here is implemented.
- Rule this design follows: **no per-path fixes.** An invariant is enforced in exactly one place, or it
  is made impossible by construction. See the failure history below for why.

## 1. Why the current design keeps failing

The verbatim contract is "every source text block is published exactly as written". Today the pipeline
enforces it **after the fact**: the model retypes all 289 blocks inside free HTML, a 2,000-line binder
(`services/source_content/text_stamping.py`, `source_stamp_dom.py`, `source_runs.py`,
`browser_readback.py`) then searches the model's DOM for each block, guesses run boundaries, repairs
terminal punctuation, stamps a QID, and finally a Chromium readback compares rendered text to the source.

Slide HTML is produced or mutated at five points, and each point carries its own copy of the checks:

| Producer | Where | Check present before 2026-09-11 |
| --- | --- | --- |
| author (single / batch) | `authored_presentation_pipeline.py:737-746`, `:779` | policy + stamp |
| author rescue (simplified retry) | `authored_author_stage_rescue.py` via pipeline `:692` | none until W25 |
| gate repair candidate | `authored_presentation_generation.py:2057` | policy + stamp + readback |
| VQA repair candidate | `authored_presentation_generation.py:2560` | none until W23 |
| terminal flag-only | `authored_terminal_slide_flag_only.py` | n/a (no mutation) |

Result over 2026-09-10/11 (runs 11-22, same 35-slide input, `repeatability-20260911.md`): publish rate
40%, and every failure was a different seam — run-13 `content:"1"`, run-14 candidate readback,
run-17 VQA mutated one block (integrity), run-18 `</img>` refused by the token-level policy, run-19 rescue
output not validated, run-22 provider 429 on the rescue call. Each got its own fix (W21, W23, W24, W25,
W26). That is the pattern this design ends.

Three root causes, not six:

1. **Text is verified instead of guaranteed.** Any path that touches HTML can alter text; therefore
   every path needs the binder and the readback, and one missing call kills a deck.
2. **Markup policy is syntactic.** `utils/renderer_isolation.py` is a token-level `HTMLParser`; it
   refuses syntax the browser tolerates (`</img>`) while the real policy questions are semantic
   (which elements, attributes, CSS properties, image bytes).
3. **Overload handling lives with callers.** Batch authoring has cooldown + recovery
   (`authored_batch_policy.py`), rescue had none, other provider callers each decide for themselves.

## 2. Design

### 2.1 Placeholder authoring (text by construction)

The model never writes source text. The prompt shows each block's text **for sizing and layout
judgement only**, and the output contract is: emit one placeholder token per block (or per run when the
block has projected runs), never the text.

- Token form: `{{q:<sourceBlockId>}}` for a whole block, `{{q:<sourceBlockId>:<runStart>-<runEnd>}}`
  for a run span. Ids and run boundaries come from the existing `project_source_runs` projection, so
  today's binding-id vocabulary (`source_run_binding_id`) is reused unchanged.
- Inline emphasis is expressed by wrapping a token (`<strong>{{q:12:0-0}}</strong>`), never by
  splitting text. This removes the run-mapping guesswork entirely.
- The server substitutes tokens **once, at the single admission gate (2.3)**, escaping the source
  text for HTML and stamping `data-source-block-id` / `data-qid` exactly as the binder does today.
- Deterministic pre-render checks replace the binder's search: every expected token present exactly
  once; no unknown token; DOM order of tokens follows source order unless the run is marked
  `order_free_before` (the existing pipe-join rule). A violation is a coded rejection
  (`source_placeholder_missing|duplicate|unknown|order`) raised before any Chromium work, so the
  retry is cheap and the feedback to the model is exact.
- Chromium readback keeps only what cannot be decided statically: visibility (clipped, occluded,
  off-canvas) and telemetry. Integrity, missing, and order failures cannot occur.

What this changes for the model: output length drops by roughly the source text volume, which also
lowers exposure to the output-token limit and to account rate limits. Layout authority stays with the
model; the server owns text characters only. This is not Deck IR or source-native (the model still
authors HTML/CSS; no IR, no server layout).

### 2.2 Parser-based normalization and semantic policy

Replace "strip one known bad pattern" normalizers (`authored_void_end_tag_normalize.py`, the
`content:` stripper, and the ad-hoc rules in `authored_html_normalize.py`) with one step:

1. Parse the model output with an HTML5-conformant parser (browser-equivalent tree construction:
   void end tags ignored, implied end tags, attribute normalization).
2. Serialize the tree back to canonical HTML.
3. Apply the renderer policy **to the tree**: element allowlist, attribute allowlist
   (`renderer_markup_policy.GLOBAL_ATTRIBUTES`), CSS property/value allowlist per declaration, image
   byte checks. The policy content is unchanged; only its input moves from tokens to a DOM.

Every normalization records a receipt entry (`rule`, `count`, `detail`) so evidence still shows what
was rewritten. The policy stays fail-closed for anything that is semantically disallowed; it stops
failing on syntax the browser would have repaired anyway.

### 2.3 One admission gate, enforced at the checkpoint

`admit_slide_html(slide_index, html, origin) -> Admission` is the only way HTML reaches a checkpoint.

- Fixed order: normalize (2.2) → policy (2.2) → placeholder substitution and static source checks (2.1)
  → visibility readback (Chromium, verbatim decks only) → verdict.
- Verdict is one of `admitted`, `flag_only(reasons)`, `rejected(code, evidence)`. The mode table
  below decides which check is fatal, flag, or skipped; path code contains no `if source_reflow`
  branches.

| check | ordinary authored | verbatim, terminal fallback off | verbatim, terminal fallback on |
| --- | --- | --- | --- |
| normalization + policy | fatal | fatal | fatal |
| placeholder static checks | skip | fatal | fatal |
| visibility readback | skip | fatal | flag_only |
| telemetry / diagnostic | skip | fatal (retryable) | fatal (retryable) |

- Enforcement: the checkpoint writer refuses HTML that does not carry an admission receipt whose
  digest matches the HTML. A new repair path that forgets the gate cannot persist anything.
- Evidence: the receipt carries `code`, the predicate that fired, block ids, and digests. No layer may
  collapse a code into `authored_unexpected_failure`; unknown codes pass through unchanged.
- Vocabulary: attribute names, flag reasons, and rejection codes are imported from one policy module;
  a test asserts every attribute a consumer reads is admitted by the renderer policy (the
  `data-intentional-overlap` vs `data-allow-overlap` class of defect).

### 2.4 Provider overload in one layer

`utils/provider_idempotency.py` (the bounded provider call) becomes the only place that recognizes
overload (429, 5xx, call timeout) and applies the shared cooldown state from `authored_batch_policy`.
Callers (batch, single, rescue, VQA, gate repair) stop handling overload themselves. Overload retries
never consume an author-budget attempt; content rejections do. A whole-account usage-limit response
(`usage_limit_reached` with a reset time) is surfaced as a deck-level coded pause, not as a slide
failure.

## 3. What is measured before implementation

These numbers are produced from stored data only (no generation, no provider calls) and go into the
first implementation task's report:

1. Across the published verbatim decks (runs 11, 15, 16, 20, 21): how many source blocks the model
   rendered as more than one element, and how many of those carry inline emphasis. This fixes the
   run-token granularity and shows how much styling placeholder authoring must preserve.
2. Replay of runs 13, 14, 17, 18, 19 HTML through 2.1 + 2.2: each must turn into a coded pre-render
   rejection or an admitted slide; none may reach the Chromium readback as an integrity failure.
3. Output-token delta per slide with text removed from the model output (from stored provider usage
   receipts), to size the speed and rate-limit effect.

## 4. Rollout

1. **Shadow.** Implement `admit_slide_html` and run it beside the existing per-path checks on every
   mutation, logging verdict differences. Target: zero unexplained differences over one repeatability
   series before any path switches.
2. **Switch the producers.** Replace the five per-path check blocks with the gate; delete the
   per-path copies. Enable checkpoint enforcement.
3. **Placeholder prompt behind a registry knob** (`AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING`, default
   off) so the binder path remains until a series shows the placeholder path publishes without
   integrity/missing/order failures. Then the binder's search-and-repair code is deleted.
4. Parser-based normalization and the provider layer change ship with step 2; they do not depend on
   the knob.

Success criterion for the whole design: one repeatability series of five identical runs with zero
deck-level failures caused by text integrity, markup syntax, path-specific validation gaps, or rescue
overload. Flagged (review-required) slides remain allowed; their count is a layout metric, not a
correctness one.

## 5. Not in scope

- Layout repair quality (`local-fit`), the pending decisions (a)/(b)/(c) in
  `authored-local-fit-overlap-space-design-20260909.md` §10, and the open-source adoption list in
  `layout-repair-open-source-review-20260911.md`. Those act on flagged-slide counts, not on deck death.
- Any change to what the model is allowed to design. The renderer policy content is unchanged.
- Hybrid export.

## 6. Decisions requested

1. Approve 2.1-2.4 as the direction; implementation starts with the measurements in section 3.
2. Run-token granularity for emphasis: whole-block tokens only (simplest, loses intra-block emphasis)
   versus run tokens (keeps emphasis, model must place several tokens). Recommendation: run tokens,
   decided finally by measurement 3.1.
3. Whether the binder path is kept behind the knob for one release or removed as soon as the
   placeholder series passes. Recommendation: remove after one passing series.
