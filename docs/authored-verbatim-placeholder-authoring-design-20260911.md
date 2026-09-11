# Verbatim decks: text by construction, one admission gate, one repair loop — design (rev 3)

- Date: 2026-09-11 (rev 3. rev 2 followed the independent review in
  `artifacts/design-review-placeholder-authoring-20260911/review.md`; rev 3 adds one repair loop
  (2.5) and single capture per digest (2.3). Earlier revisions are in git history.)
- Scope: the document-verbatim generation path (`source_content_restyle`, `source_reflow=True`) of the
  authored pipeline. Ordinary prompt-driven authoring and the Hybrid export lane are not changed.
- Status: **accepted by the user on 2026-09-11** (`.spec-memory/decisions/authored.verbatim-placeholder-authoring.1.json`). Section 6 records the answers. Implementation is tracked as tasks T1-T6 below.
- Rule this design follows: **no per-path fixes.** An invariant is enforced in exactly one place, or it
  is made impossible by construction.
- What rev 2 changed, in one line: substitution is a construction guarantee, **not** a proof of the
  published artifact — rendered integrity stays; the gate gets an explicit three-state lifecycle, a
  substitution security boundary, the existing terminal-fallback rule, bounded overload retries, and
  a rollout that can actually be measured.
- What rev 3 changed, in one line: the number of HTML producers is reduced, not just gated — the
  gate-repair, VQA, structural/patch and rescue paths collapse into **one bounded repair loop** (2.5),
  and the gate owns Chromium capture so one expanded-HTML digest is captured once (2.3).

## 1. Why the current design keeps failing

The verbatim contract is "every source text block is published exactly as written". Today the pipeline
enforces it **after the fact**: the model retypes all 289 blocks inside free HTML; a ~2,000-line binder
(`services/source_content/text_stamping.py`, `source_stamp_dom.py`, `source_runs.py`,
`browser_readback.py`) then searches the model's DOM for each block, guesses run boundaries, repairs
terminal punctuation, stamps a QID; finally a Chromium readback compares rendered text to the source.

Slide HTML is produced or mutated at these points, each with its own copy of the checks:

| Producer | Where | Check present before 2026-09-11 |
| --- | --- | --- |
| author (single / batch) | `authored_presentation_pipeline.py:737-746`, `:779` | policy + stamp |
| illustration + source-image validation | `authored_presentation_pipeline.py:821-844` | stamp |
| author rescue (simplified retry) | `authored_author_stage_rescue.py` via pipeline `:692` | none until W25 |
| gate repair candidate | `authored_presentation_generation.py:2057` | policy + stamp + readback |
| VQA repair candidate (incl. full reauthor, `author_vision_qa.py:610-649`) | `authored_presentation_generation.py:2520-2603` | none until W23 |
| structural / patch repairs (`setText` in `author_slide_patch_contracts.py:131-145`) | repair loops | source-specific checks |
| terminal flag-only | `authored_terminal_slide_flag_only.py` | n/a (no mutation) |

Result over 2026-09-10/11 (runs 11-23, same 35-slide input, `repeatability-20260911.md`): 5/13
published; every failure was a different seam (run-13 `content:"1"`, run-14 candidate readback, run-17
VQA mutated a block, run-18 `</img>`, run-19 rescue unvalidated, run-22 provider 429 on rescue, run-23
binder could not bind a short title). Each got its own fix (W21, W23, W24, W25, W26). This design ends
that pattern.

Four root causes:

1. **Text is verified instead of guaranteed.** Any path that touches HTML can alter text; therefore every
   path needs the binder, and one missing call kills a deck.
2. **Markup policy is syntactic.** `utils/renderer_isolation.py` is a token-level `HTMLParser`; it refuses
   syntax the browser tolerates (`</img>`) while the real policy questions are semantic.
3. **Overload handling lives with callers.** Batch authoring has cooldown + recovery; rescue had none;
   other callers decide for themselves.
4. **Too many producers.** Four post-author repair paths each re-ask the model and each can break a
   slide that authoring got right; their measured yield is low (2.5).

## 2. Design

### 2.1 Placeholder authoring (text by construction)

