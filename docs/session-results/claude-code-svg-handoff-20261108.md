# Claude Code handoff: SVG implementation and real-document verification

Status recorded on 2026-11-08, approximately 11:55 KST. The user stopped Codex
work and requested this Git handoff. This document transfers context; it does
not start another generation, experiment, deployment, or agent session.

## Start here

1. Work only in `C:/project/PPT-agent/ppt-agent`. Read `AGENTS.md`, `CLAUDE.md`,
   this document, and the two controlling briefs linked below. Do not inspect
   other worktrees; the user explicitly ended that investigation.
2. Inspect the current checkout without resetting, cleaning, stashing, or
   overwriting existing changes. The implementation baseline is GitHub `main`
   at `e4f710d34bd265ca05e054c185799954406989e8`, before this documentation commit.
3. On the same machine, read the private stop checkpoint, pilot summary, and
   independent review listed under **Local evidence**. They contain the exact
   authorized input, artifact locations, normal API context, and attempt history.
4. Select the next action from **Remaining work** with the user. The latest
   substantive scope in the pilot session was verification of existing results
   accessible in the current account only. Fresh generation was explicitly
   excluded. The earlier two-hour window is not a new execution authorization.
5. Use the exact affected input and retain original and failed attempts. If a
   genuine defect is established, make the smallest justified change and run
   exactly one smallest relevant check. Do not run a blanket test/build/lint
   sequence. For an authorized runtime replay, retain a separate receipt.

Canonical scope, in precedence order:

- The user's latest instruction and [repository policy](../../AGENTS.md).
- [SVG freeze and quality brief](../codex-brief-svg-freeze-quality-20261107.md).
- [Original visual-quality goal](../codex-brief-original-level-20261106.md).
- This handoff for the latest execution state. Earlier component reports and
  integration-journal entries describe their writing-time state; a historical
  "pending" or "not committed" entry does not undo a later merge or receipt.

## Stop state and Git checkpoint

- The new pilot lead acknowledged the user's stop. It reported no known owned
  in-flight job; its Office/browser helpers and both workspace exports had
  returned. Its two native agents were already completed and received the stop
  instruction. Root's earlier native implementation/review agents are completed.
- No further Codex development, generation, tests, or runtime changes are part
  of this handoff. The application servers were left running; stopping agents
  did not mean stopping the managed application or touching user Office windows.
