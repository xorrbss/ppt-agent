# Canvas expansion — decisions and index (rev 4, 2026-09-29)

This is the stable decisions document. Each workstream has (or will have) its own spec that is reviewed and locked independently:

| Workstream | Spec | Status |
|---|---|---|
| Admission and stages | `docs/canvas-expansion-admission-spec-20260929.md` | written from an executed truth table; awaiting independent review |
| Content budget | `docs/canvas-expansion-content-budget-spec-20260929.md` | written from the outline stage's real knobs; awaiting independent review |
| Quality chain (prompts, repairs, reuse, export) | §3 below | inventory verified; to be extracted into a spec |
| Design-pack variants | §5 below | outline only; needs a manifest v2 spec |
| Picker and 자동 | §6 below | outline only |
| Bench gate | §8 below | outline only; needs a statistics spec |

## Context

The upload flow exposes two disjoint canvas selectors: an aspect-ratio `<select>` over `16:9` / `9:16` / `9:13` and — when the A4 capability is on — an A4 orientation `<select>` that replaces it (`ConfigurationSelects.tsx:247-285`). The product wants one picker — `자동`, `A4 가로` (default), `A4 세로`, `16:9`, `9:16`, `1:1`, `21:9`; `4:3` excluded — with one non-negotiable requirement: **output quality must not drop on any canvas**, speed second.

Method (adopted after two independent reviews of earlier revisions scored 54/100 for the same reason — mechanisms inferred from reading multi-branch code): the two hardest areas, admission and content budget, are specified from **executed** truth tables and the outline stage's real knobs, and every other spec must cite code the same way. The admission refactor that earlier revisions proposed is dropped: it was not required by the quality goal and was the source of every P0.

## Priorities and the four parity conditions

| Priority | Implication |
|---|---|
| Quality parity | Every geometry-reading stage — brief, measurement, repair, review, export — operates on the deck's canvas, for both drivers (authored HTML and svg). A canvas × style combination is exposed only after the parity bench passes. |
| Speed second | No additional LLM call per slide on any canvas; outline-stage deck-level calls stay ≤ 3. `자동` is client-side. Checkpoint reuse and repair paths are canvas-neutral. |

1. Canvas-aware quality chain (§3). 2. Per-canvas content budget (budget spec). 3. Per-canvas design-pack masters (§5). 4. Bench-gated exposure (§8) with machine-written stage records.

## Decisions

| # | Decision |
|---|---|
| 1 | A4 rows = the existing real-A4 document canvas (`document_canvas_for_orientation()`: 1188×840 / 840×1188, EMU = px × 9000, 60px safe margin — `services/document_canvas.py:107-116`). No 13:9 preset. |
| 2 | A4 landscape is the product default (already true: `useUploadPageState.ts:150-152`). API default `aspect_ratio="16:9"` unchanged — *assumption*. |
| 3 | New presets `1:1` (720×720) and `21:9` (1680×720); presets keep EMU = px × 9525 and the 64px safe margin, so existing decks and digests are untouched. |
| 4 | `9:13` stays registered (existing decks, API, `kbs-company-proposal`), hidden in the picker — *assumption*. |
| 5 | **Admission is not refactored.** Both preset branches and the A4 route policy stay; the new presets get a stage gate in front of the preset branches (admission spec §3). |
| 6 | `자동`: client-side; meaningful only with a source document; label "문서 비율에 맞춰 선택". |
| 7 | **Persisted form unchanged**: A4 → `theme.documentCanvas`; presets (incl. new) → `theme.authored_vnext.canvas`. The `document-canvas/v1` capability negotiation (`require_canvas_capability`, 409) does not widen. |
| 8 | Canvas registry `contracts/canvas/v1/registry.json` is **generated** from the existing constructors (`document_canvas_for_orientation`, `canvas_metadata_for_aspect_ratio` + `canvas_physical_size`) with a `--check` mode; it is the source for the picker, the prompt shape, and the budget factor — not for admission. |
| 9 | Design packs: per-canvas variants under a manifest v2 (own spec); `document_canvas_contain` only via an explicit request option. |
| 10 | UI: one custom dropdown (coloured A4 badges) replaces both `<select>`s. |
| 11 | **No exposure regression**: the executed baseline table (admission spec §2) is a checked-in regression fixture; every existing row must stay byte-identical. |

## Facts that earlier revisions got wrong (settled by execution)

