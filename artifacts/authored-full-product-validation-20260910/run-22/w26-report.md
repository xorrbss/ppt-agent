# W26 — run-22 rescue overload recovery

## Result

The tail author-rescue now reuses the batch author's `AuthoredWaveSchedule` and `is_authored_provider_overload` policy. A simplified rescue that receives HTTP 429/500/503, a call timeout, another batch-policy overload signal, or a provider outcome with `recovery_allowed=True` waits out the shared exponential cooldown and replays the same rescue ordinal once. The physical overload replay remains ordinal 3, so `provider_attempt_count` stays 3; deterministic content rejection and other non-overload errors keep the prior fail-closed or flagged-placeholder behavior.

Implementation commit: `127d3d4a8935985f764c57df12bb836bc7069858`.

## Run-22 root cause

The retained worker log proves the following sequence:

1. Lines 567-568 deferred slides 7 and 22 to the tail rescue after `BrowserSourceReadbackError:source_content_browser_readback_failed` and `ValueError:authored_header_contract_rejected` respectively.
2. Lines 569-570 started one simplified rescue attempt for each slide. Lines 571-572 reserved two provider calls, and lines 573-574 recorded two HTTP 429 Too Many Requests responses.
3. No cooldown or second rescue call appears before line 577 records the terminal author failure. The durable/API record in `06-final-status.json` names slide index 21 (slide 22), `ProviderOutcomeError`, `public_provider_bridge_exception`, `provider_attempt_count: 3`, and 33/35 completed slides.
4. The task status window was 654.312 seconds (`08:45:48.111587Z` to `08:56:42.423764Z`); the workspace observation logged 655,496 ms. The retained httpx/call-reservation lines have no timestamps or slide identifiers, so individual call latency and one-to-one slide attribution cannot be recovered.

The provider bridge converts the 429 to `ProviderOutcomeError("public_provider_bridge_exception", recovery_allowed=True)`. Before this change, `rescue_author_stage` made one call and immediately re-raised every source-reflow exception; unlike the batch path, it neither advanced the overload policy nor waited on its cooldown nor made the already-bounded recovery call.

## Reused policy and implementation

`services.authored_batch_policy.is_authored_provider_overload` recognizes `TimeoutError`, HTTP 429/500/503, `provider_circuit_open`, `public_provider_bridge_exception`, `public_provider_call_timeout`, and bounded timeout/rate-limit text. `advance_authored_batch_policy` opens the existing `min(30 * 2^(n-1), 300)` second cooldown and reduces the concurrency cap; the batch lane already gives failed slides one recovery pass.

W26 moves the existing schedule to deck scope so the rescue sees any cooldown already opened by batch authoring. The rescue advances that same state, waits through a dynamically extended shared cooldown, and retries once with the identical `provider_attempt_offset=2` / `provider_attempt_limit=3` plumbing. Re-reserving ordinal 3 is idempotent in the checkpoint layer and the attempt observer still reports 3, so overload recovery does not become a fourth content attempt. The structured observability allowlist now retains `public_provider_bridge_exception` and `public_provider_call_timeout` instead of collapsing either to `authored_unexpected_failure`; the API already retained these codes through the author-stage envelope.

## Verification

- Focused unit command: `78 passed, 1 deselected` across `test_authored_author_stage_rescue.py`, `test_authored_author_stage_deferral.py`, and `test_authored_batch_policy.py`. Coverage includes overload-then-success after a 30-second simulated shared cooldown, bridge and timeout overload exhaustion after exactly one replay, unchanged non-overload behavior, and ordinal/accounting remaining at 3.
- The one deselected test, `test_the_author_flag_reaches_the_published_quality_gate`, currently fails because the concurrent flagged-slide vocabulary work adds `classification: repair_not_admitted` to the receipt entry. W26 did not touch that policy or its vocabulary; all other 78 selected tests passed.
- Ruff passed on all five touched files using the pipeline's established `F403,F405` ignores for its dependency-facade star import.
- File sizes after the implementation commit: `authored_presentation_pipeline.py` 1504 lines, `authored_presentation_generation.py` 3468 lines, `authored_author_stage_rescue.py` 468 lines, `authored_wave_scheduler.py` 208 lines, and `authored_generation_observability.py` 260 lines. W26 adds no file-size-ratchet offender.

## Evidence boundary

This is offline implementation and unit evidence. W26 did not restart or touch the local stack and did not submit a generation/export operation because series-3 runs own that runtime. Live confirmation requires a coordinator-scheduled generation-worker restart followed by a fresh identical 35-slide run; there is no claim here of a published PPTX, 35/35 completion, 289/289 readback, or residual-zero quality.
