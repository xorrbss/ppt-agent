# Chromium capture sites on the authored generation path

Date: 2026-09-12. Tree `C:\project\PPT-agent\ppt-agent`, branch `serve/main-20260902`, base `adeb6b8d1`.
Paths are relative to `servers/fastapi/`.

Scope: every call that can start a Chromium render (or ask for browser telemetry that may start one)
of *slide HTML* while an authored deck is being generated. The inventory is the one in
`artifacts/impl-review-20260911/review-gate-and-loop.md` A2, re-verified against this tree and
extended with the two entry-point wrappers the review counted as one site each.

The owner is `services/authored_admission_capture.py`:

- `capture_once(html, index=None, capture=..., **render_kwargs)` -- key `(digest(html), profile)`,
  where `profile` is a digest of every render kwarg except `timeout`.
- `capture_png_once(...)` -- a PNG-only renderer adapted onto the same key.
- `captured_metrics_or_existing(...)` -- telemetry for exactly these bytes from the owner's ledger,
  falling back to `ensure_browser_metrics`.

`index` is needed only to *admit* a capture (enforce mode) and to attribute it per slide in the
receipt; coalescing works without it, because the key is the document, not the slide.

## Before / after

| | sites | share |
| --- | ---: | ---: |
| through the owner, before | 9 | 36% |
| through the owner, after | 25 | 100% |

"Before" is the review's count at `5df4dd3cf`, and it was *nominal*: the owner short-circuited to the
raw renderer whenever the gate mode was `shadow`, which is the shipped default, so in the shipped
configuration **0 of 25** sites actually coalesced. After this change the owner runs in every mode.

## The table

`owner` = routed through the capture owner. `index` = the capture is attributed to a deck slide.

| # | file:line | stage | profile | owner before | owner after | index |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `services/authored_presentation_pipeline.py:1148` | render, no local fit | default (PNG only) | yes (enforce only) | yes | yes |
| 2 | `services/authored_presentation_pipeline.py:1165` | render, local fit first pass | `fit=True` + policy + deck scope | yes (enforce only) | yes | yes |
| 3 | `services/authored_presentation_pipeline.py:1295` | source-image validation telemetry | metrics | yes (enforce only) | yes | yes |
| 4 | `services/authored_presentation_generation.py:657` | post-VQA local fit, first pass | `fit=True` + policy | yes (enforce only) | yes | yes |
| 5 | `services/authored_presentation_generation.py:681` | post-VQA local fit, verification | `fit=False, fresh=True` + baseline | yes (enforce only) | yes | yes |
| 6 | `services/authored_presentation_generation.py:1764` | gate-repair candidate capture | default | yes (enforce only) | yes | yes |
| 7 | `services/authored_presentation_generation.py:1987` | flagged-slide metrics refresh | default | yes (enforce only) | yes | yes |
| 8 | `services/authored_presentation_generation.py:2575` | VQA capacity candidate telemetry | metrics | yes (enforce only) | yes | yes |
| 9 | `utils/llm_calls/author_vision_qa.py:133` | VQA correction render | default | yes (enforce only) | yes | yes |
| 10 | `services/authored_local_fit_admission.py:98` | local-fit **verification** render | `fit=False, fresh=True` + baseline | no | yes | yes |
| 11 | `services/authored_local_fit_admission.py:171` | local-fit original restoration | `fit=False, fresh=True` + policy | no | yes | yes |
| 12 | `services/authored_figure_best_of.py:43` | best-of default render adapter | default | no | yes | via 13 |
| 13 | `services/authored_quality_loop_stage.py:74` | best-of variants + critique correction | default | no | yes | yes |
| 14 | `services/authored_style_anchor_stage.py:253` | style-anchor probe render | default (PNG only) | no | yes | no (anchor has no deck index) |
| 15 | `utils/llm_calls/author_structural_repair_loop.py:89` | structural loop baseline telemetry | metrics | no | yes | yes |
| 16 | `utils/llm_calls/author_structural_repair_loop.py:270` | structural candidate capture | default | no | yes | yes |
| 17 | `utils/llm_calls/author_vision_qa.py:614` | VQA retained deck telemetry | metrics | no | yes | yes |
| 18 | `utils/llm_calls/author_vision_qa.py:635` | VQA per-candidate telemetry | metrics | no | yes | yes |
| 19 | `services/authored_generation_repair.py:492` | bounded repair evidence binding | default | no | yes | when the caller knows it (2 of 5 callers) |
| 20 | `services/authored_terminal_slide_fallback.py:235` | terminal remake admission capture | default | no | yes | yes |
| 21 | `services/authored_generation_quality_gate.py:100` | final quality gate deck telemetry | metrics | no | yes | yes |
| 22 | `services/authored_generation_scene_persistence.py:107` | Scene envelope deck telemetry | metrics | no | yes | yes |
| 23 | `services/authored_final_metrics.py:105` | final metrics readback for stale slides | metrics | no | yes | yes |
| 24 | `services/authored_figure_best_of.py:90` | best-of baseline variant | via 12/13 | no | yes | via 13 |
| 25 | `services/authored_quality_loop.py:107` | critique correction render | via 13 | no | yes | yes |