- A4 admits `master_pin` (fixed-master route) but **not** `design_pack_pin` — `registered-template` mode is unsupported by `select_a4_route` (`document_canvas_capabilities.py:157-170`). A4 + design pack needs its own route (pack-variant workstream).
- Portrait presets admit source restyle on **every** style (`authored_canvas.py:290-302`); only free-design topic decks consult `supported_aspect_ratios_for_style`.
- 16:9 admits everything on the authored driver; svg never admits design packs on any canvas.
- The svg default driver is told the canvas size (`project_style.py:62`) but has no shape composition guidance.
- The portrait rewrite of the inset arithmetic never matched (`author_slide_rules.py:141-142` wraps the phrase); presets other than 16:9 get no DOCUMENT CANVAS clause today (`author_deck.py:1325`, `document_canvas_prompt.py:6-7`).
- `browser-source.ts:47` blocks A4 **and** 9:13 through authored-hybrid today.

## Picker rows

| Row | canvas id | logical px | Default | Exposure |
|---|---|---|---|---|
| 자동 | — | — | — | always |
| A4 가로 | `a4-landscape` | 1188×840 | **yes** | A4 candidate/verified stage (unchanged) |
| A4 세로 | `a4-portrait` | 840×1188 | | same |
| 16:9 | `wide-16x9` | 1280×720 | | always |
| 9:16 | `tall-9x16` | 720×1280 | | as today (`default` topic; every style for source restyle) |
| 1:1 | `square-1x1` | 720×720 | | stage gate (admission spec §3, §5) |
| 21:9 | `ultrawide-21x9` | 1680×720 | | stage gate |
| (hidden) | `tall-9x13` | 720×1040 | | as today |

Row order per mockup: 자동, 16:9, 9:16, A4 가로, A4 세로, 1:1, 21:9. With a pin, rows the baseline table blocks are disabled with the table's reason.

---

## 3. Canvas-aware quality chain — inventory (verified; spec pending)

| Location | Today on non-16:9 | Fix |
|---|---|---|
| `utils/llm_calls/author_slide_design_system.py:139-162` | rules rewritten only for 720×1280 / 720×1040; A4 rules say 1280×720 beside a canvas clause saying 1188×840 | canvas-parameterised rules + shape clause (§3.1) |
| `utils/llm_calls/author_slide_rules.py:141-142` | inset phrase wraps a line; its portrait rewrite never matched | §3.1 + token-coverage test |
| `utils/llm_calls/author_deck.py:1283` | infographic briefs only at 1280×720 | remove the size condition **after** the next row (kept on 2026-09-29: removing it alone would contradict the design system) |
| `utils/authored_infographic/contracts.py` (`slot_contract`, `_box_rule`, `LOW_HEIGHT_MAX_PX`) via `author_infographic_marker.py` | slot contracts assume a 720px-high page (content bottom y=648/688px, 430px minimum height) | make `content_bottom_px` and the height rules canvas-derived, then lift the `author_deck.py:1283` gate |
| `utils/llm_calls/author_deck.py:1325` + `document_canvas_prompt.py:6-7` | presets get no DOCUMENT CANVAS clause; exemplar is a 1280×720 slide on every canvas | always pass the safe area — **this changes the 16:9 prompt**; the pre-change 16:9 bench receipt is frozen as the reference (§8) |
| header contract (`models/authored_header_contract.py:34-35,284,440`, `models/authored_header_row_fit.py:59`, `utils/llm_calls/author_header_prompt_clauses.py:26,55`, `utils/authored_header_prompt.py:340`, `services/authored_author_stage_placeholder.py:223`) | 1280/720 in model, validators, row fit, prompt, placeholder | §3.2 |
| `utils/llm_calls/author_batch.py:205` | "1280x720 viewport" | drop the number |
| `services/authored_composition_policy.py:2045` | paint-order repair only for 1280×720 / 720×1280 | accept the deck canvas (`_deterministic_repair_candidate(canvas_size=…)`; caller `authored_presentation_generation.py:1984`) |
| `services/workspace_service.py:1089,1125,1136,1143` | local-fit checkpoint reuse only for 16:9 (speed) | resolve the presentation canvas |
| svg driver `utils/llm_calls/author_slide_svg.py:32-61` | size known, no shape guidance | append the same shape clause |
| `services/image_generation_service.py:152-164` | per-model landscape; unknown models square; slot hint says 16:9 | §3.3 |

Already canvas-aware: local-fit callers, `author_vision_qa.py`, `document_canvas_scene_layout.py`, svg geometry, `slide_capture.py`, `pptx-assembler.ts`, `export_size_points`.

