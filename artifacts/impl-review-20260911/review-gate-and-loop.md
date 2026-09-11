# Independent review: T2 admission gate (`b5a98b9f2`) and T6 single repair loop (`b24e137a4`)

Date: 2026-09-11. Tree: `C:\project\PPT-agent\ppt-agent`, branch `serve/main-20260902`, HEAD `5df4dd3cf`
(reported at review start; `git log` at read time showed `5208a895e` on top as a docs-only commit).

Scores: **PPT quality 7/10 · Speed 6/10 · Architecture 6/10.**

This is a read-only source review. No source, test, config, stack or database was modified; no provider
call, no browser render, no test execution, no secret read. The only file written is this one.
Consequently **every claim below is static**: I did not run `pytest`, so the pass counts in the T2/T6
reports are taken as reported, not verified.

Citation convention: paths are relative to `servers/fastapi/` unless they start with `artifacts/` or
`docs/`. `D` = `docs/authored-verbatim-placeholder-authoring-design-20260911.md` (rev 3). `W27` =
`artifacts/design-review-placeholder-authoring-20260911/review.md`. **Verified** = I read the code or
the stored artifact. **Inference** = an expected consequence I did not measure.

---

## A. Scores

### A1. PPT quality impact — 7/10

**What is genuinely preserved (verified).**

The three publication-critical predicates survive intact and, in enforce mode, become *harder* to skip:

- The **flag-only rule is narrower than `D`'s literal table and matches today's code**, exactly as
  W27 P1-4 demanded. `services/authored_admission.py:151` grants `flag_only` only when
  `context.terminal_fallback_eligible AND context.already_admitted_flags` — i.e. only on a slide the
  terminal policy already flagged. Integrity, missing blocks and order can never become a flag
  (`:134-147` returns `rejected` first); telemetry is `rejected` + `retryable=True` (`:148-149`). The
  same triple is re-asserted at the writer: `services/authored_admission_receipt.py:98-101` refuses a
  `flag_only` receipt unless `source_reflow AND terminal_fallback_eligible AND already_admitted_flags`.
  The flags fed into the final gate come from the real flagged list
  (`services/authored_admission_publication.py:112-122`), so the enforce path reproduces the
  `generation.py:2875-2904` rule rather than widening it.
- **Rendered integrity is kept, not replaced by static token checks** (W27 P1-1). The captured branch
  still calls `inspect_browser_source_readback` (`authored_admission.py:208-217`) against the same
  Chromium telemetry, and `admit_slide_html` refuses a capture with no telemetry at all
  (`:205-207`).
- **A captured representation cannot be silently rewritten.** `authored_admission.py:200-204` rejects
  any captured HTML that the renderer policy would change (`expanded != html`), which is the correct
  answer to the brief's "normalizers mutate HTML post-admission" question: after capture, the receipt
  digest and the bytes are forced to be the same object. For the `expanded` state the receipt is
  computed over the *returned* HTML (`:237-248` uses `digest_html(expanded)`), so a consumer that
  persists `admission.html` can never hold a digest mismatch.
- **No check was weakened per slide.** `check_producer` in enforce runs only the common gate
  (`services/authored_admission_session.py:189-194`) and the gate's own order —
  raw diagnostics → policy → text step → post-expansion policy → captured readback
  (`authored_admission.py:169-219`) — is a superset of what each removed per-path block did; the
  removed bodies survive as shadow comparators (`authored_admission_legacy.py:14`,
  `authored_admission_publication.py:20`). The offline same-input parity table
  (`artifacts/verbatim-design-impl-20260911/T2-static-parity.json`, 464/464) supports "no new
  rejections on stored bytes", and is honest that it covers only static verdicts.
- **No exception is swallowed into `admitted`.** `admit_slide_html` initialises `verdict="admitted"`
  but only three exception classes downgrade it to `rejected` (`:222-231`); anything else re-raises
  (`:233`). There is no `except Exception: pass` on the admit path.
- **T6 removes four HTML mutators.** With the knob on, the verbatim path has two producers: the author
  stage and the loop. The `setText`-class contracts are unreachable (`author_structural_repair_loop.py`
  is not called at all — `authored_presentation_generation.py:2196-2219`), and the VQA repair block is
  guarded off (`:2418 if getattr(request,"vision_qa",False) and not single_loop`). That directly
  removes the run-17 seam. Verified: no second, still-live VQA/structural entry point on the verbatim
  path.
- **A provisional (not admitted) loop candidate cannot publish.** The loop *does* select a
  `provisional` candidate internally (`services/authored_repair_loop.py:153-159`,
  `authored_repair_loop_deterministic.py:177-183`), but the caller discards it:
  `authored_presentation_generation.py:2259-2262` sends anything with
  `final_status != "admitted"` to `declare_unadmitted_gate_repair` and `continue`s, leaving
  `htmls[index]` frozen. `validate_repair_result` still runs on the admitted ones (`:2240-2241`).

