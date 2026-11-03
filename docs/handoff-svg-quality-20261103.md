# Handoff: SVG quality and latest-Claude work (2026-11-03)

State of branch `claude/exciting-planck-oqcy7w` for whoever continues this work
in a new session. Everything below is pushed; nothing lives only in a local
checkout except the scratch measurement scripts, whose settings are copied
into "Live runs" so they can be recreated.

## Where things stand

Live result after this round (Anthropic `claude-sonnet-5-5`, svg strategy,
no image provider, Korean prompt "사내 업무 자동화 도입 계획"):

| run | slides | time | cost | quality gate |
|---|---:|---:|---:|---|
| before (claude-sonnet-4-6, default style) | 5 | ~400 s | ~$1.5 | 5/5 review required |
| sonnet-5-5, default style, free header | 5 | 120 s | $0.55 | 5/5 passed |
| sonnet-5-5, `exec-report`, default header | 6 | 241 s | ~$1.2 | 6/6 passed, 10 icons |
| sonnet-5-5, `strategic-navy`, default header | 6 | 247 s | ~$1.2 | cover fell back to text page |
| sonnet-5-5, `flat-infographic`, default header | 6 | 222 s | ~$1.2 | 1 svg quality flag, 1 text fallback page |

Done in this round (commit subjects on the branch):

- Anthropic structured output uses `output_config.format` instead of a forced
  `tool_choice`, which the 5.5 models and Fable 5.1 reject
  (`utils/anthropic_structured_output.py`, installed from `utils/llm_config.py`).
  Schemas the grammar compiler rejects ("compiled grammar is too large", e.g.
  the svg design review) are retried once as prompt-JSON; schemas over the
  documented limits (`AuthoredHeaderContract`, `AuthoredPatchPlan`,
  `JSONObjectResponse`) go prompt-JSON directly on models that reject forced
  tool choice. Unknown Claude ids get `max_tokens=32768` instead of llmai's
  8192, and `thinking: disabled` is mapped per model (`between_tools` on
  Sonnet 5.5, adaptive + low effort on Opus 5.5 / Fable).
- `ANTHROPIC_MODEL` default is `claude-sonnet-5-5` (the old default,
  `claude-sonnet-4-20250514`, is past end of life).
- The vision design review no longer discards every verdict when one evidence
  string exceeds 14 words / 160 characters; it normalises that string
  (`vision_advisory._bounded_text`, diagnostic `evidenceNormalization`).
- Library vector icons are placed when image generation is unavailable
  (`build_asset_offer(icons_only=True)`, receipt `library_icons_only`), and a
  curated concept table raises label coverage from 3.6 % to about 80 % on a
  278-label corpus (`icon_pack/concepts.py`, `concept_aliases.json`).
- Vendored converter patch `xfrm-bounds.1`: axis-aligned shapes trace with
  their exact xfrm frame (1-EMU mismatch made exports with circle icons fail).
- Fallback pages render markdown as plain text and do not repeat the title;
  covers show presenter/date only when the user supplied them.
- CLI waits up to 60 s for the chained export id after status completes.
- `servers/nextjs/package-lock.json` gained the missing nested `@swc/helpers`.

Not adopted on purpose:

- llmai 0.3.15: no Anthropic change, but it alters OpenAI/OpenRouter strict
  schemas and Codex headers. Re-evaluate separately.
- `AUTHORED_ADMISSION_GATE_MODE=enforce`: 17 tests assume shadow; see
  `docs/authored-admission-offline-parity-20261102.md`.
- Turning the content brief off by default: see
  `docs/svg-brief-off-live-pilot-20261102.md`.

## Next work, in recommended order

1. **Text fallback pages ignore the deck style.** The `strategic-navy` cover
   fell back to a white text page inside a navy deck. `fallback.py` should
   take background, text colours and fonts from the style snapshot, and the
   cover role deserves a cover-shaped fallback. Also find why that cover
   exhausted its attempts (author receipts on page 0).
2. **Remaining svg quality flags** (`svg_source_quality_failed`, text fallback
   on a roadmap page in `flat-infographic`): read the page receipts' failure
   codes, group them, fix the most frequent cause. The earlier claude-sonnet-4-6
   finding (uniform header pages failing `body_intrusion`) did not reproduce on
   5.5 in these three decks; confirm with more decks before closing it.
3. **Charts and diagrams**: numeric pages should use native charts and
   process/timeline/cycle content should use diagrams
   (`assets/infographic_plan.py`, `drivers/svg/native_requirements.py`,
   author rules in `author_slide_svg.py`).
4. **SVG illustrations drawn by the model** (no image provider): one
   illustration call per scene page, palette-bound, no text, validated by the
   renderer and dropped on failure; behind a flag until measured. Design notes
   in the conversation: reuse the AssetOffer scene slots
   (`SCENE_BBOX_HINT`), keep PPTX editability (custGeom).
5. Known open items: the readback test
   `test_converter_patch_rebuild_matches_shipped_bytes` fails because older
   vendor edits (`chart-furniture.16`, `native-fixed-text.1`, `table.py`)
   have no patch file and the upstream baseline commit is not in the repo;
   `Today's Date` is still passed to the outline and content-brief prompts;
   the fake-LLM harness has no canned infographic plan (an extended copy was
   written during this round but not committed).

## Verifying changes

- Full pytest takes about 45 minutes on the Linux VM; the baseline at
  `22ea253f` was 344 failed / 11,919 passed, almost all from missing Windows
  fonts (Malgun Gothic), `pythoncom`, and long-standing svg baseline tests.
  Compare failure lists, not counts.
- Agent worktrees created by the session harness start from `origin/main`,
  not from this branch: check out `claude/exciting-planck-oqcy7w` first.
- Running `uv run` inside a worktree can repoint the shared `.venv` editable
  install at that worktree; run `uv sync --frozen` in the main checkout after.

## Live runs

Use the fake-LLM harness in real-provider mode
(`servers/fastapi/scripts/fake_llm_e2e/`). The provider key is stored in the
cloud environment as `PRESENTON_ANTHROPIC_API_KEY` and is exported to the
backend process only, never printed:

```bash
export FAKE_LLM_CLIENTS=0 LLM=anthropic ANTHROPIC_MODEL=claude-sonnet-5-5
export ANTHROPIC_API_KEY="${PRESENTON_ANTHROPIC_API_KEY:?}"
export PUBLIC_PROVIDER_PRICING_REVISION=anthropic-55-20261102
export PUBLIC_PROVIDER_PRICING_CATALOG='{"schemaVersion":"public-provider-pricing-catalog/v1","revision":"anthropic-55-20261102","entries":[{"providerId":"anthropic","modelId":"claude-sonnet-5-5","inputMicrounitsPerMillionTokens":2000000,"outputMicrounitsPerMillionTokens":10000000,"cachedInputMicrounitsPerMillionTokens":200000,"currency":"USD"}]}'
export CANVAS_BENCH_ENABLED=true FAKE_LLM_LOG_PROVIDER_ERRORS=1
cd servers/fastapi && scripts/fake_llm_e2e/run_backend.sh <app-data-dir>
```

Then, in another shell, `BODY='{"content":"...","n_slides":6,"language":"Korean","export_as":"pptx","authored_style":"exec-report"}' scripts/fake_llm_e2e/run_generation.sh 6`.
Stage timings are in the `svg_outline_span` log line; per-call tokens,
latency and cost are in the `public_provider_logical_calls` table of the
harness database. Start and stop the backend through a pid file; `pkill -f`
matches the calling shell in this VM.
