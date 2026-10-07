# Canvas residual defects: root causes and generic fixes (2026-09-30)

Design only. Baseline `origin/main` 040b8c397 (`serve/main-20260902`). No code was changed for this
document; every root cause below was verified by reading the named lines and, for item 1, by an
offline reproduction in the same headless Chrome binary the renderer uses.

Four residual defects from the canvas-expansion and svg programmes are covered:

| # | Symptom | Root cause (one line) | Recommendation |
|---|---|---|---|
| 1 | 97 of 99 svg ink fallbacks are runs containing U+00B7 | Chromium canvas `measureText` caches shaped words first-wins; U+00B7 shapes to a different glyph alone (KOR `locl`, 231 u) than inside a Hangul run (default, 529 u), so the probe's advance self-check fails, and which runs fail depends on element order | Per-cluster identity with two deterministic canvas witnesses, per-cluster cell fallback, no whole-run canvas shaping (`glyph-ink/v2`) |
| 2 | 409 `document_canvas_client_upgrade_required` on decks with a `documentCanvas` | Gate is correct; every in-repo HTTP client of the gated endpoints already sends the header except the MCP server, and nothing pins that | Keep the 409 as the single gate; centralize the header in `getHeader()` and the MCP request hook; add a contract test on both runtimes |
| 3 | Raster-redesign decks blocked with `svg_*_evidence_stale` after reorder | `renumber_redesign_page` rewrites `svg_content` and `svgReceipt.source_digest` but not `svgReceipt.quality.source_digest`, which the export evidence compares | Enforce "persisted svg bytes are the measured bytes" at one setter; the raster branch re-measures through the same step as every other mutation |
| 4 | Registered PPTX design packs never generate (`source_native_authored_pack_required`) | `_source_json` accepts only `source_kind == "authored"`; PPTX packs pass admission and the pin lookup and fail deep in `execute()`; no UI registers packs at all | (a) Remove PPTX pack ingestion; keep authored packs |

Sections 1 to 4 give the evidence, the fix, files and LOC, tests and acceptance. Section 5 lists open
decisions, section 6 the self-score.

---

## 1. Middle-dot fallback in svg `text_overlap` ink measurement

### 1.1 Where the measurement lives

- Probe: `servers/fastapi/utils/slide_capture_text_ink.py` (`TEXT_INK_JS`, 169 lines). `inkBox`
  (l.79-152) measures one owned text run: `measureText(content)` for the whole run (l.108) for the
  font cell self-check, then per grapheme cluster (l.119-142): the cluster alone (l.126) for the ink
  extents, and the in-context advance `width(seg + next) - width(next)` (l.128-130) which must equal
  the laid-out advance `start(next) - start(seg)` within 1 px (l.137). Whitespace clusters are
  skipped (l.121). Any failing cluster returns `null` for the **whole run**, and the consumer then
  compares the run's full line box.
- Decision: `servers/fastapi/services/svg_text_overlap.py` (83 lines), `comparison_fragments`
  l.34-44 chooses ink or line box per fragment.
- Recorded outcome (design doc `svg-text-overlap-false-positives-design-20260929.md` §9): 753 of 852
  fragments on ink; 97 of the 99 fallbacks contain U+00B7.

### 1.2 Reproduction (scratch only, same Chrome binary)

