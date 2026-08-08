# Exact reconstruction evaluation harness

This manual harness measures the authored image-to-HTML path without adding work to normal generation or export. It supports local source/render pairs, a five-slide self-test, and an explicitly gated real-model run. The default text measurement OCRs both the source PNG and rendered PNG; it never substitutes manifest or HTML text silently.

## Safety gates

Live evaluation requires both `PRESENTON_EXACT_EVAL_LIVE=1` and `--acknowledge-model-cost`. It is capped at five slides. Start with one or two slides; expand to five only after the smoke run succeeds. The configured provider and model name may appear in the report, but API keys, prompt bodies, source pixels, and user configuration contents never do.

The repository has no checked-in exact-reconstruction source deck. The built-in synthetic fixture therefore supplies five unique 1280×720 source images with Korean, English, numbers, symbols, repeated spaces, line breaks, shapes, colors, and stable object boxes. A real sample can be supplied with `manifest.example.json` as the schema guide.

`--render-benchmark-slides 90` is a separate, local-only renderer/cache load
check. It submits one deterministic Korean/English HTML document 90 times
concurrently and reports logical requests versus uncached Chrome renders. It
makes zero model and OCR calls; it proves cache coalescing, not VQA quality or
the cost of 90 distinct live slides.

Run commands from `servers/fastapi`:

```powershell
uv run python scripts/evaluate_exact_reconstruction.py --self-test
uv run python scripts/evaluate_exact_reconstruction.py --render-benchmark-slides 90
uv run python scripts/evaluate_exact_reconstruction.py --manifest evaluation/exact_reconstruction/manifest.json
$env:PRESENTON_EXACT_EVAL_LIVE="1"
uv run python scripts/evaluate_exact_reconstruction.py --live --acknowledge-model-cost --synthetic-slides 1 --provider openai
```

The model-free deterministic text check is explicit:

```powershell
uv run python scripts/evaluate_exact_reconstruction.py --self-test --text-source manifest
```

`--text-source html_proxy` is also explicit and requires `source_text` plus `render_html`. It is labeled `html_visible_text_proxy` and is not OCR. The default `--text-source tesseract` never falls back to either mode.

## Reproducible Windows OCR setup

The evaluator searches `PATH`, then the standard 64-bit and 32-bit `C:\Program Files\Tesseract-OCR` locations. An explicit `--tesseract-command` has priority. The verified Windows engine installation is:

```powershell
winget install --id UB-Mannheim.TesseractOCR --version 5.4.0.20240606 --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
```

That installer initially supplied `eng` and `osd` but not `kor` on the verified
machine. Use a non-committed evaluation directory containing a matched, pinned
language pair before installing or selecting the language files:

```powershell
$evalTessdata = ".test-runtime/tesseract/tessdata_fast-4.1.0"
New-Item -ItemType Directory -Path $evalTessdata -Force | Out-Null
Invoke-WebRequest "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata" -OutFile "$evalTessdata/eng.traineddata"
Invoke-WebRequest "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/kor.traineddata" -OutFile "$evalTessdata/kor.traineddata"
Get-FileHash "$evalTessdata/*.traineddata" -Algorithm SHA256
```

Expected SHA-256 values:

- `eng.traineddata`: `7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2`
- `kor.traineddata`: `6B85E11D9BBF07863B97B3523B1B112844C43E713DF8B66418A081FD1060B3B2`

Run real source/render OCR from `servers/fastapi`:

```powershell
uv run python scripts/evaluate_exact_reconstruction.py --self-test --text-source tesseract --tessdata-dir ../../.test-runtime/tesseract/tessdata_fast-4.1.0 --ocr-language kor+eng
```

On the verified integration host, the hash-checked `kor.traineddata` was also
installed into `C:\Program Files\Tesseract-OCR\tessdata`. A subsequent run
without `--tessdata-dir` reported `eng`, `kor`, and `osd` and completed the same
five-slide OCR self-test. Keep downloaded staging files under ignored
`.test-runtime`; do not copy or commit the system executable or traineddata into
the repository.

## Metrics and call accounting

Each slide reports canvas size, pixel MAE/RMSE/PSNR, pixel similarity, edge similarity, and optional stable-ID object-box IoU. Text output includes separate raw and normalized CER, Levenshtein insertions/deletions/substitutions, deletion-based missing rate, exact line-break match, line-break F1/counts, and Korean/English/digit/symbol preservation. Normalization is Unicode NFKC plus newline canonicalization, horizontal-whitespace collapsing, and per-line trimming; it does not erase line-break changes.

The report records OCR engine version, requested and available languages, inspection time, per-image OCR time, exit code, and a stable failure reason. `unavailable`, `language_missing`, `timeout`, and `failed` are distinct. An unavailable text metric is written to the report and exits with status 3. Tesseract stderr and OCR text are not copied into the report. `--ocr-timeout-seconds` defaults to 90 seconds per image; timed-out children are terminated and reaped.

The live path calls the existing exact authoring and high-quality-review functions. It performs one initial source-vs-render comparison per slide, re-authors only flagged slides while reusing the initial critique, performs a final comparison only for changed candidates, and accepts a candidate only when the same deterministic severity tuple is strictly improved. A missing/duplicate source mapping fails before model calls.

`--provider openai` or `--provider google` is an explicit evaluation-only override. It is useful when the interactive configuration selects a different provider but already contains credentials for the requested evaluator. The override changes process environment only and never rewrites persistent configuration.

Generated reports and images default to `.test-runtime/exact-reconstruction-eval/` and must not be committed.
Each report owns a sibling `*-artifacts` directory, and an exclusive `*.lock` file prevents overlapping runs from charging twice or overwriting the same report. If the process is forcibly killed, remove a stale lock only after confirming that no evaluator process remains.
