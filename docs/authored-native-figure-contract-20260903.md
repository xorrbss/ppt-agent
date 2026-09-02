# Authored native figure contract (2026-09-03)

Markup contract between the archetype component library / authoring prompt
(Stage 2) and the authored-hybrid PPTX exporter (`servers/nextjs/lib/authored-hybrid`).
It states exactly which HTML the exporter turns into editable OOXML objects and
which HTML stays on the raster backplate, so figure slides of archetype
`kpi`, `comparison`, `before_after` and `timeline` export as native tables,
text boxes and shapes instead of screenshots.

The exporter side of this contract is implemented in `native-table-observed.ts`
(markup-driven tables), `browser-source.ts` (evidence), `font-policy.ts` /
`bundled-fonts-collector.ts` (Pretendard), and reported through
`quality.slides[n].fallbackElementIds`.

## 1. What the exporter promotes natively today

| Authored markup | OOXML result | Notes |
| --- | --- | --- |
| Block or inline text element (`p`, `h1`-`h6`, `span`, `div`, `li`, `td` text...) | `p:sp` text box with rich runs | Korean runs use the embedded Pretendard face when the stack names it, otherwise Noto Sans KR / Malgun Gothic. |
| Element with solid `background-color` and/or solid `border`, uniform pixel `border-radius`, or `border-radius:50%` | `p:sp` rectangle / rounded rectangle / ellipse (with fill and outline) | Gradient fill or `box-shadow` keeps the *shape paint* on the backplate (`gradient-backplate-only`, `shadow-backplate-only`); its text is still native. |
| Thin element (height or width <= a few px) with solid background | `p:sp` line | Used for timeline rails, dividers, bar baselines. |
| `<img>` with a PNG/JPEG/WebP data URL | `p:pic` (with crop for `object-fit: cover`) | External URLs are rejected by preflight. |
| Inline `<svg>` with <= 24 visible primitives and no `text`, `image`, `use`, `mask`, `pattern`, `foreignObject` | one `p:sp` freeform per primitive | Larger SVGs and SVGs with text become one raster island (`complex-diagram` / `svg-text`). |
| `<table data-native="table">` (section 3) | `p:graphicFrame` DrawingML table | New markup-driven path; no server payload required. |
| `<table>` without `data-native` | raster island (`complex-table`) | Unchanged legacy behaviour. |
| `<canvas>`, class or `data-ppt-role` containing `chart` | raster island (`complex-chart`) | Native charts are sealed off (section 5). |

## 2. Text-safety rules the exporter enforces (raster reasons)

An element is left on the backplate, and reported in
`fallbackElementIds`, when any of these apply to it, an ancestor, or a
descendant. Stage 2 CSS must not use them on elements that carry text or
figure data:

- `::before` / `::after` that paint (`pseudo-element`), `background-clip: text`,
  `text-shadow`, `filter`, `backdrop-filter`, `mix-blend-mode`, `mask`,
  `clip-path`, CSS `columns`, `writing-mode: vertical-*`.
- `transform` other than a pure rotation on the element itself
  (`complex-transform`, `transformed-ancestor`), `opacity` other than 1 on a
  table/chart, animations.
- `overflow: hidden` / `clip` that actually clips content (`overflow-clipped`).
- Percentage or asymmetric `border-radius` on a text container (only uniform
  px radii and exact circles are native).
- Text boxes overlapping other text boxes, or text painted above a raster
  island that cannot itself be native (`z-band-unsafe`).
- Class tokens `chart`, `diagram`, `flowchart`, `timeline`, `mindmap` on a
  wrapper, or `visual` / `scene` on a large (>= 240x180 px, >= 12 descendants)
  wrapper with an `aria-label`: these force a **raster island** for the whole
  wrapper. Use archetype class names such as `arch-timeline`, `tl-rail`,
  `kpi-row` that do not match those tokens as whole words (the matcher splits
  on whitespace, `_` and `-`, so `timeline-rail` *does* match; `tlrail` or
  `roadmap-rail` does not).

Decoration is free on sibling elements that carry no text.

## 3. Native table contract (`data-native="table"`)

```html
<table class="cmp-table" data-native="table">
  <thead>
    <tr><th>지표</th><th>Before</th><th>After</th></tr>
  </thead>
  <tbody>
    <tr><td>처리 시간</td><td>48h</td><td class="cmp-up">6h<br>(-87%)</td></tr>
    <tr><td>오류율</td><td>4.2%</td><td class="cmp-up">0.8%</td></tr>
  </tbody>
</table>
```

