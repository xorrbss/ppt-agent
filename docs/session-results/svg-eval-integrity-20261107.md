# SVG evaluation accounting and comparison integrity

Outcome: implementation and independent read-only review completed; the single focused check passed.
Baseline and unchanged HEAD: `8d6d21fb8611fe6b6312fc2d4ba5186406aa05d6`. The initial worktree was clean.
No local commit was created; preserve this worktree for owned-file integration.

## Owned changed paths

- `servers/fastapi/scripts/svg_template_eval/decide.py`
- `servers/fastapi/scripts/svg_template_eval/metrics.py`
- `servers/fastapi/scripts/svg_template_eval/run_eval.py`
- `servers/fastapi/scripts/svg_template_eval/README.md`
- `servers/fastapi/tests/unit/test_svg_template_eval_decide.py`
- `servers/fastapi/tests/unit/test_svg_template_eval_metrics.py`
- `servers/fastapi/tests/unit/test_svg_template_eval_accounting.py` (new)
- This report.

## Resulting behavior

Schema 2 retains the existing result fields and adds separate attempt accounting, actual-page rate evidence,
condition/runtime identities and harness source hashes. Completion/failure rates use all recorded attempts;
authorized retries remain separate records with explicit lineage. Successful-deck scores remain conditional.
Comparison reports expose failed, unknown, missing-metric and missing-cell evidence independently. Worse completion
with better survivor scores withholds the global decision. Raw page counts and numeric-token retention remain
descriptive; normalized rates pool actual measured page denominators. Legacy results are readable but missing
identities prevent unsupported comparisons. Threshold verdicts are explicitly descriptive heuristics.

Attempt records are saved before POST and task IDs before polling. Generation completion survives measurement,
export or judge exceptions. HTTP 5xx/408 and transport uncertainty remain unknown and stop further cell dispatch.
Normal resume fills unattempted slots only; `--resume --retry-failed` explicitly authorizes failed-slot replacements.
Unknown outcomes block resume. Recovery preserves history, includes all matching durable jobs and retains missing
PPTX evidence. Public requests are matched using the existing DTO, including complete template pins, canvas, frame
maps and page counts, with deterministic template header defaults normalized. Server-only fields do not prevent
matching. Recovery never dispatches a generation or retries failed measurements.

Judge identity binds actual image bytes, settings, implementation, explicit version and served models; mixed models
block judge verdicts. No judge model, rubric, version or provider configuration was changed. New records omit judge
prose and customer-derived labels. Structured category/value/unit/series evidence has a bounded pure comparison
contract; current deck records explicitly report it unavailable rather than infer associations from numeric tokens.
Leakage candidates remain heuristic; optional common-phrase evidence is not supplied by the driver.

## Validation and review

Working directory: `servers/fastapi` in this worktree. Exact single check:

`C:/project/PPT-agent/ppt-agent/servers/fastapi/.venv/Scripts/python.exe -m pytest tests/unit/test_svg_template_eval_metrics.py tests/unit/test_svg_template_eval_decide.py tests/unit/test_svg_template_eval_accounting.py -q`

Result: exit 0, **42 passed in 4.89s**. No other test, lint, type, build, runtime or provider check was run.
The check covers survivor bias, unequal page denominators, unavailable evidence, incompatible identities, explicit
retry lineage, durable uncertainty, completed-generation measurement failure, real persisted DTO request shape,
pin/size/canvas/frame-map mismatch, multiple failed jobs without presentation IDs and pending-measurement recovery.

Independent reviewer: `gpt-6.1-sol`, high, read-only. Implementation developer: `gpt-6.1-sol`, high.
Resolved findings: failure erasure; empty-checkpoint first-draft inflation; recovery alias/default mismatch;
null-PID misidentification; pending measurement abandonment; ambiguous HTTP outcomes classified as failures.
Final reviewer found no remaining material issue. Workers ran no checks; the lead executed the selected check once.

## Preservation and integration requests

All changed production files are evaluation-only scripts. Final scoped Git status contains only the owned files
above. No non-SVG engine, entry point, stored data, dedicated test, CI file, runtime authoring policy, legacy SVG
snapshot interpreter or reserved root-owned file changed. No server, DB, app-data, authentication, provider, COM,
merge, push or deployment operation was performed. Existing `recovery.py` helpers remain unchanged; the driver no
longer uses its history-dropping resume/newest-only selection helpers.

Root integration requests:

1. Add the new SVG accounting test path to the root-owned `servers/fastapi/tests/svg_unit_subset.txt` through the
   existing subset maintenance flow; preserve all existing collection and non-SVG participation.
2. Supply verified optional runtime/source identity through `run`/`matrix --identity-manifest` or an equivalent
   sanitized run record. Manifest shape: `default` identity plus optional `cells` identity overrides. Required runtime
   fields on each `evaluation_identity`: `code_revision`, `generation_config_sha256`, `policy_revision`,
   `policy_sha256`; policy revision accepts a string or integer. Required matching `condition_identity` fields:
   `input_sha256`, `template_pin_sha256`, `request_sha256`, `generation_config_sha256`. Matrix hashes repository-owned
   fictional inputs locally; arbitrary requests require operator-supplied sanitized digests. Runtime source and
   admitted policy/config are never inferred from harness HEAD or live environment. Missing evidence remains
   unavailable. Code/policy may differ between variants; configuration mismatch is explicit and blocks comparison.
3. Wire Session04's owned `content_sizing_metrics.svg_content_sizing(svg, *, body_bounds=None)` into
   `run_eval.measure_deck` after both changes land. Supply admitted body bounds in SVG user coordinates when known;
   preserve nested availability, policy revision, units, page-fallback provenance and limitations. This session did
   not create, edit or copy Session04's module.

## Unmeasured and unavailable

No live completion/quality improvement, actual service loading, paid spend, DB recovery, PPTX/export fidelity or
visual artifact acceptance is established here; these remain root-owned validation. Recovery cannot reconstruct
unsaved pre-job rejections or transport history, infer historical runtime identity, or safely correlate an uncertain
POST that lacks a task ID; such uncertainty remains visible and blocks automatic resume. No geometry or semantic
truth is proved by the new structured helper. The required `compiled-spec-memory` skill was not available in the
listed/local skill locations, so no automatic spec-memory recording is claimed.
