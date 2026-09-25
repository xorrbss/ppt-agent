# Re-review: `4c0eb5a15` — "admit at the writer, assert the deck, agree with the prompt"

Date: 2026-09-11. Tree: `C:\project\PPT-agent\ppt-agent`, branch `serve/main-20260902`, HEAD `4c0eb5a15`.
Scope: verification of the three fixes claimed for B1/B2/B3 of
`artifacts/impl-review-20260911/review-gate-and-loop.md`, plus a re-score of T2/T6.

Scores (was 7 / 6 / 6): **PPT quality 8/10 · Speed 6/10 · Architecture 7/10.**

Read-only. No source, test, config, stack or database was modified; **no test was executed**, no
provider call, no browser render, no secret read. The commit's "163 passed" is taken as reported.
The only file written is this one.

Citation convention: paths relative to `servers/fastapi/` unless they start with `artifacts/`.
**Verified** = I read the code. **Inference** = an expected consequence I did not measure.

---

## A. Verdict on each claimed fix

### B2 — deck cardinality: **fixed, correctly, in one place.** Verified.

- The predicate was extracted, not duplicated: `services/source_content/browser_readback_inspection.py:28-48`
  holds the only length comparison and returns `(units, slides, metric_slides)`;
  `inspect_browser_source_readback` (`:51-61`) now calls it instead of inlining it, so the slide
  inspector and the deck assertion are literally the same code. `browser_readback.py:795-805` is a
  thin re-export with a lazy import (cycle avoidance), no second implementation.
- It runs **before the mode branch**: `services/authored_admission_publication.py:148`
  (`_assert_deck_cardinality(**kwargs)`) precedes `current_session()` at `:149`, so legacy
  (session `None`), shadow and enforce all execute it on the same inputs. Verified by reading, not
  by the test.
- Its own guard matches the legacy guard exactly (`:127` vs `:36`:
  `source_reflow and composition_profile.text_mode == "verbatim"`), and the failure is converted to
  the same `AuthoredStageError(stage="render", retryable=not terminal_fallback_enabled)` the legacy
  branch builds (`:136-144` vs `:95-103`).
- No false-positive risk from the added metrics-length term: `final_browser_metrics` is built as
  `resolve_final_browser_metrics(htmls, ...)`, which returns `[None]*len(htmls)` filled per index
  (`services/authored_final_metrics.py:67-91`), so `len(metrics) == len(htmls)` by construction.
- The four counters the enforce `combined` receipt dropped are now aggregated
  (`authored_admission_publication.py:240-251`).

Two notes, neither blocking:

- **N3 (low, behaviour change at the shipped default).** For the cardinality failure the legacy path
  raised `source_content_verbatim_readback_failed` (`services/source_content/readback.py:261-266`);
  the new assertion raises `source_content_browser_readback_failed` first. No production consumer
  keys on the old code (only `tests/unit/test_source_content_readback.py:68,79`, which call the
  function directly), so this is an operator/receipt-facing string change, not a behaviour change —
  but the earlier review's "the default configuration is inert" is now very slightly less true.
- **N4 (cosmetic).** In enforce, `missing_block_ids` / `integrity_failures` / `telemetry_failures`
  can only ever be non-empty for a `flag_only` slide: any of them makes `rendered_verdict` return
  `rejected` (`services/authored_admission.py:134-149`), and a rejected admission raises at
  `authored_admission_publication.py:201-209` before `combined` is built. The aggregation is honest
  but mostly unreachable.

### B3 — repair brief vs. authoring contract: **fixed, exactly as claimed.** Verified.

- `services/authored_repair_loop_evidence.py:83-99` derives the boundary from
  `placeholder_prompt_for_slide(index)` — the *same function*, with the same argument, that the
  authoring prompt uses at `utils/llm_calls/author_slide_prompt.py:460`. Not a re-read of the knob:
  it inherits all three of that resolver's conditions (knob on **and** a session context **and**
  `source_reflow` **and** `reference_mode == "source_content_restyle"`,
  `services/source_content/placeholder/prompt.py:9-21`). So on a verbatim non-restyle deck with the
  knob on, prompt and brief both fall back to the verbatim rule — consistent, which a plain
  `placeholder_authoring_enabled()` branch (the fix I proposed) would have got wrong.
- `index` is the slide index, not a cycle index: `services/authored_repair_loop_model.py:96-109`
  threads `propose(index, ...)` straight through.
- The verbatim rule it substitutes is coherent with what the model is actually handed
  (`contents[index]`, the source text): "reproduce every source text block exactly as it was given
  to you" (`authored_repair_loop_evidence.py:76-81`).
