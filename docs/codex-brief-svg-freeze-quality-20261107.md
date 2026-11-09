# Codex execution brief: improve SVG and freeze other modes (2026-11-07)

> Superseded for authored (owner decision 2026-11-09): the authored strategy is being removed; see `docs/authored-removal-execution-20261109.md`. The freeze still applies to every other non-SVG mode.

Status: execution handoff for a new session; no implementation or server validation is claimed here.
Multi-session execution refinement: `docs/svg-freeze-multisession-20261107/00-coordinator.md` and its four session specs now define independent Orca sessions. Each session uses a bounded internal implementation/review team with role-specific models. This overrides the earlier ban on internal delegation and the earlier single-session concurrency description; no further coordinator hierarchy is allowed. The root retains shared-file integration and managed-stack/runtime/PPTX validation.
Authority: the owner's latest instruction in this conversation on 2026-11-07.

## 1. Controlling decision and scope

Improve SVG generation through completion, using an orchestrator and task-specific agents, then start or reload the managed local stack and verify the integrated result.

Every existing non-SVG mode and its dedicated tests are frozen, not removed. This supersedes the retirement/deletion direction in `docs/authored-removal-design-20261105.md`, including stopping existing generation entry points, removing drivers, archiving decks for retirement, deleting code/tests, and dropping database objects. That document is historical dependency evidence only. Its earlier owner-confirmed removal decisions are not current execution authority.

Freeze means preserve the existing behavior, entry points, availability, request/response contracts, stored data, recovery paths, code and dedicated tests. Do not disable non-SVG generation, convert an explicitly requested non-SVG strategy to SVG, remove UI/CLI/API options, or add skip/xfail/deselection to hide tests. Do not delete, move, rename or rewrite dedicated non-SVG tests or their fixtures. Do not change their collection or CI participation. Already removed modes need not be restored.

SVG work may change genuinely shared code only when necessary for SVG and when the existing non-SVG contract remains intact. Preserve existing non-SVG cases in mixed test files; add SVG cases without rewriting those cases. A filename containing `authored` is not proof that it belongs exclusively to a frozen engine.

SVG pictures, icons, source images, style catalogues, rendering, recovery, PPTX/PDF export, checkpoints, provider accounting and admission infrastructure remain supported. This is not a rename or repository-wide cleanup project. No data deletion, schema removal, driver retirement or compatibility shutdown belongs to this work.

## 2. Read first and refresh evidence

Read the nearest `AGENTS.md`, the global policy, `CLAUDE.md`, this brief, `docs/codex-brief-original-level-20261106.md`, `docs/original-level-handoff-20261106.md`, and `servers/fastapi/scripts/svg_template_eval/README.md`. Read only the directly relevant preservation sections of the removal design. This brief controls where those documents conflict.

The investigation observed HEAD `8d6d21fb8`; the new session must refresh HEAD, worktree state and applicable instructions. Preserve all pre-existing tracked and untracked user changes, including style assets. Inspect target files and their direct dependencies, not the whole repository.

Native requirements v4 already exists, introduced by `7c42f37a4`. Do not implement that brief item again or treat the historical 2.81 overall / zero-native-chart result as a current baseline.

A concrete concern requiring verification is in `tests/unit/test_svg_native_chart_retention_v4.py::test_v4_keeps_drawn_content_when_only_native_geometry_is_unprojectable`: the fixture's `wrong_geometry` changes a bar height, yet content preservation is expected. Trace the complete publication and export path before deciding the fix. Presence of the right numeric text alone does not prove a truthful chart. A flagged quality result is not a release approval.

The required `compiled-spec-memory` skill was not found in the available project/user skill locations during handoff preparation. This document records the owner's instruction, but does not claim skill-based automatic recording. If that skill is available in the new session, use it to record only the directly authorized freeze decision and subsequently verified contracts. Do not invent an auto-recorded result.

## 3. Team and ownership

Recommended main session: `gpt-6.1-sol`, reasoning `high`.

