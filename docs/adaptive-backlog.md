# Adaptive layout redesign — backlog & status

Single-source progress tracker for the content-first adaptive slide system
(theme = tone & manner; AI composes a fresh archetype per slide; editable PPTX
preserved). Design authority: `adaptive-layout-design.md` +
`adaptive-layout-design-revision.md` (R1–R7).

## Status

- [x] **Phase 1** — renderer + spec scaffold (3 archetypes), DOM/export proof.
- [x] **Phase 2** — tone & manner theme tokens (`presentationThemeTokens.ts`).
- [x] **Phase 3** — AI composer (interactive `/prepare`+`/stream`) + foundation
      (per-archetype models, `deck_plan` column).
- [x] **P3-1** — one-shot `/generate` adaptive branch (`compose_and_project` +
      `get_layout_by_name._build_adaptive_layout`). commit `62916c8b`.
- [x] **P5** — full 14-archetype library + new blocks (chart-SVG / table / image
      / icon / card / column / step / quote) + density helper + composer
      kind-matching & adjacency. Adversarially reviewed (21 raw → 3 low fixed).
      commits `32787b6c`, `d883f829`, `1f6b1025`, `60c621b7`, `18c4079e`.
- [x] **P4a** — deterministic `data-block-id` editor binding (R2). Shared
      `lib/adaptiveBlockEdit.ts` (read/write resolver) + `TiptapTextReplacer`
      block-id grafting + `updateAdaptiveBlock` reducer + caller routing + node
      regression test (8 checks incl. no-misbind). commit `9959fc11`.

## Rollout — adaptive is now the DEFAULT (decided)

The content-first adaptive composer is the **default** for new decks (option 1 of
the rollout fork — flip now, accept that byte-PPTX export is DOM-verified only
until G4). Changed defaults `"korean-biz"`/`"general"` → `"adaptive"` in:
`store/slices/presentationGenUpload.ts` (UI initial selection),
`presentation-templates/select.ts` (`DEFAULT_TEMPLATE_ID`), and
`scripts/ppt-agent.mjs` (CLI).

**Legacy retirement (post-G4).** Now that G4 is green, the upstream generic
fixed-template groups (general / modern / standard / swift / code / education /
product-overview / report / pitch-deck / neo-*) are **retired from the NEW-deck
pickers** via `RETIRED_GROUP_IDS` + `selectableTemplates` in
`presentation-templates/index.tsx` (consumed by `outline/TemplateSelection.tsx`
and dashboard `TemplatePanel.tsx`). The fork's curated groups (korean-biz /
financial-chart / comparison-table / roadmap / org-chart) are kept alongside
`adaptive`. **Rendering is untouched** — `templates` + `getLayoutByLayoutId` keep
ALL groups registered, so EXISTING decks (incl. retired groups) still render
(verified: an existing `general` deck renders main-slide=2; adaptive renders 7;
build healthy, tsc=0). The "(베타)" label is removed (적응형). To un-retire a
group, drop its id from `RETIRED_GROUP_IDS`.

**Risk accepted:** new decks default to adaptive whose PPTX export is verified at
the DOM-contract level (canvas=0, real leaves, 1280×720) but NOT yet at byte level
on Windows (G4 needs Docker/Linux + `convert-win32.exe`). Recommended follow-up:
build + run the G4 byte-PPTX round-trip gate in Docker/CI, then it is fully
de-risked. ADAPTIVE auto-routing (ignore template) and legacy removal were NOT
done (kept coexistence + user choice).

## Deferred / re-sequenced