- [PR #149](https://github.com/xorrbss/ppt-agent/pull/149) merged the reviewed
  SVG implementation, fixtures, reports, and source-state evidence at
  `8cf6362b311d0e6a9c9b4b6678a93e949cdf2077`.
- [PR #150](https://github.com/xorrbss/ppt-agent/pull/150) merged six diagnostic
  and acceptance harness sources; its merge is baseline `e4f710d34...` above.
  Root verified local HEAD, GitHub main, and the Bitbucket CI mirror at that SHA
  before writing this handoff. This handoff's publication is a later docs commit.
- Pilot production changes: **none**. Newly paid application generation
  requests: **zero**. Pilot test commands: **zero**. Existing-result replay and
  native/Office inspection did run; these are not fresh-generation acceptance.
- At stop, Git status reported pre-existing modifications to
  `servers/fastapi/assets/icon_families/lucide/nodes.json` and
  `servers/fastapi/utils/slide_capture_pipe_render.py`. Root's content diff was
  empty for those paths. Preserve them; do not infer ownership from status.
- Other existing untracked material includes diagnostic/runtime directories
  under `docs/session-results/`, `national-ai-msp-proposal` assets/style YAML,
  and `servers/fastapi/authored_style_samples/pending.txt`. These were not
  packaged by this docs handoff. Do not add them with a broad `git add .`.

## Already implemented: extend only for a demonstrated gap

| Area | Implemented behavior | Evidence and acceptance boundary |
| --- | --- | --- |
| Non-SVG freeze | Existing non-SVG behavior, dedicated code/tests and CI selection preserved. No retirement or disabling. Shared SVG changes retain legacy contracts. | Freeze audit retained all 478 baseline SVG CI paths and added paths to 495, with zero removals. This is not a full non-SVG runtime acceptance suite. |
| Native chart integrity V5 | Full existing supported bar, column, line, area, pie and donut contracts, numeric authority, paint/composition capture, strict consumer, and genuine receipt integration. | Nine synthetic chart cases reached capture, publication, HTTP PPTX export and native data/workbook audit. The artifact's own final Office check is still separate. Do not restrict supported configurations to the easiest proof cases. |
| Template pictures | Existing vector asset planning/offers support scenes, step pictures, pairs and pictographs with template policy, exclusions, optional failure and admission pinning. | [Picture implementation report](svg-template-pictures-20261108.md), followed by root integration. Fresh generated-deck behavior remains unverified. |
| Content sizing | Default-off body-composition guidance, frozen admission policy, conservative card-envelope whitespace helper. | [Sizing implementation report](svg-content-sizing-20261107.md). The proxy is not glyph-ink measurement, a quality score or proof of visual improvement. |
| Evaluation | Actual-page denominators, complete attempts/retries/failures, judge identity compatibility and recovery accounting. | [Evaluation report](svg-eval-integrity-20261107.md) and root integration. No completed live paired improvement experiment. |
| Source compatibility | Preset-shape conversion and original-source/export compatibility integrated with reviewed source fingerprints. | [Source-preset report](svg-source-preset-compatibility-20261108.md) and the real-document receipts below. Unknown unsupported shapes must still fail closed. |

Source entry points, relative to `servers/fastapi/`, for scoped follow-up:

- Charts: `services/generation_core/drivers/svg/native_chart.py`,
  `native_chart_evidence.py`, `native_requirements.py`, `render.py`, `publish.py`,
  `native_chart_visual.py` in that same directory, and the capture/consumer helpers named
  by [the integration journal](svg-root-integration-20261107.md).
- Pictures: `services/svg_templates/picture_policy.py`,
  `services/generation_core/drivers/svg/asset_slots.py`,
  `services/generation_core/assets/{offer,vector_offer,infographic_plan}.py`.
- Sizing/pins: `services/svg_templates/{body_composition,generation}.py`,
  `services/svg_generation_admission.py`,
  `scripts/svg_template_eval/content_sizing_metrics.py`.
- Evaluation: `scripts/svg_template_eval/{run_eval,metrics,decide,recovery}.py`.
- Pick the existing test for a changed layer, for example
  `tests/unit/test_svg_template_feature_pinning.py` or the corresponding test
  named in its component report. These examples are not a multi-suite command.

Historical focused results on the merged implementation include: producer
120 passed; consumer 211 passed (nine artifact cases initially skipped and later
run separately, 9 passed); marker 8 passed; source ledger 58 passed with one
existing conditional skip; first-flush publication regression 1 passed; native
PPTX audit 9/9. These were separate scoped runs, not tests rerun for this docs
handoff. See [the final chart report](svg-freeze-final-verification-20261108.md).

## Latest real-document result: 35-page existing-source replay

This is a real existing source-preserving document, not newly generated content
from a registered template. All 35 original pages were retained.

| Item | Observed result |
| --- | --- |
| Actual source file | 35 pages; 4,552,211 bytes; whole original file rehashed and matched to stored source/manifest receipts. |
| Original source SHA-256 | `24638e786b772da8b2885bf3e3d56637cbbd4e2a11aff230942ee0054cf35525` |
| Normal export | Workspace ExportPPTX succeeded at stored revision 1; 35 slides, 11,636,004 bytes. |
| Exported PPTX SHA-256 | `ac121f8175219406e0498427cbc1ab193f34afdd2ce743a814ce1d29b9c9847e` |
| Office | The same unchanged PPTX rendered to 35 PNGs at 1188 x 840. Independent review recomputed all 35 PNG hashes. Owned renderer exited. |
| Native readback | 1,031 text runs, 2 tables, 125 pictures, 1,449 shapes and 2 embedded fonts. This is native representation evidence, not a manual editing test. |
| Source table comparison | 51 cells: 46 exact concatenated-text matches; 5 whitespace/paragraph differences; zero substantive character differences. Use logical slide order, not XML part number. |
| Persistence | Normal before/after presentation GETs were identical, including revision, SVGs, style snapshot and producer receipts. All 35 source-page receipt identities matched. |
| Visual sample | Original/output Office images for pages 9, 13 and 32 independently inspected. No obvious clipping or frame collision in that sample. All pages rendered, but not all pages visually accepted. |
| Image limitation | Three page-13 screenshots changed from 1400 x 787 JPEG to 1088 x 612 PNG. Pixel identity is false; fine screenshot text is not fully legible at the inspected size. Resampling stage/intended policy was not established, so this is not yet a proven product defect. |
| Layout boundary | Original source is 16:9; saved output canvas is 1188 x 840, with stored V4 requirements. This does not prove pixel-identical layout or fresh V5/template-picture/sizing behavior. |
| Registered template | A separate template revision 2 passed normal capability preflight as available. It was not applied to a fresh deck and does not belong to the existing replay's provenance. |

An auxiliary ten-page source-linked replay self-identifies as synthetic test
data and was excluded from real-document acceptance. Its missing table headers
already existed in analysis/OCR. Preserve, but do not count it as a successful
fresh real-document pilot. Failed browser diagnostics, wrong-aspect Office
attempt, and initial invalid table-audit ordering were retained separately.

## Remaining work, ordered for continuation

1. **Finish the acceptance review of existing artifacts.** Read the 35-page
   results first. Review additional relevant pages if requested, distinguish
   native text from raster screenshot text, and inspect the exact source/image
   export path before calling downsampling a bug. Manual PowerPoint editing and
   affected PDF behavior were not tested. Do not silently normalize the five
   table whitespace differences into byte identity.
2. **Close the separate nine-chart artifact's Office gap.** Its normal export
   passed native auditing, but earlier rendering was blocked by pre-existing
   PowerPoint processes. The later 35-page Office result does not close this
   different artifact's gap. Use its exact unchanged PPTX and original chart
   manifest; record hashes, renderer identity and all nine PNGs. Never close,
   kill, attach to, or bypass user/unknown Office instances. Busy means defer or
   use a clean authorized Office worker. See the chart report for its paths.
3. **Fresh registered-template pilot needs a new explicit scope decision.**
   The latest user narrowed the pilot to existing results only. No fresh
   template generation, picture planning, content-sizing experiment, outline /
   authoring / quality / resume / remake product-path acceptance has been
   completed. Once separately authorized, use one genuinely authorized,
   source-sized mixed-content document and a verified framed SVG template;
   retain the actual admitted pins through normal save/export. Do not truncate
   a large original or substitute the synthetic replay. A cached six-page DOCX
   candidate returned 404 under the current owner context: do not reuse or
   re-register it without permission. A public nine-page input was prepared
   before the user's scope reply, uploaded via the canonical asset gateway and
   given a normal reference snapshot; it was not generated and is not evidence
   of fresh acceptance. Preserve its preparation; do not auto-resume it.
4. **Pictures and sizing need real generated-output evidence.** On an authorized
   fresh run, inspect scene/spot/pair/pictograph offers, cover exclusions,
   palette, optional failure, actual placement, card/table dimensions, internal
   and body whitespace, reading order and readability in the exported deck.
   Current legacy replay pins do not activate those features. No public
   request-only picture/sizing override was established; do not invent one,
   repin existing decks or turn global flags on merely to pass a check.
5. **Quality and connectors measurement remain separate experiments.** A small
   pilot is not the prescribed paired study: four inputs x three repeats x
   matched control/variant = 24 decks. Connectors policy needs at least three
   runs with `CONTENT_STRUCTURE_POLICY_SCOPE=template` and
   `CONTENT_STRUCTURE_POLICY_PARTS=connectors`; do not create a new flow engine.
   Use complete attempt/page/spend accounting and compatible judge settings.
6. **Frozen shared-path compatibility remains bounded.** Where a genuine SVG
   fix touches shared code, use the relevant existing fixture/check to protect
   non-SVG behavior. Do not delete, skip, rewrite or deselect dedicated legacy
   tests. The preservation audit is not adversarial tenant/RLS or whole-product
   runtime acceptance.

There was no demonstrated outstanding coding defect at stop and no pilot code
fix. Acceptance gaps may reveal a scoped fix; they are not instructions to
rebuild the already merged features. Do not describe the remaining work as
"only charts" or "only Office".

## Execution and budget boundaries

- Freeze every currently existing non-SVG mode, including authored, adaptive
  and vNext paths where present, together with its tests. Preserve availability,
  API/UI behavior and stored data. The old authored-removal design is historical.
- Keep legacy pinned snapshots and source ledgers meaningful. Never replace
  producer receipts or loosen verifier hashes/anchors just to accept an artifact.
- Exact fresh opt-in names: `SVG_NATIVE_CHART_INTEGRITY_V5`,
  `SVG_TEMPLATE_PICTURES`, `SVG_TEMPLATE_CONTENT_SIZING`. These experiments are
  default-off. At the earlier runtime verification V5 was absent and V4 remained
  the fresh default; the pilot changed no global settings. Verify effective
  per-process settings before a future run without printing secret values.
- Preserve free body composition. No original-page body skeleton reuse, fixed
  slots, parallel picture pipeline or artificial supported-chart restriction.
- New paid application calls require a concrete authorized case, template and
  model, pages/decks, call/token limits, cost ceiling and stop conditions. The
  budget review found no public request-only call/token override; the installed
  Codex wire adapter did not enforce passed `max_tokens`, and an application
  request cost cap was not an established hard supplier-charge limit. Do not
  promise a hard ceiling that the execution path cannot enforce. This handoff
  authorizes no paid run. Existing agent inference and provider-free replay
  must not be confused with paid application generation.
- Use `scripts/start-local-stack.ps1` for managed lifecycle. Do not launch
  `server.py` directly. Preserve PostgreSQL for the API and both controllers;
  preserve ports, launch environment, proxy and unrelated services. Reload only
  a changed server when needed. Useful future read-only probes are the script's
  `-Status`, proxy `/` and `/api/v1/auth/status`; API-root 404 is not a health test.
  No new server probe or restart was performed for this documentation handoff.
- Do not commit `app_data`, original documents, screenshots, raw runtime/API
  responses, owner/tenant identifiers, credentials, private manifests or logs.
  Keep Korean status reports concise; do not paste raw source/diffs/document
  text or whole command output into user-facing replies.
- GitHub is the source of truth. Push merged GitHub main to `bitbucket-ci` for
  its CI mirror only after the approved merge; do not develop on the mirror.

## Local evidence (not in Git)

All paths below are relative to
`app_data/svg-two-hour-pilot-20261108/` in the same checkout. On another machine,
request an authorized local evidence transfer rather than committing these files
or assuming they are available. Start with the first four small files, not the
large raw manifest or private presentation payloads.

- `STOPPED-FOR-CLAUDE.md`: explicit stop acknowledgment, owned job/agent state.
- `execution/pilot-result-summary.json`: final scope, result, excluded attempts.
- `review/real35-artifact-review.json`: independent hash/native/visual findings.
- `execution/attempt-audit.jsonl`: retained attempt accounting.
- `execution/real35-export-attempt-1/export.pptx` and `audit-result.json`.
- `execution/office-real35-attempt-1.private.json`,
  `execution/real35-office-attempt-1/`, `execution/real35-png-manifest.json`.
- `execution/real35-original-source-match.private.json`,
  `execution/office-real35-original-attempt-1.private.json`,
  `execution/real35-original-office-attempt-1/`.
- `execution/native-real35.private.json`,
  `execution/real35-native-table-source-comparison-attempt-2.private.json`,
  `execution/real35-source-receipt-audit.private.json`,
  `execution/real35-persistence-invariance.private.json`.
- `execution/real35-browser-attempt-1/`: normal UI screenshots; an inherited
  screenshot basename includes `replay10`, but the recorded input is real35.
- `execution/template-capability-response.private.json`,
  `intake/intake-report.private.json`, `review/public9-call-budget-review.json`.
- `execution/public-source-preflight/`: prepared public input, not generated.
- `execution/pilot-manifest.json`: comprehensive private inventory.
- `execution/real-document-pptx-and-pngs.zip`: convenient local output bundle;
  contains real document content, so do not publish it.

## Prompt to start Claude Code

Use this as the initial instruction after the user's chosen next scope is clear:

> Read `AGENTS.md`, `CLAUDE.md`, and
> `docs/session-results/claude-code-svg-handoff-20261108.md`. Take over from
> the stopped Codex agents in this checkout. Verify the local stop checkpoint
> and independent real35 review first, preserve all non-SVG code/tests and
> unrelated changes, and report what is proven versus pending. Continue only
> the next task I authorize. Use separate read-only intake/review agents when
> useful and one writer/runtime owner; select supported models appropriate to
> task difficulty rather than assuming Codex model IDs work in Claude Code.
> Keep provider-spend approval and private evidence boundaries intact. Do not
> inspect other worktrees or repeat completed implementation by default.