Rows 12, 24 and 25 call an injected `render` callable rather than the renderer directly; routing the
injection point (13) routes all three. Row 14 renders a style-anchor candidate that has no deck slide
index of its own, so it coalesces without being attributed.

### Legitimate second captures, kept

Row 10 is the design's one permitted extra render per slide: the fitter changed the HTML, so the
verification render carries a *different digest* **and** a fit-disabled profile. It is a distinct key
and the owner will not serve the first pass for it. The same is true of rows 5 and 11.

### Sites deliberately not routed

These call the same renderer but are not on the authored generation path, so they hold no session and
`capture_once` would pass straight through anyway:

| file:line | why |
| --- | --- |
| `services/authored_evidence_ledger.py:660` | no caller in the tree |
| `services/authored_vnext/generation/legacy_bridge.py:459,1116,1381`, `services/authored_vnext/deck_ir/product_adapter.py:684`, `services/authored_vnext/source_native_generation.py:3551` | authored-vnext lane |
| `services/slidespec_authored_executor.py:292,1678`, `services/slidespec_authored_importer.py:669` | slidespec lane |
| `services/composer/measure.py:154` | composer |
| `services/workspace_quality_operation_handler.py:397`, `services/authored_quality_review_support.py:190,192` | post-generation quality review |
| `api/v1/ppt/endpoints/design_packs.py:236`, `services/authored_exact_source_snapshot.py:135` | endpoints outside generation |
| `utils/llm_calls/author_vision_qa.py:132` | the monkeypatched-renderer escape hatch, not a Chromium capture |

## Receipt

`quality_gate_receipt["captureOwnership"]` now carries, for the whole attempt and per slide:

```
{"mode", "captureCount", "captureReuseCount", "retainedCaptures",
 "slides": [{"index", "captureCount", "captureReuseCount"}, ...],
 "unattributed": {"captureCount", "captureReuseCount"}}
```

`captureCount` is the number of renders the owner actually started; `captureReuseCount` is the number
of calls it served from an existing capture. The receipt's per-admission `captureCount`
(`services/authored_admission_receipt.py`) is unchanged and still counts admissions, not renders --
it was the number the review found under-reporting, and this field is the replacement the live series
should read.

## Bounds

`RETAINED_CAPTURES = 32` caps the number of PNG-bearing captures the owner keeps at once; only
*completed* entries are evicted, so the attempt-exit cancellation of in-flight captures still sees
every task it started. Browser metrics are retained for the whole attempt in a separate PNG-free
ledger, because the deck-wide readbacks (rows 21-23) ask for them long after the pixels are gone.
This bound is new: before this change the map was unbounded, which was tolerable only because the
owner was inert at the default.

## Not measured here

Live capture counts. Everything above is static plus the offline unit suite
(`tests/unit/test_authored_capture_owner.py`, synthetic metrics fixtures, no Chromium). The
before/after render count per slide that design section 4(c) asks for needs a live run; read it from
`captureOwnership` in the quality-gate receipt.
