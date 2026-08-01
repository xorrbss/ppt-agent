# Authored font-render matrix

This is a renderer-only regression matrix. It does not add OCR, VQA, model,
or network calls to normal generation or export. The browser fixture lives in
`servers/fastapi/tests/unit/test_authored_render_policy.py`; it uses the same
headless-shell flags as `utils/slide_capture.py` and is skipped only when no
Chrome/Chromium executable is available.

## Windows observation (2026-08-02)

Environment: Windows headless Playwright shell
`chromium_headless_shell-1228`, 1280 x 720, device scale factor 1. The test
executes both the production PNG screenshot path and a same-flags `--dump-dom`
probe to record computed values.

| Fixture | Computed family | Size / weight / line-height | Width / layout result |
| --- | --- | --- | --- |
| English bold | `Arial, sans-serif` | 11px / 700 / 13px | 66px / one line |
| Korean bold | `Arial, "Malgun Gothic", sans-serif` | 10px / 700 / 14px | 42.78125px / one line |
| Mixed Korean-English | `Arial, "Malgun Gothic", sans-serif` | 8px / 400 / 11px | 41.796875px / one line |
| Natural wrap | same Korean fallback stack | 10px / 400 / 12px | 72px / 36px (three lines) |
| Explicit line break | `Arial, sans-serif` | 10px / 700 / 12px | 72px / 24px (two lines) |
| Symbols | same Korean fallback stack | 11px / 400 / 14px | 88.546875px; `① ≤ → “한글” € %` paints |

The canvas fallback detector reported Arial, Malgun Gothic, and Noto Sans KR
available, and a deliberately nonexistent font unavailable. Computed CSS
`font-family` exposes the declared fallback stack rather than the specific face
selected per glyph; the detector and Korean/mixed glyph ink/width assertions
therefore supply the observable fallback evidence.

The same browser PNG test also checks that author-specified overflow cropping,
z-index overlap, an `<img>`, a bordered table, and SVG chart bars retain their
exact sampled pixels. This guards against renderer-side visual correction.

## Linux reproduction and risk

Linux was not available in this Windows workspace, so no Linux font pixels or
metrics are claimed here. The test is deliberately platform-detectable:

- it runs against any configured Chrome/Chromium and records computed metrics;
- the Windows-only availability assertion for Arial/Malgun Gothic is gated by
  `os.name == "nt"`;
- a Linux run still rejects the nonexistent-font false positive and validates
  8/10/11px, line-height, wrapping, Unicode ink, crop, overlap, table, and
  chart geometry.

Before a Linux release, run the test in the target image and retain its measured
font metrics. Install or bundle the required Korean font (or select an approved
Linux fallback) if the availability signal or widths differ materially. Exact
pixel equivalence across Windows and Linux is not guaranteed: font rasterizers,
font files, and fallback substitution can alter glyph widths, weight, and line
breaks even while the renderer preserves authored CSS.

## Renderer/cache benchmark

Run from `servers/fastapi`:

```powershell
uv run python ../../scripts/bench_authored_render_policy.py --samples 3
```

The benchmark is renderer/cache-only (`model_calls=0`). Windows observation on
the same shell: three distinct cold renders averaged 0.392 seconds; first
render was 0.480 seconds; a cache hit was 0.000068 seconds; changing
`RENDER_CACHE_VERSION` caused a 0.577-second rerender. The estimate for 90
unique slides is 35.3 seconds serially or about 8.8 seconds at the configured
render concurrency of four; 90 identical cached requests estimate 0.486
seconds. These are local renderer timings, not the much larger remote VQA
model-call cost.
