# V5 native chart acceptance on a Windows PC: checklist (2026-11-08)

Purpose: confirm what the Linux cloud runs could not confirm. That means the
Windows product timings of the export-time V5 chart proof, and how the exported
native charts look and behave in real PowerPoint. Everything else (unit tests,
the fake-LLM real-Chrome run, CI gates) was verified in the cloud; see
`codex-review-followup-20261108.md`.

Branch: `claude/exciting-planck-oqcy7w`. V5 stays default-off. Turn it on only
for this check.

## 0. Prepare (about 10 min)

1. Stop the stack, update and install:
   ```powershell
   .\scripts\start-local-stack.ps1 -Stop
   git fetch origin; git checkout claude/exciting-planck-oqcy7w; git pull
   cd servers\fastapi; uv sync --frozen; cd ..\nextjs; npm ci; cd ..\..
   ```
2. Turn V5 on for the API and both controllers.
   - Add `"SVG_NATIVE_CHART_INTEGRITY_V5": "on"` to `api.json`,
     `worker-generation.json` and `worker-export.json` in the directory you
     originally seeded the stack from.
   - Re-seed: `.\scripts\start-local-stack.ps1 -ImportEnvironment <that directory>`.
   - If those JSON files are gone, stop here and ask for a store export option
     rather than starting processes by hand.
3. Start: `.\scripts\start-local-stack.ps1`. Check `-Status`.
   - Startup migrations must run (the branch adds no migration, but the schema
     report should show "current").

## 1. One chart-heavy deck through the product (paid: one small generation)

1. Generate a 6-page SVG deck with export to PPTX. Use a short source text with
   explicit data:
   - one bar chart with a "단위: 억원" caption;
   - one 100% stacked chart with % labels;
   - one donut;
   - one line chart with markers;
   - one rounded-value chart (e.g. 12.345 shown as 12.3).

   The CLI is fine:
   `node scripts/ppt-agent.mjs --async --slides 6 --export pptx --out <dir> "<prompt>"`.
2. Record:
   - generation time;
   - per-page result (any fallback pages);
   - export time from the export operation;
   - in the API log, the `svg_export_stage_seconds` line with
     `chart_paint_verification`, plus per-chart verify durations.
3. Expectations, from the cloud run:
   - render never fails on chart paint;
   - each chart takes 60–120 s to verify at export;
   - **one export verifies about 3 charts**, and the rest stay drawn (pictures).
     **Run the export a second time** (dashboard export, or `ppt-agent.mjs`
     again on the same deck) and confirm that more charts become native.

## 2. PowerPoint check (about 15 min)

Open the second export in PowerPoint and, for each chart page, check:

| Check | Pass when |
|---|---|
| Native chart | Click the chart → Chart Design tab appears; right-click → Edit Data opens a workbook with the deck's numbers |
| Looks right | Same bars/lines/slices, colours and order as the in-app preview |
| Labels | Value labels show the same numbers as the preview (rounded labels stay rounded; % labels stay %) |
| Unit caption | "단위: 억원" is still on the page as editable text near the chart |
| Drawn charts | Charts not yet verified are pictures that look like the preview (no blank areas) |
| No repair prompt | PowerPoint opens the file without "repair" warnings |

Screenshot any mismatch, together with the in-app preview of the same page.

## 3. Report back

- The timings from 1.2.
- The number of native charts after export 1 and after export 2.
- The PowerPoint table above, with screenshots of failures.
- The API log lines that mention `chart_paint` or `svgNativeChartPaint`.

## 4. Afterwards

Remove `SVG_NATIVE_CHART_INTEGRITY_V5` from the three JSON files, re-seed and
restart, unless you decide to keep V5 on.

## Known limitations (do not report as new)

- **Charts verified per export:** about 3 per export, because of the export
  deadline reserve. A product decision is pending: either a follow-up
  verification run, or a deadline that scales with the chart count.
- **Pages that stay drawn:**
  - raster-reconstruction pages;
  - template pages whose render inputs cannot be reproduced at export;
  - decks that went through the common-header design operation.
- **LibreOffice colours:** LibreOffice draws donut/pie slices in its default
  colours. PowerPoint should use the declared colours, which is exactly what
  step 2 checks.
