# Authored Hybrid Review — bounded live-model validation (2026-08-02)

## Scope and cost gate

- Runtime configuration was validated without printing credential values: provider `codex`, model `gpt-5.6-sol`.
- The benchmark runner counts the actual generation boundary for both structured VQA/patch calls and raw full-slide author calls. Schema/transient retries and multimodal author retries therefore consume the same ceiling.
- The safe CLI default and the executed campaign ceiling are both **4 provider generations**.
- The completed campaign used exactly **4 generations**: 3 structured and 1 raw full-author generation. It ran for 202.718 s wall time.
- Before this safe run, an exploratory command was mistakenly started with a ceiling of 28 and terminated after roughly 11 seconds; its process tree was then explicitly killed. It produced no final counter, so its actual request count is unknown and is not included in the exact count of 4 above. No result from that aborted run is treated as evidence.

The runner serializes no credential value, prompt body, response body, or generated HTML. It suppresses provider exception stacks and emits sanitized error type/code fields only.

## Measured live results

| Path | Actual provider calls | Measured stages | Outcome |
|---|---:|---|---|
| Local patch | 2 structured | render + metrics 416.806 ms; local DOM 3.273 ms; initial VQA 7.761 s; proposal 21.204 s; total 29.386 s | **Rejected safely.** DOM found medium clipping + high overflow; VQA found high overflow + medium empty-space. The proposal emitted `fontsize`, which is not an allowed CSS property, so validation returned no plan and no candidate was applied. |
| Forced full fallback | 1 structured + 1 raw author | render + metrics 468.478 ms; local DOM 1.405 ms; initial VQA 4.715 s; fallback stage 159.131 s; total 164.316 s | **Failed safely.** VQA found high placeholder + high empty-space. The raw author request ended in a provider read timeout; no changed HTML was accepted. |

Safety behavior was correct in both paths: an invalid local patch did not reach application, and a missing full-fallback candidate did not replace the original slide. No comparison VQA was attempted without a candidate.

## Target judgment

- Problem-path 20–60 s: the rejected local-patch attempt was 29.386 s, within the range, but it did not produce an accepted repair. The full fallback was 164.316 s and failed, so the end-to-end problem-path target is **not met** by this campaign.
- Clean-path 5–10 s and cold/warm p50/p95: **not re-measured** under the four-call ceiling. The earlier accepted evidence remains warm clean 5.528 s and cold clean 27.461 s, but it is not a repeated distribution.
- Browser startup was separated from model work for the two attempted repair fixtures (416.806 ms and 468.478 ms), but two heterogeneous samples are not reported as cold/warm p50/p95.
- 8-slide audit promotion and the 10/11-slide live boundaries: **not executed after the budget reached 4**. The runner now reports these stages as `skipped: provider_budget_exhausted`; no zero-call fallback result is accepted as live evidence. The earlier 8-slide evidence (8 → 3 image requests) remains valid but does not cover 10/11.

## Operational findings

1. The local-patch guard prevented a malformed model property from mutating the slide. The observed `fontsize`/`font-size` mismatch is a concrete live-model robustness gap; fixing it requires a production prompt/schema or normalization decision outside this benchmark-only change.
2. The forced author call exceeded the desired latency and failed at the provider read timeout. Timeout/fallback policy needs a separate production decision; this benchmark does not weaken the existing fail-closed behavior.
3. Several production critique helpers convert provider failures to `None`. A benchmark must check the cost ceiling before starting another stage, otherwise budget exhaustion can resemble a completed zero-call review. `LIVE_BENCHMARK.py` now performs that pre-stage check.

## Code and verification

Added:

- `evaluation/hybrid_review/LIVE_BENCHMARK.py`
- `tests/unit/test_authored_live_benchmark.py`

Verification from `servers/fastapi`:

```powershell
uv run pytest tests/unit/test_authored_live_benchmark.py -q
# 6 passed

uv run python -m compileall -q evaluation/hybrid_review/LIVE_BENCHMARK.py tests/unit/test_authored_live_benchmark.py
git diff --check -- evaluation/hybrid_review/LIVE_BENCHMARK.py tests/unit/test_authored_live_benchmark.py evaluation/hybrid_review/LIVE_BENCHMARK_RESULTS_2026-08-02.md
```

The project environment does not bundle Ruff, so the isolated `uvx` runner was used without modifying project dependencies:

```powershell
uvx ruff format --check evaluation/hybrid_review/LIVE_BENCHMARK.py tests/unit/test_authored_live_benchmark.py
# 2 files already formatted
uvx ruff check evaluation/hybrid_review/LIVE_BENCHMARK.py tests/unit/test_authored_live_benchmark.py
# All checks passed!
```

Safe live reproduction (defaults to the same four-generation ceiling):

```powershell
uv run python evaluation/hybrid_review/LIVE_BENCHMARK.py --user-config ../../app_data/userConfig.json --provider codex --stage-timeout-seconds 180 --clean-repeats 3
```

Do not raise `--max-provider-requests` without an explicit cost decision. A four-call campaign cannot prove all repair, repeated clean, audit-promotion, and 8/10/11 boundary paths; those items remain honestly incomplete rather than being inferred from mocks or zero-call failures.
