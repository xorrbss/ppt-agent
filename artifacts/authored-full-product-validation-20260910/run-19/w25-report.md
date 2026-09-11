# W25 — run-19 author-rescue readback routing

## Result

The rescue seam now runs a recovered source-reflow slide through the canonical browser source-readback inspector. A visibility-only result is admitted only behind the existing source-reflow terminal-fallback predicate and is recorded as `verbatim_flag_only`; missing, order, integrity, telemetry, or diagnostic failures remain fatal and are surfaced as the coded `source_content_browser_readback_failed` author-stage outcome. The observability allowlist now retains that code instead of rewriting it to `authored_unexpected_failure`.

No policy/config file was changed, no stack process was restarted, and no generation or export operation was submitted. Product-path confirmation requires a worker reload and a fresh identical 35-slide run, so it was intentionally not attempted while run-20 owned the live stack.

## Run-19 root cause

The sequence is deterministic from the retained artifacts and code:

1. Worker log lines 749–751 deferred slides 3, 13, and 34 after their two-call author budgets. Lines 752–754 started their one strict source-reflow rescue call.
2. Line 765 logged slide 34 as recovered. The next operation-specific line, 766, recorded an author-stage failure with provider attempt count 3.
3. `rescue_author_stage` returned HTML without validating source bindings. `process_slide` then ran `validate_authored_slide_html` and `stamp_source_blocks`; a `BrowserSourceReadbackError` there was wrapped as an author-stage error before the author checkpoint or Chromium render.
4. The run-19 checkpoint confirms slide 34 has no author HTML or render artifact and retains block ID `source-slide:34:text:6`. Therefore the failed predicate was pre-browser source-binding admission, not order, telemetry, or visibility. The old wrapper did not retain `BrowserSourceReadbackError.detail`, so the historical record cannot distinguish the normal missing/no-exact-DOM-match branch from the conflicting/duplicate-binding integrity branches; that evidence limit is recorded explicitly in `08-rescue-readback.json`.
5. `source_content_browser_readback_failed` was absent from `authored_generation_observability._FAILURE_CODES`, which changed only the structured log label to `authored_unexpected_failure` even though the durable/API error code remained `source_content_browser_readback_failed`.

## The three prior ValueErrors

The exact messages for slides 3, 13, and 34 are not recoverable from run-19. The deferral log stored only `type(error).__name__`, and the corresponding checkpoints contain only attempt reservations (slides 3 and 13) or the later rescue failure (slide 34); none contains rejected HTML. Because `BrowserSourceReadbackError` has its own class name despite inheriting `ValueError`, the three log entries prove the prior failures were base `ValueError` candidate rejections, not source-readback exceptions disguised by inheritance. The possible producer set is bounded to the batch candidate validators (header contract, source-image occurrence slots, semantic-QID validation, or batch framing), but choosing one retrospectively would invent evidence.

New runs preserve this information. `author_rejection_code` maps known ValueError families to bounded stable codes in the deferral log, and `DeferredAuthorBudgetExhausted.prior_failure` carries the sanitized original reason into the coded terminal failure or the flagged-slide receipt (`reasons`) when visibility-only admission applies.

## Implementation

- `authored_author_stage_rescue.py`: canonical one-slide readback classification; visibility-only `verbatim_flag_only`; hard predicates become a coded `AuthoredStageError` with prior-failure evidence.
- `authored_author_stage_deferral.py`: stable candidate-rejection codes and bounded prior failure propagation.
- `authored_presentation_pipeline.py`: tracks the rescue result, preserves source-stamping detail, and invokes the readback route before render/quality/persist completion. Its line count remains exactly the 1504-line ratchet baseline; `authored_presentation_generation.py` was not changed and remains 3468 lines, below its 3473-line cap.
- `authored_generation_observability.py`: allowlists `source_content_browser_readback_failed`.
- Unit coverage reproduces rescue success followed by visibility-only flagging and missing/integrity hard failure, and checks the observability code never collapses to `authored_unexpected_failure`.

## Verification

- Ruff: passed for the changed standalone services/tests. The pipeline and dependency aggregator also passed with their established star-import/re-export ignores (`F403,F405` and `F401`).
- Focused unit tests: `30 passed, 1 deselected` across `test_authored_author_stage_rescue.py` and `test_authored_author_stage_deferral.py`. The deselected pre-existing publication test currently fails because its fake pipeline supplies no local-fit receipt while the current environment enables local fit; it is outside this routing change.
- File-size ratchet: this change introduces no offender and keeps the touched pipeline at its recorded 1504 lines. The repository-wide command still reports unrelated dirty-worktree offenders in local-fit, export, workspace publication, and frontend files; none is owned by W25.

## Evidence boundary

This is offline implementation evidence, not a claim of 35/35 product success. No published PPTX, 289/289 whole-deck readback receipt, or residual-zero result was generated by W25.
