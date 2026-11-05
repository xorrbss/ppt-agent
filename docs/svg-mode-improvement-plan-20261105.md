# SVG mode improvement plan (2026-11-05)

Work items for the default generation mode (`strategy=svg`, driver `svg-v1`).
They are written so that a coding agent (Codex) can take them one at a time.
The evidence for every item is in
`docs/svg-mode-quality-performance-review-20261105.md` (cited below as
"review §N").

Baseline at `696be2ac` (review §2–3):

| | Value |
|---|---|
| Document quality | 58/100 |
| Performance | 50/100 |
| Engineering health | 30/100 |
| 6-slide deck | 206–267 s, $1.26–1.43 |
| Cached tokens | 0 |
| Native charts and tables in default decks | 0 |

Target after phases 1–3:

| | Value |
|---|---|
| 6-slide deck | 130–170 s, $0.7–0.9 |
| Text fallback pages | none on the two reference prompts |
| Native chart | one on every page that presents a sourced numeric series |

All targets are estimates and must be confirmed with the measurement protocol
in §5.

## 1. Rules for whoever implements this

- Follow `AGENTS.md` and `CLAUDE.md`. After each item, run the single check
  listed for it, not the full suite (the full suite takes 17–45 min).
- **Do not paper over a blocker.** If an item cannot be done as written, stop
  and report the cause and the structural change it needs. That includes a
  sandbox that refuses a write, a digest contract that would have to be
  bypassed, or a test that can only pass with a hard-coded value. Do not add
  stubs, dummies or special cases.
- **Digest-pinned text.** Prompt text and rule constants are durable digest
  inputs:
  - `SVG_AUTHOR_RULES` is never edited in place (see
    `utils/content_structure_policy.py`);
  - authoring profiles are versioned (`services/svg_authoring_profile.py`).
  A change to prompt bytes or to what enters a receipt needs a new
  profile/policy revision. Earlier revisions must keep validating so stored
  operations still resume and export.
- **Settings.** New knobs go through `constants/settings_registry.json` and
  `utils/get_env.py` (`CLAUDE.md`, "Configuration"). Do not add another
  unregistered `SVG_*` knob.
- **Layering.** Keep `node scripts/check-structural-layering.mjs` green. Its
  allowlist may only shrink.
- **Language.** Code, comments, commits and repository docs are English.
  User-visible strings go through `next-intl` (`i18n/messages/{en,ko}.json`).
- **Gated items.** An item marked "Gate: Dn" depends on the owner decision Dn
  in §2. Its status there must be `confirmed` before you start it. Items
  without a gate can start now.

## 2. Owner decisions

Status: **all five confirmed by the owner on 2026-11-05.** Each decision
takes the recommended option. Gated items may start; their remaining
conditions (paired measurement, advisory-first rollout) still apply.

| Decision | Status | Confirmed option | Unblocks |
|---|---|---|---|
| D1 | confirmed 2026-11-05 | C: deterministic reduced header by default, `uniform` opt-in, cover exception on | P8 |
| D2 | confirmed 2026-11-05 | C: per-canvas floor (16:9 hard 10.5 pt, target 18–20 px body, A4 9 pt) with an outline density cap, advisory first | S6 |
| D3 | confirmed 2026-11-05 | C: `connectors` on for svg; server-side `chart:` literal; plan archetype to the author; `tables` off until fixed | S1, S2 |
| D4 | confirmed 2026-11-05 | Accept 1:1 and registered-style restyle rows; restore A4-only raster reconstruction | E2 (admission baseline) |
| D5 | confirmed 2026-11-05 | C: patch files plus a byte-identity-tested digest alias table | E3 |

### D1. Default common header mode

**Context (review §3.2, §4.1, §4.10).**
- The default for a fresh svg request is `uniform`
  (`services/svg_common_header_design_plan.py:55`). It takes one design call,
  two fully authored representative pages and one vision selection call
  before any body page starts.
- Measured cost: 77–99 s and about 28 % of cost per deck. One representative
  page is always thrown away.
