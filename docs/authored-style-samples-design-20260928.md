# Authored style sample preview — design (2026-09-28, revision 3)

Status: implemented on `main` (feature `5f045dc88`; revision 3 changes in
`feat/style-samples-freshness`). Revision 2 folded in the review of revision 1
(repository size, per-image catalogue loads, partial rollout, pipeline drift,
private-function coupling, image element, cache headers) and brought sample
generation into scope. Revision 3 adds the demo-topic digest, a cache version
on image URLs, and the `pending.txt` exception to D2.

## 1. Goal

Let a user click an authored style card and see a representative deck in that
style before choosing it: a large slide stage with `‹ ›` paging and `n / N`,
an "all slides" numbered thumbnail grid, and a sticky "use this style" action.
The interaction is modelled on Genspark's skill preview dialog, with one
addition that only makes sense for a style catalogue: every style's sample deck
uses the **same demo content**, and moving to the previous/next style keeps the
current slide number, so styles can be compared page for page.

## 2. Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | Samples live in a new root, `servers/fastapi/authored_style_samples/<style-id>/`, never in `authored_style_assets/`. | `authored_style_assets/` holds reference images taken from real source documents (third-party content; several YAML briefs forbid reusing their claims). Serving only from the samples root makes "reference images are never shown to users" a structural property of one endpoint. |
| D2 | Samples are **all or nothing**: the check script (§6.3) passes only when the samples root is empty or holds a valid deck for every public canonical style not listed in `authored_style_samples/pending.txt`. The dialog is enabled when the loaded catalogue has samples. | Page-for-page comparison needs every style. A partial state would add a click to every card and show an empty dialog for most of them. Because the check script forbids partial states, the runtime switch is simply `styles.some((s) => s.samples)` and the dialog's empty state is defensive code. `pending.txt` exists so that adding a style YAML does not require generating its sample in the same change; the check prints every pending style so the list cannot grow unnoticed, and it rejects an entry whose sample directory already exists. |
| D3 | Sample metadata is embedded in `GET /authored/styles`; only image bytes get a new endpoint. | ~39 styles × ≤24 short titles is small. Embedding removes a list endpoint, a fetch hook, and loading/abort state from the dialog. |
| D4 | The catalogue omits `samples` entirely for a style without valid samples. | The response stays byte-identical to today while no samples exist, and the frontend normaliser keeps its "attach only when present" rule. |
| D5 | Staleness is enforced before merge, not at runtime. The check script fails when a sample's recorded `styleDigest` no longer matches its style or its `topicDigest` no longer matches the demo prompt. The request path never computes digests; it only derives a 12-hex `cache_version` from the two recorded digests and appends it as `&v=` to image URLs, so a regenerated deck defeats the day-long `Cache-Control` without any route change. | Hiding stale samples at runtime would make a style's samples vanish after any YAML edit. Computing digests per request would hash reference images per request. |
| D6 | The style digest covers the parsed YAML **minus display-only keys**, plus the bytes of referenced image assets; never the YAML file bytes. It is computed by the samples module's own `yaml.safe_load`, not by the loader's private `_read_style_mapping`. | `core.autocrlf=true` rewrites line endings on Windows checkouts, so byte hashes differ across machines. Editing a description does not change rendering and must not stale a sample. Excluding a fixed display-only list treats any future key as render-affecting by default. Reading the YAML directly avoids coupling to a private loader function. |
| D7 | The digest does not cover the generation pipeline. `manifest.json` records `generatorCommit` as provenance, and the check script prints each deck's generator commit and age without failing on it. | Prompt or renderer changes drift samples away from real output; no hash of style inputs can detect that. Regeneration after pipeline changes is an operating rule (§10), stated as a known limit. |
| D8 | Images are WebP only, fixed names `NN.webp` (stage, long edge 1280 px) and `NN.thumb.webp` (grid, long edge 320 px), with byte caps of 96 KB and 14 KB enforced by the check script. The manifest records the canvas as pixel `width`/`height`, not an aspect-ratio label. | SVG in `<img>` cannot load web fonts. Fixed names remove file names — and path validation — from the manifest. The caps bound one generation of 39 × 10 slides to about 40 MB in git; without them a generation would be 40–60 MB and every regeneration accumulates in history. Recording pixel dimensions keeps the contract neutral between the 16:9 prompt-generation canvas (1280×720) and the A4 document canvas (landscape 1280×905, portrait 905×1280) that the source-content path uses; the sample canvas is chosen by the generation session, not by this code. |
| D9 | Samples are committed directly to git for the first generation (about 40 MB). If a second generation is needed, moving the samples root to a versioned download (the `scripts/sync-presentation-export.cjs` pattern) is the fallback; the storage contract (§4) does not change. | About 40 MB once is acceptable; repeated generations are not. Deciding the download mechanism before it is needed would be speculative. |
| D10 | The image route trusts the samples root: it validates the id shape, reads the manifest, and serves the file. It does not load the style catalogue. | The check script already guarantees a sample directory exists only for a public canonical style, so the runtime check would be redundant, and the dialog issues up to 24 image requests at once — loading 46 YAML files per request would mean ~1,100 parses per open. |
| D11 | In preview mode the card grid is a list of buttons, not a radio group. | `handleAuthoredRadioKeyDown` moves focus **and calls `.click()`** on arrow keys (`AuthoredStyleCard.tsx:287-315`), so a radio card that opens a dialog would open one per arrow press. Radio semantics stay unchanged when preview mode is off. |
| D12 | The dialog renders images with a plain `<img loading="lazy">`, not `next/image`. | The files are already optimised WebP, and the Next image optimiser fetches server-side without the session cookie that the `/api/` auth middleware requires. |

