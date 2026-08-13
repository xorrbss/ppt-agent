# Authored 90-slide generation baseline (offline harness)

This experiment is a reproducible, read-only timing/cost **estimator**, not a
live benchmark. It deliberately avoids external providers, customer input,
browser/OCR subprocesses, and application persistence. Its only output is the
JSON report selected by `--output`; keep it in this directory.

## Run

From `servers/fastapi` (so normal project Python tooling is used):

```powershell
uv run python ..\..\docs\experiments\authored-vnext\01-generation-baseline\baseline_harness.py `
  --output ..\..\docs\experiments\authored-vnext\01-generation-baseline\runs\offline-90x3-verified.json --runs 3
```

The harness requires three passes or more. It hashes the fixture, captures the
repository Git SHA and local Python/browser discovery manifest (an existing local
path plus a version-shaped value only), and reports p50/
p95 stage timing, critical path, peak resource usage, retry multiplier, token
counts, cost estimate, and the 30-minute decision. The `browser_version` and
font manifest intentionally remain `null`/not-inspected because no renderer is
launched.

## Inputs and interpretation

`fixture_offline_v1.json` has no prompt, HTML, credentials, provider endpoint,
or customer data. Its durations/tokens are explicitly planning assumptions.
Change them only with a dated, privacy-safe measurement manifest; set model
input/output price per million tokens there before using cost figures. The report
labels these values `estimated_from_fixture_assumptions` so they cannot be
misrepresented as observed provider performance.

The scheduler mirrors the authored resource topology: author, illustration,
render, OCR and VQA run per-slide with their respective caps, then one deck-wide
persist and export tail. Initial authored generation does not invoke OCR; VQA is
optional. Disabled fixture stages have a zero duration. A zero price is reported
as `provider_price_unavailable`, never as a $0 deck cost.

## Live-measurement blocker and safe next command

No live calibration is run here. Credible author/provider latency, token/cost,
retry, illustration, VQA, or customer-data measurements require a configured
provider and therefore can incur cost/transmit data. Before that separate,
explicitly-authorized test, collect three sanitized runs with only aggregate
durations, token counts, model/provider IDs, and failure classes; never capture
prompts, HTML, raw OCR text, URLs, headers, request IDs, or exception bodies.

Current code already provides a no-model render/OCR route benchmark:

```powershell
cd servers\fastapi
uv run python scripts\benchmark_authored_hybrid_review.py --ocr-self-test --output ..\docs\experiments\authored-vnext\01-generation-baseline\runs\ocr-self-test.json
```

That command may launch local rendering/OCR prerequisites but makes no model
request; it is a component calibration, not an end-to-end 90-slide result.

## Privacy guard

The harness rejects credential/payload key names and values that resemble bearer,
`sk-`, or `api_key`/`secret`/`password` assignments. It neither reads environment
variables nor logs their values. Inspect the JSON field `privacy_check.pass` before
publishing a report.