- It also caused the duplicated cover title (D1) and a cover text fallback
  (D2), and it always fails for Pretendard-heading styles (fixed by Q3
  regardless of this decision).

| Option | Effect |
|---|---|
| A. Keep `uniform` | Richest header design; −0 s |
| B. `free` by default | Fastest; deck consistency left to the model |
| C. **Deterministic reduced header by default, `uniform` opt-in** | Consistent header with no LLM cost; needs a less plain reduced header |
| D. Keep `uniform`, optimise it (1 candidate, cache, parallel start) | Keeps design; about half the saving; large change |

**Confirmed 2026-11-05: C** (the recommended option), together with:
- `common_header_cover_exception` defaulting to true (server and UI);
- a reduced header that shows a divider and an eyebrow by default;
- the reduced header font fixed by Q3.

Implementation condition: P8 changes the default only after paired runs (§5)
against A show no drop in pass rate, fallback pages or judge score. If they
do show a drop, stop and report instead of changing the default.

### D2. Body font floor

**Context (review §3.3, §4.3, §4.4).**
- Hard floor today: 12 px (`AUTHORED_FONT_FLOOR_PX`,
  `models/authored_vnext/capability_registry.py:61`). That is 9 pt on a
  1280 px canvas; readability policy is `max(9.0, …)` pt
  (`services/document_canvas_generation.py:60`).
- Live slides used dense 14 px body text.
- Raising the floor alone increases overflow failures, because the outline
  asks for 4–6 full sentences per slide.

| Option | Effect |
|---|---|
| A. Keep 9 pt | Report-like density, poor when projected |
| B. Hard 12 pt on 16:9 | Readable; fallback pages expected to rise sharply |
| C. **Per canvas:** 16:9 hard floor 10.5 pt (14 px), prompt target body 18–20 px, A4 stays 9 pt, plus an outline density cap | Readability up, failures controlled |

**Confirmed 2026-11-05: C** (the recommended option).
1. Ship the floor as an advisory measurement first and count how many pages
   would fail.
2. Then raise the hard floor.

### D3. Content structure policy (tables, charts, connectors)

**Context (review §4.7).**
- `CONTENT_STRUCTURE_POLICY_SCOPE` defaults to `off`, so default decks get no
  native chart or table and keep the "no arrows" rule.
- With it on, 10 of 10 table pages fell back to text on a template deck
  (`docs/content-structure-policy-20261104.md`).

| Option | Effect |
|---|---|
| A. Keep off | 0 native charts/tables; weak diagrams |
| B. All on | Table pages fall back (measured 10/10) |
| C. **`connectors` on for svg decks; charts from a deterministic server-side `chart:` literal (S2); plan archetype passed to the author (S1); `tables` stay off until the table fallback cause is fixed** | Diagrams and native charts without the measured regression |

**Confirmed 2026-11-05: C** (the recommended option).

### D4. Canvas admission drift

**Context (review §4.14).** `test_canvas_admission_baseline` shows 980 rows
(of 2,646) changed from rejected to admitted; an earlier count of 51 came
from truncated output. Besides 1:1, registered-style topic/restyle is also
admitted on 21:9, 9:16, 9:13 and A4, and A4 raster is admitted for styles
other than `screenshot-blue`; E2 accepted these as the same svg-path change.

| Change | Recommended handling |
|---|---|
| 1:1 topic/restyle generation | Accept: matches the canvas-bench promotion; re-capture these rows |
| Registered style + svg restyle | Accept: re-capture |
| **Raster reconstruction on 16:9 and 1:1** | **Restore A4-only** until a non-A4 raster pilot is measured: quality has only been measured on A4 |

**Confirmed 2026-11-05:** as in the table (the recommended handling). If a
user turns out to rely on non-A4 raster reconstruction, report it to the
owner before restoring the A4-only rule. Do not keep it admitted silently.

### D5. Converter reproducibility versus stored export proofs

**Context (review §4.9 item 5).**
- `chart-furniture.16` and `native-fixed-text.1` were edited into
  `vendor/slide_master` without patch files.
