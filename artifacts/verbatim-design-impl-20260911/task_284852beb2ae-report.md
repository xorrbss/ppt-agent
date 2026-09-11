# T2 — Central authored admission and receipt enforcement

Task: `task_284852beb2ae`; dispatch `ctx_873796f1b55e`.
Authority: design rev 3, accepted spec decision, review pitfalls, T1 measurements and coordinator clarifications during this dispatch. The implementation commit is reported in the completion message; this report and the parity JSON are committed with it.

## Changes

`admit_slide_html(context, html, metrics=..., png=...)` owns raw diagnostics, the current rewrite/renderer policy, the default binder or T5 text step, post-expansion policy and captured browser readback. Its immutable context pins source/projection digests, identity, attempt/fence, policy, representation, reference mode, source-reflow mode, capture profile and existing flags. The gate imports T3 raw tree diagnostics when tree normalization is selected; it does not introduce another parser or Chromium call. T5 selection and pre-normalization placeholder-context checks are lazy behind its registered knob; TextStepResult normalizations/deviations reach Admission and its bounded receipt.

The gate runs beside legacy checks in default `shadow` mode, returning the legacy result unchanged and recording bounded structured `authored_admission_shadow` events plus quality receipt `admissionShadow`. In `enforce`, adapters execute the common gate; old static/readback blocks survive only as shadow comparators. Single/batch provider output enters immediately after HTML extraction, before pointer/font/extent rewrites; an attempt-local expanded-output marker prevents downstream author/rescue/VQA adapters from expanding inserted source text again. Raw provider responses explicitly bypass that marker. Illustration and geometry edits are expanded; captured state never calls text_step.

HTML checkpoint writers require an AdmissionReceipt in enforce mode before artifact or DB mutation, binding HTML, metrics, PNG, attempt token, slide and fenced revision. Existing checkpoint bundles persist browser metrics; captured receipt requires their presence and stores metricsDigest plus metricsStorage=checkpoint_bundle. The supplied historical fixtures lack those metrics, so historical captured replay cannot be reconstructed. Same expanded-input digest/profile captures coalesce into one in-flight render, and downstream admission/writer receipts retain capture reuse. No browser pass was added.

As clarified by the coordinator, rejected captured candidates may be persisted only as nonpublishable evidence with a valid receipt. Final publishable receipts require admitted or already-flagged, eligible source-reflow visibility-only flag_only verdicts; integrity, missing blocks, order and telemetry remain rejected. W25 rescue cannot mint a new flag in enforce. Publication rechecks captured admission and the persisted publishable receipt. Legacy orphan adoption is disabled under enforce because those HTML artifacts have no receipt.

The new vocabulary is in authored_admission_policy.py. Renderer GLOBAL_ATTRIBUTES is unchanged. SERVER_STAMPED_ATTRIBUTES separately inventories attributes inserted by local fit/repair; the consumer test checks fitter/readback/terminal/flag-classification reads against GLOBAL_ATTRIBUTES plus that inventory. Explicit dead readers are browser_readback.py:293-295 (`data-source-image`, `data-decorative`, `data-decoration`) and slide_capture_fit_observe.py:103 (`data-role`); they are exceptions documented for removal, not added renderer permissions. This follows the coordinator's accepted vocabulary clarification.

## Stored HTML parity

This is **current legacy static checks versus the new static gate on identical stored bytes**, with legacy normalization and placeholder authoring off. It is not an assertion that a historically failed job passed, nor captured/browser parity. Published runs 16/20 use the published render bytes as expanded static inputs. Failed runs contribute surviving author/illustrated checkpoints, including the coordinator's authoring_passed run-18/19 fixtures and run-17's stored failing-slide checkpoint. Unpersisted rejected model candidates are not manufactured from historical job verdicts. Static replay constructs only a deterministic per-unit outline from the exported block order; it does not claim that outline is the original captured projection.

| Run | Stored representation | Existing verdict | Gate verdict | Count |
|---|---|---|---|---:|
| run-13 | author | admitted | admitted | 33 |
| run-13 | illustrated | admitted | admitted | 33 |
| run-14 | author | admitted | admitted | 34 |
| run-14 | illustrated | admitted | admitted | 34 |
| run-16 | render | admitted | admitted | 35 |
| run-17 | author | admitted | admitted | 35 |
| run-17 | illustrated | admitted | admitted | 35 |
| run-18 | author | admitted | admitted | 34 |
| run-18 | illustrated | admitted | admitted | 30 |
| run-19 | author | admitted | admitted | 32 |
| run-19 | illustrated | admitted | admitted | 32 |
| run-20 | render | admitted | admitted | 35 |
| run-23 | author | admitted | admitted | 31 |
| run-23 | illustrated | admitted | admitted | 31 |

All 464 inputs have verdict parity (199 author, 195 illustrated, 70 published render). JSON rows contain run/slide, codes, predicates, block IDs and digests, not source text: `T2-static-parity.json`. Input fixtures remain at `artifacts/verbatim-design-measurements-20260911/fixtures`; T2 did not duplicate source exports or access the DB. `scripts/authored_admission_shadow_report.py` aggregates live worker log rows by producer/existing verdict/gate verdict/count; `scripts/authored_admission_fixture_replay.py` produced this offline table.