**What could regress (verified mechanism, unmeasured effect).**

1. **The deck-level cardinality assertion disappears in enforce.** `inspect_browser_source_readback`
   fails closed when `len(units) != len(slides)` or `len(htmls) != len(slides)`
   (`services/source_content/browser_readback_inspection.py:46`). In enforce the gate calls it
   per slide with a one-element manifest and outline (`authored_admission_context.py:76-91`), and
   `final_readback` iterates `enumerate(htmls)` only (`authored_admission_publication.py:116-124`).
   A deck that renders fewer slides than the source manifest has units is therefore no longer caught
   at publication. See B2.
2. **The rescue's slide-time integrity failure becomes a deck-time one.** Under enforce
   `source_reflow_rescue_readback` returns `None` before the legacy hard-failure raise
   (`services/authored_author_stage_rescue.py:100-115`), and `check_producer` with `evidence=True`
   deliberately skips `result.require()` (`authored_admission_session.py:189-191`). Integrity is still
   caught — at `final_readback` — so correctness holds, but the deck runs to completion first.
3. **The loop's model step is a full re-author, not a repair.**
   `services/authored_repair_loop_model.py:318-320` calls `author_slide_html(contents[index], ...)`;
   the slide's current HTML is never passed. This is the same mechanism as the VQA full re-author that
   lost run-17, now promoted to the only model repair. It is fenced by a strictly monotonic evaluator
   (`services/authored_structural_repair.py:255-266`: no visibility or composition regression versus
   *both* baseline and current selection), so the integrity risk is contained; the exposure is
   **yield** — a re-roll discards whatever the first draft got right.
4. **The repair prompt is self-contradictory in the combination the knobs actually allow.**
   `services/authored_repair_loop_evidence.py:85-88` unconditionally instructs "place every source
   occurrence through its placeholder token". With `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING=false`
   (the default) there is no block table and no token contract in the prompt, and the model is
   simultaneously handed the source text in `contents[index]` and forbidden from writing it. See B3.

Not 8 or 9 because of (1) and (4); not 5 or 6 because every per-slide predicate that matters is
preserved, the flag-only boundary is implemented strictly, and four mutation seams are genuinely
removed.

### A2. Speed — 6/10

**Capture-once is not enforced in the default configuration (verified).**
`services/authored_admission_capture.py:15` short-circuits to the raw capture when
`index is None or session is None or session.mode == "shadow" or not session.contexts`. Since
`AUTHORED_ADMISSION_GATE_MODE` defaults to `shadow`
(`utils/get_env_programme.py:26-33`; `constants/settings_registry.json:777-784`), **as shipped the
capture-once rule does nothing**. `captured_metrics_or_existing` is likewise enforce-only
(`authored_admission_capture.py:65`).

**Capture call-site count (verified by exhaustive enumeration).** 25 distinct capture sites are
reachable from the authored generation path. **9 route through `authored_admission_capture.py`**:
`authored_presentation_pipeline.py:1148`, `:1165`, `:1293`;
`authored_presentation_generation.py:653`, `:677`, `:1760`, `:1983`, `:2571`;
`utils/llm_calls/author_vision_qa.py:133`.
**16 call the renderer directly and are outside the memo and outside the receipt's `captureCount`**,
including the two fit-verification renders that are the closest analogue of the ones that *were*
wrapped — `services/authored_local_fit_admission.py:93` and `:162`, both `fresh=True` — plus
`authored_figure_best_of.py:91,123` (1 + N−1 renders per figure slide),
`authored_quality_loop.py:107`, `authored_style_anchor_stage.py:249`,
`author_structural_repair_loop.py:88,267`, `author_vision_qa.py:613,630`,
`authored_generation_repair.py:486`, `authored_terminal_slide_fallback.py:233`, and three deck-wide
`ensure_browser_metrics` gathers at the tail (`authored_generation_quality_gate.py:98`,
`authored_generation_scene_persistence.py:105`, `authored_final_metrics.py:103`).
So `D` 2.3's "the gate owns Chromium capture" is implemented for roughly a third of the capture
surface. The receipt's `captureCount` (`authored_admission_receipt.py:66`) counts only admissions the
gate saw and will therefore **under-report** real captures per slide — the exact number experiment (c)
is supposed to produce.

**Marginal saving is smaller than it looks.** The renderer already has a digest-keyed LRU plus
in-flight join (`utils/slide_capture.py:1880-1930`) and a capture cache (`:1262-1290`). The admission
memo's incremental value is limited to calls the renderer cache already misses — chiefly `fresh=True`
verification renders — and those are exactly the ones routed *around* it at
`authored_local_fit_admission.py:93,162`. Inference: the realised saving in enforce is small and
unmeasured.

**Enforce adds work.**
- Two full `validate_authored_slide_html` passes per admission (`authored_admission.py:176-181` and
  `:194-199`), each of which parses and canonicalises the document; a captured admission also runs a
  third stamping pass (`:184`).