- Rebuilding `MANIFEST.json` changes `converter_digest()` and invalidates every
  stored svg export proof, although the executed converter bytes do not
  change.
- The rebuild is held on branch `wip/s2n` (`30ed896b8`).

| Option | Effect |
|---|---|
| A. Keep holding | Reproducibility harness stays broken |
| B. Rebuild and accept one-time invalidation | Users must re-export old decks |
| C. **Add the patches and a reviewed digest alias table ("old digest → new digest, executed bytes identical"), enforced by a test that proves byte identity** | Proofs stay valid; reproducibility restored |

**Confirmed 2026-11-05: C** (the recommended option). Longer term, split the digest into "executed code
identity" and "provenance metadata" so metadata changes cannot invalidate
proofs.

## 3. Work items

Format: **ID — title** · Priority · Gate · then Goal, Change, Files,
Acceptance, Check.

### Phase 0 — measurement first (do these before any performance item)

**M1 — Record Anthropic cached tokens and cache-write cost** · P0 · no gate
- **Goal:** prompt-cache work (P1) becomes measurable.
- **Change:**
  - read `cache_read_input_tokens` and `cache_creation_input_tokens` from
    Anthropic usage into the logical-call accounting;
  - add the cache-write surcharge to the pricing model;
  - extend the pricing catalog entry schema if needed.
- **Files:** `services/provider_usage_cached_tokens.py`, the
  `public_provider_*` accounting and pricing modules,
  `utils/anthropic_structured_output.py`.
- **Acceptance:** a recorded fake Anthropic response with cache fields
  produces non-zero cached tokens and the correct cost in
  `public_provider_logical_calls`.
- **Check:** the unit test module of the cached-token parser.

**M2 — Separate the header stage in the timing receipt** · P1 · no gate
- **Goal:** the timing receipt stops booking header time under `outline`.
- **Change:** emit `header_design`, `header_representative` and
  `header_selection` as their own stages. Today they are added to `outline`
  (`generation_core/drivers/svg/collect.py:527→543`).
- **Acceptance:** an attempt-timing receipt lists the three stages, and
  `outline` no longer includes them.
- **Check:** the timing receipt unit test
  (`generation_core/drivers/svg/timing.py` tests).

**M3 — Instrument the vision review** · P1 · no gate
- **Goal:** the vision review's cost and effect become visible.
- **Change:** per deck, record
  - vision review calls and cost;
  - failed axes;
  - redesigns attempted and adopted.
- **Files:** `generation_core/drivers/svg/quality.py`,
  `quality_recovery.py`.
- **Acceptance:** the deck quality receipt carries these counts.
- **Check:** `tests/unit/test_svg_page_quality.py`.

### Phase 1 — correctness defects, no owner decision needed

**Q1 — Bind icon colour to the style** · P0 · no gate · review §4.8 items 1–2
- **Change:**
  - paint icons with the same accent the author and vector drawings use
    (`apply_style_defaults()`, `deck_palette()`) instead of `brand.primary`
    or `#1E5EFF` (`generation_core/assets/offer.py:352,384`);
  - carry the ink colour on `AssetSlot` instead of re-reading it with a regex
    (`asset_slots.py:55-57`), so fill-based Phosphor families get it too;
  - normalise badge stroke width to the main icon stroke
    (`icon_pack/families.py:107-110`).
- **Acceptance:**
  - for every built-in style, icon ink equals the style accent;
  - Phosphor regular/fill/duotone icons are no longer `#1E5EFF`;
  - badge and main stroke widths are within 10 %.
- **Check:** a new parametrised test over `authored_styles/*.yaml` next to
  `tests/unit/test_authored_icon_pack_sheet.py`.