- Single call site; the old unconditional paragraph is gone (grep: one production caller,
  `authored_repair_loop_model.py:109`).

### B1 — session-less writers: **the reported failure is gone; the underlying exposure is not.** Verified.

What is genuinely fixed:

- One entry point, both writers: `admit_checkpoint_write` is called at
  `services/authored_checkpoint_state.py:197` and `services/authored_checkpoint_stage_batch.py:164`,
  and nothing else calls the old `validate_checkpoint_write` (grep: the name no longer exists outside
  `_validate_checkpoint_write`, which is private to `authored_admission_checkpoint.py`).
- No new double-admission. A producer that already holds a receipt skips `prepare_checkpoint_write`
  (`authored_admission_checkpoint.py:111`), so the generation path
  (`authored_presentation_artifacts.py:266`, which calls `prepare` itself) still admits once; the
  batched delegation (`authored_checkpoint_state.py:215`) re-*validates* the same receipt, which is
  two `require_admission_receipt` calls per batched render write and nothing more.
- Rejected HTML still cannot reach the database: `admit_checkpoint_write` is the first statement of
  both writers, and a rejected author-stage receipt is `publishable`-classed, so
  `require_admission_receipt` raises `RECEIPT_REJECTED` (`authored_admission_receipt.py:104-107`)
  before any session or artifact IO. Verified.
- The actual live call sites are narrower than the commit message implies and are *not* broken:
  `services/authored_vnext/generation/legacy_bridge.py:330` (author) and `:345` (illustrate) pass
  HTML; `:361` and `:376` pass `html=None` and short-circuit at
  `authored_admission_checkpoint.py:109`. So the render-stage `CAPTURE_MISSING` trap
  (`authored_admission_receipt.py:120`) is not reachable from the bridge. Workspace page-remake
  reaches the same two calls through `legacy_bridge` (imported at
  `services/workspace_page_remake_operation_handler.py:16`).

What is not fixed, and is the reason I am not calling B1 closed:

- **N1 (HIGH — blocks the enforce flip).** The session-less path admits foreign HTML with
  `validate_authored_slide_html` → `admit_authored_html`, the **strict authored profile with no
  fallback** (`authored_admission_checkpoint.py:47` → `authored_admission.py:176-181`). The renderer
  that already rendered those same bytes does **not** use that profile: `utils/slide_capture.py:195-206`
  (`_admit_render_source`) catches `RendererPolicyError` from `admit_authored_html` and falls back to
  `admit_static_output_html`. That fallback exists precisely because renderer-produced HTML is not
  authored-policy-clean (it re-admits arbitrary `data-*` attributes and CSS `url(data:)`,
  `utils/renderer_static_policy.py:188-228`). Consequence: a vNext / page-remake deck can render fine
  and then be refused at its first author checkpoint.
  **Failure scenario.** Operator flips `AUTHORED_ADMISSION_GATE_MODE=enforce` for the verbatim
  series. A workspace page-remake on a PPTX-imported deck builds its slide from
  `services/authored_vnext/master/preview.py:783-960`; a retained native scene embeds base64 JPEGs
  (`:336-349`). `admit_authored_html` enforces `MAX_HTML_BYTES = 2 MiB` and
  `MAX_TOTAL_ASSET_BYTES = 2 MiB` per document (`utils/renderer_isolation.py:49,57,371,283-287`).
  The deck fails with `receipt_rejected` at `authored_checkpoint_state.py:197` — same subsystem,
  same blast radius as B1, different code. Pre-fix it failed with `receipt_missing`; post-fix it
  fails only if the bytes actually violate the authored policy, which I could not settle statically.
  What I *did* settle statically for the ordinary (non-native-scene) preview: every tag and
  attribute it emits is allowlisted (`utils/renderer_markup_policy.py:20-29,37-58,104-107`), its
  `overflow:hidden` is not a hiding declaration (only `auto|scroll`,
  `utils/llm_calls/author_slide_hidden_overflow.py:106-130`), and its `<meta charset="utf-8">` is
  already canonical (`utils/llm_calls/author_slide.py:584-596`), so that case is probably a fixed
  point. The native-scene / image-heavy case is the unproven one. **Nothing tests any of it** — no
  test pushes a `build_preview` output through the gate (grep over `tests/`: `legacy_bridge` appears
  in three files, none of them admission tests).
