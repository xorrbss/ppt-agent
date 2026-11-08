# Server-rendered SVG charts: design and prototype measurement (2026-11-08)

## Problem

An SVG author draws every chart twice:
- as contract primitives (bar rects, label texts, axis lines at pixel coordinates);
- as a `data-native-chart` JSON declaration.

The PPTX export throws the drawing away and builds the native chart from the
JSON (`export_projection.py`). Two authorities for one chart create the need
to prove that they agree:
- **V4:** static geometry checks, 8% scale tolerance. A failure exports the
  chart as a picture.
- **V5:** a stricter geometry proof plus a Chrome paint proof of 60-140 s per
  chart (`svg_chart_paint_verification.py`, `svg_chart_paint_followup.py`).

Including the vendored exporter, about 14,800 lines serve charts, about 10,000
of them for verification.

## Evidence that the drawing, not the data, is the weak part

All 28 chart declarations in three real V5 runs of the same 6-page chart deck
(`claude-sonnet-5-5`) were checked against the source numbers:
- **28/28** declared values were numbers the source states.
- **19/28** declarations were rejected, and **all 19** of those had correct JSON.
  Some were verifier false rejections since fixed; the rest were drawing mistakes:
  - one bar 115 px where the scale gives 122 px;
  - icons placed inside the chart group;
  - the declared title not drawn;
  - a category label running into its bar.

## Design

The author declares the chart and its box only:

```xml
<g id="native-chart-SLUG" data-native-chart='JSON'
   data-chart-render="server" data-chart-box="X Y WIDTH HEIGHT"/>
```

**Server drawing** (`services/generation_core/drivers/svg/server_chart.py`):
- The server draws the group's contents from the JSON, deterministically, inside
  `extract_native_declarations`. Every replay of a stored raw response (author,
  partial recovery, page remake) therefore draws the identical tree. The
  existing table cell folding works the same way.
- The drawing follows the existing native contract:
  - one baseline and scale;
  - the axis drawn before the bars;
  - value labels next to their datum;
  - a single series named by its drawn title;
  - legend swatches for several series;
  - value text in whichever of white or near-black contrasts more with its bar.
- Font and text colour are the page's dominant ones.
- The JSON is normalised to what was drawn (label position, category order,
  clustered grouping, colours), so the export and the preview read one source.

**Values must come from the source.** `apply_server_chart_grounding` (render
stage) fails a server chart whose values are not numbers the page source states
(`native_requested_chart_missing`, reason `server_chart_value_ungrounded`). It
replaces the geometry question with the one that remains: is the data right.

**Scope of version one:**
- bar and column charts, clustered, one or more series, positive values (the
  native bar contract's own limit);
- anything else (line, area, pie, donut, stacked, non-positive values, a box
  under 160x120, category labels wider than their band) is dropped with a reason
  and left empty. The author rule keeps those forms author-drawn.

**Gating:**
- `SVG_SERVER_CHARTS` (internal, default off) is frozen at admission as
  `style_snapshot["server_chart_render"] = "svg-server-chart/v1"`.
- Only such decks get the author rule (`SERVER_CHART_RULE`, standard and
  layered profile-10 prompts) and the grounding check.
- Raw responses without the marker, which is every stored deck, extract exactly
  as before. The verifier ledgers were re-pinned with that reason.

## Measurement

Same input (6 pages, five numeric series), real `claude-sonnet-5-5`, V4 checks
(V5 off), PPTX export:

| | Existing (author-drawn) | Server-rendered |
|---|---|---|
| Decks | 2 | 3 |
| Charts drawn | 7 | 9 |
| Native charts in the PPTX | 5 (71%) | **8 (89%)** |
| Charts lost to drawing errors | 1 (3-series column, scale mismatch beyond 8%: exported as a picture) | **0** |
| Charts lost to a page-level text overlap outside the chart | 1 | 1 |
| Retries caused by a chart | 0 | 0 |
| Generation time per deck | 143-147 s | **122-134 s** |
| Cost per deck | $0.94 (33.1k output tokens) | **$0.74 (28.6k output tokens)** |

**Other findings:**
- Every accepted chart in both arms was accurately drawn (scale spread at most
  0.18%). Under V4 an inaccurate chart is not retried; it silently becomes a
  picture.
- The two charts lost to page-level overlap kept a correct drawing. The page was
  published as its last valid version, which binds no native objects. That is
  unrelated to how the chart is drawn.
- Server charts also pass the unchanged V5 static checks (`test_svg_server_chart.py`).
  V5 still demands its paint proof for them.

**Caveat:** five decks are a small sample. Generation time and cost vary run to run.

## Hardening and wider measurement (same day)

**Changes after review of the first prototype:**
- inside value labels that do not fit move every label outside;
- the legend wraps;
- category names wrap to two lines and shrink to 14 px before a chart is refused;
- chart text follows the page body size (14-20 px);
- the marker names a frozen renderer revision, and its output is pinned by digest tests;
- correction prompts show server charts as placeholders. A real deck had lost a
  native chart because the model copied the drawn bars;
- grounding binds each value to its category (and series), with the
  value-before-name direction decided per text. Replayed on 41 real bar/column
  declarations, it rejected none.

**Measurement.** Three prompts:
- p1: business review;
- p2: consumer survey with long category names and decimals;
- p3: library statistics with 만명 units and decimals.

Each prompt ran 2 decks per arm, with real `claude-sonnet-5-5` and V4 checks:

| | Author-drawn | Server-rendered |
|---|---|---|
| Decks | 6 | 6 |
| Declared charts in the final pages | 23 | 17 |
| Native charts in the PPTX | 16 (70%) | 16 (94%) |
| Declared charts lost to drawing errors | 3 (scale mismatch → picture) | 0 |
| Declared charts lost to page-level text overlap | 4 | 1 |
| Pages with a likely undeclared bar chart (heuristic) | 2 | 6 |
| Cost per deck | $0.98 | **$0.68 (-31%)** |
| Generation time per deck (mean) | 144 s | **113 s (-22%)** |

**What it shows:**
- Server charts remove the drawing errors and make almost every declared chart
  native.
- They do not raise the number of native charts per deck (16 each). Under the
  server rule the model more often draws a chart itself without a declaration,
  for example cost shares as an undeclared bar group, which then exports as a
  picture.
- Two rule edits (a smaller minimum box; "every chart is declared") did not
  change that on the page that showed it most. They were reverted, so the
  committed rule is the one measured.
- The sample is still small: 2 decks per cell.

## What it does not solve yet

- Line, area, pie, donut and stacked charts are still author-drawn.
- A chart lost because its page fell back to the last valid version.
- **V5 decks:** a server-drawn chart is correct by construction, so its paint
  proof proves nothing new. Skipping it for server charts needs an owner
  decision, because it changes V5's acceptance rule.
- Visual design: the drawing is plain (title, legend, axis, bars, labels). Its
  look was checked on rendered pages, not judged against author-drawn charts.

## Next steps (proposed)

1. **Detect undeclared charts:** a group of three or more bars or columns
   sharing a baseline, with value texts, outside any declaration. Make it a
   quality failure with a "declare it" repair hint, measured as advisory first.
   This is now the main loss in both arms.
2. Extend the renderer to stacked, line, area, pie and donut, each with the
   same "unchanged native checks pass" tests. Stacked share charts are where
   the model drew undeclared bars.
3. Decide whether V5 skips the paint proof for server-rendered charts.
4. Measure with 5+ decks per cell and the template eval judge. Then decide the
   default.