## 3. Non-goals

- Hover-cycling thumbnails on cards (evaluated after samples exist).
- Localising the existing hard-coded Korean strings in `AuthoredStyleCard.tsx`
  and `AuthoredStylePicker.tsx`.
- A second demo topic or per-category topics.

## 4. Storage contract

```
servers/fastapi/authored_style_samples/
  <style-id>/
    manifest.json
    01.webp
    01.thumb.webp
    ...
```

`manifest.json`:

```json
{
  "schema": "authored-style-samples/v1",
  "styleId": "kbs-company-proposal",
  "styleDigest": "sha256:<64 lowercase hex>",
  "topicDigest": "sha256:<64 lowercase hex>",
  "demoTopicId": "business-plan-v1",
  "canvas": { "width": 1280, "height": 720 },
  "generatedAt": "2026-10-01T09:00:00Z",
  "generatorCommit": "<40 hex>",
  "slides": [
    { "title": "Cover" },
    { "title": "Market" }
  ]
}
```

Rules (runtime-validated unless marked *check only*):

- `schema` equals `authored-style-samples/v1`.
- `styleId` equals the directory name, matches `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`,
  and (*check only*) is a public canonical style: not an alias key of
  `AUTHORED_STYLE_ALIASES`, not a private exact-reconstruction preset.
- `canvas.width` and `canvas.height` are integers in 320–1280 with
  `max(width, height) == 1280`. Every deck in the samples root uses the same
  canvas (*check only*), so page-for-page comparison never mixes shapes.
- `slides` has 1–24 entries. Entry *i* (0-based) is slide `NN = i + 1`,
  zero-padded to two digits. `title` is a non-empty string of at most 80
  characters.
- `styleDigest` and `topicDigest` are `sha256:` followed by 64 lowercase hex
  characters. (*check only*) `styleDigest` equals §5's style digest and
  `topicDigest` equals §5's topic digest of the current
  `scripts/authored_style_samples_topic.md`.
- `generatorCommit` is 40 lowercase hex characters.
- (*check only*) Every slide has both `NN.webp` and `NN.thumb.webp` as regular
  non-symlink files, starting with the WebP magic bytes (`RIFF….WEBP`), with
  pixel size equal to `canvas` for `NN.webp` and to `canvas` scaled to a
  320 px long edge (rounded) for `NN.thumb.webp`, and at most 96 KB / 14 KB. No other files are in the directory. The check
  script ignores `Thumbs.db` and `.DS_Store` so local runs on Windows/macOS do
  not fail on OS metadata.
