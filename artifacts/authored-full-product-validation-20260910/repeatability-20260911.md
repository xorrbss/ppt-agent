# W19 -- Verbatim publication path repeatability (runs 11-15)

Date: 2026-09-11. Stack: local-stack-lib supervised (postgres/api/next/proxy/controllers),
`AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW=true`. Same fixed input for every run
(35-slide PPTX, sha256 `7746aaeddbf1222fd73c9870ac7297af98446b8e5df22d52c809b4d69b5340ae`,
289 source text blocks, `authored_style=hyundai-culture-center`, `content_policy.text=verbatim`).
Runs executed strictly one at a time. run-11 was executed the day before (2026-09-10) and is
reproduced here from `run-11/06-final-status.json`, `run-11/07-download-evidence.json`, and an
audit generated in this session (`run-11/08-audit.json` did not exist beforehand -- the stack
was down at investigation start, so it was produced retroactively once the stack was back up,
before runs 12-15 began, so numbers below are exactly what `audit-full-run.ps1` reports for that
completed job).

## Summary table

| run | published | operation id | job id | op attempt/retry | job attempt/resume | wall seconds | failure code | slides completed |
|---|---|---|---|---|---|---|---|---|
| 11 | **yes** | 97267adb-db55-436a-afaf-02a31192a7fa | d185a6d0-bc47-4c8e-8fc9-23d5ad1713fa | 1/0 | 1/0 | 736.2 | -- | 35/35 |
| 12 | no | d23e6f20-3e69-47ea-8602-53a0fa581b6f | f56fd372-675a-40ed-81a7-1ad37d4811bf | 1/1 | 1/0 | 805.7 | `source_content_browser_readback_failed` | 35/35 (readback rejected before publish) |
| 13 | no | 19a08008-3cbd-4cab-80dd-e76ec25a1144 | 88c1e3e6-ef8d-40e6-9661-61d884a23a6d | 1/1 | 1/0 | 645.6 | `authored_slide_author_failed` | 33/35 |
| 14 | no | 8b55257f-9b08-4b68-9319-acdf405275c1 | 2661e54f-5898-48ff-8781-a37704e1f1f3 | 1/1 | 1/0 | 597.7 | `source_content_browser_readback_failed` | 34/35 |
| 15 | **yes** | efebf8d1-f189-458b-8966-022a843eec44 | 89cf6fc9-579c-4c92-ae4c-234cc62d3d6c | 1/0 | 1/0 | 763.9 | -- | 35/35 |

**Publish rate (runs 11-15): 2/5 = 40%.**

## sourceReadback gate detail (published runs only -- unpublished runs never reach a stored receipt)

| run | checkedSlides/Blocks | integrityVerified | missingBlockIds | orderViolations | visibilityFailures | flaggedVisibilityFailures | pptx sha256 | slide XML count |
|---|---|---|---|---|---|---|---|---|
| 11 | 35/289 | true | 0 | 0 | 1 | 1 | `d615a1e480ec28f07df5976591b6c007cd90b62a0b0957fc679befbad8eb908f` | 35 |
| 15 | 35/289 | true | 0 | 0 | 6 | 6 | `9ce83b0154136204d5f66bcc5167e846cfc40a30c1a4190481a9eac96055a745` | 35 |

For runs 12-14 the operation never reached `succeeded`, so no `authoredQualityGate`/`sourceReadback`
receipt is stored on the presentation row -- the audit's `sourceReadback.*` checks all report
`null`/failed for those three, which is expected (not a gate defect) given the job never published.

## Flagged slide indices (0-based), published runs only

- **run-11** (7 flagged): `10, 14, 16, 26, 28, 29, 30`. Visibility-only sub-reason: index 16
  (`source-slide:17:text:3`).
- **run-15** (17 flagged): `6, 8, 10, 11, 12, 14, 16, 18, 20, 21, 22, 24, 25, 26, 29, 30, 34`.
  Visibility-only sub-reasons: index 11 (`source-slide:12:text:11`), index 20
  (`source-slide:21:text:6`, `source-slide:21:text:9`), index 26 (`source-slide:27:text:8`,
  `source-slide:27:text:12`), index 34 (`source-slide:35:text:6`).