Guarantees a native DrawingML table (`nativeTableElements` +1 for the slide)
when **all** of the following hold. Each rule maps to the rejection reason the
exporter records in `fallbackElements[].reasons`.

| Rule | Reason when violated |
| --- | --- |
| The `<table>` carries `data-native="table"`. Untagged tables keep the legacy raster behaviour silently. | (none, stays `complex-table`) |
| Rectangular grid: every row has the same number of cells, no `colspan` / `rowspan`, 1-100 rows, 1-20 columns. | `unsupported-native-table` |
| Cells contain text only. No `img`, `svg`, `canvas`, `video`, `picture`, `iframe` anywhere inside the table. Icons belong beside the table, not in it. | `unsupported-native-table` |
| One text style per cell: the cell's computed `color` (opaque), `font-family`, `font-size`, `font-weight` are what the whole cell gets. Inline `<strong>` inside a cell does not survive; make the whole cell bold via `th` or a cell class. `font-style: italic` and `text-decoration` are not supported. | `unsupported-native-table` |
| Paint is solid only: `background-color` on the table, rows or cells must be fully opaque or fully transparent; no `background-image` / gradients; borders on cells must be `solid` (or none) with an opaque color and width <= 20 pt (26 px). Dashed / dotted / double borders, or translucent fills, reject the table. | `unsupported-native-table` |
| No rotation, `opacity` 1, and none of the raster reasons of section 2 on the table or its ancestors (filter, transform, overflow clip, ...). | `unsupported-table-css` |
| Declared (`data-qid`) slides: the table's QID must be a leaf. Put `data-qid` on the `<table>` only, never on `tr` / `td` / `th`. | `table-has-declared-children` / `unsupported-native-table` |

What is carried into OOXML, per cell: text (line breaks from `<br>` become
paragraphs), fill (cell, else row/table root `background-color`), text color,
bold (weight >= 600), font family, font size, `text-align`
(left/center/right/justify), `vertical-align` (top/middle/bottom), and per-side
solid borders. Column widths and row heights follow the rendered geometry.
Header rows are not special: style `th` like any bold cell.

Cell font families map like text runs: `Pretendard` and `Noto Sans KR` stay
when embedded, unknown web fonts become their Office substitute
(`Inter` -> `Aptos`, `Noto Serif KR` -> `Batang`, ...).

In compatibility (non-`data-qid`) slides the admitted table owns its cell text
candidates; they are neither emitted as separate text boxes nor counted as
raster fallbacks.

## 4. Archetype recipes

The figure data of these archetypes must be plain HTML text, native shapes or
a `data-native` table. Numbers must never be baked into SVG, canvas or an
image.

### 4.1 `kpi`

Preferred: cards.

```html
<section class="kpi-row">
  <div class="kpi-card">                      <!-- solid fill + border: native shape -->
    <span class="kpi-label">월간 활성 사용자</span>
    <strong class="kpi-value" data-ppt-role="numeric">1.2M</strong>
    <span class="kpi-delta kpi-delta-up">+18%</span>  <!-- solid pill: native shape + text -->
  </div>
  ...
</section>
```

- Each card is a `div` with solid `background-color`, optional solid border,
  uniform px radius. No `box-shadow` on the card if the card fill must be
  editable (a shadow leaves the fill on the backplate; the text stays native).
- Value, label and delta are separate text elements. `data-ppt-role="numeric"`
  on the value marks it as a number (font role hint); `caption` marks the label.
- The delta pill is a sibling `span` with its own solid fill, not a
  `::before` badge.

Alternative for dense grids: a 2-row `<table data-native="table">`
(values in row 1 with a bold cell class, labels in row 2).

### 4.2 `comparison`

Use `<table data-native="table">` with the compared options as columns and the
criteria as rows (section 3). Emphasis: a cell class that sets an opaque
`background-color` and/or bold weight (for example `.cmp-win`).
Do not use check-mark icons inside cells; use text (`O` / `X`, `우수`, ...).

Card-based comparison (two columns of cards) also exports natively as long as
the cards follow the `kpi` card rules.

### 4.3 `before_after`

Preferred: a three- or four-column native table `항목 | Before | After | 변화`.
The delta column is text (`-87%`, `+2.4pt`); color it via a cell class.

Card form: two cards (`.ba-before`, `.ba-after`) with a connector between
them. Safe connectors:

- a text element containing an arrow glyph (`→`, `⟶`) sized like a heading, or
- a thin `div` (height 2-4 px, solid background) as the shaft plus a small
  solid `div` rotated 45 deg as the head (rotation on a shape without text is
  native), or