The model never emits source text. The prompt carries a compact **block table** — for each block: id,
full text, character count, hard line breaks, marker/pipe semantics — so the model can size and group
content; the output contract is one placeholder token per block, never the text.

- Token form: `{{q:<sourceBlockId>}}` for a whole block. Sub-block tokens (character-range emphasis)
  are **not** part of the initial contract: the review showed that projected runs follow delimiters,
  not style boundaries, so run tokens cannot express in-sentence emphasis anyway. Whether a bounded
  character-range form (`{{q:<id>@<start>-<end>}}`, grapheme-safe, disjoint, covering the whole block)
  is added is decided by measurement 3.1.
- Inline styling is expressed by wrapping a whole-block token; the model cannot split text.
- Tokens are recognized **only in text nodes** of the parsed tree. A token in an attribute, a `<style>`
  block, a comment, or hidden content is a coded rejection. Substitution is a single non-recursive pass
  over the original text nodes using a server-controlled id table; source text that happens to look like
  a token is inserted as data, never re-expanded. Inserted text is HTML-escaped.
- Static checks, before any Chromium work: every expected token present exactly once; no unknown
  token; no residue in model-owned syntax. Each is a coded rejection
  (`source_placeholder_missing|duplicate|unknown|context`) with exact feedback for the retry.
- Source occurrences versus furniture: page markers, chapter breadcrumbs and repeated headers that
  today's binder tolerates as bounded duplicates (`text_stamping.py:22-24`) keep the existing header
  contract; source-owned page numbers use source tokens. Two distinct source blocks with equal text are
  two occurrence ids. No occurrence id is minted for decoration.
- Order: the deck-level source-unit/outline order check (`browser_readback_inspection.py:58-77`) is
  kept as is. Within a slide, DOM order of tokens is **recorded** as a deviation, not rejected, matching
  today's binder (`browser_readback.py:673-698`); the review is right that DOM order is not visual order.
- After substitution the expanded HTML is re-checked against the resource limits and the renderer
  policy (size, depth, text bytes, attributes) because expansion changes them.
- **Rendered integrity stays.** The Chromium readback still asserts rendered text equals source text,
  QID identity and uniqueness, capture-to-HTML binding and telemetry. Only the search-based *binding*
  (finding where the model put the text) disappears; the assertion that the final artifact is right does
  not. This costs no extra Chromium pass — it runs on the same capture the visibility check needs.
- Every path that generates HTML — single and batch author, rescue, VQA full reauthor — receives the
  same token contract. Geometry/style patches (`setText`-free) may operate on expanded HTML only when
  source-owned text and ids are byte-unchanged and the final evidence is renewed; rendered screenshots
  stay diagnostic input and are never a source of output text.

### 2.2 Normalization on a parsed tree, semantic policy kept

1. Parse model output with an HTML5-conformant parser (browser-equivalent tree construction) and pin
   the fragment/namespace behavior.
2. Run an **enumerated DOM rewrite pass** carrying today's semantic normalizers unchanged:
   disallowed CSS declarations (including every `content:`), hidden-bound-text handling, title line
   breaks, name-attribute stripping. Parsing replaces only the syntax-level fixes (void end tags and
   the like); it does not replace these rules.
3. Serialize canonically and apply the renderer policy to the tree (element/attribute/CSS/image
   checks). Raw-input diagnostics that the tree loses (duplicate attributes, oversized input) are
   captured before parsing and still reject.
4. Every rewrite records a receipt entry (`rule`, `count`, bounded `detail`).

A new serializer changes HTML digests and therefore capture reuse; the rollout (section 4) accounts
for that by switching normalization separately from the gate.

### 2.3 One admission gate with an explicit lifecycle

`admit_slide_html(context, html) -> Admission` is the only way HTML reaches a checkpoint. The gate
distinguishes three representations and says which stage may hold which:

| State | Content | May be checkpointed as |
| --- | --- | --- |
| `token` | model output, placeholders unexpanded | author-stage candidate only, versioned as `html_repr=token` |
| `expanded` | after 2.2 + substitution + static checks | author checkpoint (pre-illustration), repair candidates |
| `captured` | expanded HTML plus Chromium metrics/PNG | render/VQA checkpoints, publication |