| Role | Preferred model / effort | Responsibility |
|---|---|---|
| Orchestrator | gpt-6.1-sol / high | Scope, dependency order, shared-file integration, checks and final acceptance |
| Freeze and dependency reviewer | gpt-6.1-sol / high | Establish protected non-SVG code/tests and review shared changes; no retirement implementation |
| Chart correctness developer | gpt-6.1-sol / high | Drawing/data proof, safe retention and pinned-revision behavior |
| Evaluation developer | gpt-6.1-sol / high | Complete-attempt accounting, comparable metrics and result provenance |
| Picture pipeline developer | gpt-6.1-sol / high | Vector planning, parts, budget, placement and snapshot policy |
| Content sizing developer / visual reviewer | gpt-6.1-terra / medium for scoped edits; high for visual review | Implement an agreed narrow design; inspect the resulting artifacts |
| Independent correctness reviewer | gpt-6-astra / high | Challenge chart and compatibility claims independently of their author |
| Optional read-only mapper | gpt-6-luna / medium | Bounded file/reference and existing-evidence inventory |

These are rotating roles, not permanent simultaneous agents. Use at most five active agents including the orchestrator: normally three workers plus one independent reviewer plus the main agent. Confirm model availability in the new session. Reserve Astra xhigh for a demonstrated unresolved contract conflict. Do not use max/ultra by default. Distinguish developer-agent models from the application's generation and judge models.

When the delegation tool requires a fresh context to select a model, provide a self-contained task brief instead of inheriting all history. Include the controlling freeze decision, exact owned paths, protected contracts, expected deliverable, and output restrictions. Do not allow recursive delegation. Report summaries and references, not source, diffs, customer text or complete logs.

Use separate worktrees for independent writes. Assign every writable file one owner. The orchestrator owns integration of shared admission, environment-setting, revision-ledger and project-policy files. A worker reports a proposed change for a shared file instead of racing another worker to edit it. Keep topics independently reviewable.

## 4. Execution order

### P0: establish the freeze boundary

Inventory only the targeted strategies, their entry points, directly connected common infrastructure and dedicated tests. Record the starting protected file set and relevant hashes/status without copying customer material or secrets. Classify shared and mixed files explicitly. Do not search for all dead code or start a mass rename.

Known shared dependencies include `models/authored_infographic_plan.py`, the authored style catalogue/data, canvas contracts, checkpoint services, renderer infrastructure, and imports from `author_slide_svg.py` into `author_slide.py`. `svg_presentation_service.py` also imports the authored service's semaphore. Preserve these dependencies; do not extract them merely to enable deletion, because deletion is out of scope.

Deliver a small task board with owners, paths, acceptance conditions, dependencies and the single smallest proposed check per implementation unit. Resolve conflicts between old documents and the freeze decision before assigning writes.

### P1: chart correctness and evaluation, in parallel

Chart work must separate failure to construct an editable native object from an SVG that misrepresents values, category/series association, units or geometry. Investigate missing/hidden/repeated values, category reassignment, misleading bar proportions, pie/donut proportions where applicable, and other unsafe retention cases. Use small synthetic fixtures; do not place customer-derived text in tests.

Preserve old stored SVG snapshots, prompt bytes and their pinned interpretation. Implement a fresh native-requirements revision for new admissions if necessary; do not silently change old v3/v4 behavior. Native-requirements revisions and verifier revision ledgers are different mechanisms. Do not bump verifier revisions or append source hashes to conceal changed stored-evidence behavior. If the fix cannot preserve the existing evidence contract, report the exact conflict and continue independent work.

Evaluation work must account for every attempted deck, including failures and retries, and report completion/failure rates separately from successful-deck scores. Keep failed records when a retry is authorized. Express first-draft acceptance and fallback counts with their page denominators. Require matching inputs, template pins and judge settings/versions; record the actual code and relevant configuration identities. The current cell-paired threshold rule is a heuristic, not a statistical confidence statement.

Keep numeric-token retention as a descriptive metric, and add appropriately scoped category/value/unit/series evidence for data claims. Distinguish template-specific wording from common-phrase leakage candidates. Do not claim that a zero heuristic count proves no leakage. Do not inflate this into a new general semantic-evaluation platform.

### P2: SVG pictures and content sizing

After the shared contracts from P1 are integrated, develop pictures and sizing as separate changes behind default-off internal knobs frozen at admission. Register knobs through the existing setting mechanism and preserve literal off-path prompt bytes.

Trace the existing vector path first: `vector_illustrations=True` already permits scene planning without an image provider or style illustration prompt. Do not add a duplicate pipeline to solve a stale diagnosis. Plan scene/spots/pairs/pictograph parts from the source content and template style. Original pages containing any picture are not the same population as scene-eligible pages; do not copy the reported 47% into the scene cap. Preserve requests that prohibit illustrations, optional-failure behavior and bounded provider-call budgets. Avoid duplicate cover art in framed templates.