- [x] **P4b — block CRUD (add/del/reorder).** DONE (user-requested after the
      initial deferral). `lib/adaptiveBlockEdit.ts` CRUD helpers (locateUnit /
      delete / move / addAdaptiveUnit) handle the non-uniform model uniformly
      (top-level card/stat/step blocks AND nested bullets/column `items[]`), with
      16/16 node tests; `presentationGeneration` slice reducers delegate to them;
      a new edit-only `AdaptiveBlockControls` panel (mounted in
      `EditableLayoutWrapper` for adaptive content) lists repeatable units with
      move/delete/add controls. Verified: tsc=0; node tests 16/16; editor render
      shows the `블록 편집` panel with correct unit labels (불릿/카드/지표/단계);
      **export-clean** (absent from the readOnly `/pdf-maker` DOM); legacy decks
      unaffected (panel gated on `archetype`). The **click round-trip**
      (click→dispatch→store) is **verified via Cypress**:
      `AdaptiveBlockControls.cy.tsx` mounts the panel with the real store and
      asserts delete/add/move/card-delete mutate `slide.content.blocks` — **5/5
      passing**. **Schema-driven property panel: deferred YAGNI** — inline text
      editing (P4a) + image/icon pickers + item CRUD cover the common cases.

      **Cypress infra fix (Next 16):** the cypress binary install hangs on its
      default downloader, and cypress 14's `framework: "next"` devServer expects
      Next's compiled webpack (`.init`), which Next 16 (Turbopack) no longer ships.
      Fixed both: install the binary from the CDN directly
      (`CYPRESS_INSTALL_BINARY=<cdn zip>`, extracted via Python since cypress's
      unzipper also stalls on Windows), and switch `cypress.config.ts` to
      `framework: "react"` + a minimal webpack pipeline (babel-loader + `@` alias).
      So component tests run again. **Gated in CI**: the `test-all` workflow runs
      this spec via `cypress-io/github-action` (scoped to AdaptiveBlockControls, no
      `continue-on-error`) — verified green in CI (run 27094181261).
