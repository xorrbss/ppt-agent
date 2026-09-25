# W23 — run-17 final-readback integrity failure

## Result

`source-slide:10:text:17` failed the rendered-text span predicate. The source
text is `□ 인스타 프로필  □ 블로그 방향  □ 상품  □ 가격  □ 타깃`, while
Chromium observed only the first four items. The bound element retained the one
correct deterministic QID (`source-block-3e464ca90148cc93`) and a non-empty
semantic role (`decoration`), so missing/duplicate/wrong-QID, QID-count, and
missing-role predicates are all false.

The mutation was produced by an accepted VQA repair candidate. The author
checkpoint still contains the fifth child as
`<div ... data-semantic-role="body">□ 타깃</div>`. The persisted render checkpoint
has the same child QID rewritten as an empty decoration element. Their HTML
digests are respectively
`sha256:a800fdf8afc6ce1485e56cc2cde04c0e61584b6777613b34d380eba47f34979a`
and
`sha256:784fbe76cb49509381baa5943b6c4b270e4bcfc0720d2773d892eeaf199fa37e`.

This is not original authoring, gate repair, or the terminal flag-only path:

- Original authoring preserved all five checklist items.
- Run-17's gate-repair candidate readbacks at worker-log lines 187–194 name
  other slides only; slide 10 was not a gate-repair candidate.
- The terminal source-reflow fallback logs "flagged without remake" and does
  not mutate slide HTML.
- Post-VQA admission updates the render checkpoint with the selected candidate,
  which matches the persisted damaged bytes.

## Why the earlier checks missed it

Gate repair supplies `validate_source_repair_candidate`, which runs both
`_require_source_verbatim_bindings` and browser source readback before a
candidate is admitted. The later VQA path did not call that validator. It
canonicalized and rendered the VQA output, then passed it directly to
`_admit_vqa_capacity_candidate`; that admission checks the capacity/DOM envelope
but not immutable source bindings. Because the parent source-bound element was
marked `decoration`, the ordinary DOM/capacity checks did not treat the removed
fifth child as a content-integrity regression. Final deck readback was therefore
the first stage to compare the post-VQA bytes against the immutable source.

## Fix

The post-VQA path now runs its already-scoped source repair validator on the
exact candidate HTML and metrics before capacity admission. A
`BrowserSourceReadbackError` rejects only that candidate and leaves the frozen
pre-VQA HTML/PNG selected; it does not relax final readback, make integrity
retryable, or convert integrity into a flag-only outcome. The helper is kept in
`services/authored_vqa_source_integrity.py`, and
`authored_presentation_generation.py` remains at its 3,473-line cap.

The regression test reproduces run-17's nested checklist shape: the parent
retains the correct source QID exactly once and a semantic role, while the fifth
child retains its QID but becomes an empty decoration. The candidate is rejected;
the intact five-item shape is admitted.

## Evidence and verification

Read-only acquisition used the sanctioned
`servers/fastapi/scripts/run_authored_checkpoint_repair_probe.ps1` wrapper with
`--scan-only`, job `ce953f11-df1b-4216-bfe4-a0744ce8f8ec`, and slide 10. The
probe used PostgreSQL `SET TRANSACTION READ ONLY`; checkpoint digest
`a843a3335cc8e7df7ef6465e5fda3d1a697ffe2578c380d5149597dda2f53b59`
was identical before and after, and `originalJobMutated` was false. Offline
`inspect_browser_source_readback` reproduced one integrity failure and one
visibility failure, both on `source-slide:10:text:17`, with no missing blocks,
order violations, telemetry failures, or diagnostic error. Structured evidence
is in `08-integrity-readback.json`.

Verification performed from `servers/fastapi`:

- `uv run pytest tests/unit/test_authored_vqa_source_integrity.py` — 1 passed.
- In the combined run with
  `tests/unit/test_source_content_browser_readback.py`, the existing browser
  readback file passed 33/33; the new fixture initially failed until its nested
  text nodes reproduced the browser's whitespace boundaries, then the focused
  regression passed.
- `uv run ruff check services/authored_vqa_source_integrity.py tests/unit/test_authored_vqa_source_integrity.py`
  — passed.
- Ruff over every touched Python file reports the facade's pre-existing 37
  `F401` unused imports caused by its extracted-runtime binding pattern; after
  correcting the helper import it reports no `F821` or new-file violation.

The known six pre-existing failures in `tests/unit/test_authored_pipeline.py`
were not used as W23 evidence and that broad file was not run. No live service
was restarted or mutated. End-to-end verification needs the generation worker
to load this code, so the coordinator must schedule that after the active
run-18/W22 work permits a stack-owned restart.