## Removed per-path blocks

Old line references below refer to parent HEAD 5590702f7; current references identify the replacement adapter. Legacy bodies intentionally remain in centralized shadow comparators until rollout parity permits retirement.

| Original block | Replacement/current reference |
|---|---|
| authored_presentation_generation.py:1516 repair seal + unit selection + browser verification (41 lines; W21/W23 repair path) | authored_presentation_generation.py:1520 → source_candidate |
| authored_presentation_generation.py:2854 final static/browser/terminal flag readback (68 lines) | authored_presentation_generation.py:2824 → authored_admission_publication.final_readback |
| authored_presentation_pipeline.py:737 author validation + binding | authored_presentation_pipeline.py:740 → author_html |
| authored_presentation_pipeline.py:779 resumed author validation | authored_presentation_pipeline.py:776 → author_html(fresh=False) |
| authored_presentation_pipeline.py:841 illustrated text-binding rejection and :870 source-image stamp | authored_presentation_pipeline.py:837 / :857 → common expanded adapters |
| authored_presentation_author_batch.py:20 validation + :31 binding | authored_presentation_author_batch.py:21 → author_html |
| authored_illustration_stage.py:55 finalized validator | authored_illustration_stage.py:56 → illustrate_html |
| authored_author_stage_rescue.py:105-165 W25 custom readback/hard-failure/flag minting | authored_author_stage_rescue.py:100 → common gate; old body in authored_admission_legacy.py only for shadow |
| authored_generation_quality_gate.py:38-58 deck seal loop | authored_generation_quality_gate.py:38 → common legacy comparator; captured gate owns predicate in enforce |
| author_slide.py:1232 provider-return validator | author_slide.py:1181 raw gate; :1230 expanded gate |
| author_batch.py:288 provider-return validator | author_batch.py:240 raw gate; :287 expanded gate |
| author_vision_qa.py:718 full-reauthor finalized validator | author_vision_qa.py:722 expanded gate; capture adapter at :129 |
| authored_generation_observability.py failure-code allowlist/collapse | Bounded stable codes propagate unchanged |

## Minimal checkpoint plumbing extensions

- authored_checkpoint_state.py: receipt parameter, pre-write validation, metadata retention, enforce orphan guard. T4 provider-ledger functions are not part of this change.
- authored_checkpoint_stage_batch.py: the same checks for its directly callable batched writer and recovery guard.
- authored_checkpoint_service.py: forwards the receipt argument.
- authored_presentation_artifacts.py: explicitly obtains the producer receipt before invoking the writer.
- authored_checkpoint_html.py: optional publishable read requires receipt/HTML digest/attempt match.
- authored_presentation_author_batch.py and authored_illustration_stage.py: common admission adapter calls described above.
- authored_presentation_pipeline_deps.py: no final content change; direct imports keep existing facade export behavior.
- Registry/get_env_programme.py: only AUTHORED_ADMISSION_GATE_MODE registration/accessor/export, default shadow. No T3/T5 settings hunk is included.
- author_slide.py/author_batch.py and author_vision_qa.py: coordinator-approved raw/expanded/capture hooks only, preserving provider reservation and prompt ownership.

## Verification

- Final focused pytest command: both new admission test modules plus the two existing rescue readback regression tests — **80 passed in 6.25s**. Covers all new modules through gate, receipt, session, capture, producer, legacy comparator, publication and reporting paths; three-mode integrity/visibility/telemetry table; immutable context; writer rejection before I/O; evidence versus publishable receipts; concurrent capture reuse and persisted reuse; raw diagnostic ordering; provider expanded marker; VQA capture reuse; bounded shadow vocabulary and unchanged codes. Historical run-13/14/17/19/23 failure classes use explicitly synthetic captured metrics; run-18 malformed closing-tag behavior uses the existing normalizer.
- New admission modules, both tests and both scripts: Ruff clean. Touched legacy files retain pre-existing facade/dynamic-import findings, with **zero introduced findings** in T2-ruff-baseline.json (comparison uses actual code/message counts). This is the coordinator-approved baseline distinction, not a claim that old generation/pipeline modules are lint-clean.
- `npm run test:settings-registry`: passed, 2 tests (the T2 entry/accessor synchronized).
- `npm run test:file-size-ratchet`: fails only the ten documented pre-existing offenders, listed in T2-file-size-ratchet.txt. T2 adds no offender: generation 3378 < 3473, pipeline 1496 <= 1504, all new service modules <= 264 lines; raw-provider and VQA files shrink.
- Offline replay: 464/464 same-input static verdict parity, including both published 35-slide decks. No live provider, browser, stack restart or DB mutation was used.

## Remaining live evidence

The coordinator must run the authorized live shadow series after the provider quota reset (2026-09-15), collect each producer's structured parity including rescue/VQA/structural/terminal branches, inspect persisted metrics and fenced receipts, and verify actual worker capture counts. Then run enforce with T3/T5 switches as planned and verify complete published decks/PPTX artifacts, strict source text/order coverage, terminal visibility-only handling, retry/resume and stale-fence rejection. Stored static parity plus synthetic metrics does not replace that product evidence. No release or live success is claimed here.