### 3.1 Prompt
Placeholders in `DESIGN_SYSTEM_RULES` and page-number rules (`{canvas_size}`, `{footer_y}`, `{no_page_number_y}`, `{offscreen_y}`, `{example_content_width}`, …) formatted from the canvas; `author_canvas_shape.py` with `classify_canvas_shape` (baseline / wide-landscape / portrait / square / ultrawide), `canvas_tokens`, `composition_clause(shape)`; the clause is appended to both drivers' rules. Tests: no `1280`/`720` literal outside the baseline across all registry canvases; token coverage; shipped-portrait output changes only in the two documented places.

### 3.2 Header contract
`AuthoredHeaderContract` gains `canvas_width_px`/`canvas_height_px` (defaults 1280/720 for stored payloads); all five consumers read the contract's size; `resolve_header_contract()` receives the canvas.

### 3.3 Illustrations
`resolve_openai_image_size(model, shape)`: landscape as today; portrait → 1024×1536; square → 1024×1024; models without the size fall back to square. Slot-ratio hint formatted per shape.

## 5. Design-pack variants (outline; spec pending)
Manifest v2 (`canvas_id`, `variant_of`, `status`), version-conditional digest payload so v1 packs still seal; registry key `(pack_id, version, canvas_id)`; `latest()` returns the base; variants drafted per pack × canvas by one deck-level re-layout call, contact-sheeted, human-approved. A4 + design pack requires a new route in `select_a4_route` — part of this workstream.

## 6. Picker and 자동 (outline; spec pending)
`upload/components/CanvasSelect.tsx` replaces both `<select>`s; rows from the registry; enabled/reason from the style catalogue (`supportedAspectRatios` kept, plus `supportedCanvases`); `자동` = nearest enabled canvas to the source document's proportions when present, else the default; client sends `{canvas}` **or** a legacy family, never both. Closed unions widened; fixtures; i18n keys.

## 7. Export/capture
`browser-source.ts:47` allow-list → compare against the expected size in the injected config (**first step**; blocks A4 and 9:13 today). `PdfMakerPage.tsx:55-77`, `SafeHtmlSlidePreview.tsx`, `presentation_export_powerpoint_qa.py:878,951`, `slide_capture.py` crop offsets.

## 8. Bench gate (outline; statistics spec pending)
Receipt JSON per canvas × style × driver from `ppt-agent.mjs --batch`, `authored_run_contact_sheet*.py`, `benchmark_authored_hybrid_review.py`: defect counts, per-slide seconds, retries, pairwise rating. The pre-change 16:9 receipt is frozen as the reference. Pass criteria and sample size to be specified with adequate power (≥ 40 pairs at ±8 pp or a non-inferiority test). `promote` writes `stages.json` (admission spec §5).

## 9. Sequencing
0. **Done 2026-09-29** — admission baseline fixture (`scripts/canvas_admission_table.py`, 2,610 rows over sync/async/v2) and `utils/llm_calls/outline_canvas_budget.py` (unwired) with tests.
1. Quality chain on the A4 default path + `browser-source.ts` (§3, §7). **Done 2026-09-29 except the infographic rows**, with the 16:9 prompt kept byte-identical (25 baseline fixtures in `tests/fixtures/prompt_16x9_baseline/`; the only 16:9 text change is the `author_batch.py` viewport phrase). Applying the DOCUMENT CANVAS clause to 16:9 waits for the frozen 16:9 reference receipt.
   **2026-09-29 (steps 2–8 implemented)**: header contract (`f9b9afdd7`), infographic briefs on every canvas (`508d97f43`), outline budget (`43912c40b`), 1:1/21:9 behind the stage gate + registry (`f0e870211`), remaining export/capture (`af7986c90`), picker + 자동 (`28daa284e`), v2 admission fixes (`f5b3c51e0`), design-pack variants (`17059ccb6`, no approved variants yet), bench runner + A4 pilot (`8b4901b2f`, PASS vs 16:9; see `docs/canvas-bench-pilot-20260929.md`). Open: the full bench matrix and promotion of 1:1/21:9; the 16:9 DOCUMENT CANVAS clause; the pilot's product defects (provider-settings import on Windows, svg timing receipt `rc_invalid`, constant svg export gate blockers, number wrapping in exported PPTX).
2. Header contract (§3.2).
3. Registry generator (decision 8) + new presets with the stage gate (admission spec §3–5).
4. Content budget (budget spec) + illustration sizes (§3.3).
5. Remaining export/capture (§7).
6. Picker + 자동 (§6).
7. Pack variants (§5).
8. Bench → promote (§8).

## History
- rev 1–3.2: see git history. Independent reviews: rev 3.1 → 54/100, rev 3.2 → 54/100 (P0s in admission unification and floor/ceiling budget both times).
- rev 4: admission unification dropped; admission and budget moved to executed/derived specs; persisted form kept; A4 + design pack fact corrected.