**Overlap:** 6 of run-11's 7 flagged indices (`10, 14, 16, 26, 29, 30`) recur in run-15; only
index 28 does not. Every index that recurs does so in exactly 2 of the 5 runs -- **no slide
index is flagged in 3 or more runs**, because only 2 of the 5 runs (11 and 15) ever produced a
flagged-slide set at all (runs 12 and 14 failed before/at the terminal-fallback pass with no
flagged set persisted; run-13 failed even earlier, mid-authoring). The flagged-count itself is
not stable run-to-run (7 vs 17 on identical input), so "repeatability" here holds for *whether*
a slide tends to need the fallback, not for a fixed count.

## Failures: code, verbatim cause, and whether it's a previously-seen manifestation

Known codes from runs 7-11 per the task brief: `authored_composition_safety_gate_failed`,
`authored_slide_author_failed` (content:`""`), `authored_vqa_residual_dom_issues`,
`source_content_browser_readback_failed`. **All three run-12/13/14 failures use codes already
in that set -- no new failure code appeared.** However, two of the three surfaced through a
different concrete cause than the code's previously-recorded manifestation:

- **run-12 -- `source_content_browser_readback_failed`.** Verbatim API error: `{"status_code":500,
  "code":"source_content_browser_readback_failed","detail":"Presentation generation failed"}`.
  All 35 slides completed authoring/render before the failure; it happened at the final
  terminal-fallback browser-source-readback pass (services/authored_presentation_generation.py
  ~line 2893-2900: a `hard_failure` or `unflagged_visibility` condition). The terminal fallback
  had already flagged 8 slides without remake (0-based `[2, 10, 11, 13, 14, 21, 23, 26]`; 2 of
  those, 14 and 26, also had explicit "gate repair not admitted" warnings). **Block-level detail
  (which block_ids, integrity vs. visibility) is not recoverable from logs in this build** --
  verified by grep that `services/source_content/browser_readback.py` and
  `browser_readback_inspection.py` contain zero logger calls, and the workspace-operation
  observability line only records a generic `error_code":"other"`. Full detail in
  `run-12/09-readback-failure.json`.
- **run-13 -- `authored_slide_author_failed`.** Verbatim cause (from
  `utils.llm_calls.author_deck` WARNING): `slide 23/35 failed after 1 attempts; refusing a
  placeholder for exact/strict authoring (last error: CSS property is not allowed: content:
  "1")`. This is a **different concrete cause than the previously-seen empty-content
  (`content:""`) variant** -- same code, a CSS-property-rejection manifestation this time.
  33/35 slides completed.
- **run-14 -- `source_content_browser_readback_failed`.** Verbatim API error identical in shape
  to run-12's. But **the failure happened at a different, earlier pipeline stage**: a
  `BrowserSourceReadbackError` raised while validating a repair candidate for slide index 2
  (0-based, logged as "slide 3") during per-slide authoring/repair -- an integrity/text-binding
  check (`_require_source_verbatim_bindings`), not the final visibility/pixel-occlusion pass
  used by the terminal fallback. The slide was logged as "recovered on the simplified attempt"
  immediately before a terminal `authored_unexpected_failure` for the same slide -- i.e. the
  rescue's own recovery did not actually hold. This run never reached the terminal-fallback
  flagging pass at all, so there is no flagged-slide set to compare the failing slide against.
  34/35 slides completed. Full detail in `run-14/09-readback-failure.json`.

## A new failure mode -- not a generation failure, a tooling bug (run-15)