**Q2 — Fallback pages that look like the deck** · P0 · no gate · review §4.5 item 1
- **Change:**
  1. The placeholder uses `_page_colors` and the cover body. Today it is fixed
     white (`drivers/svg/fallback.py:702`) with `_BODY_COLOR`.
  2. `source_fallback` picks text ink from the background with the existing
     `_ink_on` logic instead of fixed `#172033`
     (`drivers/svg/source_fallback.py:53, 168, 351`). The target is contrast
     ≥ 4.5:1.
  3. The body font floor follows the readability policy instead of 4 px
     (`fallback.py:49`).
  4. The title is drawn once: no header repeat in the body, and no title
     repeated in the cover body.
  5. A long cover title wraps to two lines before any cut. A cut happens on a
     word boundary.
  6. `terminal._fallback` catches the same exception set as the template path
     (`terminal.py:231`). v3 frames get a placeholder instead of failing the
     deck (`terminal.py:302-305`).
- **Acceptance:**
  - on `strategic-navy` and `default`, the cover, content and placeholder
    fallbacks render with style background and fonts at contrast ≥ 4.5:1;
  - no title text appears twice;
  - the D2 cover title ("2026년 국내 전기차 시장 분석: 판매 추이, 점유율, 가격대, 전망")
    renders without an ellipsis.
- **Check:** `tests/unit/test_svg_fallback_deck_style.py`.

**Q3 — Reduced header works for every built-in style** · P0 · no gate · review §4.10 item 1
- **Change:**
  - the reduced header v1 uses a heading font whose space advance is known.
    Measure the bundled Pretendard digests with the renderer and add them to
    `_CHROMIUM_SPACE_ADVANCE_EM_BY_FONT_DIGEST`
    (`services/svg_common_header.py:25-29`), or fall back to Noto Sans KR as
    the v2 design fields already do (`svg_common_header_design_calls.py:96-102`);
  - do not catch `svg_common_header_font_material_invalid` as a generic
    fallback: that would hide the cause.
- **Acceptance:** a regression test runs uniform-header preflight for every
  `authored_styles/*.yaml` and none raises.
- **Check:** that new test.

**Q4 — Header provider errors fall back instead of failing the deck** · P1 · no gate · review §4.10 item 2
- **Change:**
  - a provider failure or representative-page exception in the optional
    header stage takes the reduced-header fallback;
  - the failed call is not bought again: the `unknown` step state is kept
    (`svg_common_header_generation.py:293-297`, `:643-644`;
    `svg_common_header_design_plan.py:122`).
- **Acceptance:** a fake provider timeout in the design call yields a deck
  with `mode: reduced` and no second design purchase.
- **Check:** the header policy unit test module.

**Q5 — Failure classification** · P1 · no gate (land after Q2) · review §4.4 items 1–2
- **Change:**
  1. `measurement_incomplete` triggers one re-measurement without a provider
     call before the page is classed.
  2. `text_clipped` gets one deterministic local repair (shrink within the
     floor, or widen the frame). It is re-measured, and if it still clips it
     becomes a content kind (`quality/page_failure_kind.py:64`). Today clipped
     text is published.
- **Acceptance:** a fixture with clipped glyphs is either repaired or falls
  back; it is never published as flag-only.
- **Check:** `tests/unit/test_svg_page_quality.py`.

**Q6 — Keep units on PPTX chart value labels** · P0 · no gate · review §4.7 item 3
- **Change:**
  - `_source_data_label_format` (`drivers/svg/export_chart_style.py:608-631`)
    recognises a shared prefix/suffix (`%`, `억원`, `만대`, `명`, `$`) and
    emits an Office format such as `0"%"` or `#,##0"억원"`;
  - apply the same to the fixed value axis (`export_projection.py:190`).
- **Acceptance:** a native chart whose drawn labels are "30%" opens in
  python-pptx with a data-label number format that renders "30%".
- **Check:** `tests/unit/test_svg_chart_fidelity.py`. Investigate its current
  `c:dLbls` failure first (E7).

**Q7 — Detect drawn-chart proportion errors** · P1 · no gate · review §3.3 (D2 p6)
- **Change:**
  - add a geometry check: when a group has ≥ 3 bars sharing a baseline, each
    with a numeric label in one unit, bar extents must be proportional to the
    values within 3 %;
  - start it as advisory and promote it after precision is labelled.