- (*check only*) The samples root is empty, or every public canonical style
  has a directory that passes all rules (D2), except styles listed one per
  line in `authored_style_samples/pending.txt` (blank lines and `#` comments
  ignored). A pending entry must be a public canonical style and must not have
  a sample directory; the check prints each pending style in its report.
- `demoTopicId` and `generatedAt` are provenance; nothing branches on them.

## 5. Style digest

`style_render_digest(style_yaml: Path, assets_root: Path) -> "sha256:<hex>"`
in `utils/authored_style_samples.py`:

1. `yaml.safe_load` the file (its own read; D6).
2. Drop the display-only keys `name`, `description`, `category`, `tags`,
   `use_cases`, `preview`.
3. Serialise the rest as canonical JSON (`sort_keys=True`,
   `separators=(",", ":")`, `ensure_ascii=False`, `default=str` so YAML dates
   or other non-JSON scalars cannot raise).
4. For every path string under `reference_images` and
   `slide_reference_images`, append `\n<relative path>\n<sha256 of file bytes>`
   in sorted path order. Paths resolve under `assets_root` with the same
   containment rule as the loader; an unresolvable path is an error the check
   script reports.
5. SHA-256 the result.

`topic_prompt_digest(topic_file=None)` in the same module hashes the text
between the `## Prompt` and `## Fixed` headings of
`scripts/authored_style_samples_topic.md`, with CRLF normalised and outer
whitespace stripped, so notes around the prompt and checkout line endings do
not stale decks while any prompt edit does.

Only the check script and the generator call these. The request path never
does; it reads the recorded digests from the manifest and derives
`StyleSamples.cache_version` (first 12 hex of SHA-256 over
`"<styleDigest>|<topicDigest>"`) for the image URLs.

## 6. Backend

### 6.1 `utils/authored_style_samples.py` (new, target ≤220 lines)

- `AUTHORED_STYLE_SAMPLES_DIRECTORY` beside `AUTHORED_STYLE_ASSETS_DIRECTORY`.
- `load_style_samples(style_id, samples_root=None) -> StyleSamples | None`
  validates the id shape and reads `manifest.json` against the runtime rules of
  §4. Any violation logs one warning (style id and rule, never an absolute
  path) and returns `None`. A missing directory returns `None` silently.
- `sample_file_path(style_id, index, size, samples_root=None) -> Path | None`
  builds the fixed name and returns it only if it is a regular non-symlink file
  inside `<samples_root>/<style_id>/`.
- `style_render_digest(...)` per §5.

`utils/authored_styles.py` is not modified (frozen size exception, 664 lines).

### 6.2 Catalogue and endpoints

- `authored_style_catalogue()` (`utils/authored_style_catalogue.py`) attaches,
  per canonical style with valid samples:

  ```json
  "samples": {
    "canvas": { "width": 1280, "height": 720 },
    "slides": [
      { "index": 1, "title": "Cover",
        "thumb": "/api/v1/ppt/authored/styles/<id>/samples/1?size=thumb&v=<cache_version>",
        "full":  "/api/v1/ppt/authored/styles/<id>/samples/1?size=full&v=<cache_version>" }
    ]
  }
  ```

  Attached in the catalogue, not in `AuthoredStyle.public_dict()`, so
  `authored_style_conversion_io.py`'s `public_dict()` comparison is unaffected.
  One manifest read per style per catalogue request (39 small JSON files).
- `AuthoredStyleSummary` (`api/v1/ppt/endpoints/authored.py`) gains
  `samples: Optional[AuthoredStyleSamples] = None`; the route's existing
  `response_model_exclude_none=True` gives D4.
- New route `GET /authored/styles/{style_id}/samples/{index}` with
  `index: int = Path(ge=1, le=24)` and `size: Literal["thumb", "full"] = "thumb"`
  (D10):
  - `load_style_samples(style_id)` must be non-`None` and `index` within its
    slides; else 404.
  - `sample_file_path(...)` served as `FileResponse(media_type="image/webp")`
    with `Cache-Control: private, max-age=86400`; 404 if `None`. The `v`
    query parameter is not declared and is ignored; it exists only so that
    the URL changes when the deck does (D5).
  - Protected by `SessionAuthMiddleware` like every `/api/` path; same-origin
    `<img>` requests carry the session cookie, as the existing card preview
    already relies on.