run-15's generation **succeeded and published** (audit verdict `passed`, identical shape to
run-11). But `run.ps1`'s own download step returned HTTP 404: it hardcodes
`GET /app_data/exports/<uuid>.pptx`, while this pipeline (`source_content_restyle` + `authored`)
writes the artifact under `/app_data/workspace-artifacts/users/<owner>/<operation_id>/<uuid>.pptx`
-- a different directory that the same `/app_data` static mount also serves. The file was
recovered directly from the server-reported `result.path`; its sha256
(`9ce83b0154136204d5f66bcc5167e846cfc40a30c1a4190481a9eac96055a745`) matches the DB
`artifactHash` exactly, confirming the publish itself was sound and only the convenience
download step in the harness script is broken. `run-11`'s prior download apparently worked
against the same `exports/` URL for a file in the same `workspace-artifacts/` location, which is
not reproducible now (the file no longer exists in either run's `exports/` path) -- most likely
either a since-expired copy or a race with an async mirror step; this was not investigated
further as it is outside the scope of this task. Separately, run-15 also hit a non-fatal
exception while persisting its generation-attempt timing receipt (`ValueError:
generation_attempt_v2_flagged_slides_invalid` in
`authored_generation_timing_receipt_e4.py:flagged_slide_entries`), which is why
`timingReceiptFilePresent` is `false` in its audit despite the run otherwise passing cleanly.

## Bottom line

- Publish rate over 11-15: **2/5 (40%)**.
- No brand-new failure *code* appeared; two of three failures were a new concrete *cause* under
  an already-seen code (run-13's CSS-content rejection; run-14's earlier-stage readback
  failure).
- The verbatim publication path is not push-button repeatable at this concurrency/profile: the
  same fixed input produced 2 successes and 3 distinct failure shapes across 5 attempts, and even
  the two successes disagreed on how many slides needed the terminal-fallback (7 vs 17).
- One new, unrelated tooling bug was found and worked around (`run.ps1` download path
  assumption); it does not affect generation correctness.

## Series 2 (runs 16-20) — same input, code at 6cc0b78ce..009ac6543 (W21 fixes included), stack restarted 14:55

| run | outcome | attempt/retry | stage of failure | code / cause | flagged (0-based) | visibility | wall |
|---|---|---|---|---|---|---|---|
| 16 | published | 1/0 | - | - | 11: [4,6,8,9,10,14,18,19,25,26,30] | 6 (slides 9,19,26) | 754 s |
| 17 | failed | 1/1 | final deck readback | `source_content_browser_readback_failed`, integrity=[source-slide:10:text:17] (fatal by policy rev2) + 12 visibility blocks | - | - | 790 s |
| 18 | failed | 1/1 | author, slide 31 | `authored_slide_author_failed`, `RendererPolicyError: HTML closing tag is unbalanced: img`, 2 provider attempts | - | - | 615 s |
| 19 | failed | 1/1 | author rescue, slide 34 | `authored_unexpected_failure` <- `BrowserSourceReadbackError` block source-slide:34:text:6 after the simplified rescue attempt (3 provider attempts); slides 3/13 also deferred to rescue | - | - | 669 s |
| 20 | published | 1/0 | - | - | 18: [2,6,9,10,14,15,16,18,19,20,22,26,27,28,30,31,32,33] | 1 (slide 33) | 715 s |

- Publish rate 2/5 (40%), identical to series 1 (runs 11-15). Every failure is a distinct shape, none repeated within the series, and none matches a series-1 shape (run-13 `content:"1"`, run-14 candidate-validation readback, run-12 readback) — the W21 fixes held for the shapes they targeted.
- Follow-ups dispatched (Codex): W23 (run-17 integrity), W24 (run-18 void end tag), W25 (run-19 rescue-path readback routing). None of those fixes were live during this series; the stack ran the same code for all five runs.
- Flagged-slide count on the published runs keeps swinging (7, 17, 11, 18). Per the offline local-fit replay (f7 session, `artifacts/local-fit-source-reflow-replay-20260911/`), only about a third of flagged slides carry a blocking defect in the published bytes; the rest are `gate_repair_not_admitted` flags.
- Series-1 hybrid export follow-through: W20 (throughput, 6cc0b78ce) and W22 (line-break integrity + per-slide fallback, f48c8a6f3) brought the run-11 deck to `editable-hybrid`, 35/35 native slides, `slideFallbacks: []`, 893 s worker time inside the 1,740 s grant (evidence `run-11-hybrid-export/47-evidence.json`, `48-hybrid-export.pptx`).

## Series 3 (runs 21-23) — stack restarted 17:23 with W23/W24/W25 fixes; interrupted by the account usage limit

| run | outcome | stage of failure | code / cause | flagged (0-based) | wall |
|---|---|---|---|---|---|
| 21 | published | - | - | 13: [1,2,14,16,18,22,24,25,26,28,29,31,32], visibility 11 | 775 s |
| 22 | failed | author rescue, slide 22 | `public_provider_bridge_exception`: two rescue calls answered HTTP 429; first `usage_limit_reached` (plan pro, resets 2026-09-15 01:53Z) | - | 655 s |
| 23 | failed | author, slide 2 | `source_content_browser_readback_failed`: binder found no unique DOM element for run 0 of the title block (`꿀벌쌤`); ran under escalating 429 cooldowns | - | 3,464 s |

- run-23 is the text-binder failure class (model split or restyled a block so the search-based binder could not bind it) — the class that `docs/authored-verbatim-placeholder-authoring-design-20260911.md` §2.1 removes by construction.
- The series cannot continue until the provider usage limit resets or the account is reset; W26 (rescue overload cooldown, 127d3d4a8) was not live for runs 22-23.