- a small inline `<svg>` with one `path` / `polygon` (<= 24 primitives, no text).

Do not draw the arrow with `::after` on a text container and do not use CSS
border-triangle tricks on elements that carry text.

### 4.4 `timeline`

```html
<section class="rm-timeline">          <!-- not "timeline": that token forces raster -->
  <div class="rm-rail"></div>           <!-- thin solid div: native line -->
  <ol class="rm-steps">
    <li class="rm-step">
      <span class="rm-dot"></span>      <!-- border-radius:50%, solid fill: native ellipse -->
      <span class="rm-date" data-ppt-role="caption">2026 Q1</span>
      <strong class="rm-title">파일럿</strong>
      <span class="rm-desc">2개 사업부</span>
    </li>
    ...
  </ol>
</section>
```

- Rail and dots are paint-only elements; labels are separate text elements
  positioned beside them (absolute or grid positioning is fine, transforms
  are not).
- Milestone metrics (dates x values) may also be a `data-native` table under
  the rail.
- Never name the wrapper class `timeline`, `roadmap-timeline`, or anything
  containing the whole-word tokens listed in section 2.

### 4.5 Bars without charts

A bar (`kpi` / `comparison` bar variants) is a `div` with solid fill and an
explicit width (class-driven `--w` custom property or a width utility class);
the value is a sibling text element, not text inside a pseudo-element. Such
bars export as editable rectangles plus text boxes. Stacked bars are several
adjacent solid `div`s. Do not put the bars inside a wrapper whose class
contains `chart`.

## 5. Native charts (reserved)

`native-chart.ts` can serialize `c:chart` parts for bar / column / line / area /
pie / donut from a declared `authored-native-chart/v1` payload, but admission
is hard-disabled (`nativeChartAdmissionEnabled()` returns `false`) because the
PowerPoint Desktop reopen qualification currently fails in `chart.dll`. Until a
fresh first-open / save / reopen seal passes, any element marked as a chart
(`data-ppt-role="chart"`, `class~=chart`, `<canvas>`) is a raster island and is
reported in `fallbackElementIds` with reason `complex-chart` /
`unsupported-native-chart`.

Consequently Stage 2 must **not** render `kpi` / `comparison` / `before_after`
/ `timeline` numbers as charts. Use the recipes in section 4; a markup-driven
chart attribute will be added to this contract only after the chart seal
passes. The reserved payload shape is the `AuthoredNativeChartPayloadV1`
interface in `native-chart.ts` (`chartType`, `categories`, `series[]{name,
values, color}`, `title`, `legend`, axes).

## 6. Fonts

- Name `Pretendard` in the deck CSS (`font-family: "Pretendard", "Malgun Gothic", sans-serif`).
  The exporter injects the bundled regular/bold WOFF2 faces into the offline
  Chrome render and embeds the matching TrueType faces into the PPTX, so the
  backplate, the measured line breaks and the editable text share one face.
- Use weights 400 and 700 (or 600+ for bold). 500 renders as regular and
  600-900 as bold in both the browser and PowerPoint by design.
- Noto Sans KR remains the default Hangul face for stacks that do not name
  Pretendard.

## 7. Export feedback for Stage 4 / Stage 6

Every hybrid export writes `<file>.pptx.quality.json` (also returned as
`quality` in the export result). Per slide:

```json
{
  "slideNumber": 5,
  "editable": true,
  "nativeTextElements": 9,
  "nativeShapeElements": 4,
  "nativeTableElements": 1,
  "rasterFallbackElements": 1,
  "fallbackElementIds": ["fig.hero-scene"],
  "fallbackElements": [
    { "elementId": "fig.hero-scene", "domPath": "body > section:nth-of-type(2) > div:nth-of-type(1)", "candidateKind": "complex", "reasons": ["complex-diagram"] }
  ],
  "fallbackReasons": ["complex-diagram"]
}
```

- `fallbackElementIds` is the sorted, de-duplicated list of element ids
  (declared `data-qid`, or the compatibility ordinal id) left on the raster
  backplate; `rasterFallbackElements` is the count the coverage manifest is
  validated against.
- Backplate-only paint (`gradient-backplate-only`, `shadow-backplate-only`) is
  listed with the element that keeps its text native; Stage 4 may ignore those
  two reasons when applying the "more than 2 fallbacks per slide" rule.
- The same ids appear in `coverageManifest.slides[n]` aggregates and in
  `qidCoverage` for declared slides.
