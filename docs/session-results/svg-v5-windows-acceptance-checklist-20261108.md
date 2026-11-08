# V5 native chart acceptance on a Windows PC: checklist (2026-11-08)

Purpose: confirm what the Linux cloud runs could not confirm. That means the
Windows product timings of the export-time V5 chart proof, and how the exported
native charts look and behave in real PowerPoint. Everything else (unit tests,
the fake-LLM real-Chrome run, CI gates) was verified in the cloud; see
`codex-review-followup-20261108.md`.

Code: `main` (PR #152 export-time verification, PR #153 background
continuation). V5 stays default-off. Turn it on only for this check.

## 0. Prepare (about 10 min)

1. Stop the stack, update and install:
   ```powershell
   .\scripts\start-local-stack.ps1 -Stop
   git fetch origin; git checkout main; git pull
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
2. **Do not export again by hand.** After the first export finishes, the
   remaining charts are verified in the background:
   - the deck's workspace timeline shows a **"네이티브 차트 검증"**
     (`svg.chart.verify`) item;
   - when it has newly verified charts, **a refreshed export is created
     automatically**, as a new PPTX artifact in the same timeline.

   Wait until that refreshed export appears. In the cloud run it came about
   4 minutes after the first export; it takes longer with more charts, up to
   5 background rounds of about 10 minutes each. Then download the newest PPTX
   from the workspace or dashboard. The CLI's `--out` file is the *first*
   export only.
3. Record:
   - generation time;
   - per-page result (any fallback pages);
   - the first export's time, and in the API log the `svg_export_stage_seconds`
     line with `chart_paint_verification`;
   - each `svg.chart.verify` round: start, end, and charts passed. The result
     is shown on the timeline item; the API log only warns from
     `svg_chart_paint_followup` when something could not be enqueued or
     submitted;
   - when the refreshed export appeared, and how long it took.
4. Expectations, from the cloud run (3 + 3 charts, about 9 minutes from
   generation to the final artifact):
   - render never fails on chart paint;
   - each chart takes 60–120 s to verify;
   - the first export holds about 3 native charts; the rest are drawn
     (pictures);
   - background verification starts right after the first export;
   - the refreshed export holds all verifiable charts as native charts;
   - while a background round runs, a new edit or export on the same deck waits
     at most for the current chart capture (about 2.5 min).

## 2. PowerPoint check (about 15 min)

Open the **refreshed (newest) export** in PowerPoint and, for each chart page,
check:

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

- The timings from 1.3.
- The number of native charts in the first export and in the refreshed export.
- Whether the refreshed export appeared without a manual export, and after how
  long.
- The PowerPoint table above, with screenshots of failures.
- The API log lines that mention `chart_paint` or `svgNativeChartPaint`.

## 4. Afterwards

Remove `SVG_NATIVE_CHART_INTEGRITY_V5` from the three JSON files, re-seed and
restart, unless you decide to keep V5 on.

## Known limitations (do not report as new)

- **First export is partial by design:** it verifies about 3 charts within its
  deadline reserve. Background verification and the automatic refreshed export
  complete the rest.
- **Background verification limits:**
  - at most 5 rounds per deck revision;
  - charts still unverified after that wait for the next manual export;
  - editing the deck stops the current chain, because evidence is bound to the
    revision;
  - if the refreshed export is refused (for example on capacity), it is not
    retried, but the verification evidence is kept and the next manual export
    makes those charts native.
- **No background verification:** decks exported with a header revision, and
  source-preserved decks, verify at export time only.
- **Pages that stay drawn:**
  - raster-reconstruction pages;
  - template pages whose render inputs cannot be reproduced at export;
  - decks that went through the common-header design operation.
- **LibreOffice colours:** LibreOffice draws donut/pie slices in its default
  colours. PowerPoint should use the declared colours, which is exactly what
  step 2 checks.
