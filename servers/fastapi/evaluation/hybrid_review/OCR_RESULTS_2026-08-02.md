# Authored OCR suspect-only benchmark - 2026-08-02

## Scope and command

This is a deterministic local benchmark. It uses real Chrome captures and the
local Tesseract executable only; it never reads a provider credential and reports
`model_calls: 0`.

```powershell
cd servers/fastapi
uv run python scripts/benchmark_authored_hybrid_review.py --ocr-self-test --output .test-runtime/authored-hybrid-review-ocr-self-test-final.json
uv run pytest tests/integration/test_authored_ocr_live_smoke.py -q
```

The final measured JSON is
`.test-runtime/authored-hybrid-review-ocr-self-test-final.json`.
It has four slides: a clean slide, a CSS-clipped Korean/digits/symbol slide, an
unclipped candidate, and a deliberately clipped regression candidate. The source
string is `price` in Korean followed by `2026 +12%`; the defect therefore removes
Korean, numbers, and symbols from the rendered capture.

## Measured result

| Field | Measured value |
| --- | ---: |
| Model calls | 0 |
| Fixture render time | 815.736 ms |
| No-OCR route total time (4 slides) | 8.401 ms |
| Local suspect slides | 2 of 4 (indices 1, 3) |
| Suspect-only image OCR calls | 2 |
| Suspect-only OCR time | 1262.120 ms |
| Suspect-only route total time | 1270.521 ms |
| OCR cache hits during route | 0 |
| Cache-probe elapsed time | 0.0 ms |
| Candidate-verification cache hits | 1 |
| True positives / false positives / false negatives | 2 / 0 / 0 |

`total_ms` intentionally excludes the one-time fixture render so the current
no-OCR route and the suspect-only route operate on the same captures. The OCR
increment in this run is 1262.120 ms for two local suspects. A clean production
deck that has no local suspects makes zero image-OCR calls.

The engine was ready: `tesseract v5.4.0.20240606`, requested languages
`kor+eng`, available languages `eng, kor`, tessdata directory
`C:\project\PPT-agent\ppt-agent\tessdata`, and unavailable reason `null`.

The intentionally clipped baseline was rejected with 12 text omissions: 6
numbers, 2 symbols, and 2 Korean characters. The unclipped candidate was accepted
with zero remaining omissions; the candidate comparison reported
`ocr_improved`. The deliberately clipped candidate was rejected and the candidate
comparison reported `text_regression`. These values are recorded in the JSON
instead of being inferred from markup.

The environment-gated real-binary smoke test passed: `1 passed in 2.09s`. It
also required CER <= 0.10 and zero missing/substituted Korean, digit, and symbol
characters; the observed fixture CER was 0.0. It skips with an explicit reason
when the executable, `kor+eng` data, or a Korean font is absent. It makes no
model call.

## Deployment notes

On Windows, `tesseract.exe` is often installed at
`C:\Program Files\Tesseract-OCR\tesseract.exe` but is not on `PATH`. Set
`TESSERACT_CMD` to the exact executable when discovery cannot find it. Point
`AUTHORED_OCR_TESSDATA_PATH` (or `TESSDATA_PREFIX`) at a directory containing
both `kor.traineddata` and `eng.traineddata`; mismatched binary/data versions and
fonts can change OCR output and benchmark timings.

Docker images need the Tesseract executable plus both language files and must use
a container path such as `/app/tessdata`, not a Windows drive path. The smoke test
is deliberately skipped rather than faked when that runtime is unavailable. OCR
is process-bound and local: its measured impact is paid only for DOM-suspect
slides, has no model-key dependency, and is cached within the request-local
`AuthoredOcrCache`.