- `context` is immutable and pins: presentation/operation, slide index and source unit, manifest and
  projection digests, policy revision, fenced revision and attempt token, terminal-fallback eligibility
  (the existing strict-profile rule in `authored_terminal_slide_policy.py:36-60`), and the set of
  already-admitted flags for the slide.
- Fixed order inside the gate: raw-input diagnostics → parse + rewrite → policy → substitution +
  static checks → post-expansion limits → (captured only) rendered integrity + visibility.
- Verdict is `admitted`, `flag_only(reasons)` or `rejected(code, evidence)`. Mode table:

| check | ordinary authored | verbatim, terminal fallback off | verbatim, terminal fallback on |
| --- | --- | --- | --- |
| raw diagnostics, parse/rewrite, policy | fatal | fatal | fatal |
| placeholder static checks | skip | fatal | fatal |
| rendered integrity (text, QID, order, capture binding) | skip | fatal | fatal |
| visibility | skip | fatal | fatal on unflagged slides; `flag_only` only on slides already flagged by the terminal policy (today's rule, `authored_presentation_generation.py:2875-2904`) |
| telemetry / diagnostic | skip | fatal (retryable) | fatal (retryable) |

  Repair-candidate rejection and terminal publication remain separate decisions: a candidate that
  fails visibility is rejected (the frozen pre-repair HTML stays), and the terminal policy decides
  flag-only publication exactly as today.
- Enforcement: the checkpoint writer refuses HTML without an admission receipt whose digest matches the
  HTML **and** whose context matches the checkpoint's fenced revision. Metrics/PNG are reused only on
  identical expanded-HTML and capture identity.
- Evidence: the receipt carries `code`, the predicate, block ids, digests and the context above; it is
  mapped into the existing quality/E4 timing/operation receipts (counts and bounded reasons preserved,
  detailed evidence behind digests). No layer collapses a code into `authored_unexpected_failure`.
- Vocabulary: attribute names, flag reasons and rejection codes are imported from one policy module; a
  test asserts every attribute a consumer reads is admitted by the renderer policy.
- Consumers that count characters (capacity gate, `authored_composition_policy.py:1516-1547`) and the
  Hybrid text-run assembler always see expanded HTML; `token` state never leaves the author stage.
- **Capture once per digest.** The gate owns Chromium capture. A `captured` admission is keyed by
  (expanded-HTML digest, capture profile); any later consumer that needs metrics or a PNG for the same
  key reuses the admission instead of rendering again. Today one slide can be captured up to five times
  (fit=True render, fit=False verification render, VQA capture, repair-candidate validation, final
  readback). After this change the count per slide is one per distinct expanded HTML plus one
  verification render when the fitter changed the HTML, and the receipt records the number.

### 2.4 Provider overload: one classifier, bounded physical calls

- Overload classification (429 / 5xx / call timeout / `usage_limit_reached`) and the cooldown schedule
  move into the bounded provider call (`utils/provider_idempotency.py`) with the shared
  `authored_batch_policy` state. Callers stop classifying.
- Physical-call bounds are **kept**: every request is reserved against the attempt ledger
  (`authored_checkpoint_state.py:203-280`), the operation deadline applies, and overload retries are
  bounded and visible in the receipt. Overload retries do not consume a *content* attempt; they do
  consume reservation and time. The generic bridge code is not treated as transient wholesale — only
  the classified statuses are.
- The ledger says two author ordinals per operation attempt
  (`authored.provider-attempt-budget-per-operation-attempt.1.json`) while code allows three (the third
  reserved for rescue). This must be reconciled in the decision ledger before implementation; the
  design does not pick a number.
- A whole-account `usage_limit_reached` becomes a deck-level coded pause with the reset time persisted
  and a resume path, not a slide failure.

### 2.5 One bounded repair loop instead of four repair paths

Today a slide can be rewritten by four LLM-driven paths after authoring: gate repair
(`authored_presentation_generation.py:2040-2310`), VQA repair including full reauthor
(`:2520-2640`, `author_vision_qa.py:610-649`), structural/patch repair
(`author_structural_repair_loop.py`, `setText` in `author_slide_patch_contracts.py:131-145`), and the
author-stage rescue (`authored_author_stage_rescue.py`). Measured value of those paths on the verbatim
deck:

- 44 of 53 review flags on published decks are `gate_repair_not_admitted`, i.e. a repair was attempted
  and not admitted; 32 of the 53 flagged slides have no blocking defect in the published bytes
  (`artifacts/flagged-overreport-20260911/report.md`).
- The 2026-09-04 audit recorded 24 LLM repair rounds with 3 successes.
- Four of the six deck-killing failures of 2026-09-11 (runs 14, 17, 19, 22) originated in a repair or
  rescue path, not in authoring.

Design: after the author stage there is exactly one repair loop, and it is the only producer of HTML
besides the author stage.

- Input: the `captured` admission of the slide (expanded HTML, metrics, PNG, verdict evidence).
- Order of attempts, fixed and bounded: (1) deterministic repairs that need no model
  (normalization rewrites, local-fit candidates, paint-order/overlap repairs already in
  `authored_generation_repair.py`); (2) at most `N` model repair calls (N is a registry knob, default 1)
  using one prompt family that receives the gate's evidence and the block table, and is bound by the
  same token contract as authoring: it can move, restyle and restructure, it cannot emit source text;
  (3) the terminal policy (flag-only publication or coded failure), unchanged.
- Every candidate the loop produces goes through `admit_slide_html`; a rejected candidate leaves the
  frozen previous admission in place. No candidate is ever admitted on a weaker check than authoring.
- The rescue path folds into the author stage as the last author attempt under the same contract and
  the same overload rules (2.4); it is no longer a separate producer with its own validation.
- VQA (vision review) stays as an **evidence source** feeding the loop's prompt, not as a path that
  writes HTML. Structural/patch contracts are retained only as deterministic, text-free operations
  inside step (1); the `setText`-class operations are removed from the verbatim path.
- Flags: a slide leaves the loop either admitted, or terminal with the loop's last coded verdict. The
  `repair_not_admitted` history stays in the receipt (classification added in 2dbd44a00) but is no
  longer the dominant reason for a review flag, because far fewer candidates are attempted.

Expected effects, to be confirmed by experiment (c): fewer seams (two producers instead of six), fewer
provider calls per deck, fewer `gate_repair_not_admitted` flags, and no change to what a
deterministic repair may do. Risk: slides that today are rescued by the second or third model repair
attempt may end up flagged instead; N is a knob so the trade-off is measurable, and the series compares
N=1 against the current behavior on the same input.

## 3. Measured before implementation (stored data only; no generation, no provider calls)

1. Emphasis inside blocks, measured as styled spans **within a projected run** (not merely blocks split
   across elements), over published verbatim decks (runs 11, 15, 16, 20, 21). Decides whether the
   character-range token form is needed.
2. Replay of runs 13, 14, 17, 18, 19, 23 HTML through 2.2 and the static checks as **negative
   fixtures**: each must yield a coded pre-render rejection or an admitted slide, and none may reach the
   readback as an integrity failure. Historical binder failures are kept as negative fixtures, never
   converted into successes.
3. Token accounting: if raw provider-usage receipts exist outside the run directories, compute the net
   output-token delta with the real tokenizer (old emitted source tokens minus ids/wrappers, plus retry
   output); if they do not exist, record that honestly and instrument E4 receipts so the first token-mode
   series produces the number. Current run directories carry no usage receipts.
4. The bound 43-page / 96-image-occurrence contract
   (`source-content.template-restyle-contract.7.json`) is added to the fixture set; the 35-slide deck
   alone is not sufficient acceptance material.

## 4. Rollout — four independent switches, in this order

1. **Centralize on the current representation.** Implement the gate and the receipt enforcement with
   the existing binder as the text step. Shadow: run the gate beside every per-path check on the same
   expanded HTML and metrics; target zero unexplained verdict differences over one repeatability
   series. Then delete the per-path copies. This step alone ends the "missing check on one path" class.
2. **Parser-based normalization** behind its own switch; compare receipts and digests against the old
   normalizers on stored HTML before enabling.
3. **Token mode** behind `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` (default off), pinned per operation
   and recorded in the checkpoint (`html_repr`). Legacy operations resume under the old representation;
   the binder is removed only after no unfinished legacy job remains and one token-mode series passes.
4. **Provider layer** change, separately switched and separately measured.
5. **One repair loop** behind `AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP` (default off) with knob `N`;
   enabled only after step 1 has removed the per-path checks, so the loop is born behind the gate.
   Experiment (c) runs the same input with the loop off and on (N=1) and compares publish rate,
   flagged-slide rate and classification, provider calls per deck, and wall time.

Three separate experiments, because a changed prompt cannot be compared by shadowing:
(a) gate-verdict parity on stored expanded HTML + metrics; (b) synthetic token fixtures and final
expanded bytes for substitution correctness (including run-23's short-title class and adversarial
token-looking source text); (c) fresh identically configured runs for product quality, wall time,
token usage and flags — the only experiment that measures the prompt change and the repair-loop
change; it also reports Chromium captures per slide before and after the capture-once rule in 2.3.

Success criterion: one repeatability series of five identical runs with zero deck-level failures
caused by text integrity, markup syntax, path-specific validation gaps or rescue overload, with complete
E4 receipts, real Fidelity publication, and flagged-slide rate not worse than the series before.
Flagged slides remain a layout metric, not a correctness one.

## 5. Not in scope

- Layout repair quality (`local-fit`), the pending decisions (a)/(b)/(c) in
  `authored-local-fit-overlap-space-design-20260909.md` §10, and the open-source adoption list in
  `layout-repair-open-source-review-20260911.md`. Those act on flagged-slide counts, not on deck death.
- Any change to what the model is allowed to design. Renderer policy content is unchanged.
- Hybrid export implementation. Its compatibility with changed text-run boundaries is verified in
  experiment (c), not assumed.

## 6. Decisions (answered 2026-09-11)

Answers: (1) approved; (2) whole-block tokens, character-range only if 3.1 demands; (3) ledger aligned to code: three ordinals, the third is the last author attempt that replaces rescue; (4) binder removed after one passing token-mode series and no unfinished legacy jobs; (5) N=1 default, N=2 compared in (c).

Original questions:

1. Approve 2.1-2.4 as the direction; implementation starts with section 3.
2. Token granularity: whole-block tokens only (recommended; emphasis via wrapping the block) versus
   adding bounded character-range tokens. Final answer from measurement 3.1.
3. Attempt ordinals: reconcile the ledger (two) with code (three) before section 4 step 4.
4. Binder removal: after one passing token-mode series **and** no unfinished legacy jobs
   (recommended), or kept for one release.
5. Repair-loop budget `N`: default 1 (recommended; the data in 2.5 shows extra model repair attempts
   mostly produce unadmitted candidates), measured against N=2 in experiment (c).

## 7. Implementation tasks

| id | task | depends on | owns |
| --- | --- | --- | --- |
| T1 | measurements 3.1-3.4 (read-only) | - | `artifacts/verbatim-design-measurements-20260911/` |
| T2 | admission gate on the current representation, receipt enforcement at the checkpoint writer, shadow across every producer, then removal of per-path checks (rollout step 1) | - | `services/authored_admission*.py` (new), `authored_presentation_generation.py`, `authored_presentation_pipeline.py`, `authored_checkpoint_state.py` |
| T3 | parser-based normalization with the enumerated DOM rewrite pass and receipts, behind a switch (rollout step 2) | - | `utils/authored_html_normalize*.py`, `utils/renderer_isolation.py`, new parser module |
| T4 | provider-layer overload classification with bounded physical calls and quota pause (rollout step 4) | - | `utils/provider_idempotency.py`, `services/authored_batch_policy.py`, `services/authored_wave_scheduler.py` |
| T5 | placeholder substitution module, static checks, block table prompt, knob (rollout step 3); integrates into the gate from T2 | T2 interface | `services/source_content/placeholder_*.py` (new), `utils/llm_calls/author_slide_prompt.py` |
| T6 | single repair loop with knob and N; rescue folded into the author stage (rollout step 5) | T2 | `services/authored_repair_loop*.py` (new), rescue/VQA/gate-repair call sites |