### 6.3 Check script `servers/fastapi/scripts/check_authored_style_samples.py` (new)

Enforces every rule in §4 including the *check only* ones, §5 style and
topic digest equality, D2 all-or-nothing with the `pending.txt` exception,
and the consistency of `pending.txt` itself. Loads the public catalogue once via
`load_authored_styles()` + `authored_style_catalogue()` to know the canonical
ids. Prints one line per problem (`<style-id>: <rule>`), then one line per deck
with its `generatorCommit` and the number of commits since it (via
`git rev-list --count`, best effort). Exit 1 on any problem; a missing or empty
root prints `no authored style samples` and exits 0.

CI wiring: `bitbucket-pipelines.yml` runs only Ruff for Python, so the script
is added to the `workspace-chat-first-e2e` step right after the Ruff line:

```yaml
- .bitbucket-uv/bin/uv run --directory servers/fastapi python scripts/check_authored_style_samples.py
```

`scripts/required-ci-contract.test.mjs` gains an assertion for that line next
to the existing Ruff assertion; the Ruff line itself is unchanged so the
existing regex still matches.

## 7. Frontend

### 7.1 API types (`services/api/authored-style-samples.ts`, new)

- `AuthoredStyleSamples = { canvas: { width: number; height: number }; slides: { index: number; title: string; thumbUrl: string; fullUrl: string }[] }`.
- `normalizeAuthoredStyleSamples(value: unknown): AuthoredStyleSamples | undefined`
  applies `getApiUrl` to both URLs, drops malformed entries, returns
  `undefined` for an empty result.
- `services/api/authored.ts` (472 lines) adds `samples?: AuthoredStyleSamples`
  to `AuthoredStyleSummary` and attaches it in `toAuthoredStyleSummary` only
  when defined (~5 lines).

### 7.2 Components (`outline/components/style-sample/`, new)

| File | Responsibility | Target |
|---|---|---|
| `StyleSampleDialog.tsx` | shadcn `Dialog`. Left header (category, name, full description, tags, use cases); right stage; `‹ previous style` / `next style ›`; sticky footer `Use this style →`, marked when already selected. | ≤220 |
| `StyleSampleSlides.tsx` | Stage (`fullUrl`, `‹ ›`, `n / N`) and the "All slides · N" grid of `thumbUrl` tiles with `01` badges, plain `<img loading="lazy">` (D12), tile and stage aspect from `canvas.width / canvas.height`. Empty state: the existing `AuthoredStylePreview` rendered large plus a "sample slides are being prepared" note. | ≤180 |
| `useStyleSampleNavigation.ts` | `{ styleIndex, slideIndex, goToStyle(delta), goToSlide(n) }`. Changing style keeps `slideIndex`, clamped to the new deck's last slide. | ≤80 |

Keyboard: `←`/`→` page slides; `Shift+←`/`Shift+→` page styles; `Esc`
closes. Image `alt` is `"<n>. <title>"`.

### 7.3 Picker and card wiring

- `AuthoredStylePicker.tsx` (373 lines): `samplePreviewEnabled = styles.some((s) => s.samples)`;
  `previewStyleId` state; the card gets
  `onSelect={samplePreviewEnabled ? setPreviewStyleId : handleStyleSelect}` and
  `mode`; the grid is `role="radiogroup"` or `role="list"`; the dialog gets
  `styles={visibleStyles}`, the selected id, and
  `onConfirm={(id) => { handleStyleSelect(id); setPreviewStyleId(null); }}`.
- `AuthoredStyleCard.tsx` (417 lines) gains `mode`. In `"preview"` the button
  drops `role="radio"`, `aria-checked`, roving `tabIndex`, and `onKeyDown`, and
  gets `aria-haspopup="dialog"` and `aria-current` when selected.

