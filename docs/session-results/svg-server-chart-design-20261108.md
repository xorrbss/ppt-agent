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

## Undeclared charts, stacks, units and the larger measurement (2026-11-09)

**Changes:**
- **Undeclared charts** (`undeclared_chart.py`). The detector looks for three
  or more bars or columns that share an edge and thickness, differ in length,
  carry numeric labels and sit outside any declaration.
  - Run over 264 stored real pages, it found 12, and all 12 were genuine
    undeclared data charts.
  - In server chart decks such a page gets the **layout** failure
    `undeclared_chart_drawn` (the data is still visible, so the page is never
    replaced by a fallback) and a declare-it repair hint.
- **Stacked and percent-stacked server charts.**
- **Series-first grounding.** The grounding check accepts either order with
  several series ("2025년 A사 61" as well as "A사 2025년 61"). It had rejected
  correct brand-awareness charts, which became fallback pages. Replayed on all
  237 stored bar/column declarations, it rejects none.
- **Value labels carry the title's unit** ("38억원", "22%", "88.1점"). A page
  must contain the source's number-plus-unit literals, and unit-less labels
  turned chart pages into fallback pages.
- **V4 decks only.** Admission enables server charts for V4 decks only,
  because the V5 unit grammar does not read units such as 만명 or 점.

**Measurement.** Same three prompts, real `claude-sonnet-5-5`:
- Server arm: the final code, 3 decks per prompt.
- Author-drawn arm: 5 decks per prompt. Its code path is unchanged by this
  work.

| | Author-drawn (15 decks) | Server-rendered (9 decks) |
|---|---|---|
| Declared charts in final pages | 55 | 31 |
| Native charts in the PPTX | 32 (58%) | 28 (**90%**) |
| Native charts per deck | 2.13 | **3.11 (+46%)** |
| Undeclared bar charts left per deck | 0.20 | **0.00** |
| Fallback pages per deck | 0.27 | **0.11** |
| Cost per deck | $0.85 | **$0.68 (-20%)** |
| Generation time per deck (mean) | 129 s | **115 s (-11%)** |

**What the intermediate runs showed** (15 server decks before the last two
fixes):
- 93-95% native share already.
- More fallback pages (7-8) than author-drawn (4). Two causes, both fixed above:
  - the series-first grounding miss;
  - unit-less value labels.

**Caveats:**
- The server arm with the final code is 3 decks per prompt.
- The arms were run at different times of the day.
- Visual quality was checked on rendered pages, not by the judge.

## What it does not solve yet

- Line, area, pie and donut charts were still author-drawn (added in renderer
  revision two, below).
- A chart lost because its page fell back to the last valid version.
- **V5 decks:** a server-drawn chart is correct by construction, so its paint
  proof proves nothing new. Skipping it for server charts needs an owner
  decision, because it changes V5's acceptance rule.
- Visual design: the drawing is plain (title, legend, axis, bars, labels). Its
  look was checked on rendered pages, not judged against author-drawn charts.

## Next steps (proposed)

1. Decide the default for V4 decks. If it is turned on, keep the knob as
   the way back.
2. ~~Extend the renderer to line, area, pie and donut~~ (done in revision two,
   below; not yet measured on real decks).
3. Decide V5: whether it skips the paint proof for server charts, and whether
   its unit grammar reads 만명/점-style units.
4. Have the template eval judge compare chart page design.

## Renderer revision two: line, area, pie and donut (2026-11-09)

A published revision never changes its output, so the new families are a new
revision, `svg-server-chart/v2`. Fresh `SVG_SERVER_CHARTS` admissions freeze it
(`SERVER_CHART_CURRENT`); decks admitted with v1 keep the v1 renderer and v1
author rule (`server_chart_rule` dispatches on the frozen revision).

**What v2 draws:**
- Bar and column charts: the same code as v1. A test asserts identical SVG and
  normalised JSON for v1 and v2 markers; the v1 digests are unchanged.
- Line and area: two or more categories, one or more series, values zero or
  above, a zero-based value axis drawn before the series, value labels above
  each point. Areas are filled at 30% opacity under a full-colour outline so
  overlapping series and their labels stay readable. Two value labels of one
  category closer than a line height drop every label.
- Several lines carry no value labels. The existing native appearance contract
  (`native_chart_visual.py`) proves value labels against a single line only,
  and a multi-line chart with labels was exported as a picture.
- Pie and donut: exactly one series, at most 8 positive slices, clockwise from
  twelve o'clock, one distinct colour per slice (declared colours kept when
  they are valid and distinct). Slice names sit beyond their slice. Values sit
  inside the slice (`center`); when one does not fit, every value moves under
  its slice name (`outsideEnd`). Slice names that would overprint each other
  (one large slice and several tiny ones) refuse the chart with
  `server_chart_slice_labels_collide`, and the author draws it.

**Refusal reasons added:** `server_chart_value_negative`,
`server_chart_trend_needs_categories`, `server_chart_polar_needs_one_series`,
`server_chart_too_many_slices`, `server_chart_slice_labels_collide`.

**Checks (`test_svg_server_chart.py`):**
- Seven cases (one line, two lines, one area, two areas, a pie, a pie with
  small slices, a donut) pass the unchanged V4 and V5 requirement checks against
  a `chart:` source literal and without one, and export as native charts.
- V2 output is pinned by digest tests like v1.

**Not done yet:**
- No measurement on real decks; the v2 author rule is unmeasured.
- V5 decks stay excluded (the unit grammar does not read 만명 or 점).
- The PPTX colour of an area series is the exporter's default: the
  existing `_series_colors` reads colours for bar, column and line only.
