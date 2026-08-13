# Authored 90-slide baseline: code map and evidence

Baseline Git SHA: `b387a34735482d4f6dfbf2b955fbf19fd0d2315e`. This is a code map for the current authored image-per-slide path; it is not an architecture proposal. Provider calls were not made for this experiment.

| Stage | Implementation and durable boundary | Defaults / retry / timeout | Current evidence and gap |
|---|---|---|---|
| Job and cluster admission | `presentation_generate.py:2182` runs the claimed job; `presentation_generation_job_service.py:30-50,125-141,791-820` owns lease, cluster admission and heartbeat. | Lease 300 s, heartbeat 30 s, dispatcher 1 s; `AUTHORED_CLUSTER_JOB_CONCURRENCY` falls back to `AUTHORED_JOB_CONCURRENCY`, default 1; capacity poll 1 s. | Job attempts, token, stage and error state are durable. Quota wait is a closed structured event; it has no provider-token/cost field. Lease is ownership, not a deck deadline. |
| Author / slide worker | `authored_presentation_service.py:344-643`, called through `:646-662`. Each slide flows author → illustrate → render → per-slide persist checkpoint. | Job cap 1; `AUTHORED_SLIDE_PIPELINE_CONCURRENCY=8`. On a failure, queued work stops but already in-flight calls finish and checkpoint. | End summary logs total and summed author/illustrate/render work but not per-slide distributions or usage. |
| LLM author | `author_deck.py:31-50,127-220,267-281`; provider call is `author_slide.py:640-719`. | Dedicated `AUTHORED_AUTHOR_CONCURRENCY=5`; optional RPM default disabled; 429 cooldown 10–60 s. Normal max 2 attempts; transient max 4 with 1.5/3/6 s waits. | Model/provider name is logged, but provider response input/output tokens and USD are not extracted or persisted. `max_tokens` is a cap, not observed usage. |
| Illustration | `authored_illustrations.py:37-55,134-212` → `ImageGenerationService`. | `AUTHORED_ILLUSTRATION_CONCURRENCY=4`, one slot per slide. Image-service 429 retry is up to 6 calls with up to 30 s wait. | Provider/model usage and cost are absent. Do not run the real image path in a privacy benchmark: `image_generation_service.py:138` prints the image prompt. |
| Render | `slide_capture.py:1501-1619,1622-1660,1681-1730`; invoked at `authored_presentation_service.py:525-546`. | Per-call default 60 s, Chrome virtual-time budget 20 s, `AUTHORED_RENDER_CONCURRENCY=4`, cache 32, Chrome reuse enabled by default. | Runtime/cache/process counters exist; pipeline only emits aggregate wall/work time. Local component calibration in this experiment measured three unique real Chrome renders. |
| In-generation VQA | Optional `request.vision_qa` at `authored_presentation_service.py:788-826`, then `author_vision_qa.py:421-841`. | One cycle; `AUTHORED_VQA_CONCURRENCY` defaults to author cap (5), cache 64. A flagged slide can make high-effort full re-author and re-render calls. | Fix count/timing/cache are logged. VQA token/cost is absent; quality telemetry calls unavailable cost `provider_usage_unavailable`. |
| OCR | Not in initial authored generation. It is quality-review-only: `authored_quality_review_service.py` and `authored_ocr_quality.py:26-31,635-669`. | Local Tesseract cap 2; default 20 s, max 60 s; timeout kills and reaps the child. | Safe payloads include digest/metrics rather than OCR text. A 90-slide initial-generation estimate leaves OCR at zero. |
| Export / publication | `authored_presentation_service.py:190-216,252-289,906-1047`; checkpoints `authored_checkpoint_service.py:656-765`. | PPTX/PDF is local pure Python, validated then atomically replaced. Publication fence 300 s. | Immutable slide PNGs plus DB rows/checkpoints are durable. Export/persist p50/p95 are not currently emitted. |

## Measured versus estimated

Measured local render calibration (no provider): three unique 1280×720 authored HTML renders: 1.359 s, 0.875 s, 0.657 s; mean 0.964 s. The renderer's own calculation projects 90 unique slides at concurrency 4 to 21.7 s. This is a renderer/cache component result only, not a 90-slide end-to-end deck result.

The offline three-pass report (`runs/offline-90x3-verified.json`) is a synthetic estimate with explicit assumptions: author 75 s/slide; render 1.8 s/slide; persist 3.5 s and export 12 s; OCR/VQA/illustration disabled. Its deterministic critical path is 1369.1 s (22.82 min) at author=5/render=4. Its p50/p95 equal the input assumptions, so they are not measured provider percentiles.

## Baseline decision

- **Latency:** 30 minutes passes only for fixture assumptions (430.9 s slack). Actual suitability is **unproven**: author/VQA calls have no repository-level explicit provider timeout and no end-to-end histogram.
- **Cost:** **unavailable**, not $0. Fixture input 216,000 and output 450,000 author tokens are planning assumptions; price is deliberately unset.
- **Retry amplification:** fixture 1.0×; code permits transient author up to 4.0× per slide, plus multimodal text-only fallback and image retry. No production counter establishes observed amplification.
- **Peak concurrency:** estimated author 5, render 4, persist/export 1; OCR/VQA disabled. Illustration has its own cap of 4 when enabled.

## Priority bottlenecks and next experiments

P0:

1. Capture sanitized provider receipts (model/provider, input/output tokens, request count, retry class and stage latency) at shared author/VQA/image boundaries; never capture prompts, HTML, OCR text, IDs, paths or headers.
2. Run a real end-to-end 90-slide staging test with an explicit deck deadline and per-stage p50/p95; the UI 30-minute polling limit is not a server deadline.

P1:

3. Sweep author cap 3/5/8 below provider RPM and compare author p95, 429 rate, cost and retry amplification.
4. Measure the export/persist tail with 90 real rendered PNGs and storage/database manifest.
5. Run a separate suspect-only OCR/VQA quality-review soak; do not merge it into initial generation without recording its routing fraction.