Font: the bundled `servers/nextjs/public/fonts/noto-sans-kr/NotoSansKR-Variable.ttf` (the
`svg-default-bundled-fonts/v1` profile the wrapper injects). Chrome:
`find_chrome()` → `ms-playwright/chromium_headless_shell-1234/chrome-headless-shell.exe`, launched with
the flags of `utils/slide_capture.py:1663-1685` (no `--lang`; the page has no `lang` attribute, so
Blink's default locale is the machine UI locale, `navigator.language === "ko"`).

**Font facts (fontTools, upem 1000).** U+00B7 maps to `periodcentered`, advance 529, ink y 311-400.
GSUB `locl` lookup 39, registered for language `KOR` under every script (`latn`, `hang`, `DFLT`
excluded), substitutes it with `glyph22604`, advance 231, ink y 337-425. `fwid` (lookup 34) maps it to
`uni2027` (1000). GPOS `kern` lookup 6 also covers it.

**SVG layout of U+00B7 depends on its run context** (100 px, weight 700, per-char
`getEndPositionOfChar - getStartPositionOfChar`):

| text | context of `·` | laid-out advance |
|---|---|---|
| `A · B` (no lang, locale ko) | Latin run, KOR `locl` applies | 32.6 (narrow glyph) |
| `고 · 스 A` | after Hangul: ICU script extensions of U+00B7 exclude Hangul, so the run breaks and `·` is shaped as DFLT | 59.2 (default glyph) |
| `예지보전·품질` | same, between Hangul | 59.2 |
| `A · B` with `xml:lang="en"` or `font-feature-settings:'locl' 0` | no KOR `locl` | 59.2 |
| `·` alone in its own `<text>` | isolated | 32.6 |

**Canvas `measureText` for the same substrings is order-dependent.** Blink shapes canvas text
through a word cache keyed by the word's text and the font; the first shaping of a word is reused
for every later string containing that word, whatever the context. Verified two ways:

1. Same page, same context, two measurement orders: with whole-string measurements first,
   `measureText("·")` returned 59.2; with `"·"` measured first it returned 32.6 (and then
   `"고 · 스 A"` measured 348.8, i.e. with the narrow dot, while the SVG laid it out with the wide one).
2. The verbatim probe (`TEXT_INK_JS` with `px`/`ownedTextNodes`/`fontEvidence` stubbed) on
   `예지보전·품질` and `보고 · 스마트` at 51 px: **all clusters within 0.01 px, ink returned** — until a single
   `measureText("·")` is issued before the probe runs, after which both runs return `null`
   (`전`: layout 46.92, canvas `width("전·") - width("·")` = 77.11 − 16.63 = 60.48).

**The production probe reproduces exactly this order dependence** (`render_svg_with_metrics`, 51 px,
weight 700, one `<text>` per row):

| page | result |
|---|---|
| `예지보전·품질 조기경보` alone | `null` (line box) |
| `보고 · 스마트` alone | ink |
| `보고 · 스마트`, then `예지보전·품질 조기경보` | ink, ink |
| `예지보전·품질 조기경보`, then `보고 · 스마트` | `null`, `null` |
| `A·B` | ink |

The same run is decided on ink or on the line box depending on which other runs the page happens to
contain and in which DOM order. That is the mechanism behind "97 of 99 fallbacks contain U+00B7": the
probe's own `measureText(segment)` (l.126) and `measureText(content)` (l.108) are what seed the cache,
in element order, with whichever glyph variant the first string happened to select.

### 1.3 Root cause

`utils/slide_capture_text_ink.py:108,126,128-130,137`. The self-check assumes canvas shaping of a
substring is a function of the substring and the font. In Blink it is a function of the substring,
the font **and the first string that ever contained that word on the page**, and for characters with
language- or script-dependent substitutions (U+00B7 via `locl`/KOR; also any `calt`/`liga`/`locl`
alternate) the isolated and in-run shapes differ by 0.3 em. Two consequences:

- the advance self-check fails for the neighbour of `·` (`전` above) or for `·` itself, and the whole
  run drops to its line box (the false-positive geometry the ink probe was built to avoid);
- the verdict is not a deterministic function of the page: the same svg bytes can produce different
  `overlap_basis` records, which is exactly what an evidence digest (item 3) must not depend on.

No canvas API shapes a substring the way the SVG run shapes it (SVG shapes the run as one HarfBuzz
buffer; canvas shapes per word and caches), so "make the canvas advance faithful" is not achievable in
general. Whitespace was never checked (l.121), which is why the visible symptom is only `·` although
the Hangul-run space is also laid out 28.0 vs canvas 22.7 at 100 px (repro table, `단계별 도입 계획`).

### 1.4 Generic fix: `glyph-ink/v2`

Single enforcement point: `inkBox` in `utils/slide_capture_text_ink.py`. Principle: **horizontal
geometry comes from the SVG layout only; the canvas is a witness for the vertical ink of one cluster,
and it is trusted per cluster, never per run.**

1. **Remove whole-run canvas shaping.** `measureText(content)` (l.108) exists only for
   `fontBoundingBoxAscent/Descent`, which are font-level; take them from `measureText(" ")` or the
   first cluster. This removes the largest cache seed.
2. **Per-cluster identity with two deterministic witnesses.** For cluster `c` with laid-out advance
   `a = start(next) − start(c)` (or end-of-char for the last cluster):
   - witness W1 (isolated): `measureText(c)` with `fontKerning = 'none'`; matches if
     `|W1.width·k − a| ≤ 1 px`;
   - witness W2 (in-context): `measureText(c + next).width − measureText(next).width` with
     `fontKerning` as today; matches if within 1 px.
   Every measured string is either one cluster or one cluster pair; the probe never measures longer
   strings, so each cache entry is seeded by the probe itself in a fixed way (single clusters are always
   shaped alone; pairs are always shaped as pairs). W1 catches plain glyphs (all Hangul, unkerned Latin,
   `전` next to `·`), W2 catches kerned pairs and `·` between Hangul (`width("·품") − width("품")` = 30.19 =
   layout). A match is accepted from either witness; acceptance still requires a canvas shaping that
   reproduced the laid-out advance, the same evidence the current check demands.
3. **Cluster box.** Horizontal: the laid-out cell `[start.x, end.x]`, widened by the matching witness's
   overhang (`max(0, actualBoundingBoxLeft)` on the left, `max(0, actualBoundingBoxRight − width)` on
   the right) so the box can only grow. Vertical: the matching witness's `actualBoundingBoxAscent/
   Descent`; for W2 that is the union with `next`, which is a safe over-approximation (the run box is
   the union of cluster boxes anyway).
4. **Per-cluster cell fallback instead of per-run fallback.** A cluster no witness matches keeps its
   laid-out cell at full line-box height (`[start.x, end.x] × [baseline − fontAscent, baseline +
   fontDescent]`). The run's ink rect is the union of ink clusters and cell clusters. For
   `보고 · 스마트` (standalone `·` between spaces: W1 = W2 = narrow 16.63 vs layout 30.19) only the 30 px
   column of the dot stays full height; the rest of the run is ink. This is still fail-closed: the cell
   is the geometry the line-box verdict uses today, restricted to the one cluster that could not be
   witnessed.
5. **Record it.** The per-fragment ink entry gains `cellClusters: n`; `svg_text_overlap.py` reports the
   fragment basis as `ink` (n = 0) or `ink_cell` (n > 0) and counts both in `overlap_basis.fragments`.
   `TEXT_INK_PROBE_VERSION = "glyph-ink/v2"`. `SVG_RENDERER_VERSION` stays (same reasoning as the
   2026-09-29 decision: bumping it makes every existing svg deck `svg_*_evidence_missing`).
6. **Keep every other refusal** (multi-rect runs, CTM, `textLength`, decorations, unloaded faces, char
   count mismatch, baseline mismatch, stroke padding): unchanged.

Why not the alternatives:

- *`getExtentOfChar` for horizontal, canvas for vertical only, no self-check*: the two `·` variants
  differ vertically by 25-37 units (0.03 em); at body sizes that is under 1 px, but the design would
  rest on a property of one font. The witness rule keeps a checkable guarantee.
- *Pixel-ink oracle in the probe* (render each run alone to a canvas and read the mask): exact, and the
  right **test** oracle (§7.2 of the 2026-09-29 doc, still not implemented), but fonts inside an SVG
  image must be re-embedded per run and every run costs a full raster; not justified when the witness
  rule leaves a 30 px column as the residual.
- *Pin `lang`/`locl`*: setting `xml:lang="en"` or `'locl' 0` on the wrapper would make SVG and canvas
  agree for this font, but it changes what the page paints (KOR forms are the intended rendering for
  Korean decks) and fixes one substitution, not the class.

### 1.5 Files and LOC

| file | change | LOC |
|---|---|---|
| `servers/fastapi/utils/slide_capture_text_ink.py` | `inkBox` rewrite per 1.4 (steps 1-4), version bump, docstring | ~+45 / −25 |
| `servers/fastapi/services/svg_text_overlap.py` | `ink_cell` basis, `cellClusters` count | ~+12 |
| `servers/fastapi/tests/unit/test_svg_text_overlap_browser.py` | order-pair test, standalone-dot test, `cellClusters` assertions | ~+45 |
| `servers/fastapi/tests/unit/test_svg_text_overlap.py` | basis counting for `ink_cell` | ~+15 |
| `servers/fastapi/tests/fixtures/svg_text_overlap/` | `syn_ORD_a_then_b.svg`, `syn_ORD_b_then_a.svg`, `syn_DOT_standalone.svg` | 3 files |
| `docs/svg-text-overlap-false-positives-design-20260929.md` | §9 addendum pointing here | ~+6 |

### 1.6 Tests and acceptance

- **Order independence (Chrome):** the two `syn_ORD_*` pages contain the same two runs in opposite DOM
  order; both must return ink for both runs with `cellClusters == 0`. Then the pairwise assertion: for
  every fixture, the set of `(qid, cellClusters, rect)` is identical across two consecutive
  `render_svg_with_metrics` calls and across a run of the page with its `<text>` elements reversed.
- **Standalone dot:** `보고 · 스마트` yields `cellClusters == 1` and the rect's height equals the line box
  only over the dot's `[start.x, end.x]` (assert via a second, ink-only run built without the dot).
- **Existing suite:** the 12 `fp_*` pages still pass with `overlap_basis.pairs == []`; TP1-TP6 still
  `text_overlap` on basis `ink`; N1-N4 pass; every fallback-condition case still returns the line box.
- **Corpus number:** re-run the round-1 offline re-measure (36 pages); acceptance is **0 line-box
  fragments caused by U+00B7** and `cellClusters > 0` on at most the standalone-dot runs, with no new
  `text_overlap` pair.
- **Pixel-truth oracle** (§7.2 of the 2026-09-29 doc) for the `·` fixtures: rule verdict equals
  ink-mask intersection. Recommended in the same change; it is the only test that does not trust the
  probe's own geometry.
- `uv run ruff check .`; live check on one regenerated 16:9 and one 1:1 deck.

---

## 2. 409 `document_canvas_client_upgrade_required`

### 2.1 The gate

`servers/fastapi/services/document_canvas.py:102-115`, `require_canvas_capability(theme,
supported_versions)`: returns silently when the theme has no `documentCanvas`; 422
`document_canvas_contract_invalid` when it does not parse; 409 when the comma-split
`X-Document-Canvas-Contracts` header does not contain `document-canvas/v1`
(`models/document_canvas.py:19`). The trigger is the presence of `documentCanvas`, which
`services/authored_canvas.py:191-230` attaches whenever the request carries `page_size` (A4 and the
other document sizes, plus every raster-redesign deck via `document_canvas_from_source_slide_size`);
a 16:9 request without `page_size` stays on the legacy `authored_vnext.canvas` and is never gated.
So "non-16:9" is the right description of when it fires.

Call sites: `api/v1/ppt/endpoints/presentation_crud.py:239-244` (`GET /{id}`), `:256-261`
(`GET /{id}/export-projection`), `presentation_versions.py:61-80` (two), `presentation_share.py:413-424`
(`GET /public/{token}`). `GET /presentation/all` (`presentation_crud.py:113-233`) is **not** gated.
The gate arrived with `8700b52be` (raster redesign into editable A4 slides). Its purpose is the read
path: a reader that ignores `documentCanvas` lays an A4 deck out as 16:9 and exports it wrong.

### 2.2 Client inventory

| client | file:line | endpoint | header |
|---|---|---|---|
| `DashboardApi.getPresentation` (the only browser reader; used by `usePresentationData.ts:30`, `OutlinePage.tsx:173/193/254`, `useOutlineStreaming.ts:175`, `PdfMakerPage.tsx:261`) | `servers/nextjs/app/(presentation-generator)/services/api/dashboard.ts:108-123` | `GET /{id}` | yes, literal at l.116 next to `getHeader()` |
| `DashboardApi.getPresentations` (`DashboardPage.tsx:77`, `DevaxWorkspaceShell.tsx:58`, `UploadDesignGallery.tsx:27`) | `dashboard.ts:72-84`, `dashboard-list-query.ts:42` | `GET /all` | not required |
| public view | `servers/nextjs/app/p/[token]/PublicPresentationView.tsx:26-29` | `GET /public/{token}` | yes, literal |
| export route | `servers/nextjs/lib/export-presentation-route.ts:168-175` | `GET /{id}/export-projection` | yes, literal (asserted by `app/api/export-presentation/route.test.ts:204`) |
| versions / restore | `services/api/presentation-generation.ts:544-549`, `572-578` | `/{id}/versions`, `/restore` | yes, literal |
| share create/get/delete | `presentation-generation.ts:597/614/631`, `dashboard.ts:169/190/210` | `/{id}/share` | not gated |
| authored emission / editor | `lib/authored-hybrid/active-emission.ts:715`, `lib/authored-editor/client.ts:80` | not gated | n/a |
| `scripts/canvas_bench_live.py` | `servers/fastapi/scripts/canvas_bench_live.py:33,108-109` | `GET /{id}` | yes, own literal |
| `raster_deck_evidence.py` | `servers/fastapi/scripts/raster_redesign_eval/raster_deck_evidence.py:24-25` | `GET /{id}` | yes, own literal |
| root `scripts/` (`ppt-agent.mjs`, `lib/ppt-agent-client.mjs:115/191/209`, benchmarks) | | only `POST /generate`, `/generate/async`, `GET /status/{task}`, workspace artifacts | no reader of `GET /{id}`; nothing to send |
| `servers/nextjs/scripts/public-hybrid-export-worker.mjs:165-167` | | reads the canvas from the job payload | n/a |
| workspace operation handlers, export workers (`services/`, `utils/`) | | read the DB, not HTTP | n/a |
| **MCP server** | `servers/fastapi/mcp_server.py:15-21,53` | every OpenAPI operation, including `GET /{id}` | **no**: `FastMCP.from_openapi` exposes the header as an optional tool argument; `attach_request_auth_headers` forwards only `authorization` and `cookie` |

Tests asserting the header on a client: only `route.test.ts:204` (export projection). Nothing asserts
it on `getPresentation`, the public view, the versions calls, the two Python scripts or the MCP proxy.
The Cypress specs (`PresentationPage.cy.tsx`, `workspaceChatFirstRoute.cy.ts`,
`canonicalConversationRoute.cy.ts`) intercept `GET /{id}` without checking headers.

### 2.3 Decision: keep the 409, centralize the header, prove it with a contract test

Relaxing the gate for read-only paths is rejected: the read path is the only thing it protects. A
reader that has not negotiated `document-canvas/v1` is exactly the reader that will render an A4 deck
as 1280×720 and hand it to an exporter; the list endpoint is already ungated because it returns no
geometry. Every browser client already complies, so relaxing would only serve out-of-repo readers
(curl, ad-hoc harnesses, the MCP proxy), and for those the 409 with a named code is the correct
answer: it is the contract telling them to upgrade.

What is wrong today is not the gate but that compliance is five copies of a string literal with no
test, plus one in-repo client (MCP) that never sends it. Fix at one point per runtime:

1. **Next.js:** `services/api/header.ts` becomes the single owner:
   `export const DOCUMENT_CANVAS_CONTRACTS = "document-canvas/v1"` and `getHeader()` returns it as
   `X-Document-Canvas-Contracts` on every request (harmless on ungated endpoints; the server ignores it).
   `dashboard.ts:116`, `presentation-generation.ts:549/578`, `export-presentation-route.ts:168` and
   `PublicPresentationView.tsx:29` drop their literals and use `getHeader()` (the public view currently
   sends the header alone; it keeps that shape via a `getPublicHeader()` in the same file). The value is
   shared with `lib/document-canvas.ts` so the client's declared contract and its parser cannot drift.
2. **MCP:** `mcp_server.py:attach_request_auth_headers` sets `X-Document-Canvas-Contracts` on every
   proxied request (3 lines; the MCP server is a full client of the deck model through the projection
   it returns, so it must negotiate, not be exempted).
3. **Python scripts:** `canvas_bench_live.py:33` and `raster_deck_evidence.py:24` import
   `DOCUMENT_CANVAS_CONTRACT_VERSION` from `models.document_canvas` instead of restating it.
4. **Server side:** export `DOCUMENT_CANVAS_CONTRACTS_HEADER = "X-Document-Canvas-Contracts"` from
   `models/document_canvas.py` and use it in the four `Header(alias=...)` declarations, so the name has
   one owner too.

**Contract test that fails when a new in-repo client forgets the header.**

- `servers/nextjs/tests/document-canvas-header-contract.test.mjs` (node:test, discovered by
  `run-ci-node-tests.mjs`): walks `app/`, `lib/`, `components/`, `scripts/` (not `node_modules`,
  `.next-build`) and asserts (i) the literal `"X-Document-Canvas-Contracts"` appears only in
  `services/api/header.ts`; (ii) every `fetch(` whose URL literal or template contains
  `/api/v1/ppt/presentation/` and is not one of the listed ungated paths (`/all`, `/share`,
  `/generate`, `/status`, `/authored-emission`, `/canonical`) passes `headers: getHeader(` or
  `getPublicHeader(` within the same call expression. A new reader written with a raw header object
  fails (i); one written without headers fails (ii).
- `servers/fastapi/tests/unit/test_document_canvas_header_clients.py`: (i) `mcp_server.
  attach_request_auth_headers` adds the header to an `httpx.Request` (unit, no network); (ii) every
  `scripts/**/*.py` that contains `/presentation/` and `requests.get|httpx.get|urlopen` imports
  `DOCUMENT_CANVAS_CONTRACT_VERSION`; (iii) an HTTP-level test through the FastAPI app: `GET /{id}` on
  a deck with `documentCanvas` returns 409 without the header and 200 with it (today only the pure
  function is tested, `tests/unit/test_document_canvas.py:173-176`).

### 2.4 Files and LOC

| file | LOC |
|---|---|
| `servers/nextjs/app/(presentation-generator)/services/api/header.ts` | +8 |
| `dashboard.ts`, `presentation-generation.ts` (×2), `lib/export-presentation-route.ts`, `app/p/[token]/PublicPresentationView.tsx` | −5 literals, +5 helper calls |
| `servers/nextjs/lib/document-canvas.ts` | +2 (re-export the constant) |
| `servers/nextjs/tests/document-canvas-header-contract.test.mjs` | +70 |
| `servers/fastapi/mcp_server.py` | +3 |
| `servers/fastapi/models/document_canvas.py` | +1 |
| `presentation_crud.py`, `presentation_versions.py`, `presentation_share.py` | 4 alias literals → constant |
| `scripts/canvas_bench_live.py`, `scripts/raster_redesign_eval/raster_deck_evidence.py` | 2 literals → import |
| `servers/fastapi/tests/unit/test_document_canvas_header_clients.py` | +60 |

Acceptance: the new node test and pytest file pass; `route.test.ts:204` still passes; a deliberate
raw-header client added in a scratch branch makes the node test fail; `GET /{id}` on an A4 deck through
the MCP proxy returns 200 in a live check.

---

## 3. Raster-redesign decks blocked as stale by export page evidence

### 3.1 Evidence and the relabel path

- Checker: `services/svg_export_page_evidence.py:77-97` (`_page_domain`). `_quality` (l.53-61)
  requires `svgReceipt.quality.{source_digest, renderer_version, contract_version,
  measurement_complete, geometry_passed, passed}` with the current versions; l.83-85 compares
  `svg_contract.source_digest(slide.svg_content)` (sha256 of the raw bytes, `svg_contract.py:80`) with
  `quality["source_digest"]` and reports `svg_{authoring|geometry_font}_evidence_stale` on mismatch.
  Called from `services/svg_export_quality.py:108-109`.
- Writer of `quality.source_digest`: `services/svg_renderer.py:335` inside `render_svg_with_metrics`,
  i.e. the digest of the string that was actually measured. Generation persists it via
  `generation_core/drivers/svg/render.py:117,185` → `publish.py:30-43,135`. Two other writers re-measure
  and replace `quality` in full: `svg_page_remake.py:115,127` and the generic mutation path
  `svg_document_mutations.py:196-210` (`refresh_svg_page_numbers`: `finalize_svg` → rebind →
  `render_svg_with_metrics` → `slide.svg_content = final` and `svgReceipt.quality = capture.quality`).
- Relabel: `services/svg_raster_renumber.py:34-40` (`relabel_page_number`, footer digits only, `zfill(2)`)
  called by `renumber_redesign_page` (l.69-99). Lines 88-99 update `svgReceipt.source_digest`,
  `rasterDisplayPageNumber`, `pageNumberRelabel={from,to,measuredSourceDigest}` and assign
  `slide.svg_content = svg` — **without touching `svgReceipt.quality`**. The module docstring (l.1-15)
  states the intent: "nothing is re-measured or materialized".
- Bypass: `svg_document_mutations.py:167-176` — the raster branch calls `renumber_redesign_page` and
  `continue`s past the generic re-measure at l.196-210.
- Timing: the footer is drawn before measurement (`source_content/raster_redesign_layout.py:77-84`,
  `page_number = index + 1`), so a deck exports fine until the first reorder, insert or delete moves a
  raster page (`display != current`). After that, `quality.source_digest` describes the pre-relabel
  bytes for that page forever, and both page domains fail.
- Branch `raster/quality-20260929` (2 commits ahead of HEAD) touches none of these files.
- Why the tests are green: `tests/unit/test_svg_raster_renumber.py:35` uses a receipt fixture
  `{"source_digest": "old", ...}` with no `quality` block, so the stale check has nothing to compare;
  `test_svg_export_page_evidence.py:152-157` proves the checker detects a changed page but no test runs
  a relabel through it. `pageNumberRelabel` is written at `svg_raster_renumber.py:95` and read nowhere.

### 3.2 Root cause

Two writers of `slide.svg_content` exist after measurement, and only one of them re-establishes the
invariant the export checker enforces ("the persisted bytes are the measured bytes"). The raster
branch was fenced off from the generic path for reasons unrelated to measurement (crop slot ids,
source manifest, the tenant-context fence in `test_workspace_slide_order_raster_fence.py`), and the
`pageNumberRelabel` record was meant to carry the proof that the change is footer-only, but nothing
consumes it. This is a symptom of the invariant being enforced by convention at each call site.

### 3.3 Options

- **A. Re-measure at the single point where page bytes are finalized (recommended).** Make the
  invariant structural: one setter, `publish_measured_page(slide, *, svg, quality, png)` in
  `services/svg_contract.py` (or a new `services/svg_page_publication.py`), is the only code in
  `services/` that assigns `slide.svg_content`, and it raises `svg_page_evidence_mismatch` unless
  `quality["source_digest"] == source_digest(svg)`. All five writers route through it (`publish.py:135`,
  `terminal.py:92-107`, `svg_page_remake.py:127`, `svg_document_mutations.py:203-210`,
  `svg_raster_renumber.py:98`). The raster branch then cannot skip measurement: `renumber_redesign_page`
  builds its inputs with `raster_redesign_generation.raster_measurement_inputs` (l.122; the same
  `allow_image_only` + materialized-preview call `render.py:100-120` makes at generation) and calls
  `render_svg_with_metrics` after the relabel, replacing `quality` and the PNG. `pageNumberRelabel`
  stays as provenance. Cost: one Chrome render per moved raster page, which is what the generic path
  already pays for every moved svg page; the "nothing is re-measured" docstring becomes "only the
  footer changes, and the page is re-measured like any other mutation".
- **B. Re-digest without measuring.** Set `quality["source_digest"] = source_digest(svg)` in the relabel.
  Cheapest (3 lines) and rejected: it makes the geometry domain assert a measurement that did not
  happen. The footer is excluded from the font floor and from required text, but not from
  `text_off_canvas`/`text_overlap` (a 2-digit to 3-digit relabel changes width), and a digest that can be
  written without measuring is no longer evidence.
- **C. Canonical-form digest with the relabel as a verified transform.** Define
  `canonical_page_bytes(svg)` (footer digits normalized) and use it on both the writer (`svg_renderer.py:335`)
  and the checker (l.84); record `pageNumberRelabel` as a verified transform. Also rejected for now: it
  changes the meaning of every stored digest (a version bump or backfill of all svg decks), and it
  weakens the same guarantee as B for the footer element, only more carefully. It would be the right
  shape if relabels were frequent and measurement expensive; today neither holds.

### 3.4 Files and LOC (option A)

| file | change | LOC |
|---|---|---|
| `services/svg_contract.py` (or new `services/svg_page_publication.py`) | `publish_measured_page` setter with the digest assertion | +30 |
| `services/generation_core/drivers/svg/publish.py:135`, `terminal.py:92-107`, `services/svg_page_remake.py:127`, `services/svg_document_mutations.py:203-210` | route through the setter | ~+10 / −10 |
| `services/svg_raster_renumber.py` | re-measure after relabel via `raster_measurement_inputs`, save PNG, route through the setter, docstring | ~+35 / −8 |
| `services/svg_document_mutations.py:167-176` | keep the raster dispatch, drop the comment that promises no measurement | ~+2 / −3 |
| `tests/unit/test_svg_raster_renumber.py` | fixture receipt gains a real `quality` block; assert `svg_export_quality` passes after reorder; assert the setter raises on a mismatched digest | +40 |
| `tests/unit/test_svg_export_page_evidence.py` | relabel-then-check case | +15 |
| `tests/unit/test_svg_page_publication.py` (new) | every `services/` assignment to `slide.svg_content` goes through the setter (AST scan) | +35 |

Acceptance: a raster-redesign deck reordered through `WorkspaceSlideOrderOperationHandler` exports
with `authoring` and `geometry_font` `passed`; `svg_*_evidence_stale` is unreachable from a relabel;
the AST test fails if a new writer bypasses the setter; live check on the shared stack with one
reordered raster deck (per the "defect reports need live path" practice).

---

## 4. Registered PPTX design packs never generate

### 4.1 Inventory

- **Rejection:** `services/authored_vnext/source_native_generation.py:269-274`, `_source_json`:
  `if len(source_entries) != 1 or package.manifest.source_kind != "authored": raise
  SourceNativeGenerationError("source_native_authored_pack_required")`, called at the top of
  `execute()` (l.3272). `manifest.source_kind` is `Literal["pptx", "authored"]`
  (`design_pack/manifest.py:29`); the PPTX pack's single source entry is `source/reference.pptx`
  (`ingestion.py:71-72`), which `_source_json` reads as JSON. So a PPTX pack is unexecutable by
  construction, independent of canvas, and has been since before `d808c09f9` (which did not touch this
  file).
- **Admission order:** `models/generate_presentation_request.py:219,319-322` (pin required iff
  `generation_mode == "registered-template"`); `generation_core/admission/strategy.py:50-53` (a pin
  resolves an omitted strategy to authored); `svg_generation_admission.py:57-59` (explicit svg rejects
  a pin: `assert_design_pack_projects()` always raises); `authored_canvas.py:343-347`
  (`design_pack_canvas_unsupported` unless 16:9 without `page_size`); `generation_route.py:82-100`
  (`load_pinned_design_pack`: id, major version, digest, `canvas_id == DESIGN_PACK_CANVAS_ID`). None of
  these checks `source_kind`; the PPTX pack fails only inside the worker.
- **Registry:** `services/authored_vnext/design_pack/` — `compiler.py` 1418 (l.92-1065 PPTX-only,
  `compile_pptx_design_source` at l.895; l.1066-1418 authored), `ingestion.py` 170, `registry.py` 190,
  `models.py` 411, `manifest.py` 81, `canvases.py` 65, `structural_render.py` 56, `base.py` 28,
  `__init__.py` 40. Endpoints in `api/v1/ppt/endpoints/design_packs.py` (427): `POST /design-packs/v1/
  from-pptx` (l.375-412, 100 MB cap), `POST /from-presentation` (l.319, builds an authored source from a
  deck), `GET ""` (l.413, list). No DELETE. The response (`DesignPackPinResponse`, l.89-96) carries
  `sourceKind` and a constant `outputCapability: "fidelity"`; there is no status field, and manifest v2's
  `status` was removed in `d808c09f9`. The 16:9 pin is `canvas_id` matched against
  `constants/canvas_registry.json` (no literal 1280/720 in the package).
- **UI:** no registration, listing or deletion surface exists. `rg "design-packs"` over
  `servers/nextjs/{app,components,lib}` returns nothing. The only pin surface is the `/public-v2` canary
  (`app/(presentation-generator)/public-v2/PublicV2OperationsClient.tsx`, 428 LOC: three free-text
  inputs id/version/digest at l.363 when `generationMode === "registered-template"`; contracts in
  `lib/public-v2/operation-client-contracts.ts:42-65`, parsing in `operation-client.ts:338-352,393`),
  linked from `DashboardPage.tsx:226`. No i18n key exists for `source_native_authored_pack_required`,
  `design_pack_canvas_unsupported` or `svg_registered_template_not_supported`.
- **Tests:** `test_source_native_design_pack.py` (256; ingests a synthetic PPTX), `test_design_pack_
  canvas_admission.py` (117), `test_design_pack_manifest_and_render.py` (155), `test_source_native_
  product_route.py` (755). No test mentions `source_native_authored_pack_required`.

### 4.2 Decision

The question is what a PPTX pack is *for* now that authored is frozen, svg is the product default
(and refuses pins by construction), packs are 16:9-only, and the only executor reads authored JSON.
The answer is nothing: PPTX ingestion produces an artifact no code path can consume, reachable only by
an API call no UI makes. Options:

- **(a) Remove PPTX pack ingestion; keep authored packs (recommended).** Delete `POST /from-pptx`,
  `compile_pptx_design_source` and its ~970 helper lines, `source_kind == "pptx"` from the manifest
  literal, the PPTX branch of `ingestion.py`, and `test_source_native_design_pack.py`'s PPTX cases.
  `_source_json` keeps its check (it becomes the type system's statement that packs are authored) but
  the enforcement moves to the single admission point, `load_pinned_design_pack`
  (`generation_route.py:82-100`), which rejects any pack whose manifest is not `authored` with
  `design_pack_source_unsupported` before a worker is dispatched. `outputCapability` is dropped from the
  response (it is a constant) and `sourceKind` becomes `Literal["authored"]`. The bundled PPTX→pack path
  is kept recoverable from git; the raster-redesign pipeline is the live PPTX-intake product and is
  unaffected.
- **(b) Registration-only with a surfaced status.** Add `generatable: false` to the list/register
  response and reject at `load_pinned_design_pack`. Cheap (~40 LOC) and honest, but it keeps 1,000 lines
  and an upload endpoint whose only purpose is to store something the product declares unusable.
  Reasonable only if a PPTX→authored conversion is planned.
- **(c) PPTX→authored conversion.** The compiler already extracts frames, slots, chrome and capacity
  from the PPTX; turning that into an `authored-design-source/v1` (the `from-presentation` shape) is a
  projection of ~300-500 LOC plus fidelity tests. Rejected while authored is frozen and svg does not
  take pins: it would make PPTX packs generatable only on the frozen strategy.

Recommendation: **(a)**, with the admission move as the single enforcement point regardless of which
option is chosen. If the product wants PPTX packs back, (c) is the path and (b) is its first commit.

### 4.3 Files and LOC (option a)

| file | change | LOC |
|---|---|---|
| `api/v1/ppt/endpoints/design_packs.py` | remove `/from-pptx` (l.375-412), `output_capability`, narrow `source_kind` | −45 |
| `services/authored_vnext/design_pack/compiler.py` | remove l.92-1065 PPTX compiler (keep `_sha256`/`_digest_bytes`/`_canonical_bytes` if the authored path uses them) | ≈ −950 |
| `design_pack/ingestion.py`, `manifest.py`, `models.py`, `__init__.py` | drop the PPTX branch and exports | ≈ −60 |
| `services/authored_vnext/generation_route.py:82-100` | `design_pack_source_unsupported` at pin load | +8 |
| `services/authored_vnext/source_native_generation.py:269-274` | unchanged (defence in depth) | 0 |
| `tests/unit/test_source_native_design_pack.py` | PPTX ingestion cases removed; add the pin-load rejection with a hand-built `pptx` manifest fixture | ≈ −120 / +25 |
| `tests/unit/test_design_pack_manifest_and_render.py`, `test_source_native_product_route.py` | fixtures that build PPTX packs move to authored sources | ≈ ±40 |
| `servers/nextjs/lib/public-v2/operation-client-contracts.ts` | no change (the pin shape is source-agnostic) | 0 |
| `docs/`, `compatibility/upstream-byte-parity.json` | record the removal as the 2026-09-25 removals were | +10 |

Acceptance: `POST /design-packs/v1/from-pptx` returns 404; a hand-registered `pptx` manifest is rejected
at admission with `design_pack_source_unsupported` before any worker dispatch (assert no
`presentation_generation_jobs` row); `from-presentation` packs still generate on authored 16:9;
`uv run ruff check .`; `npm run test:next-ci` unchanged.

---

## 5. Open decisions

1. **Item 1, tolerance.** Keep 1 px for both witnesses, or scale to `max(1 px, 0.02 em)` for large
   display sizes? The reproduction never needed more than 0.02 px for a true match, so 1 px is proposed.
2. **Item 1, whitespace.** Spaces remain unmeasured (no ink). If the product ever wants a horizontal
   gap rule, that is a style policy, not this gate (unchanged from the 2026-09-29 decision 3).
3. **Item 1, existing decks.** As on 2026-09-29: no re-measure pass; `review_required` decks keep their
   verdict until touched. Confirm.
4. **Item 2, `getHeader()` on every request.** Sending the contract header on ungated endpoints is
   harmless today; if a future endpoint wants to *refuse* readers that declare the contract (none is
   foreseen), the helper split (`getHeader` vs `getPublicHeader`) is the place to express it.
5. **Item 3, Chrome cost on reorder.** Option A pays one render per moved raster page inside the
   workspace operation. If the shared stack's operation budget cannot absorb this for large raster
   decks, the fallback is option C with a version bump, not option B.
6. **Item 4, product decision.** (a) deletes the fork's PPTX-template intake that the
   "registered-template" mode was built around. This needs an explicit yes; the admission move in
   `load_pinned_design_pack` should land either way.
7. **Item 4, `/public-v2` pin form.** With (a) the free-text digest form is the only pin surface; it
   should either list authored packs from `GET /design-packs/v1` or be removed with the canary.

## 6. Self-score

| item | root cause verified | fix generic (single point) | tests specified | confidence |
|---|---|---|---|---|
| 1 | Yes: reproduced order dependence in the production probe (`A then B` vs `B then A`), isolated the glyph substitution (`locl`/KOR, 529 u vs 231 u) and the cache mechanism with the verbatim probe | Yes: `inkBox` only; the consumer gains one basis label | Yes, incl. order-independence and the still-missing pixel oracle | 9/10 — the exact Blink cache granularity (word vs string) is inferred from behaviour, not from source; the fix does not depend on it |
| 2 | Yes: gate and every client read; MCP gap found | Yes: one header owner per runtime, one gate | Yes: node scan + pytest HTTP-level | 8/10 — the node contract test is a source scan, which is the right tool for "a new client forgot" but can be fooled by indirection |
| 3 | Yes: writer, checker and the bypass at `svg_document_mutations.py:167-176` read; test blind spot named | Yes: setter enforces the invariant for all five writers | Yes | 8/10 — `raster_measurement_inputs` reuse in the mutation path was not executed; the isolated-worker path may need the operation's owner context (see the fence test) |
| 4 | Yes: rejection line, admission order, absence of UI | Yes: admission at pin load | Yes | 7/10 — (a) is a product decision; LOC for the compiler split is an estimate from function boundaries, not a dry run |

Overall 8/10. Items 1 and 3 are ready to implement on confirmation; item 2's MCP fix is ready; item 4
waits on decision 6.