- **Acceptance:** the D2 p6 SVG
  (`slide_5.svg` from the live run; recreate it as a fixture) is flagged, and
  D2 p2 is not.
- **Check:** the new detector's unit test.

**Q8 — Export font instancing without a cold cost per export** · P0 · no gate · review §4.9 item 1
- **Change:**
  - preferred: build static Noto Sans KR 400/700 instances at image build
    time and ship them as trusted digest-checked assets;
  - alternative: pass a dedicated read-mostly cache root to the isolated
    worker (`services/isolated_worker_process.py:46-95`,
    `services/svg_pptx_font_subset.py:146-153`).
  If the worker sandbox forbids the write or read, stop and report. Do not
  widen the sandbox silently.
- **Acceptance:** two consecutive 5-slide PPTX exports both complete
  instancing in under 2 s.
- **Check:** `tests/unit/test_svg_pptx_font_subset.py`.

**Q9 — Process spawn without `POSIX_SPAWN_SETSID`** · P1 · no gate · review §4.13
- **Change:** `utils/slide_capture_pipe.py:60` falls back to
  `subprocess.Popen(start_new_session=True)` when `os.POSIX_SPAWN_SETSID` is
  missing (uv's python-build-standalone).
- **Acceptance:** the five setsid test failures pass on a Linux uv
  interpreter.
- **Check:** the slide capture pipe test module.

### Phase 2 — performance

**P1 — Anthropic prompt caching for author, repair and vision calls** · P0 · no gate (needs M1)
- **Change:**
  - split the author system prompt into a deck-stable block (rules and design
    JSON) and a variable block;
  - move correction and visual-intent rules out of the middle of the system
    rules into the user message (`author_slide_svg.py:1176-1203`);
  - mark the stable block with `cache_control`, using the existing
    `cache_system_prompt()`;
  - warm the cache once, during the outline or header stage, before pages fan
    out, so five concurrent pages do not all pay the write surcharge;
  - drop operational metadata from the design JSON (review §4.3 item 2).
  This changes prompt bytes, so it needs a new authoring profile revision.
- **Acceptance:** in a live run, author/repair calls after the first show
  cached input tokens of at least 60 % of their system prompt.
- **Check:** a unit test asserting the cache breakpoint and the stable-prefix
  bytes across two pages of one deck.

**P2 — Stop paying a 400 on every vision review** · P0 · no gate
- **Change:**
  - remember, per process and per schema digest, that the grammar compiler
    rejected the schema, and go straight to prompt-JSON next time
    (`utils/anthropic_structured_output.py:697-718`);
  - do not paste the full schema twice in the fallback
    (`vision_advisory_prompt.py:1158`).
- **Acceptance:** one deck shows at most one grammar 400 per schema.
- **Check:** `tests/unit/test_anthropic_structured_output.py`.

**P3 — Effort and output caps for deck-level calls** · P1 · gate: measured with §5
- **Change:**
  - set an explicit effort and `max_tokens` for brief, outline, plan, icons
    and header titles on Anthropic (`utils/llm_config.py:474-513`);
  - allow a per-stage model (for example `claude-haiku-5-5` for icons, titles
    and plan) through a registered setting.
- **Acceptance:** paired runs show no quality loss (§5) and a wall-time drop.
- **Check:** the llm_config unit test module.

**P4 — Cheaper renders** · P0 · no gate · review §4.6
- **Change:**
  1. Embed a per-slide font subset (fontTools; cache by
     source-digest + weight + charset-digest) instead of the full 10.4 MB
     variable font (`services/svg_default_fonts.py:90-101`). Record source
     digest, subsetter version and charset digest as provenance. The
     measurement font must stay byte-identical in glyph outlines, so
     overlap and fit results do not change.
  2. Compute the render cache identity once per render from font digests,
     not font bytes (`utils/slide_capture.py:128-151`). Remove the parent-side
     font materialization (`:227-237`).
  3. Move PIL work, the metrics JSON round trip, checkpoint compare/fsync and
     PNG writes off the event loop with `asyncio.to_thread`.
  4. Set `set_render_deck_scope` for svg so production workers can be reused.
- **Acceptance:**
  - warm render ≤ 2.0 s on the review's fixtures;
  - event-loop stall < 0.1 s;
  - glyph-ink overlap results identical on the text-overlap corpus.
- **Check:** `tests/unit/test_svg_text_overlap_browser.py` (corpus parity).

**P5 — Start plan and icon selection in parallel with header design** · P1 · no gate
- **Change:** remove the representative-page manifest from the header design
  input (`svg_common_header_generation.py:227-230`), so the infographic plan
  and icon selection run concurrently with the design call.
- **Acceptance:** live timing shows plan and icons overlapping the header
  design stage.
- **Check:** the header design call unit test.

**P6 — Overload handling** · P1 · no gate
- **Change:**
  - sleep the cooldown outside the author permit (`drivers/svg/author.py:541`,
    `collect.py:1460`);
  - honour `Retry-After` (`services/provider_overload.py:519`);
  - share one cooldown per deck.
- **Acceptance:** a simulated 429 on one page does not block the other
  permits.
- **Check:** the provider overload unit test module.

**P7 — Provider-specific timeouts** · P2 · no gate
- **Change:** set the Anthropic author timeout from measured throughput (about
  320 s instead of 893 s) and add explicit timeouts to plan and icon
  selection (`select_deck_icons.py:108-119`).
- **Check:** the author effort/timeout unit test.

**P8 — Apply D1** · P0 · Gate: D1
- **Change:**
  - change `fresh_header_mode` and the UI default per D1;
  - set `common_header_cover_exception` default true
    (`models/generate_presentation_request.py:306`,
    `servers/nextjs/app/(presentation-generator)/services/api/presentation-generation.ts:433`);
  - enable divider/eyebrow on the reduced header.
- **Acceptance:** §5 paired runs. Target: −60 s or more per 6-slide deck, no
  drop in pass rate or judge score.
- **Check:** the header policy unit test module.

**P9 — Content brief cap** · P2 · gate: measured with §5
- **Change:** cap brief output and effort, or skip the brief for short
  prompts.
- **Evidence:** turning the brief off doubled flags on `claude-sonnet-4-6`
  (`docs/svg-brief-off-live-pilot-20261102.md`), so cap it rather than remove
  it.

**P10 — Vision review shape for 6–8 pages** · P2 · no gate
- **Change:** use one full review instead of local groups plus a serial global
  review when the page count is at most 8 (`drivers/svg/quality.py:444`), and
  downscale review images.

### Phase 3 — document quality

**S1 — Give the author the infographic plan** · P0 · Gate: D3
- **Change:**
  - pass each page's plan entry (archetype, nodes, emphasis, takeaway) to the
    svg author as an advisory block next to visual intent, with a short
    per-archetype composition hint (process, timeline, cycle, hierarchy,
    comparison, KPI);
  - apply the connector rule on those pages (`utils/content_structure_policy.py`
    `CONNECTOR_RULE`);
  - add `chart` and `table` to the plan vocabulary
    (`models/authored_infographic_plan.py:44-66`).
  New profile revision.
- **Acceptance:** pages planned as `timeline`/`process` render with
  connectors or steps in a live run, and the planned `hub_spoke` is drawn.
- **Check:** a prompt capture unit test asserting the plan block for a
  planned page.

**S2 — Deterministic `chart:` literal from sourced numbers** · P0 · Gate: D3
- **Change:**
  - when a slide's source states at least three labelled values in one unit,
    the server writes the `chart:` literal that `native_requirements.py`
    already understands, using only values present in the text;
  - extend the literal parser for `30%p`, `1조 2,000억원` and labels with
    commas (`native_requirements.py:77-91`).
- **Acceptance:** the D2 prompt produces native, editable charts for its
  sales and share series.
- **Check:** the native requirements unit test.

**S3 — Prompt restructure** · P1 · no gate (measured with §5)
- **Change:**
  - three layers in a fixed priority order, stated in one line at the top:
    (1) output contract, (2) design principles, (3) content fidelity;
  - add a per-style type scale and spacing tokens, an "information type →
    visual form" menu, and five lines of Korean width guidance consistent with
    the fallback width model (`drivers/svg/fallback.py:66`);
  - attach benchmark-specific blocks only when their signal is present;
  - resolve the pie-versus-no-curves contradiction;
  - remove the "report a readability failure" sentence;
  - ask for one `<text>` with `<tspan>` lines per paragraph;
  - target: system rules ≤ 2.5k tokens.
  New profile revision.
- **Acceptance:** §5 paired runs with no rise in first-attempt failures and a
  higher judge score.

**S4 — Paragraph text boxes in PPTX** · P1 · no gate
- **Change:** enable `merge_paragraphs` for ordinary decks
  (`workers/public_svg_pptx_convert.py:116`) behind an A/B measured with a
  LibreOffice render diff against the generation PNGs.
- **Acceptance:** multi-line paragraphs export as one text box, and the
  render diff stays within the current tolerance.

**S5 — Per-object native fallback in v2 export** · P1 · no gate
- **Change:** a native object that cannot be projected stays a drawn shape
  with a receipt reason, instead of failing the export
  (`workers/public_svg_pptx_convert.py:181-183`). This matches
  `export_native_wire.py:8-10`.

**S6 — Apply D2 (font floor)** · P1 · Gate: D2
- Advisory first (count would-be failures in M3's receipt), then the hard
  floor. Add an outline density cap.

**S7 — More native chart types** · P2 · no gate
- **Change:** allow stacked and percent-stacked charts (the converter already
  supports them, `vendor/slide_master/svg_to_pptx/native_objects/chart_data.py`)
  in `native_chart.py:57-62`, then waterfall.

**S8 — Font parity in the browser preview** · P1 · no gate · review §4.11 item 1
- **Change:**
  - serve the same bundled woff2 subsets to the preview iframe (CSP
    `font-src` limited to that origin);
  - render a slide the same way everywhere (keep the fetched SVG after
    deselection);
  - prefetch the next slide in the presenter.
- **Acceptance:** a Cypress component test asserts the iframe font family
  resolves to the bundled font.

### Phase 4 — engineering health

**E1 — Run the svg test subset in CI** · P0 · no gate
- **Change:**
  - add a Bitbucket step that runs the svg unit subset with `pytest-xdist`
    (`-n 4`, about 5 min);
  - update `scripts/required-ci-contract.test.mjs` with it.
  Land after E2/E4 so the step starts green.

**E2 — Re-capture stale goldens** · P0 · Gate: D4 for the admission baseline only
- **Change:**
  - re-capture `test_svg_design_16x9_baseline` (88) and
    `test_svg_canvas_brief` (24) for adapter `svg-style/v4`, reviewing the
    diff;
  - apply D4 to `tests/fixtures/canvas_admission_baseline.json` and, for
    non-A4 raster, to admission.

**E3 — Converter patches and digest alias** · P1 · Gate: D5
- **Change:**
  - add `chart-furniture.16` and `native-fixed-text.1` patch files under
    `compatibility/slide-master`;
  - align `CONVERTER_VERSION`;
  - add the alias table and the byte-identity test (D5 option C).
- **Acceptance:** `test_converter_patch_rebuild_matches_shipped_bytes` passes,
  and stored proofs still verify.

**E4 — Test hygiene** · P1 · no gate
- **Change:**
  - add skip guards for the 25 environment-dependent failures (Windows fonts,
    pythoncom, Office, platform font widths);
  - replace ad-hoc `SimpleNamespace` stubs with typed factories (22);
  - update the 22 expectations for intentional changes;
  - fix the 2 contract violations.

**E5 — Contain incident-specific recovery code** · P1 · no gate
- **Change:**
  - move the UUID, slide-count, model-name and slide-index literals of
    `svg_timeout_repurchase.py` and `svg_presentation_checkpoint.py:250-276`
    into a read-only verifier for the stored incident;
  - keep the general functions literal-free.
  If a live path still needs them to validate stored receipts, stop and
  report which receipt and why.

**E6 — Cache code-file digests** · P2 · no gate
- **Change:** wrap the per-call code-file hash functions in `functools.cache`
  (`svg_partial_recovery.py:144`, `common_header_verifier_digest()`).

**E7 — Investigate the two suspected regressions** · P1 · no gate
- `test_svg_chart_fidelity`: no `c:dLbls/c:showVal` in exported PPTX
  (`export_projection.py:158`, `export_chart_style.py:523,709`).
- `test_svg_page_quality[...][2]`: the visual review fails only under policy
  v2.

**E8 — Recovery simplification** · P2 · no gate · review §4.5 item 3
- **Change:**
  - make partial recovery the single resume path;
  - support SQLite;
  - fall back to cold validation on a prepared-episode cache miss;
  - remove `svg_cached_author_render_recovery` (pinned to a stale converter
    digest) and the v7-only repurchase path, after confirming no stored
    operation needs them.
  Target: about 15k LOC down to about 6k.

## 4. Suggested order

1. **M1, M2, M3** — measurement.
2. **Q1, Q2, Q3, Q6, Q8, P2** — independent; small to medium.
3. **P1, P4, Q4, Q9, E4** — then E1.
4. **Owner decisions D1–D5 (confirmed 2026-11-05)** → **P8, S1, S2, S6, E2, E3**.
5. **Q5, Q7, P5, P6, S3, S4, S5, S8.**
6. **P3, P7, P9, P10, S7, E5–E8.**

Each step is one change set with its single check. Live measurement (§5) runs
after steps 3, 4 and 5.

## 5. Measurement protocol

A single live run varies too much to decide on (`CLAUDE.md`, svg template
eval note). Use paired runs.

- **Harness:** `servers/fastapi/scripts/fake_llm_e2e/` in real-provider mode,
  as in `docs/handoff-svg-quality-20261103.md` "Live runs":
  - `FAKE_LLM_CLIENTS=0 LLM=anthropic ANTHROPIC_MODEL=claude-sonnet-5-5`;
  - the `anthropic-55-20261102` pricing catalog.
  Start and stop the backend with a pid file. Use a separate app-data
  directory per run.
- **Reference prompts:**
  1. "중소기업을 위한 생성형 AI 도입 전략: 현황, 기대 효과, 단계별 로드맵, 리스크 관리",
     6 slides, `exec-report`;
  2. "2026년 국내 전기차 시장 분석: 판매 추이, 점유율, 가격대, 전망" with these
     figures, 6 slides, default style:
     - 2023 16.2만대, 2024 14.7만대, 2025 18.5만대, 2026 22만대 (forecast);
     - share: 현대 38%, 기아 27%, 테슬라 18%, BMW 5%, 기타 12%;
     - price band 4천~6천만원 44%;
  3. one `strategic-navy` deck (dark style).
- **Pairing:** N ≥ 3 per arm, same prompt, alternate arm order.
- **Record per run:**
  - wall time and per-stage time (M2);
  - cost and cached tokens (M1);
  - logical calls and repairs;
  - gate result per page;
  - fallback pages;
  - vision review counts (M3);
  - PPTX native charts, tables and text boxes (python-pptx);
  - slide PNGs.
- **Decision rule:**
  - adopt a performance change when median wall time or cost improves and
    neither the pass rate nor the number of fallback pages gets worse;
  - adopt a quality change only with a human or calibrated-judge comparison
    of the PNGs, not on gate pass rate alone.
- **Expected impact (estimates):**

| Change | Saving |
|---|---|
| P8 (D1 = C) | −60 to −100 s, −25 % cost |
| P5 | −20 s |
| P1 | −8 to −14 % cost |
| P2 | −1 round trip per vision call |
| P4 | −1.5 to −2 s per render |
| Q8 | −10 to −25 s per PPTX export |

Combined target: 6 slides in 130–170 s at $0.7–0.9.