Content sizing should reduce oversized components without worsening whole-body whitespace, readability or reading order. Measure both card-internal empty space and the page/body context. Do not add a rejecting validator before evidence supports it. Preserve free body composition: no copied source skeletons, fixed slots, Deck IR page schemas or source-text refill.

Re-measure the existing connectors policy separately; do not create a new flow-diagram engine merely to run the experiment.

### P3: independent review and integration

The independent reviewer checks data truthfulness, frozen non-SVG behavior/tests, snapshot compatibility, provider budgets and unsupported claims. The orchestrator resolves findings and integrates only accepted changes. Compare protected files against their recorded starting state; never overwrite concurrent user changes to restore a stale hash.

For frozen dedicated tests, preserve content and execution participation. For shared changes, use an existing targeted frozen test where relevant as part of the one narrow check, without rewriting it. Do not remove assertions, expand allowlists or skip failures to produce a green result. Keep pre-existing failures distinct from introduced changes.

Run exactly one smallest relevant check after each coherent implementation unit, selected for the changed layer. Extend existing focused tests only for meaningful new SVG contracts; do not create tests solely to verify document wording. Do not automatically combine lint, typecheck, build and tests or run a full suite. Re-run a check only after a change that can affect its result. The user explicitly requires runtime server validation in addition to those focused checks.

### P4: start/reload the managed stack and verify the integrated code

This phase is required, not an optional follow-up. Read `RUNNING-LOCAL.md` and the current stack-script options. Inspect `scripts/start-local-stack.ps1 -Status`, existing process ownership, ports and the preserved launch environment first, without printing credentials.

If the managed stack is absent, start it with `scripts/start-local-stack.ps1`. If present, preserve its ports/environment and reload only affected components. The inspected script supports `-RestartComponent api,worker-generation,worker-export`; choose only affected components. Let Next.js use its existing HMR/supervisor behavior. Do not stop unrelated services or manually launch `servers/fastapi/server.py`. Require PostgreSQL for the API and both operation controllers; never accept SQLite fallback.

Record evidence that the affected services loaded the integrated source, not just that a port is open. Verify health/readiness and authenticated same-origin routing, then exercise the changed SVG path against synthetic fixtures or existing authorized artifacts. Check native-object decisions, exact data representation, persisted pinned settings and PPTX export where the change affects them. Exercise relevant unchanged non-SVG contracts using existing fixtures/read-only paths; do not purchase a non-SVG generation simply to demonstrate freezing. Preserve authentication and tenant boundaries.

Visual acceptance is required for the changed SVG output. Inspect representative ordinary, data-rich and dense pages in the actual resulting PPTX, using the available authorized renderer/review path. Artifact-specific review applies only to its exact digest. Export success alone is not visual acceptance. Report a renderer or artifact-access limitation precisely and continue all independent checks.

Local stack startup, focused checks and provider-free runtime verification are authorized by the owner. Do not request permission again for these steps. Before any newly paid provider measurement, concretize the code/settings/template pins, deck count, cost ceiling and stop conditions, and obtain any still-required spend approval. Continue independent development and local verification while that approval is pending. Do not run an unlimited retry loop or repeat an uncertain provider outcome.

For approved live measurement, use a small selection experiment, then the prescribed four source-sized inputs with matched conditions and three runs per input for surviving changes. A full A/B comparison on template A is 24 decks; separate experiments and combinations require their own explicit counts. Record failures and report all actual spend. Verify a second template's count/contract metrics without claiming its uncalibrated judge is comparable. Judge overall 3.8 is an intermediate target, not proof of original-level visual acceptance.

### P5: finish with evidence, not another proposal

Continue until the authorized implementation, focused checks, managed-stack validation and artifact review are complete. Do not finish after only a plan or agent summaries. Required evidence unavailable through an external condition remains explicitly unverified; complete the independent work and report the exact remaining dependency rather than inventing success.

The final report contains changed files/commits, frozen-scope audit, actual check results, loaded service/source evidence, runtime and export observations, artifact digests/review status, measured versus unmeasured quality, and remaining blockers. Keep it concise and do not paste source, diffs, document bodies or raw logs.

No push, merge, deployment, destructive filesystem operation, database removal or customer-data transfer is authorized by this brief. Prepare any separately requested external action concretely before asking for its final approval.