### 7.4 i18n

New strings in an `authoredStyleSamples` namespace in
`i18n/messages/{en,ko}.json`; the three new files join
`tests/i18n-audited-surfaces.test.mjs`.

## 8. Sample generation

### 8.1 Inputs

- **Demo topic** `business-plan-v1`: a fictitious Korean company's business
  plan, Korean language, 10 slides, fixed prompt text stored in
  `servers/fastapi/scripts/authored_style_samples_topic.md`. No real
  organisation names. The prompt asks for the standard chapter shape (cover,
  agenda, market, product, business model, roadmap, team, financials, risks,
  closing) so every style is measured on the same page types.
- **Pipeline**: the product's default `svg` strategy through the public API,
  `--slides 10 --language ko --export pdf`, using `scripts/ppt-agent.mjs`
  against the running local stack (`http://localhost:5000`).

### 8.2 Generator `servers/fastapi/scripts/generate_authored_style_samples.py` (new)

1. Loads the public catalogue; takes `--styles a,b,c` or all.
2. For each style, calls `ppt-agent.mjs` (subprocess) with the topic prompt and
   `--style <id> --export pdf --out <scratch>/<id>/`; retries a failed deck once.
3. Rasterises the PDF with `pypdfium2` (already a backend dependency) at
   the manifest canvas and its 320 px thumbnail, writes WebP with Pillow at a quality that meets the
   byte caps (steps down quality until it fits; fails the deck if it cannot at
   quality 60).
4. Slide titles come from the generated deck's outline
   (`GET /presentation/{id}`), truncated to 80 characters.
5. Writes `manifest.json` with both digests (§5) and `git rev-parse HEAD`.
6. Emits `<scratch>/contact-sheet.html`: one row per style, 10 thumbnails each,
   for review.
7. Runs the check script at the end; a failing deck is reported, not committed.

Concurrency: one deck at a time by default (`--parallel 2` allowed). The local
stack is shared with other sessions; the generator must not raise wave
concurrency or change backend settings.

### 8.3 Procedure

1. Pilot with three styles of different families (`default`,
   `kbs-company-proposal`, `neon-venture`): validate the prompt, timing, byte
   caps, and the contact sheet. Adjust the topic prompt once if needed, then
   freeze it.
2. Full run of the remaining 36 styles.
3. Human review on the contact sheet: a deck with a broken slide is
   regenerated (deck-level, never single slides — slide numbering must stay
   consistent with the topic).
4. Commit all 39 decks in one commit (D2), then the code.

## 9. Tests

Backend `tests/unit/test_authored_style_samples.py` (fixtures in `tmp_path`):
manifest rules; `sample_file_path` symlink/missing/escape; digest LF vs CRLF,
display-only edits, `brief`/`svg_overrides`/image-byte edits; catalogue
presence/absence and alias omission; route 200 (`image/webp`, both sizes,
cache header) and 404 (unknown id, bad id shape, out-of-range index, missing
file); check script exit codes for empty root, stale digest, extra file, bad
magic bytes, oversize file, alias directory, and a partial set (D2). The
existing `test_authored_styles.py` exact `preview` assertions pass unchanged.

Frontend: `node:test` for the normaliser; Cypress `StyleSampleDialog.cy.tsx`
(stage/grid/paging, `Shift+→` keeps slide number and clamps, empty state,
confirm closes and selects); one new `TemplateSelection.cy.tsx` case with a
sample-bearing catalogue. The four existing specs that intercept
`/authored/styles` are unchanged and keep exercising radio mode.

## 10. Operating rules

- Regenerate all decks after a change to the svg prompts or renderer; the
  check script's age line is the reminder, not a gate (D7).
- Editing the demo prompt stales every deck (`topicDigest`); regenerate all
  39 or revert the edit.
- A new style YAML lands with its id appended to
  `authored_style_samples/pending.txt`; the follow-up that adds its sample
  removes the line.
- A style YAML edit that touches non-display keys fails the check until that
  style is regenerated; regenerate that style alone with `--styles <id>`.
- A second full generation triggers the D9 storage review before commit.