- A per-slide `inspect_browser_source_readback` at capture time (`:211`) **and** again at
  `final_readback` (`authored_admission_publication.py:116-124`).
- A **serial deck-wide re-persist of every render checkpoint** at final readback:
  `authored_admission_publication.py:173-186` awaits `_complete_stage_after_external_success` once per
  slide, writing HTML + PNG + metrics + evidence bundle again for slides that already have a render
  checkpoint. On a 43-page deck that is 43 sequential artifact/DB writes added to the tail.
- Crash-orphan evidence adoption is **disabled** under enforce (`authored_checkpoint_state.py:113`,
  `authored_checkpoint_stage_batch.py:175`), so a resumed attempt re-renders what it could have
  adopted.

**T6 is a real provider-call reduction (verified structurally).** Per flagged slide the knob-off path
spends up to `max_cycles=2` structural transmissions plus a VQA cycle; knob-on spends at most `N=1`
(`authored_repair_loop.py:208-227` — exactly `N` `propose` calls, one per `_model_cycle`, no
off-by-one) plus one vision-review call per flagged slide used as evidence only
(`authored_presentation_generation.py:2180-2187`). Captures per flagged slide under the loop: one for
the deterministic candidate (`authored_repair_loop_deterministic.py:161`) and one per model cycle
(`authored_repair_loop.py:143`) — no extra baseline capture, because the baseline evaluator consumes
the frozen metrics.

**No double work in shadow mode for the gate itself**, by construction:
`check_producer` runs the gate and the legacy check on the same bytes and returns the legacy result
(`authored_admission_session.py:196-206`); no second capture and no second provider call are
introduced. The shadow cost is CPU (two validator passes + a readback per producer call).

