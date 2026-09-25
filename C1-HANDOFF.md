# C1 quality — handoff (2026-09-26)

Branch `core/c1-20260926` (worktree `wt-core/c1`), base `integrate/generation-core` 76d2429ab.
Brief: `design/sessions/C1-quality.md`; design `docs/generation-core-architecture-20260925.md` §2.1.

## Commits

| Commit | What |
|---|---|
| 020fb5217 | `git mv` of 6 authored modules into `services/generation_core/quality/` + aliasing facades |
| 5abb5803d | `publish_flagged_allowed`, `TerminalPolicy`, `PageOutcome`, `advisory_record`, runtime-profile fields |
| 83dda537e | SVG driver: terminal rescue, per-page classification, vision advisory, publish, endpoint status |
| 299b2831d | Export receipt `visual_review` from the real advisory; derived `release_readiness`; release gate reads the wire shape |
| e6c1c8ca4 | `svg_export_operation.py`: one receipt call for both SVG paths (net -5 lines) |
| 4e221123d | Fallback page wraps at spaces (measured on real Chromium) |
| 4481a44db | Tests (`test_svg_page_quality.py`, `test_svg_terminal_policy_flow.py`) |
| 6a8a971bb | Live fix: `review_page_indices()` is the one `reviewPages` rule (publish, renumber, remake, receipt) |
| 9b0cecd4e | Live fix: advisory under the admitted `vqa` stage; fallback body line 32px |

## Moved modules (old path = `sys.modules` alias facade)

`authored_flag_classification`, `authored_vision_advisory`, `authored_vision_advisory_prompt`,
`authored_vision_contact_sheet`, `authored_terminal_slide_policy`, `authored_terminal_slide_flag_only`
-> `generation_core/quality/{flag_classification, vision_advisory, vision_advisory_prompt,
vision_contact_sheet, terminal_slide_policy, terminal_slide_flag_only}.py`.

Not moved: `authored_terminal_slide_fallback.py` (HTML remake machinery; the SVG driver does not call
it). `authored_author_stage_placeholder._split_content` is imported (not moved) by
`drivers/svg/fallback.py`: the module is an HTML renderer and would put HTML into the core.

The facades alias the moved module object, so monkeypatch targets, private names and the
`test_authored_admission` file-path read keep working; that path-read test now reads a facade
(passes, but checks less — L2 may repoint it).

## Seams

- `PageOutcome(index, source_format, source, png_path, quality, attempts, failures, verified, flag, classification)` — `quality/page_outcome.py`; `classify_page_outcomes()` applies the authored flag classification unchanged.
- `TerminalPolicy` fallback|flag_only|fail — `quality/terminal_policy.py::resolve_terminal_policy(source_reflow, exact_reconstruction)`; pinned in `GenerationRuntimeProfile.terminal_policy`.
- `publish_flagged_allowed(source_reflow, exact_reconstruction)` — format-neutral half of `terminal_fallback_active` (same truth table, 32-case test).
- `advisory_record(vision, page_digests)` — binds the advisory to sha256 of each published SVG.
- `GenerationRuntimeProfile.vision_advisory` — false only for raster reconstruction.
- `svg_visual_review(slides, advisory)` / `release_readiness(domains)` — `services/svg_export_visual_review.py`.

## Behaviour

- Ordinary deck: one exhausted page (`svg_validation_failed`, `svg_repair_budget_exhausted`) becomes a
  deterministic fallback page flagged `author:attempts_exhausted` (review_required). Lost lease,
  uncertain purchase, provider outage, asset failures still fail the deck.
- Source reflow: flag-only (keeps the last rendered page, receipt re-verified) only when
  `AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW=true`; default fail. Raster reconstruction: fail.
- Every page is classified after collection; `quality.classification`/`verified` are written to the
  checkpoint; publish stores `qualitySummary`, `flaggedSlides`, `advisory`; the endpoint message and
  `quality_gate_status` are read back from what was published.
- Export `visual_review`: passed | failed+`svg_visual_review_flagged` (the agreed "flagged"; the public
  contract has no such status) | failed+`svg_visual_advisory_stale` | failed+`svg_visual_advisory_unavailable`.
  Digest in `evidence.measurements.advisoryDigest` (extra evidence keys are forbidden by the contract).

## Known debts / follow-ups

- `svg_export_operation.py` is 404 lines (>350, pre-existing); split with N1's join wiring.
- A flag-only source-reflow deck publishes, but `_source_preservation_receipts` (C4 export guard)
  still requires `rendering_verified` on every page, so such a deck cannot export in
  source-preservation mode. Off by default; decide before enabling the knob in production.
- Ordinary decks' receipt keeps the legacy `authoring`/`geometry_font` = failed, so
  `release_readiness` stays blocked for them even with a clean advisory.
- A page remade after publication makes the advisory stale (by design); remake does not re-run it.
- Raster decks now also get the per-page classification (no advisory); their `qualityStatus` can be
  `passed` where it used to be blanket `review_required`.
- `GET /api/v2/operations/{id}` answers 503 `operation_authority_unavailable` for a v1-chained export
  operation; the receipt is readable from `/api/v1/ppt/workspace/presentations/{id}/timeline`.
- Ordinary decks measured many `font_unresolved` / `text_overlap` failures on model output (9/10
  pages in round 1). The classification reports them truthfully; reducing them is authoring work.

## Verification (offline)

- Focused SVG suites (14 files): 123 passed, 0 failed. Adjacent suites (9 files): identical to base
  (109 passed, same 1 pre-existing failure).
- `tests/unit/test_authored_*.py` (210 files) on base 76d2429ab, after the move, and at 4481a44db:
  2846 passed / 130 failed each; failure sets identical except two timing-flaky streaming tests
  that flip in both trees on repeat runs.
- ruff clean on every touched file.

## Live check

Stack V (8100/5100, `wt-raster/verify`), `AUTHORED_PUBLISH_FLAGGED_SLIDES` = true (default).

Round 1 on 4481a44db (2026-09-26 06:17 KST):

- Forced-failure deck (3 pages via `slides_markdown`; page 2 demands 2,500 individually drawn
  seats): page 2 failed the SVG contract twice (`svg_validation_failed`) and was replaced by the
  fallback page, flagged `author:attempts_exhausted` / `svg_fallback_page`; the deck completed
  (1245 s) and the chained `export.pptx` ran.
- Ordinary deck (10 pages, 853 s): per-page classification published 1 passed / 9 review (all 9
  with measured `font_unresolved` or `text_overlap` on the model's SVG).
- Export receipts (both decks): `visualReview` = failed / `svg_visual_advisory_unavailable`,
  `releaseReadiness` = blocked (derived) — correct for an advisory that did not run.
- Defects reproduced and fixed: reviewPages overwritten to every page after publish (6a8a971bb);
  advisory refused by public admission, `public_provider_stage_invalid` (9b0cecd4e); fallback page
  measured `text_overlap` between its own body lines (9b0cecd4e; re-measured clean on the published
  16,693-char content).

Round 2 on 9b0cecd4e: pending — blocked by the shared Codex credential returning 401 on every
stack since ~07:40 KST. To check: advisory actually runs (visualReview passed or
failed/`svg_visual_review_flagged` with `measurements.advisoryDigest`), releaseReadiness derived,
reviewPages equal to the classification. Script: session scratchpad `live_c1.mjs <base> ordinary|forced <out>`.