- **N2 (MEDIUM — latent fail-open, the inverse risk).** `_admission_without_session`
  (`authored_admission_checkpoint.py:20-56`) mints a receipt on a **fabricated identity**:
  `presentation_id=""`, `operation_id=""`, `source_unit_id=""`, `manifest`/`projection` `null`, and
  **`source_reflow=False`** — i.e. the ordinary-authored column, which skips the text step, the
  placeholder check and the rendered-integrity readback (`authored_admission.py:184,208`). The
  validation that follows is a tautology: `_validate_checkpoint_write` builds `expected = None`
  when there is no session (`:150-151`), so `require_admission_receipt` only re-checks the
  `attempt_token` / `fenced_revision` / `slide_index` the receipt was just minted *from*. The
  resulting receipt is `persistence_class="publishable"`, and the publication reader accepts it on
  digest + attempt token alone — it never checks `source_reflow`, `manifestDigest` or
  `presentationId` (`services/authored_checkpoint_html.py:44-53`).
  **Failure scenario.** Any verbatim-deck HTML checkpoint write that happens without a live session
  is now *silently downgraded to the ordinary-authored column and marked publishable*, where before
  the fix it failed closed with `RECEIPT_MISSING`. I could not reach it today — `@admission_scope`
  wraps `_generate_authored_presentation` (`services/authored_presentation_generation.py:1095`),
  `configure_admission` runs at `:1510` before any HTML checkpoint, every in-generation write goes
  through `_complete_stage_after_external_success` (10 sites, all inside the scope), and
  `asyncio.create_task` copies the context (`authored_presentation_artifacts.py:266`). But nothing
  in the code *asserts* that invariant, and the fix replaced the assertion that used to enforce it.
  Cheap remedy: stamp the session-less receipt distinguishably (e.g. a `policy_revision` suffix or a
  non-publishable persistence class) and make `checkpoint_stage_html(publishable=True)` refuse it, or
  refuse `_admission_without_session` when the job is an authored generation job.
- **N5 (LOW).** Under enforce, the writer now substitutes its own canonicalised bytes into the
  checkpoint (`authored_checkpoint_state.py:197` reassigns `html`) for callers that pass no receipt.
  The bridge keeps using its original `html` for publication and pins its own
  `html_digest = canonical_digest({"html": html})` (`preview.py:960`), so checkpoint HTML and
  published HTML can diverge under enforce. Inference: byte-identical in practice for the ordinary
  preview (see N1), unverified for the native-scene case.
- **N6 (LOW, test coverage).** The rewritten regression dropped the `batched` parametrisation
  (`tests/unit/test_authored_admission.py:271-295`), so `complete_render_stage_batched` under
  enforce now has **no** test that unadmittable HTML is refused before IO, and there is no test that
  a session-less receipt carries `source_reflow=False` (i.e. that a verbatim deck cannot be admitted
  there). The batched writer's own admission call is present in code
  (`authored_checkpoint_stage_batch.py:164`); it is simply untested.

**Summary for B1:** the *reported* defect ("the first deck after the knob flip fails with an
internal receipt code") is genuinely gone for the paths that exist today. The fix chose to route
foreign producers through the authored gate rather than scope the knob, which trades a certain
failure for two uncertain ones: a stricter policy applied to HTML that was never written against it
(N1), and a fabricated-identity receipt that the publication reader trusts (N2).

---

## B. Re-score

### B1. PPT quality — 8/10 (was 7)

The two quality deductions of the first review are addressed. The deck-level cardinality assertion
is back and now provably identical in all three modes (it is the single implementation, called above
the branch), which closes the "42 of 43 source units publishes silently" hole. The repair brief no
longer contradicts the authoring contract in the default knob combination, and it is coupled to the
prompt by construction rather than by a parallel knob read — so the loop-on/placeholder-off series
will no longer spend every model repair call telling the model to place tokens that do not exist.

Not 9: the remaining quality risks are untouched — the loop's model step is still a full re-author
rather than a repair (`services/authored_repair_loop_model.py:318-320`, B6 context), the captured
fixed-point rule is still fatal at the very end of a deck (`authored_admission.py:200-204` →
`authored_admission_publication.py:201-209`, old B12), and the digest-keyed expanded marker is still
fragile (old B5).

### B2. Speed — 6/10 (unchanged)

Nothing in this commit moves speed. The deck assertion is O(3) length comparisons. The session-less
admission adds two `validate_authored_slide_html` passes per foreign checkpoint write under enforce
(negligible). Everything the first review measured still holds: capture-once is inert at the shipped
default (`services/authored_admission_capture.py:15`), the memo covers 9 of 25 capture sites, enforce
still appends a serial deck-wide re-persist (`authored_admission_publication.py:214-227`, old B7),
and orphan-evidence adoption is still off under enforce (`authored_checkpoint_state.py:113`).

### B3. Architecture — 7/10 (was 6)

The main deduction is repaired. There is now exactly one entry point through which a checkpoint write
obtains-and-validates its receipt (`admit_checkpoint_write`), both writers call it as their first
statement, the named bypass (the vNext bridge) is gone rather than exempted, and the deck predicate
has one implementation shared by the slide inspector and the publication assertion. `prepare` at the
producer and `admit` at the writer no longer double-admit.

Not 8: the boundary is now *one place* but it is also *self-referential* — the writer both mints and
validates, and for the session-less case validation compares the receipt against the arguments that
produced it, so it is a gate, not a cross-check. Worse, the receipt it mints asserts an identity it
does not have (N2): empty presentation/operation/unit ids and `source_reflow=False` are
indistinguishable, in the persisted receipt, from a genuine ordinary-authored admission. And the
knob's blast radius is unchanged in scope (`enforce_receipts()` still falls back to the global knob
when no session exists, `authored_admission_checkpoint.py:15-17`) while its *effect* on those
subsystems is now an untested policy application (N1).