6/10: the headline speed claim is inert at the default, the mechanism covers a minority of capture
sites, enforce adds measurable tail I/O, and the honest win (T6's provider calls) is real but
unmeasured.

### A3. Architecture — 6/10

**Is the invariant enforced in exactly one place? No — in one *predicate*, but at several
boundaries, with named bypasses.**

- Two writers carry the check: `services/authored_checkpoint_state.py:197` (`complete_stage`) and
  `services/authored_checkpoint_stage_batch.py:164` (`complete_render_stage_batched`). Both no-op when
  `html is None` or the mode is not enforce (`services/authored_admission_checkpoint.py:57`).
- **The receipt is usually minted at the writer, not obtained by the producer.**
  `services/authored_admission_checkpoint.py:18-43` (`prepare_checkpoint_write`) runs `check_producer`
  itself for any call that arrives without a receipt. Exactly **one** call site passes a receipt
  explicitly (`authored_admission_publication.py:183`); the other nine rely on that funnel
  (`authored_presentation_pipeline.py:766`, `:1382`; `authored_presentation_author_batch.py:36`;
  `authored_illustration_stage.py:235`; `authored_presentation_generation.py:1083`, `:2346`, `:2617`;
  `authored_terminal_slide_fallback.py:318`). Functionally the gate still runs, but the design's
  "the producer holds a receipt and the writer only checks it" is not what the code does.
- **Two live bypasses.** `services/authored_vnext/generation/legacy_bridge.py:330` and `:345` call
  `complete_stage(..., html=...)` directly, outside any `admission_scope` and with no receipt (see
  B1). The two crash-recovery HTML writes (`authored_checkpoint_state.py:133`,
  `authored_checkpoint_stage_batch.py:182`) also write HTML without a receipt; they are suppressed
  only because enforce disables recovery upstream.
- **`slides.html_content` — the column that is actually published — has no receipt check at all.**
  `services/authored_presentation_publication.py:478` builds `SlideModel(html_content=htmls[i])` from
  the in-memory list. The only thing standing between an unadmitted byte and the DB is the digest
  comparison at `authored_admission_publication.py:185`. Every post-generation mutator
  (`authored_canonical/service.py:441,629`, `authored_page_remake_service.py:874`,
  `authored_quality_review_finalize.py:53`, `authored_header_update_service.py:144`,
  `slidespec_artifact_store.py:222,285`, `authored_vnext/generation/sql_repository.py:678`) writes
  HTML with CAS-only protection. That is defensible scoping — those are not T2's producers — but it
  means "no HTML reaches publication without a receipt" is not yet true of the product.
- **`check_producer` fails open when contexts are missing**, in enforce as well as shadow:
  `authored_admission_session.py:170` returns `legacy(html), None` whenever `session.contexts` is
  empty. `configure_admission` is called unconditionally for authored decks
  (`authored_presentation_generation.py:1510`), so this is not reachable today, but it is a silent
  downgrade rather than a coded failure.

**Mode table: implemented as designed (verified).** `authored_admission.py:129-153` reproduces `D`'s
table row for row, and every verbatim-only check is behind `context.source_reflow`
(`:184`, `:208`), so ordinary authored decks skip placeholder checks, rendered integrity and
visibility exactly as specified.

**Three states: real, not nominal (verified).** `state` is validated in
`AdmissionContext.__post_init__` (`authored_admission_context.py:58-60`), drives distinct branches in
`admit_slide_html` (`:171-174` token-only placeholder context check; `:183-192` captured-vs-expanded
text step; `:200-219` captured-only capture binding and readback), and is re-checked at the writer:
`authored_admission_receipt.py:118` forbids `token` outside the author stage and forces `captured` for
the render stage. The `token → expanded` transition is recorded (`authored_admission.py:192`
`context.for_state(text_result.state)`), so the receipt's `htmlRepr` reflects the state actually
persisted.

**Shadow/enforce switch: safe with one hole.** Shadow returns the legacy verdict and swallows gate
exceptions (`authored_admission_session.py:179-185`; `authored_admission_publication.py:125-136`), so
an instrumentation bug cannot change behaviour — *except* that `session.context(index)` is evaluated
**outside** the try at `authored_admission_session.py:173`, so a context/index defect raises in shadow
too. `get_authored_admission_gate_mode` also raises `ValueError` on an unrecognised value
(`utils/get_env_programme.py:30`), so a typo in the env var kills every generation.

**Does the loop really replace the four paths? Yes, on the verbatim path, with no double firing.**
`single_loop` is computed once (`authored_presentation_generation.py:1840`) and gates all three sites
(`:2196` repair, `:2180` evidence-only review, `:2418` VQA block). In enforce, `check_producer` never
calls the legacy comparator (`authored_admission_session.py:189-194`), `require_bound_source_html`
returns early (`authored_admission_producers.py:159-162`), and the legacy final readback is not run
(`authored_admission_publication.py:137-159` is the shadow branch only). I found **no leftover
per-path check that fires twice** in enforce.

**Not delivered as designed:** the rescue is still a separate producer (T6 report says so explicitly
and the reasoning is sound — removing it in shadow would delete a check with nothing behind it), and
nothing in code prevents the unsafe knob orderings (B3, B4).

6/10: the predicate is genuinely centralised and the state machine is real; the enforcement boundary
is not.

---

## B. Problems, ranked

### B1 — HIGH. `AUTHORED_ADMISSION_GATE_MODE=enforce` breaks the authored-vnext / page-remake paths

**Verified.** `services/authored_vnext/generation/legacy_bridge.py:330-337` and `:345-353` call
`complete_stage(..., html=html)` with no `admission_receipt`, outside any `admission_scope`.
`validate_checkpoint_write` (`services/authored_admission_checkpoint.py:57`) resolves the mode via
`enforce_receipts()`, which falls back to the **global knob** when no session exists
(`:13`). With the knob at `enforce`, `require_admission_receipt(None, ...)` raises
`AdmissionReceiptError(RECEIPT_MISSING)` (`authored_admission_receipt.py:85`). These entry points are
live: `api/v1/ppt/endpoints/authored_vnext/master/router.py:80`,
`services/workspace_page_remake_operation_handler.py:16`,
`services/workspace_quality_export_operation_handlers.py:26`.

**Failure scenario.** The operator flips the knob to `enforce` for the verbatim series. The first
workspace page-remake or vnext master-owned deck after that fails at its first author checkpoint with
an internal receipt code, in a subsystem nobody changed and nobody is watching.

**Fix (cheap).** Make `enforce_receipts()` require an open session (`return session is not None and
session.mode == "enforce"`), or scope the knob read to the authored generation path. One line, plus a
test asserting a scope-less `complete_stage(html=...)` still writes under enforce.

### B2 — HIGH. The deck-level source cardinality/order assertion is lost in enforce

**Verified.** `services/source_content/browser_readback_inspection.py:46` is the only place that
asserts `len(units) == len(slides)` and `len(htmls) == len(slides)`. In enforce the gate calls the
inspector per slide with a single-element manifest and outline
(`services/authored_admission_context.py:76-91`), so all three lengths are 1 by construction, and
`final_readback` iterates `enumerate(htmls)` (`authored_admission_publication.py:116-124`). The
combined result it synthesises (`:192-199`) carries only
`checked_slide_count / checked_block_count / evidence / visibility_failures / binding_deviations /
failed_slide_indices`; `missing_block_ids`, `order_violations`, `integrity_failures` and
`telemetry_failures` are not aggregated at all.

**Failure scenario.** A 43-unit source produces 42 rendered slides (one slide dropped by an earlier
stage). Legacy: `verify_browser_source_readback` raises
`source_content_browser_readback_failed / "source and browser slide cardinality differs"`. Enforce:
each of the 42 slides validates against its own unit, `final_readback` returns a `combined` receipt
claiming `checkedSlideCount=42`, and a deck that silently dropped a source page publishes.
Secondary: the published `source_readback` payload no longer reports order/integrity/telemetry
counters, so the receipt cannot be audited for them afterwards.

**Fix (medium).** In `final_readback`'s enforce branch, assert
`len(htmls) == len(kwargs["request"]._source_document_outline.slides) == len(manifest.units)` before
the per-slide loop, and carry the four missing counters into `combined`.

### B3 — HIGH (product). Loop-on + placeholder-off produces a self-contradictory repair prompt

**Verified.** `services/authored_repair_loop_evidence.py:85-88` always emits
"You MAY NOT write, retype, shorten, summarize, translate or invent any source text: place every
source occurrence through its placeholder token exactly once, exactly as the source contract above
requires." `services/authored_repair_loop_model.py:318-320` hands the model `contents[index]` —
the source text — as the authoring content. The placeholder block table is appended only when
`AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` is on, which defaults to `false`
(`utils/get_env_programme.py:265-270`). `single_repair_loop_enabled`
(`services/authored_repair_loop.py:53-56`) checks only `source_reflow` and its own knob — nothing
couples it to the placeholder knob or to the gate mode.

**Failure scenario.** The operator enables the loop first (it is the change with the clearest expected
win) and leaves the other two knobs at their defaults. Every model repair call now tells the model to
place tokens that were never defined while forbidding it to write the text it was just handed. The
most likely outputs are a literal `{{q:...}}` string in the slide, an omitted block, or a refusal —
all of which the legacy binder then fails to bind, i.e. the run-23 class. The T6 report records the
knob combination as an "assumption" but does not identify the prompt contradiction.

**Fix (cheap).** Make the token paragraph of `repair_correction_hint` conditional on
`placeholder_authoring_enabled()`, with a legacy branch that says "reproduce every source text block
exactly as given" — and add a test for each branch.

### B4 — MEDIUM. Nothing enforces the rollout order the design relies on

**Verified.** `D` §4 requires: gate enforcing (step 1) → normalization (2) → token mode (3) →
provider (4) → loop (5). All five are independent registry booleans/strings
(`constants/settings_registry.json:777`; `utils/get_env_programme.py:26,260-285`) with no
cross-validation. The T6 report itself relies on the ordering for its rescue decision ("adding a
knob-conditioned bypass would take effect only in gate `shadow` mode, where it would delete a check
with nothing enforcing in its place").

**Failure scenario.** Loop on + gate in shadow: `check_producer` returns the *legacy* verdict, so the
loop's candidates are judged by the per-path checks the loop was designed to replace, the rescue's
legacy hard-failure is still live, and the receipts record a "single repair loop" deck that was
actually gated the old way — the live series then attributes the result to the wrong change.

**Fix (cheap).** In `single_repair_loop_enabled`, log a loud warning (or refuse) when
`admission_gate_mode() != "enforce"`; record both knob values in the loop's deck-level receipt.

### B5 — MEDIUM. The expanded-output marker is digest-exact and therefore fragile

**Verified.** `services/authored_admission_producers.py:34-35` demotes `fresh` to `False` only when
`(index, digest_html(html))` is already in `session.expanded_outputs`, and the set is populated with
the digest of the gate's own return value (`:52-53`). In `utils/llm_calls/author_slide.py` the HTML is
mutated between the two gate calls — `_strip_nonvisual_pointer_events` (`:1183`),
`enforce_authored_font_floor` (`:1185`), `ensure_body_root_horizontal_extent` (`:1187`),
`finalize_infographic_slide_html` (`:1192`), `_bind_offered_source_image_slots` (`:1226`) — so the
digest recorded at `:1182` never matches the bytes that reach `:1230`. The same chain exists in
`utils/llm_calls/author_batch.py:240-289`.

Today this is harmless: `provider_expanded_html` passes `fresh=False` explicitly
(`authored_admission_producers.py:76`), and the legacy text step (`stamp_source_blocks`) is idempotent
(`authored_admission.py:105-111`). But the T2 report's claim that "an attempt-local expanded-output
marker prevents downstream author/rescue/VQA adapters from expanding inserted source text again" holds
only for adapters that receive the gate's bytes **unmodified**.

**Failure scenario (T5 combination).** With placeholder authoring on, any adapter that calls
`author_html` with the default `fresh=True` on HTML that was expanded and then touched by one
byte re-enters `text_step` in `token` state; the placeholder step finds no tokens and returns
`source_placeholder_missing`. Under enforce that is a slide rejection.

**Fix (medium).** Key the marker on the *context/state*, not the digest: carry an explicit
`representation` through the author call chain (the parameter already exists,
`authored_admission_producers.py:50`), or have `admit_slide_html` detect an already-bound document
instead of trusting a digest set.

### B6 — MEDIUM. The loop's own baseline and binding steps can still end a deck

**Verified.** `services/authored_repair_loop.py:184` (`await evaluator(index, slide.html, metrics)`),
`:205` (`_advance_repair_evaluator`) and `:228` (`bind_repair_candidate`) are outside any try/except.
The evaluator calls `inspect_browser_source_readback`, which raises `BrowserSourceReadbackError` on
malformed telemetry (`browser_readback_inspection.py:46,78`), and `bind_repair_candidate` raises
`ValueError("repair_candidate_evidence_mismatch")` on an empty PNG
(`services/authored_structural_repair.py:317-323`). Anything escaping `run_repair_loop` is converted
at `authored_presentation_generation.py:2363-2409` into a **non-retryable**
`authored_composition_safety_gate_failed` — the whole deck.

**Parity note (important):** the old path has the same two unguarded seams
(`utils/llm_calls/author_structural_repair_loop.py:93` and `:384`), so this is **not a new
regression** — but it does mean the loop's stated property ("one unusable candidate may not end a
deck") holds only for the capture, propose and evaluate steps *inside* a cycle
(`authored_repair_loop.py:165-174`, `authored_repair_loop_deterministic.py:164-173`), which is exactly
what `test_a_failing_candidate_does_not_end_the_deck`
(`tests/unit/test_authored_repair_loop.py:210-220`) covers. The baseline path is untested.

One behavioural difference worth recording: the old loop recovered missing metrics with a render
(`author_structural_repair_loop.py:87-88`); the new loop passes `dict(slide.metrics or {})`
(`authored_repair_loop.py:183`). On the verbatim path this is unreachable, because
`authored_presentation_generation.py:1971-1990` guarantees a Mapping (refreshing with a capture, or
raising), so it is currently latent — but it is a removed safety net.

### B7 — MEDIUM. Enforce adds a serial deck-wide re-persist at the end of generation

**Verified.** `services/authored_admission_publication.py:173-186` awaits
`_complete_stage_after_external_success` per slide, in order, at final readback — rewriting HTML, PNG,
browser metrics and the evidence bundle for slides that already hold a render checkpoint, then reading
each back with `checkpoint_stage_html(saved, publishable=True)`.

**Failure scenario.** On the bound 43-page contract this is 43 sequential DB commits plus 43 artifact
writes appended to the deck's critical path, after all provider and browser work is done. A lease
expiry or cancellation during that window now happens at the most expensive possible moment. Not a
correctness bug — `complete_stage` is fenced — but a wall-time and failure-window cost the reports do
not mention.

**Fix (cheap-medium).** Skip the write when the existing checkpoint already carries a publishable
receipt with the same HTML digest, or batch/gather the loop.

### B8 — LOW/MEDIUM. `AdmissionCaptureStore` — the class that implements the design's reuse rule — is dead

**Verified.** `services/authored_admission_receipt.py:136-151` defines `AdmissionCaptureStore` with
exactly the contract `D` 2.3 asks for (key `(html_digest, capture_profile)` *and* an
`identity_digest` equality check at `:148`). Nothing in `servers/fastapi` outside
`tests/unit/test_authored_admission.py:19,303` imports it. The shipped reuse path is
`AdmissionSession.captures` (`authored_admission_session.py:33`) keyed by
`(digest_html(html), profile)` with **no** context comparison
(`authored_admission_capture.py:20-25`), plus admission memoisation keyed by
`(identity_digest, html_digest, metrics_digest)` (`authored_admission_session.py:58-62`).

The substance is fine — a `RenderCapture` is a pure function of HTML and capture kwargs, and the
admission that follows is recomputed per index with the correct context. The problem is evidentiary:
`test_same_profile_capture_reuse_is_attempt_bound` validates a class production never runs, so the
suite does not actually prove the shipped reuse is capture-profile- and context-bound.

**Fix (cheap).** Either delete the class and retarget the test at `capture_once` / `session.admit`, or
make `capture_once` use it.

### B9 — LOW/MEDIUM. Evidence-only VQA receipts attribute the loop's successes to the vision review

**Verified.** `services/authored_repair_loop_evidence.py:152-161` emits, for every reviewed slide,
`{"attempted": True, "succeeded": index not in unresolved, "critique_source": "vision_review"}`, while
passing `fixed=()` and `accepted_fixed=()` to `visual_review_outcome` (`:162-171`). The review wrote
no HTML; the loop decided the outcome.

**Failure scenario.** The live series' receipts show the vision review "succeeding" on slides the
deterministic step fixed, reproducing — in the opposite direction — the over-report
(`artifacts/flagged-overreport-20260911/report.md`) this programme exists to remove. The scoping of
`slide_count=len(details)` is correct and well-commented; the per-slide `succeeded` field is not.

**Fix (cheap).** Report `succeeded: False` (or omit the field) for an evidence-only review, and record
the loop's own outcome under the loop's `details_sink` (`authored_repair_loop.py:289-307`), which
already carries it.

### B10 — LOW. Uncoded internal errors still collapse, and `require_admission_receipt` under-pins identity

**Verified.** `D` 2.3 says "No layer collapses a code into `authored_unexpected_failure`", and T2 does
remove the observability allowlist. But a non-`ValueError` escaping `admit_slide_html`
(`authored_admission.py:233`) is re-raised uncoded in enforce
(`authored_admission_session.py:181-182`), so it still reaches the caller as a bare exception;
`INTERNAL_ERROR` / `UNCODED_ERROR` exist in the vocabulary
(`authored_admission_policy.py:51,61`) but are used only for shadow rows.

Separately, `require_admission_receipt` compares `presentation_id` only indirectly, via the optional
`context=` identity digest; `operation_id` is optional too (`authored_admission_receipt.py:110-115`).
`validate_checkpoint_write` does supply the context whenever a session with contexts exists
(`authored_admission_checkpoint.py:61-70`), so the gap is only reachable off the generation path —
where B1 already applies. Note also that the `expected` context it builds copies `state`,
`already_admitted_flags`, `capture_profile`, `allow_illustration_slots` and `finalized` *from the
receipt being validated* (`:63-69`), so those five fields are not actually checked against anything.

### B11 — LOW. Enforce changes the order of source binding relative to the provider-side rewrites

**Verified.** In shadow, `provider_raw_html` is a no-op (its legacy callback is the identity —
`authored_admission_producers.py:37-42` with `validate_policy=False, bindings=None`). In enforce it
returns the gate's **expanded** bytes, so `stamp_source_blocks` now runs *before*
`enforce_authored_font_floor`, `finalize_infographic_slide_html` and `_bind_offered_source_image_slots`
(`utils/llm_calls/author_slide.py:1181-1229`), whereas today binding happens only after them at
`:1230`. The second admission re-stamps, and stamping is idempotent, so the net result should be
identical — but this is a real ordering change that **only exists in enforce**, is not covered by the
offline static-parity table (which replays stored bytes, not the provider chain), and is exactly the
kind of difference the live shadow series must be read for.

### B12 — LOW. Captured admissions revalidate HTML the pipeline never re-validated

**Verified mechanism, unverified consequence.** `admit_slide_html` requires a captured document to be
a fixed point of `validate_authored_slide_html` (`:200-204`). Pipeline HTML satisfies this by
construction. Fitter output does not obviously: `capture_once` admits `result.fitted_html`
(`authored_admission_capture.py:32-33`), which the local fitter produced after the last validation.
I checked the most likely failure mode and it does *not* apply — the fitter's stamped attributes are
already in `GLOBAL_ATTRIBUTES` (`utils/renderer_markup_policy.py:37-65`), and
`canonicalize_authored_slide_html`'s `finalized` difference only strips authoring-only `img`
attributes (`utils/llm_calls/author_slide.py:592-595`), which is idempotent in the safe direction.
Residual risk: any fitter/repair rewrite that the normalizer would touch turns into a rejected
captured admission — harmless inside `capture_once` (evidence only), but **fatal at
`final_readback`** (`authored_admission_publication.py:160-168`, non-retryable, at the very end of a
deck). This is the single highest-value thing to watch in the first enforce run.

---

## C. Improvements and what must be verified live

| # | Change | Why | Cost |
|---|---|---|---|
| C1 | `enforce_receipts()` requires an open session; add a regression test for a scope-less `complete_stage(html=...)` | B1 — stops the knob from breaking vnext/page-remake | Trivial (1 line + 1 test) |
| C2 | Assert deck/manifest/outline cardinality in `final_readback`'s enforce branch and aggregate the four missing counters into `combined` | B2 — restores the only lost deck-level assertion | Low |
| C3 | Branch `repair_correction_hint` on `placeholder_authoring_enabled()` | B3 — removes a contradictory prompt in the default knob combination | Low |
| C4 | Warn (or refuse) when the loop is enabled with the gate not enforcing; record both knob values in the deck receipt | B4 — makes the live series attributable | Low |
| C5 | Replace the digest-keyed `expanded_outputs` marker with an explicit representation carried through the author chain | B5 — the marker is the only thing standing between T5 and a double text step | Medium |
| C6 | Wrap `_repair_slide`'s baseline evaluate / advance / bind in the same guard the cycles use, returning `unresolved` | B6 — completes "one unusable candidate may not end a deck" (and would also cover the old path) | Low |
| C7 | Skip the final-readback re-persist when the checkpoint already holds a publishable receipt with the same digest | B7 — removes 40+ sequential writes from the deck tail | Low-medium |
| C8 | Delete `AdmissionCaptureStore` (or use it) and retarget its test at `capture_once` | B8 — the reuse guarantee is currently proved against dead code | Low |
| C9 | Route `authored_local_fit_admission.py:93,162` through `capture_once` | Speed — the two fit-verification renders are the closest analogue of what was already wrapped, and both bypass every cache | Medium (they are `fresh=True` by intent; needs a profile that preserves that) |
| C10 | Fix the evidence-only `succeeded` field | B9 — avoids a new over-report in the series receipts | Trivial |

**Must be verified live (provider quota resets 2026-09-15).** None of the following can be settled
offline, and neither T2's static parity nor T6's unit suite substitutes for them:

1. **Shadow parity on the real producer set.** `T2-static-parity.json` covers 464 stored *static*
   inputs with legacy normalization and placeholder authoring off. It contains **no captured-state
   rows, no rescue rows, no VQA rows and no structural rows**, because those need metrics the stored
   fixtures lack (the T2 report says so). Run one repeatability series in shadow and read
   `scripts/authored_admission_shadow_report.py` for verdict differences **per producer**, especially
   `author_rescue`, `vqa_repair`, `structural_patch`, `terminal_flag_only` and `publication`.
2. **B12 — captured fixed-point.** Count rejected captured admissions with predicate
   `capture_binding` in the shadow receipts. A non-zero count means the first enforce run will lose a
   deck at final readback.
3. **Actual captures per slide.** The receipt's `captureCount` sees only 9 of 25 sites. Instrument at
   the renderer (`utils/slide_capture.py`) for the before/after number `D` §4(c) asks for; the gate's
   own number will under-report.
4. **Enforce on a legacy/resumed job.** Orphan adoption is off under enforce
   (`authored_checkpoint_state.py:113`), and `checkpoint_stage_html(publishable=True)` returns `None`
   for every pre-enforce checkpoint (`authored_checkpoint_html.py:44-53` — no receipt in
   `checkpoint.error`). Verify a job started under shadow and resumed under enforce completes, and
   decide deliberately whether it should.
5. **Experiment (c) for T6** as the T6 report specifies (knob off vs N=1 vs N=2 on the same input:
   publish rate, flagged rate and classification, provider calls, captures, wall time) — but run it
   with the gate enforcing and, once available, with the placeholder knob on, or B3 contaminates it.
6. **Fence/attempt stability.** I verified statically that `attempt_token` and `_workspace_fencing_token`
   are set once per claim (`services/presentation_generation_operation_handler.py:184-189`) and that
   `configure_admission` pins them once (`authored_presentation_generation.py:1510`), so no in-attempt
   drift is possible. Still confirm on a real resumed operation that a stale-fence receipt is rejected
   with `authored_admission_context_mismatch` rather than accepted.

---

## D. Verdict

**Ship the knobs as defaulted (all off/shadow) — but fix B1, B2 and B3 before the first enforce or
loop-on run.**

The default configuration is genuinely inert: the gate returns the legacy verdict and swallows its own
errors (`authored_admission_session.py:179-206`), the writer check no-ops
(`authored_admission_checkpoint.py:57`), capture-once is disabled
(`authored_admission_capture.py:15`), and both loop knobs are off
(`utils/get_env_programme.py:265-285`; `constants/settings_registry.json:777`). The one shadow-mode
behaviour change I found is the unguarded `session.context(index)` at
`authored_admission_session.py:173`, which is unreachable while `configure_admission` runs
unconditionally. Merging at these defaults is low risk and unblocks the shadow series.

Fix first, in this order:

1. **B1** — `enforce_receipts()` must require an open session. Without it the enforce flip breaks
   `authored_vnext` and workspace page-remake, which are outside this programme's blast radius.
2. **B2** — restore the deck cardinality assertion. It is the only correctness check the enforce path
   actually loses, and losing it is the kind of silent publication this design exists to prevent.
3. **B3** — branch the repair hint on the placeholder knob. As shipped, the first operator who turns
   on the loop alone gets a prompt that forbids the model from writing text it was just handed and
   tells it to use tokens that do not exist.

B4 and B6 are cheap and should follow. B5 should be resolved before T5's knob is turned on. B7–B12 are
follow-ups that do not gate the series.

**Why not "do not ship":** the two commits do the hard part correctly. The flag-only boundary is
implemented strictly and in two independent places, rendered integrity is retained exactly as W27 P1-1
demanded, the three representations are real and enforced at the writer, the mode table matches the
design row for row, and T6 removes four HTML producers from the verbatim path with no leftover
double-firing. The defects above are boundary and rollout defects, not a flawed design.

**Why not "ship the knobs as defaulted" unqualified:** the knobs exist to be turned on, the rollout
order the design depends on is enforced by nothing but discipline, and two of the three blockers only
appear the moment someone turns one on.

**Scope note.** T3 (`utils/authored_html_tree/*`), T4 (`utils/provider_idempotency.py`,
`authored_batch_policy.py`) and T5 (`services/source_content/placeholder/*`) were reviewed
separately; I touched them only where T2/T6 call into them
(`authored_admission.py:51-58`, `:115-118`, `:171-174`). No retired design (Deck IR, source-native,
SlideSpec, Composer) is proposed or assumed anywhere above.