- [x] **Upload-page component test (backlog #7)** — DONE. The upstream
      `UploadPage.cy.tsx` tested behavior this fork removed (English "Next"
      button, `/theme` + `/documents-preview` navigation, a `research-mode-switch`,
      titles/report endpoints) and broke under Next 16's next-adapter. Rewritten
      against the fork's actual surface (`framework: react`+webpack config): the
      Korean upload page mounts with all controls, slide-count select, file
      attach + sonner toasts, advanced-settings modal opens, and empty-input
      validation — **9/9 passing locally**. Mount wrapper adds `AppRouterContext`
      + `<Toaster/>` (sonner only renders with a Toaster in-tree). The dead
      language-picker test was dropped: the fork has **no language UI** (defaults
      to Korean; `LanguageSelector` is unused dead code — left untouched, separate
      cleanup). Now **gated in CI** alongside AdaptiveBlockControls (`test-all.yml`
      cypress step, combined
      `**/AdaptiveBlockControls.cy.tsx,**/UploadPage.cy.tsx` spec —
      validated green together locally, 14/14).
- [x] **High-density overflow — measured + density-aware sizing (backlog #1).**
      DONE. anti-YAGNI "measure first": mounted each high-risk archetype at its
      schema-MAX item count at a real 1280×720 box (Korean text — the product
      default and ~full-width, the realistic worst case for wrap height) and
      flagged any leaf whose rect escaped the `overflow-hidden` root.
      **Measurement method** (re-creatable): cypress component mount of
      `AdaptiveSlide`; the cypress webpack config loads CSS as a string, so inject
      the app's real compiled utilities (`tailwindcss -c tailwind.config.ts -i
      app/globals.css -o <tmp> --minify`) so `aspect-video`/`overflow-hidden`
      actually constrain the box; measure leaf `getBoundingClientRect` vs root.
      **Result (max item counts):** at realistic text lengths, `comparison` (3×6)
      clipped −211px, `table` (6×8) −65px, `card-grid` (8) −57px; `agenda` (8)
      fit (+80px). At absolute schema-max char lengths everything clipped badly
      (comparison −1109px). → clipping IS observed at the named max densities.
      **Fix:** deterministic density step-down (extends the existing count-based
      pattern, e.g. `AgendaLayout`'s `twoCol = items.length > 4`) — when an
      archetype is at high item count, shrink type + padding one/two notches via
      the existing `--fs-*` tokens + tighter Tailwind spacing.
      `CardGridLayout` (n≥7), `ComparisonLayout` (maxItems≥5, ≥6), `TableLeaf`
      (rows≥7). **No transform, no useLayoutEffect** → SSR/headless-deterministic,
      export DOM stays clean (same `data-block-id` leaves, just smaller CSS
      values); legacy untouched (adaptive-only); `agenda` left as-is (fits).
      **Verified:** re-measured — all realistic max-count profiles now fit;
      `[max]` absolute-ceiling improved (comparison −1109→−316, table −214→−21)
      and still degrades gracefully via `overflow-hidden`. tsc=0. The pathological
      all-fields-at-max-char ceiling is rare composer output; the lever for it is
      tightening composer bounds (a #6-style product decision), not the renderer.
      Measurement harness removed after recording (temp); byte-PPTX export safety
      is the CI G4 gate.

## Next (priority order)

- [~] **THEME** — partially done; remainder deferred (rationale below).
  - [x] applyTheme consistency: `PresentationCard` now routes through the shared
        `applyPresentationThemeToElement` (de-dups + applies the Phase-2 extended
        adaptive tokens), so adaptive-deck thumbnails match the editor/export
        render. Legacy base vars unchanged. (`ThemePanel` previews a legacy
        `neo-general` template, so it needs no adaptive tokens — left as-is.)
  - [x] **heading≠body font split** — DONE. The apply sites
        (`applyPresentationThemeDom.ts`, `PdfMakerPage.tsx`) now read optional
        `fonts.headingFont` / `fonts.bodyFont` → `--heading-font-family` /
        `--body-font-family` (v1 themes carry only `textFont`, so heading=body=
        textFont — unchanged, legacy-safe). Curated pairs added to default themes:
        editorial = Playfair heading + Inter body (fixes display-font body
        readability); professional-blue / professional-dark = Space Grotesk
        heading. Verified: v2-theme deck renders heading=Playfair ≠ body=Inter
        (both fonts loaded, canvas=0); legacy v1 deck unchanged.
  - [x] **v2 per-theme density** — DONE. `deriveThemeTokens` reads optional
        `theme.data.density` (compact / comfortable / spacious) → drives the
        spacing tokens (slide-pad, section/block/inline gap). v1 themes carry no
        `density` → "comfortable" = the current look (unchanged, legacy-safe).
        Default themes: editorial = spacious, professional-dark = compact, rest
        comfortable. Verified: spacious deck renders pad-x 112 / section-gap 44
        (vs 80/32), canvas=0; v1 deck unchanged. With the font-pair (above), v2
        themes now vary color + fonts + density.
  - [x] **v2 per-theme typography / shape / elevation (#3)** — DONE (user asked
        for a way to offer "new templates" via adaptive → this is the enabler).
        `deriveThemeTokens` now reads optional `theme.data.typography` (scale +
        weights/line-heights/letter-spacing), `.shape` (radiusScale + borderWidth),
        `.elevation` (flat → no shadows, or explicit), `.motif` (colour/opacity) —
        additive + versioned, **byte-identical to v1** when absent (verified), and
        a length-coercion helper (`len()`) guards numeric inputs. Export-clean
        (token values only); legacy decks ignore the extended tokens. commit
        `acaaff0a`. **Adaptive "templates" = theme presets:** 3 packs added that
        exercise the new dims — `carbon` (dark/sharp/flat/tight), `pebble`
        (warm/rounded/elevated/airy), `broadsheet` (editorial serif/spacious) —
        in `ThemePanel/constants.ts`, commit `6da12058`. Adversarially reviewed
        (9 findings → 3 fixed: `saveAsCustom` now spreads `theme.data` so the new
        keys + density round-trip; numeric length coercion; pebble contrast
        #c0613a→#ad5230 for WCAG AA), commit `4065ff55`. **Follow-ups (Step C,
        optional):** theme-gallery picker UI for adaptive · `theme_generate` =
        style-preset + AI colours · surface saved user themes as "my templates".
  - [ ] tailwind token binding — **YAGNI** (the adaptive renderer already uses
        inline `var(--…)` tokens; tailwind class binding adds no behavior).
  - [ ] tailwind token binding — **YAGNI** (the adaptive renderer already uses
        inline `var(--…)` tokens; tailwind class binding adds no behavior).
- [x] **P6 / G8** — composer-stability acceptance metric. `utils/composer_metrics.py`
      (`evaluate_archetypes` / `summarize` / `passes` + proposed thresholds) with a
      CI unit test (`tests/unit/test_composer_metrics.py`, 6 passing) and a real-codex
      harness (`scripts/measure_composer_stability.py`). **Measured (6 decks = 2
      varied golden outlines × 3 reps, live Codex gpt-5.5):** schema-valid 100% ·
      n_slides match 100% · mean variety-ratio 0.95 · no-adjacent-dup rate 100% ·
      total adjacent dups 0 → PASSES the proposed thresholds with margin. Deck 1
      composed an identical 9-distinct archetype sequence across all 3 reps
      (cover→agenda→stat-hero→timeline→comparison→card-grid→chart-insight→
      big-statement→closing). **Thresholds FINALIZED (#6, product decision 2026-06):**
      `composer_metrics.DEFAULT_THRESHOLDS` (schema 1.0, n_match 1.0, mean variety
      ≥0.6, no-adjacent-dup ≥0.9) — adopted as the official G8 gate (variety/no-dup
      keep headroom below the measured 0.95/1.0 so normal drift doesn't flap). The
      "proposed/escalation" language is removed from the code. **Policy:** adaptive
      stays the **default with opt-out** (users can still pick curated templates
      korean-biz/financial-chart/etc.); **not** forced — KISS + user choice. To
      force adaptive later, drop the non-adaptive groups from `selectableTemplates`.
- [x] **P6 / G4** — editable-PPTX byte round-trip. **RUN in Docker and PASSES**
      (`scripts/check_adaptive_pptx_roundtrip.py`): a 7-archetype adaptive deck
      (cover / stat-hero / bullets / comparison / table / chart-insight / image-led)
      exports to PPTX via the real runtime and reopens with python-pptx as **7
      slides, all content editable text** — cover title, 8 stat text shapes, bullet
      text, comparison headings, image-led title+caption; chart and table render as
      image + extracted editable text (the converter rasterizes charts/tables/icons;
      native PPTX table is not a converter feature). **image→picture embedding is
      verified** — the harness writes a real PNG under the FastAPI-served `/app_data`
      and the image-led slide embeds it as a PPTX picture (`pic` asserted; a data-uri
      does not embed, a real fetchable URL does). So the adaptive→editable-PPTX path
      is byte-verified across text, table, chart, and image archetypes.

      **Required converter upgrade (key finding):** the pinned **v0.2.9 crashes**
      on the adaptive slides' SVG (every slide's decorative `<svg>` Motif + chart/
      icon SVGs) — `screenshotElement … cleanup … "A boolean was expected"` → HTTP
      500, no PPTX. **v0.3.3 fixes it** (export succeeds). v0.3.x changed the
      release layout (binary `convert-linux-x64` + `index.js` at the archive root;
      v0.2.x had `py/convert-…` + `index.cjs`), so adopting it needs: bump
      `presentationExportVersion` → v0.3.3, teach `export_task_service`
      `_resolve_converter_path` / entrypoint resolver the new layout (additive),
      update `sync-presentation-export.cjs` extraction, then **validate legacy
      export** still works. This is product-wide (affects all export) — a
      deliberate follow-up, not done here.

      **Local Docker run notes (Windows):** the dev stack must run from the WSL
      ext4 FS, not the `/mnt/c` bind-mount (Next.js dev fails to acquire its
      lockfile on the Windows FS bridge), and `CYPRESS_INSTALL_BINARY=0` is needed
      (cypress's postinstall binary download hangs). Run with `DISABLE_AUTH=true`
      so the export can read `/pdf-maker` without a session.

      **CI:** `.github/workflows/g4-pptx-roundtrip.yml` runs this gate in Docker on
      a Linux runner. **Validated green in real CI** (run 27089398688, 4m52s — both
      adaptive G4 and the legacy smoke passed) and **promoted to a PR-gate** (`push`
      + `pull_request` to `main`, plus `workflow_dispatch`; Linux runner avoids the
      `/mnt/c` issue). The
      P4b adaptive block-edit node tests now run in `test-all.yml` (Node 22, TS
      type-stripping). A **legacy-export smoke** (`scripts/check_legacy_pptx_roundtrip.py`)
      runs in the same G4 workflow and **PASSES** — a 2-slide `general`-template
      (non-adaptive) deck round-trips to editable PPTX with v0.3.3, so the converter
      bump leaves legacy export empirically unaffected.
- [x] **DOCS / G10** — design `§13` open questions reconciled with the build +
      G10 minors (below). Source-of-truth design docs are unchanged (frozen);
      this living doc records the implementation outcome.

## §13 open-question resolution (post-build)

Against `adaptive-layout-design.md §13`:

1. **capacity walker / prefixItems** — RESOLVED. Adaptive capacity is the
   DECLARED `ARCHETYPE_PROFILES` (revision R1); `compute_layout_capacity` is
   never run on adaptive, so the union/prefixItems undercount never arises.
2. **DOM-contract co-version (data-block-id harmless to v0.2.9)** — PARTIAL. The
   converter maps by computed style and ignores `data-*`; headless render shows
   canvas=0 + the full DOM scaffold + real text leaves. Byte-level shape-count
   proof is the Docker/CI gate **G4** (Windows lacks `convert-win32.exe`).
3. **bullets → shape unit** — to be confirmed by the G4 byte round-trip (each
   `<li>`/leaf carries `data-block-id`; renderer emits one leaf per item).
4. **chart edit fidelity** — chart is a Recharts **SVG** (not the canvas-based
   GeneralChart); export-time native-vs-vector fidelity is a G4/CI check.
5. **motif as SVG** — low-intensity decorative `<svg>` behind content; safe to
   flatten.
6. **fit synchronicity** — measured (backlog #1): max item counts clip, so high
   density now triggers a **deterministic density step-down** (type + padding) in
   `card-grid`/`comparison`/`table` — no transform, no useLayoutEffect (SSR/
   headless-deterministic, export-clean). Realistic max-count decks fit; the
   absolute schema-max-char ceiling still degrades gracefully via
   `overflow-hidden`. JS measure-and-shrink fit-to-box remains unneeded.
7. **split responsibility** — composer-native only. `validate_composition` /
   `_split_content` are NOT used on adaptive (closed schema bounds enforce
   capacity at generation; revision R1).
8. **legacy migration** — permanent coexistence, no migration. All adaptive
   logic is gated on `layout_group == "adaptive"`; legacy decks render via the
   old TSX unchanged (verified: korean-biz deck renders identically).
9. **composer strict/retry stability** — verified. `generate_structured_with_
   schema_retries` over the closed discriminated union; G8 measured 100%
   schema-valid + 100% n_slides match over 6 live decks.
10. **asset deep-walk** — verified (revision R6). `get_dict_paths_with_key`
    recurses dicts+lists; P5 test confirmed nested `__image_url__`/`__icon_url__`
    markers (two-column image, card-grid icon) are found by the asset pipeline.

## G10 minor notes

- **chart/table fidelity**: chart = Recharts `<svg>` with animation disabled
  (final geometry present at capture); table = real `<table><tr><td>`. Both
  carry `data-block-id`. Editable-PPTX fidelity confirmed only at the DOM level
  locally; shape-level fidelity is the G4 gate.
- **one-shot adaptive + TOC (backlog #8, RESOLVED — documented):**
  `include_table_of_contents=true` **is honored** on the adaptive `/generate`
  path, just not via the legacy template TOC layout. The flag flows into outline
  generation (`get_outline_messages` + `generate_ppt_outline` both receive it,
  `presentation.py` ~739/769 → system prompt "Include a table of contents slide
  in the outline sequence"), and `n_slides_to_generate` reserves a slot for it
  (~721). So the generated **outline contains a TOC slide**, which
  `compose_and_project` composes into a **native `agenda` archetype** (archetype
  profiles define agenda as "agenda / table of contents", so the composer's
  kind-matching maps it). The legacy `_insert_toc_layouts` is intentionally
  confined to the non-adaptive `else` branch (~894) — so there is **no double
  TOC** and **no dropped TOC**: adaptive gets exactly one TOC, rendered natively
  as an editable agenda slide (not a fixed template TOC layout). This is the
  intended design; verified by code trace. Empirical confirmation belongs to the
  live e2e (backlog #10). No code change — wiring is already correct.
- **`n_slides='auto'`**: `get_composition_model_with_n_slides(None)` leaves the
  count unconstrained (composer decides). The interactive `/prepare` branch
  composes exactly one SlideSpec per provided outline slide.
- **slides_markdown × composer**: the adaptive composer always works from the
  outline (not the raw `slides_markdown` images path); markers flow normally.
- **theme §3.4 preset values**: N/A until v2 theme generation lands (deferred).
- **Phase 1/2 file-list rebaseline**: the renderer was split (P5 foundation) into
  `adaptive/parts.tsx` (leaf primitives) + `adaptive/layouts.tsx` (14 archetype
  layouts) + slim `AdaptiveSlide.tsx`; editor binding lives in
  `lib/adaptiveBlockEdit.ts`; composer metrics in `utils/composer_metrics.py`.

## Autonomous backlog sweep — 2026-06-08

Worked the residual backlog (items 1–11) as an autonomous loop. **Done this sweep
(all pushed):** #7 UploadPage cypress spec rewritten to the fork surface + gated in
CI (9/9; combined gate 14/14); #1 high-density overflow measured (clips at max item
counts) → deterministic density step-down (`density.ts`, extends the count-based
pattern; no transform/effect; export-clean; review-confirmed `<500`-line cleanup);
#8 TOC-on-adaptive behavior documented (honored via outline→native agenda, no
double/dropped TOC); #11 local WSL dev container + `presentation-export.v033`
scratch removed. An adversarial-review workflow over the two code commits returned
8 findings dismissed (refuted) + 1 confirmed (the line-count breach, since fixed).

**Remaining = decision-gated (not done autonomously, by design):**
- **#2 schema-driven property panel** — backlog-marked **YAGNI** (inline text +
  image/icon pickers + item CRUD cover common cases). Build only on request.
- **#3 v2 per-theme typography/shape/elevation** — **DONE** (no longer YAGNI:
  user asked to offer new "templates" via adaptive → this is the enabler).
  Per-theme typography/shape/elevation/motif tokens + 3 presets (carbon/pebble/
  broadsheet) + review fixes. commits `acaaff0a`, `6da12058`, `4065ff55`.
  Optional follow-up: theme-gallery UI · AI style+colour generation · saved themes.
- **#4 chart multi-series / block drag-and-drop** — niche; build only if needed.
- **#10 live LLM e2e** — `userConfig` provider is `codex` but **no CODEX key/auth
  is configured** (only an OPENAI key is set); a full browser e2e also needs the
  dev stack + an e2e harness. Skipped + recorded; can run an OPENAI-based e2e, or a
  codex one once auth is provided.
- **#5 legacy fixed-template hard delete** — **DONE (user-authorized, `434d560e`).**
  Removed all 13 retired groups (general/modern/standard/swift/code/education/
  product-overview/report/pitch-deck/neo-*) — 192 files, ~51k LOC. Prereqs:
  decoupled `financial-chart` from `general/GeneralChartPrimitives` (moved into
  financial-chart/); repointed ThemePanel's theme-preview from `neo-general` →
  `korean-biz`. `index.tsx` now registers only the 6 kept groups; RETIRED_GROUP_IDS
  is empty. **BREAKING:** existing decks referencing a removed group render the
  "layout not found" placeholder (accepted, no migration). Verified tsc/build/
  cypress 15·no stray refs. (Supersedes the "Legacy retirement (post-G4)" picker-
  hide note above — code is now deleted, not just hidden.)
- **#6 G8 acceptance thresholds / adaptive policy** — **DECIDED** (2026-06):
  thresholds finalized at `DEFAULT_THRESHOLDS`; adaptive = default **with opt-out**
  (not forced). See the P6/G8 entry above.
- **#9 Windows native desktop export** — **BLOCKED (external dependency).**
  - violated: 검증 필수 / cannot build without the artifact.
  - reason: the export runtime is an **external versioned package** (`presentationExportVersion`
    v0.3.3) whose release ships only a **linux** converter — `sync-presentation-export.cjs`
    `getConverterCandidates` resolves `convert-linux-x64` / `-amd64` / `convert`, and there is
    **no `convert-win32`/`.exe`**. `export_task_service` looks for a `.exe` on Windows (os.name
    == "nt") but none exists, so native Windows desktop export can't run. Web/Docker work because
    they execute the **Linux** converter. The converter's source is NOT in this repo, so the
    win32 binary cannot be produced here.
  - required_change: (1) upstream publishes a win32 build of the export-runtime converter (a
    `convert-win32-x64.exe` release asset), OR build that converter project for win32 separately;
    (2) add the win32 candidate to `getConverterCandidates` + the sync extraction; (3) bundle it
    in the Electron packaging. (1) is the gating step and is outside this repo.