---

## C. Remaining blockers and the standing B4–B12

**Blocker for the enforce flip (not for shadow):**

- **N1** — `services/authored_admission_checkpoint.py:47`; `utils/slide_capture.py:195-206`;
  `utils/renderer_isolation.py:49,57`. Foreign producer HTML is gated by the strict authored profile
  while the renderer that accepted the same bytes uses the static-output fallback. Settle it offline
  with one test that pushes a real `build_preview` output (including a retained-native-scene slide)
  through `_admission_without_session`, **or** scope `enforce_receipts()` to the authored generation
  path, **or** give the session-less admission the same profile selection the renderer uses.

**Should be fixed before, or watched during, the first enforce run:**

- **N2** — `authored_admission_checkpoint.py:20-56` + `services/authored_checkpoint_html.py:44-53`.
  Mark the session-less receipt so it can never read back as publishable.
- **N6** — restore the batched-writer refusal test.

**Of the earlier B4–B12, the following still stand, all unchanged by this commit** (verified by
reading the current files, not by re-deriving the analysis):

| # | Status | Evidence |
|---|---|---|
| B4 nothing enforces the rollout order | stands | `services/authored_repair_loop.py:53-56` unchanged; knobs still independent |
| B5 digest-keyed expanded marker | stands | `services/authored_admission_producers.py:34-53` unchanged |
| B6 loop baseline/advance/bind unguarded | stands | `services/authored_repair_loop.py:184,205,228` unchanged |
| B7 serial deck-wide re-persist under enforce | stands | `authored_admission_publication.py:214-227` |
| B8 `AdmissionCaptureStore` is dead | stands | `authored_admission_receipt.py:136-152`; only importer is `tests/unit/test_authored_admission.py:19,303` |
| B9 evidence-only VQA `succeeded` over-reports | stands | `authored_repair_loop_evidence.py:183-194` |
| B10 uncoded internal errors; receipt under-pins identity | stands, and N2 makes the second half worse | `authored_admission.py:233`; `authored_admission_checkpoint.py:150-160` still copies five fields from the receipt being validated |
| B11 enforce reorders source binding vs provider rewrites | stands | `authored_admission_producers.py:37-42,76` unchanged |
| B12 captured fixed-point can lose a deck at final readback | stands | `authored_admission.py:200-204` unchanged |

B2's secondary complaint (the dropped counters) is addressed; B1's and B3's primary complaints are
addressed as described above.

---

## D. Verdict

**Run the shadow series now. Fix N1 (and preferably N2) before the enforce flip.**

- **Shadow: go.** The only behaviour this commit changes at the shipped default is the deck
  cardinality assertion moving ahead of `_legacy_final_readback`, which fires on a condition the
  legacy path already failed on, with a different code (N3). `admit_checkpoint_write` returns
  immediately when `enforce_receipts()` is false (`authored_admission_checkpoint.py:109`), so the
  writer path is untouched in shadow. The B3 fix is live in shadow and improves the loop-on
  configuration regardless of gate mode.
- **Enforce: fix first.** The one thing that changed about the enforce flip is *which* way the
  session-less subsystems fail, and that is still unproven for exactly the decks workspace
  page-remake serves. N1 is cheap to settle (one unit test, or a one-line knob scoping) and is the
  difference between "the flip is safe for the whole product" and "the flip is safe for the deck I
  was looking at".
- The five live-verification items from the first review are unchanged and still required
  (shadow parity per producer, B12's captured fixed-point count, real captures per slide, enforce on
  a resumed job, T6 experiment (c) with the gate enforcing). Add one: **run one page-remake or vNext
  master-owned deck in the shadow series and confirm its checkpoint receipts exist and are
  `admitted`** — under shadow the writer does not gate, so this must be read from a deliberate
  enforce-mode smoke run on a single foreign deck before the series flip, not inferred from the
  authored decks.
