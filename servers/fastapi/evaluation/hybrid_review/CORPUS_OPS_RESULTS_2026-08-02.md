# Authored hybrid corpus and operations validation — 2026-08-02

## Outcome

The deterministic `exact_reconstruction` and `style_reference` regression gates pass after adding privacy-safe OCR readiness and completion telemetry. The host OCR runtime is ready for `kor+eng`. Six checked-in PPTX artifacts (95 slides) parse successfully. This run did not treat structural PPTX parsing or synthetic gates as a statistical production-quality estimate.

## Representative corpus

### Checked-in PPTX structural corpus

`python-pptx` opened every checked-in `docs/artifacts/**/*.pptx` file and traversed every slide and shape without an exception.

| Artifact | Slides | Shapes | SHA-256 prefix |
| --- | ---: | ---: | --- |
| `ppt-master-ai-technology-proposal.pptx` | 15 | 742 | `03b9a83607ff` |
| `ppt-master-business-proposal.pptx` | 15 | 742 | `67a0ea1b5f83` |
| `ppt-master-data-analysis-report.pptx` | 15 | 742 | `efa42d79130b` |
| `ppt-master-executive-report.pptx` | 15 | 742 | `1140face3e22` |
| `ppt-master-project-performance-report.pptx` | 15 | 742 | `624d45c6691a` |
| `ai-capital-2026-data-journalism.pptx` | 20 | 947 | `5ea1bd7f13eb` |
| **Total** | **95** | **4,657** | — |

LibreOffice/`soffice` is not installed on this host, so these six files were not rendered through LibreOffice. Reproduce the structural check with `uv run python` and `python-pptx`; for an independent visual render, install LibreOffice and export each PPTX headlessly before image comparison.

### Exact reconstruction metric corpus

Command:

```powershell
uv run python scripts/evaluate_exact_reconstruction.py --self-test --output $env:TEMP\ppt-agent-exact-selftest-20260802.json
```

Actual Tesseract-backed result: 5 cases in 4.952 s (0.990 s/slide), mean pixel similarity 0.990441, mean edge similarity 0.997958, mean OCR CER 0.030556, line-boundary exact rate 0.8, and mean bounding-box IoU 0.992308. No model calls or revisions were made.

The deterministic manifest-text run completed in 0.523 s (0.105 s/slide). Its one clean case remained clean and each of four deliberately changed cases activated its expected metric channel (text mutation, line-break mutation, layout move, or visual-color mutation). For this bounded synthetic corpus: clean false positives `0/1`; defect false negatives `0/4`.

### Style-reference and fallback gates

The combined authored regression command below passed `111/111` tests in 31.41 s. It covers exact/style routing, DOM gates, OCR gates, initial and pairwise VQA contracts, local acceptance, audit rejection, and bounded full fallback.

```powershell
uv run pytest -q tests/unit/test_authored_hybrid_review.py tests/unit/test_authored_acceptance_fallback.py tests/unit/test_authored_style_comparison_vqa.py tests/unit/test_authored_review_routing.py tests/unit/test_authored_dom_quality.py tests/unit/test_authored_quality_policy.py tests/unit/test_exact_reconstruction_evaluator_ocr.py tests/unit/test_authored_quality_ocr_integration.py tests/unit/test_authored_quality_review.py
```

The controlled fallback corpus has four forced scenarios: fallback is attempted in `3/4`; the initial-indeterminate scenario deliberately does not promote an untrusted comparison. The three promoted scenarios cover accepted fallback, fallback execution failure, and fallback audit indeterminate. This is branch coverage, not an observed production fallback rate. No statistical style-reference false-positive/false-negative rate can be inferred from deterministic contract tests alone.

## OCR readiness

`GET /api/v1/ppt/presentation/quality-review/readiness` performs a five-second-bounded Tesseract inspection off the event loop. It requires both Korean and English and returns only closed status codes, safe language identifiers, a tessdata-present boolean, and elapsed time. It never returns executable commands, filesystem paths, version strings, OCR content, or raw failure text.

Five actual host probes all returned `ready=true`, `code=ocr_ready`, `available_languages=[eng, kor]`, and `tessdata_configured=true`. Inspection times were 97.108, 84.870, 80.421, 87.100, and 77.220 ms: p50 84.870 ms; nearest-rank p95 97.108 ms. Korean presentation labels (`ko`, `kor`, `KR`, `Korean`, `한국어`, and `Korean (한국어)`) now consistently request `kor+eng`.

## Completion telemetry and log hygiene

Telemetry schema version 2 adds one aggregate `stage=completed` event. It reports:

- render, local inspection, montage, critique, revision, persistence, OCR, and total timings;
- VQA request calls and cache hits/misses;
- patch attempted/accepted/rejected;
- fallback attempted/succeeded/failed;
- remaining local high-severity count and rejection reason counts.

The provider wrapper does not expose token usage or billable cost, so `vqa_cost_usd` remains `null` and `vqa_cost_status=provider_usage_unavailable` when VQA was called. This is intentionally explicit instead of inventing an estimate.

Reference mode and stage use closed allowlists. Model/prompt/cache identifiers are length- and character-bounded, and paths, multiline content, and common credential prefixes are collapsed to `unknown`. Rejection reasons are allowlisted with unknown values collapsed to `other`. Tests confirm runtime paths, API-key-like strings, prompt text, raw HTML, images, OCR text, and hashes are not logged.

## Verification and limits

- `111 passed in 31.41s` for the combined authored regression suite.
- `python -m compileall` passed for every changed backend and test module.
- Fatal Ruff rules (`E9`, `F63`, `F7`, `F82`) pass on all changed modules. A latest-tool broad `uvx ruff check` reports 23 pre-existing repository-wide style/import/deprecation findings in touched legacy files; no bulk unrelated formatting was applied.
- Production fallback frequency and provider cost remain unavailable until production telemetry is aggregated and the provider adapter exposes usage metadata.
- PPTX visual fidelity was not claimed from structural parsing because `soffice` is absent on this host.
