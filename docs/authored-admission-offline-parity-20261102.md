# Authored admission gate: offline parity re-check (2026-11-02)

`AUTHORED_ADMISSION_GATE_MODE` still defaults to `shadow`
(`servers/fastapi/utils/get_env_programme.py`): every producer call runs both
the legacy per-path checks and the common gate, and the legacy verdict wins.
`docs/authored-verbatim-placeholder-authoring-design-20260911.md` (rollout
step 1) switches to `enforce` after zero unexplained verdict differences over
one repeatability series. This note re-checks the static half of that
criterion against the current code.

## Method

`scripts/authored_admission_fixture_replay.py` replays the stored checkpoint
fixtures in `artifacts/verbatim-design-measurements-20260911/fixtures` (runs
13, 14, 16-20, 23; `source_content_restyle`). For each HTML it compares the
legacy static checks (`validate_authored_slide_html` + `stamp_source_blocks`)
with `admit_slide_html` on identical bytes.

The stored fixtures are all admitted, so they cannot show agreement on
rejections. The new `--mutations` flag also replays each fixture's first
stage with five broken variants: an injected `<script>`, an inline `onload`
handler, an external `<img>`, the HTML truncated in half, and the first
bound source sentence deleted.

The script's `state=` keyword had to become `html_repr=` (the context field
was renamed after the script was written).

## Result

| variant | legacy | gate | count |
|---|---|---|---:|
| original | admitted | admitted | 464 |
| script | rejected | rejected | 269 |
| inline_handler | rejected | rejected | 269 |
| external_img | rejected | rejected | 269 |
| truncated | rejected | rejected | 269 |
| drop_source_text | rejected | rejected | 203 |
| drop_source_text | admitted | admitted | 28 |
| **total** | | | **1,771 (0 mismatches)** |

The 28 admitted `drop_source_text` cases agree too: the deleted sentence
still occurs elsewhere in those slides, so both paths find it.

## What this does not cover

- The captured state (render metrics and PNG), which only a live run
  produces; the shadow log rows from a live series cover it
  (`scripts/authored_admission_shadow_report.py`).
- Ordinary prompt generation: every fixture is a source restyle.

## Live series (same day)

One live authored deck (5 slides, Claude Sonnet 4.6, `shadow` default) recorded
54 producer calls: 52 identical verdicts and 2 `terminal_flag_only` rows where
the legacy column reads `flag_only` and the gate `admitted`. That column
reports the slide's flag from another stage (the caller's composition-safety
flag), not an admission failure, and the slide publishes flagged under either
mode, so the difference is explained.

Switching the default to `enforce` was tried and reverted: 17 tests in
`test_authored_admission.py`, `test_authored_terminal_slide_fallback.py` and
`test_authored_workspace_sqlite_publication.py` exercise flows that assume the
shadow default (for example terminal fallback without admission receipts).
Flipping the default needs those flows and tests revisited first, not only the
knob.

## Recommendation

Run one short live authored series (a few decks, prompt-only and source
restyle) with the default `shadow` mode, aggregate the worker's
`authored_admission_shadow` lines with the report script, and switch the
default to `enforce` if it shows zero unexplained differences. Shadow mode
costs only HTML parsing per producer call, not provider calls, so waiting for
that series is cheap.
