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

## Deferred / re-sequenced

- [ ] **P4b — block CRUD (add/del/reorder) + schema-driven property panel.**
      **DEFERRED** behind THEME (reasonable-default decision, recorded here).
      Rationale:
      1. **Verifiability**: this is interactive UI; the click→edit→reorder
         round-trip cannot be verified in the headless autonomous loop
         (no click simulation in `--dump-dom`). Building unverified interactive
         UI conflicts with the project's "verification mandatory" guardrail.
         A clean validation needs Cypress component tests or a human in the loop.
      2. **Lower marginal value**: P4a already gives deterministic text editing;
         image/icon editing already works (EditableLayoutWrapper's recursive
         marker search finds nested adaptive markers); and **the export is
         fully-editable PPTX**, so structural edits can be done in PowerPoint.
      3. **Non-uniform model**: bullets / comparison use nested `items[]` while
         card-grid / stat-hero / timeline use repeated top-level blocks, so a
         generic CRUD UI is a sizable, careful effort.
      4. **Structure pressure**: `EditableLayoutWrapper` is already 472/500 lines;
         affordances would need a new overlay component.
      When picked up: add `lib/adaptiveBlockEdit.ts` CRUD helpers (item-level for
      `items[]`, block-level for repeated top-level blocks) + node tests, then a
      minimal always-visible-in-edit affordance, validated via Cypress.
- [ ] **Text auto-fit (JS fit-to-box)** — deferred in P5 (TODO in `parts.tsx`).
      Fixed sizes + `overflow-hidden` + composer maxLength bounds suffice for now;
      revisit if overflow is observed at max density.

## Next (priority order)

- [ ] **THEME** — tailwind token binding; backend v2 theme generation
      (`theme_data.py` nested, `theme_generate.py` style preset); heading≠body
      font split; unify the 4 duplicate `applyTheme` sites. (Headless-verifiable.)
- [ ] **P6 / G8** — composer-stability acceptance metric (schema-valid · variety ·
      n_slides) + thresholds.
- [ ] **P6 / G4** — editable-PPTX byte round-trip in Docker/Linux/CI
      (Windows lacks `convert-win32.exe`). **External blocker — needs Docker.**
- [ ] **DOCS / G10** — chart/table fidelity notes; one-shot adaptive skips TOC
      (composer emits agenda/section-divider natively); `n_slides='auto'` adaptive
      path; theme §3.4 preset values; Phase 1/2 file-list rebaseline; §13.
