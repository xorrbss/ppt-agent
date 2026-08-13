# Provider usage receipt benchmark implementation validation

## Scope and result

This record adds an offline-only receipt analysis path for the authored 90-slide
benchmark. It does not change the existing 01--04 experiment evidence or the
architecture design. The analyzer consumes the additive `ProviderUsageReceipt`
scalar shape: job, operation, stage, provider/model, idempotency-key hash,
retry class, latency, usage state, aggregate token counts, request count,
reported/estimated microunit costs, and pricing version.

The implementation is [`provider_usage_benchmark.py`](../../../../servers/fastapi/scripts/provider_usage_benchmark.py).
It fail-closes on fields that could carry a prompt, HTML, OCR, source URL/path,
headers, request IDs, exception bodies, or credentials. It does not import a
provider SDK, read environment credentials, or open the network.

## Reproduction

From `servers/fastapi`:

```powershell
uv run pytest tests/unit/test_provider_usage_benchmark.py -q
uv run python scripts/provider_usage_benchmark.py `
  --synthetic-fixture ../../docs/experiments/authored-vnext/01-generation-baseline/provider_usage_offline_fixture_v1.json `
  --output ../../docs/experiments/authored-vnext/01-generation-baseline/runs/provider-usage-offline-90x3.json
```

The fixture generates 90 receipt-shaped author operations for each of three
deterministic synthetic jobs (270 logical receipts). It neither calls nor
emulates a paid provider. `retry_slide: 45` creates one retry-accounting case
per job while preserving one logical receipt per idempotency hash.

## Observed offline result

The generated [`provider-usage-offline-90x3.json`](runs/provider-usage-offline-90x3.json)
records 270 unique receipts, 273 aggregate requests, and retry amplification of
1.011111. Author-stage p50/p95/p99 are 75,000 ms. The receipt-only critical
path lower-bound proxy p99 is 75 seconds, so both separately reported
1,800-second server deadline and 1,800-second UI polling timeout pass for this
fixture only.

The report contains 648,000 input and 1,350,000 output tokens, an estimated
4,050,000 microunits across three jobs, and `null` reported cost. Those values
are fixture planning assumptions, not provider observations. `unavailable`
usage is represented as `null` token/cost values—not zero—and is tested.

## Timeout interpretation

The report intentionally exposes `server_deadline_seconds` and
`ui_poll_timeout_seconds` as different fields. The fixture supplies the current
30-minute values only for an offline comparison; this work does not modify
either timeout and does not claim a live measurement. Receipt latency is a
provider-call duration, so its critical-path figure is the documented
sum-of-per-stage-maximum lower-bound proxy rather than an end-to-end wall-clock
assertion.
Stage-wall telemetry from a future approved staging run is required for a real
timeout calibration.

## Future live staging is blocked pending approval

[`staging_guardrails_v1.json`](staging_guardrails_v1.json) defines the fixed
public-synthetic corpus, hostname allowlist, $2 per-90-slide/$6 three-run cost
ceilings, offline dry-run command, and cancellation conditions. The corpus is
kept separately in [`staging_prompt_corpus_v1.json`](staging_prompt_corpus_v1.json);
only its version/hash may be placed in a receipt or log. No provider hostname
was contacted, no live provider metric was collected, and the synthetic cost
must not be treated as a quotation or a staging result.
