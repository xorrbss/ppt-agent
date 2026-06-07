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
      `continue-on-error`) — verified green in CI (run 27094181261). Other `.cy`
      specs stay ungated (next-adapter broken on Next 16).
- [ ] **Text auto-fit (JS fit-to-box)** — deferred in P5 (TODO in `parts.tsx`).
      Fixed sizes + `overflow-hidden` + composer maxLength bounds suffice for now;
      revisit if overflow is observed at max density.

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
  - [ ] **v2 per-theme typography scale / shape** — DEFERRED YAGNI: `theme_generate`
        is colors-only by design; `deriveThemeTokens` defaults for fs-scale / radius
        / shadow work across themes. Add additive+versioned if richer per-theme
        typography is wanted.
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
      big-statement→closing). **FINAL pass/fail thresholds are a product decision
      (escalation)** — proposed defaults in `composer_metrics.DEFAULT_THRESHOLDS`
      (schema 1.0, n_match 1.0, mean variety ≥0.6, no-adjacent-dup ≥0.9).
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
6. **fit synchronicity** — JS fit-to-box DEFERRED (KISS): fixed per-archetype
   sizes + `overflow-hidden` + composer `maxLength`/`maxItems` bounds. No
   transform. TODO in `adaptive/parts.tsx`.
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
- **one-shot adaptive + TOC**: the `/generate` adaptive branch skips legacy TOC
  insertion (the composer emits `agenda` / `section-divider` natively). So
  `include_table_of_contents` yields N content slides without a separate TOC
  layout on the adaptive path.
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
