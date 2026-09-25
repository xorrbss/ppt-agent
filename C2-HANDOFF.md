# C2-style handoff (gap 3: brand tokens, master pin / design pack)

Branch: `core/c2-20260926` (from `integrate/generation-core` @ `8950f150a`)

## Commits

1. `1cb823047` — `feat(generation-core): StyleContext for svg brand tokens + master pin admission`
   - `services/generation_core/style/{context,build,master_projection,design_pack_projection}.py`
   - `services/generation_core/drivers/svg/project_style.py`
   - `services/authored_presentation_artifacts.py`: `resolve_brand`/`authored_title` moved out,
     one-line facade re-export kept (authored unchanged, all existing callers untouched).
   - `utils/svg_style_adapter.py`: now builds a `StyleContext` and calls `project_style` instead
     of reading only the preset's own fields.
   - `services/svg_generation_admission.py`: `master_pin` presence no longer blocks admission by
     itself (resolved via the existing `resolve_legacy_master_pin`, then projected via
     `master_projection.project_master_contract`, mapped to 401/404/409/422 on failure);
     `design_pack_pin` still 422 but with the specific code
     `svg_design_pack_requires_source_native_patch_target` instead of the generic
     `svg_design_pack_not_supported`.
2. `511a87f72` — `feat(svg-author): tell the model to use tokens.primary/fonts/wordmark`
   - `utils/llm_calls/author_slide_svg.py` (`SVG_AUTHOR_RULES`): two added rule lines (colour/font
     tokens; wordmark placement). Kept separate at the orchestrator's request so C3 rebases
     cleanly over just this small addition when the P0 split lands
     (this file's per-slide author call becomes `drivers/svg/author.py`).

No DB migrations. No env/config changes required on any shared stack.

## What changed, functionally

- `request.primary_color` / `request.fonts` / `request.wordmark`, previously ignored by svg
  (only the resolved style preset's own tokens were read), now take precedence when explicitly
  set. Falls back to the preset's own colour/fonts when not set (unchanged behaviour).
- A `master_pin` (published `DeckMasterContract`) is now accepted for `strategy=svg`: its
  `tokens`/`font_manifest`/`layouts.standard` geometry projects into the same rule shape
  `utils/svg_template_rules.py` already produces for the built-in source-restyle catalogue
  (`master_template` key in the frozen `svg_style_snapshot`), and the resolved 5-field pin is
  recorded under `master_pin` in that same snapshot.
- `design_pack_pin` is still rejected for svg (see design doc §6, already recorded by the
  orchestrator): every `DesignFrame` requires an executable `patch_handle` onto a real
  source-native document element, which svg's from-scratch per-slide authoring has no
  equivalent for outside its own separate source-restyle/raster-reconstruction path.

## Verification done so far (offline / unit level)

- `uv run ruff check` clean on every touched/new file; all new files well under the 350-line cap
  (largest is 266 lines).
- Focused pytest, all green:
  - New: `test_svg_style_adapter.py` (8), `test_svg_generation_admission.py` (7),
    `test_master_projection.py` (4).
  - Existing, re-run for regression: `test_svg_template_rules.py`, `test_svg_source_layout.py`,
    `test_svg_source_generation_flow.py`, `test_generation_reference_admission.py`,
    `test_authored_title_recovery_contract.py`.
  - Full `-k svg` sweep across `tests/unit`: 240 passed (with `APP_DATA_DIRECTORY` /
    `DATABASE_URL` / `DISABLE_IMAGE_GENERATION` set; without `APP_DATA_DIRECTORY` one unrelated
    test fails on an unrelated `os.path.join(None, ...)` — purely a local env gap, not a code
    issue).
  - `test_authored_pipeline.py`: 8 pre-existing failures reproduced and inspected; all are about
    render-cache signatures / `canonical_capture_html` mismatches, unrelated to brand/style
    resolution — not a regression from this session's changes.
- Full `pin_svg_style()` chain simulated offline with a fixture `DeckMasterContract`
  (`tests/support/authored_vnext_master_fixture.py`) and a mocked owner/repository: confirmed
  the frozen snapshot's `tokens` reflect request overrides, `master_template` carries the
  projected master rules, and `master_pin` carries the resolved 5-field pin.
- Confirmed (by reading `author_slide_svg.py`) that the *entire* style snapshot, minus
  `references`/`diagnostics`, is already serialized into the LLM prompt verbatim — so the data
  fix alone reaches the model; the `SVG_AUTHOR_RULES` addition in commit 2 is what tells the
  model to actually act on it.

## Not yet done: live product-path check

Per the brief's Live check:
1. Prompt an svg deck with `primary_color`/`fonts`/`wordmark` set → colour/font/wordmark visible
   in the rendered SVG and exported PPTX.
2. Generate an svg deck with a registered (published) master pin → accepted, rules applied,
   receipt/checkpoint lists the pin.

This needs a running stack. Requested a slot on `raster-quality`'s V stack (port 8100); as of
this handoff I'm queued 4th behind (1) a Samsung solo timing run, (2) `p0-svg-split` deck4,
(3) `c4-runtime-export`. V's DB has no masters registered, so check 2 requires registering and
publishing a master through the product API during the slot. Will report actual product-path
results once that runs; until then, do not treat the Live check as satisfied — only the offline
simulation above is verified.

## Known gaps / decisions accepted by ppt-agent-fe

- Design pack structural rejection recorded in the design doc §6 (2026-09-26 entry); D1 must
  keep `registered-template` + `design_pack_pin` routed to authored/source-native even after
  `svg` becomes the default strategy.
