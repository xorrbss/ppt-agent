# CI coverage gap: how red FastAPI tests landed and stayed red

Investigation date: 2026-09-12. Branch under inspection: `serve/main-20260902`
(HEAD `4c0eb5a15`). Read-only investigation; no workflow, `pyproject.toml`, or
runner script was modified.

Trigger: `servers/fastapi/tests/unit/test_chat_first_generation_integration.py`
carried 20 failing tests, one of which
(`test_v1_async_generation_uses_strict_v2_admission_before_claim`) was already
failing at `76d519f21`, the commit that introduced it. At HEAD the file now
passes (`uv run pytest -q --no-header tests/unit/test_chat_first_generation_integration.py`
-> `31 passed in 28.28s`).

---

## Q1 — Why did red tests pass CI?

**Short answer: no CI system executes pytest for this repository's commits.**
The only pytest invocation in the repository lives in a GitHub Actions
workflow, and this fork's `origin` is Bitbucket. The Bitbucket pipeline, which
is the one that actually runs, contains no pytest step at all. Three
independent conditions each suffice to explain the outcome; all three hold.

### Q1.1 — What the FastAPI job executes (if it were to run)

| Property | Value | Evidence |
| --- | --- | --- |
| Workflow / job | `Test All Applications` / `test-fastapi` | `.github/workflows/test-all.yml:1`, `:14` |
| Runner | `ubuntu-latest` | `.github/workflows/test-all.yml:16` |
| Working directory | `./servers/fastapi` | `.github/workflows/test-all.yml:26-28` |
| Invocation 1 | `uv run pytest -q --no-header tests/integration/test_workspace_production_lifecycle_gate.py` | `.github/workflows/test-all.yml:63-64` |
| Invocation 2 | `uv run pytest -q --no-header --ignore=tests/integration/test_workspace_production_lifecycle_gate.py` | `.github/workflows/test-all.yml:66-67` |
| `testpaths` | `["tests"]` | `servers/fastapi/pyproject.toml:418-419` |
| `addopts` | **none defined** | `servers/fastapi/pyproject.toml:418-419` (the whole `[tool.pytest.ini_options]` table is two lines) |
| Other pytest config | none — no `pytest.ini`, `setup.cfg`, `tox.ini`, no `collect_ignore`/`norecursedirs` in any conftest | `rg` over `servers/fastapi` returned nothing |
| `-k` / `-m` / `--deselect` | **none anywhere** in `.github/workflows/` or `bitbucket-pipelines.yml` | `rg -n 'pytest .*(-k \|-m \|--deselect)'` -> no match |
| `continue-on-error` / `\|\| true` | **none**; the only `if:` in the file is `if: always()` on the diagnostics upload | `.github/workflows/test-all.yml:163` |
| Path filters | none — `test-all.yml` is not path-filtered | `.github/workflows/test-all.yml:3-8` |
| Required? | Documented as a required branch-protection context | `.github/workflows/README.md:73-79` |

**Conclusion: the invocation is unfiltered and fail-closed.** If it ran, a red
test would be a red job.

### Q1.2 — Is the file collected by that invocation?

Yes. Reproduced locally with the exact CI arguments:

```
cd servers/fastapi
uv run pytest -q --no-header --ignore=tests/integration/test_workspace_production_lifecycle_gate.py --collect-only -q
```

| Measure | Result |
| --- | --- |
| Exit code | `0` (no collection errors) |
| Test files collected | 640 |
| Tests collected | **7522** (CLAUDE.md's "~1,880 tests" is badly stale) |
| `tests/unit/test_chat_first_generation_integration.py` | **collected, 31 tests** |
| `test_*.py` files tracked under `tests/` | 641 |
| On disk but not collected | exactly 1: `tests/integration/test_workspace_production_lifecycle_gate.py` (deliberately `--ignore`d, run as its own step) |

So collection coverage of the FastAPI tree is complete. The file was collected
and would have failed the job.

### Q1.3 — Then the workflow did not run. Why not?

| Condition | Finding | Evidence |
| --- | --- | --- |
| **A. The workflow is on the wrong forge.** `origin` is Bitbucket; there is no GitHub remote for this fork. `upstream` is the read-only upstream with `pushurl DISABLED`. GitHub Actions only runs for commits pushed to a GitHub repository, so `.github/workflows/*` is dormant configuration for this fork's own commits. | `remote.origin.url = https://bitbucket.org/ibizsoftware/devax-ppt`; `remote.upstream.url = https://github.com/presenton/presenton.git`; `remote.upstream.pushurl = DISABLED` (`git config --get-regexp '^remote\.'`). The repository itself states it: "The canonical `origin` is Bitbucket" — `.github/workflows/README.md:106`. |
| **B. Even on GitHub, the trigger would not fire.** `test-all.yml` fires only on `push: branches: [main]` and `pull_request: branches: [main]`. Development happens on `serve/*`, `agent/*`, `feat/*` branches. | `.github/workflows/test-all.yml:3-8` |
| **C. The forge that does run has no pytest.** `bitbucket-pipelines.yml` contains **zero** occurrences of `pytest` (`rg -n 'pytest' bitbucket-pipelines.yml` -> no match). Its five steps are the release-readiness config check, the shared Next.js Node suite, the Dashboard Cypress gate, upstream byte-parity, and the workspace E2E. The only Python gate anywhere in it is `ruff check .` inside the E2E step. | `bitbucket-pipelines.yml:6-100`; ruff at `:50` |
| **D. And the Bitbucket PR pipeline never fired for these commits either.** Both commits reached `main` through **local merge commits pushed directly**, not Bitbucket pull requests: the last 200 commits on `main` contain **0** subjects matching "pull request" (Bitbucket writes `Merged in <branch> (pull request #N)`). `76d519f21` entered `main` via `a9cef09ac "merge: fix stalled public generation lifecycle"` (2026-09-02 08:14:42 +0900, xorrbss). Neither commit is on `main`'s first-parent line. | `git rev-list --first-parent main \| grep -c <sha>` -> `0` for both; `git log --ancestry-path --merges --oneline 76d519f21..main \| tail -3` |

**Commit provenance.**

| Commit | Date | Subject | On `main`? | Arrived via |
| --- | --- | --- | --- | --- |
| `3a9690152` | 2026-08-30 08:09:31 +0900 | `feat(public-service): add durable generation and export path` | yes (not first-parent) | local merge, direct push |
| `76d519f21` | 2026-09-02 08:10:10 +0900 | `fix(public-v2): terminate stalled browser generation` | yes (not first-parent) | `a9cef09ac`, local merge, direct push |

**Net:** a FastAPI test can be written, committed, merged into `main` and pushed
without any machine ever importing it. The `--ignore`-free, filter-free pytest
command in `test-all.yml` is a correct gate pointed at a forge this fork does
not push to.

---

## Q2 — Census of never-run or silently-skipped tests

Two layers are reported separately, because they answer different questions:
**(N) never executes on the forge that actually runs** (Bitbucket), and
**(S) would execute there but is structurally skipped** (or is outside every
runner's discovery).

### Q2.A — FastAPI pytest

| Category | Count | Notes |
| --- | --- | --- |
| **(N) Test files never executed by `origin` CI** | **641** (7522 tests) | No pytest step exists in `bitbucket-pipelines.yml`. This is the entire suite. |
| (S) Files outside `testpaths` or with a non-collectable filename | **0** | Only 1 `*test*.py` exists outside `tests/` and it is production code: `servers/fastapi/utils/slide_capture_hit_test.py`. All 641 `test_*.py` files under `tests/` are collected. |
| (S) Collection import errors tolerated by CI | **0** | `--collect-only` exits `0` with no `ERROR` lines. |
| (S) Non-test helper modules under `tests/` (correctly not collected) | 18 | e.g. `tests/mocks/llm.py`, `tests/support/workspace_crash_probe_worker.py`, `tests/authored_subprocess_app.py`, `tests/user_config_permission_contract.py` |

Structural skips that are **always true on a Linux CI runner** (and therefore
would be dead even if `test-all.yml` ran):

| Gate | Files | Tests | Always-true on `ubuntu-latest`? |
| --- | --- | --- | --- |
| `pytestmark = pytest.mark.skipif(os.name != "nt", …)` | 3 | — | **Yes.** `tests/integration/test_process_kill_on_parent_death_guarantee.py:34`, `tests/unit/test_trusted_executor_process_supervisor.py:21`, `tests/unit/test_trusted_executor_windows_transport.py:33` |
| Whole-file `pytestmark` skip/skipif (all kinds) | 9 | **41** | Includes the 3 above plus Windows-font-byte gates (`tests/unit/test_authored_cjk_release_authority_v3.py:37` needs `malgun.ttf`/`batang.ttc`/`consola.ttf`; `tests/unit/test_authored_native_typography_registry.py:28`) and an unconditional retirement (`tests/integration/test_public_worker_image_transport_live.py:28`, `pytest.mark.skip`, no condition). |
| `PPT_AGENT_POSTGRES_TEST_URL` unset | **10** | **21** | **Yes for `test-all.yml`** — the variable is not in its `env:` block (`.github/workflows/test-all.yml:17-24`). It is set only in the path-filtered `upstream-compatibility.yml:148`, which itself never runs on Bitbucket. Files: `tests/integration/test_postgresql_{async_task_read_facade,authored_cluster_quota,owner_row_level_security,provider_durable_runtime,provider_settings_sync,public_tenant_security,template_v2_canary_rollback,template_v2_migrations,workspace}.py`, `tests/unit/test_public_operation_control_plane_races.py`. **This includes the owner row-level-security gate.** |
| `find_chrome() is None` | 14 files | — | Probably false on `test-all.yml` (`chromium` is apt-installed, `.github/workflows/test-all.yml:41-49`); **always true on Bitbucket**, where no pytest runs at all. |
| Unconditional `@pytest.mark.skip` on individual tests | 2 | 2 | `tests/unit/test_trusted_executor_wire_transport.py:133`, `:251` ("Docker transport is retired"). |

### Q2.B — Root-level `node --test` suites

`npm run test:next-ci` (root `package.json`) hand-lists 9 root `scripts/*.test.mjs`
files plus `test:settings-registry`; it does **not** auto-discover root `scripts/`.
29 `*.test.{mjs,cjs}` files exist under root `scripts/`.

| Category | Count |
| --- | --- |
| Root scripts tests **run** by `test:next-ci` | 10 |
| Root scripts tests **never run in any CI job** | **19** |

The 19 (full list, not truncated):

```
scripts/authored-bounded-gate-minus1/gate-minus1-authority.test.mjs
scripts/authored-editable-hybrid-mvp/contract.test.mjs
scripts/authored-hybrid-evidence-retention.test.mjs
scripts/authored-release-readiness-operations.test.mjs
scripts/authored-vnext-controlled-load-retry-audit.test.mjs
scripts/compatibility/verify-export-platform-archives.test.mjs
scripts/intake-upstream-main.test.mjs
scripts/operational-release-preflight.test.mjs
scripts/provider-registry.test.mjs
scripts/public-service-product-evidence.test.mjs
scripts/public-service-release-evidence-bundle.test.mjs
scripts/public-service-release-gate.test.mjs
scripts/public-service-release-matrix.test.mjs
scripts/public-service-render-range-evidence.test.mjs
scripts/public-service-resource-attestation.test.mjs
scripts/public-service-trusted-collector.test.mjs
scripts/sync-presentation-export.test.cjs
scripts/verify-authored-release-required-checks.test.mjs
scripts/verify-upstream-compatibility.test.mjs
```

Two of these are named in the repo's own docs as "targeted developer commands"
(`.github/workflows/README.md:228-230`) — i.e. knowingly manual.
`scripts/provider-registry.test.mjs` is a near-miss: `test:next-ci` invokes
`check:provider-registry` (generator `--check` only), not `test:provider-registry`,
which is the script that would run the node test.

### Q2.C — Next.js `node --test` suites

| Category | Count |
| --- | --- |
| `*.test.{ts,tsx,mjs}` under `servers/nextjs` | 208 |
| Outside the runner's `ROOTS` (`app components i18n lib scripts store tests utils`) | **0** |
| Under a bracket (`[id]`) directory — the `node --test` glob trap | **0** |
| Excluded by the runner's printed rules | `lib/export-fidelity/**`, `*.e2e.test.mjs` (`servers/nextjs/scripts/run-ci-node-tests.mjs:13-22`) |

The Next.js Node suite is the one genuinely healthy discovery mechanism here:
`run-ci-node-tests.mjs` walks directories with `readdirSync` and prints its
exclusions each run (`:24-31`, `:76-79`). No bracket-directory trap is currently
active. It runs on Bitbucket (`bitbucket-pipelines.yml:12-19`).

### Q2.D — Cypress

`test-all.yml:124` names exactly 22 spec globs. 77 `*.cy.*` specs exist under
`servers/nextjs`.

| Category | Count |
| --- | --- |
| Total Cypress specs | 77 |
| Named by the 22-glob GitHub gate | 22 |
| Inside the component `specPattern` (`servers/nextjs/cypress.config.ts:27-35`) | 73 |
| **Component specs NOT named by the gate** | **51** |
| Outside the component `specPattern` | 4 |
| Specs run by the workspace-E2E job | **2** — `cypress/e2e/canonicalConversationRoute.cy.ts` and `cypress/component/CanonicalWorkspaceChatPanelLiveBackend.cy.tsx` (`servers/nextjs/scripts/run-workspace-e2e.mjs:3209`, `:3267`) |
| **Specs actually executed by `origin` (Bitbucket) CI** | **3** of 77 — `DashboardPage.cy.tsx` (`bitbucket-pipelines.yml:34`) plus the 2 workspace-E2E specs |

First 20 of the 51 unnamed component specs:

```
app/(presentation-generator)/(dashboard)/settings/SettingPage.cy.tsx
app/(presentation-generator)/(dashboard)/settings/TextProvider.cy.tsx
app/(presentation-generator)/(dashboard)/templates/components/TemplatePanel.cy.tsx
app/(presentation-generator)/authored-vnext/AuthoredPackageLifecycle.cy.tsx
app/(presentation-generator)/authored-vnext/AuthoredVnextFlowClient.cy.tsx
app/(presentation-generator)/authored-vnext/master-setup/MasterAiBindingInsert.cy.tsx
app/(presentation-generator)/authored-vnext/master-setup/MasterCatalogDialog.no-standard.cy.tsx
app/(presentation-generator)/authored-vnext/master-setup/MasterCatalogDialog.recalculation.cy.tsx
app/(presentation-generator)/authored-vnext/master-setup/MasterSetupPublishFlow.cy.tsx
app/(presentation-generator)/authored-vnext/master-setup/MasterStudioCanvas.cy.tsx
app/(presentation-generator)/authored-vnext/master-setup/MasterWysiwygInsert.cy.tsx
app/(presentation-generator)/components/AuthoredSlidePreview.cy.tsx
app/(presentation-generator)/outline/components/OutlinePage.cy.tsx
app/(presentation-generator)/outline/hooks/useOutlineStreaming.cy.tsx
app/(presentation-generator)/outline/hooks/usePresentationGeneration.master-pin.cy.tsx
app/(presentation-generator)/presentation/components/AuthoredPageRemake.cy.tsx
app/(presentation-generator)/presentation/components/AuthoredTemplateChanger.target-workspace.cy.tsx
app/(presentation-generator)/presentation/components/FlaggedSlideBadge.cy.tsx
app/(presentation-generator)/presentation/components/PageRemakeHistory.cy.tsx
app/(presentation-generator)/presentation/components/PresentationPage.agent-focus.cy.tsx
... (31 more)
```

The 4 specs outside the component `specPattern`:

```
cypress/e2e/authoredVnextLiveRoute.cy.ts          <- run nowhere
cypress/e2e/canonicalConversationRoute.cy.ts      <- run by workspace-e2e
cypress/e2e/workspaceChatFirstRoute.cy.ts         <- run nowhere (but its SOURCE TEXT is asserted, see Q3)
scripts/authored-vnext-local-happy-path.cy.ts     <- run nowhere
```

### Q2.E — Side finding: the Bitbucket Node step invokes `uv` in an image with no Python

`npm run test:next-ci` chains `npm run check:template-package` and
`npm run check:notice`, both of which are `uv run --directory servers/fastapi …`
(root `package.json`). The Bitbucket step that runs it installs only npm
dependencies — `npm ci`, `npm ci --prefix servers/nextjs`, `npm run test:next-ci`
(`bitbucket-pipelines.yml:12-19`) — on the `cypress/included:15.19.0` image
(`:1`). By contrast, the workspace-E2E step explicitly installs `python3`,
`python3-venv` and `uv` before touching anything Python
(`bitbucket-pipelines.yml:43-48`). Either that step is failing on every PR and
`main` build, or uv reaches it by some means not visible in the repository. I
could not query Bitbucket build history from here, so this is flagged rather
than asserted.

---

## Q3 — Does the meta-test catch this?

`scripts/required-ci-contract.test.mjs` is a **text-matching contract over
workflow and source files**. It never executes a suite, never enumerates what a
runner would collect, and never compares a named list against what exists on
disk — except for Cypress, where it checks the arrow in only one direction.

| Would it have caught… | Verdict | Evidence |
| --- | --- | --- |
| A `-k` / `-m` / `--deselect` added to the pytest command | **No.** It asserts the presence of two exact substrings and that the Gate-E path appears exactly twice; extra flags anywhere else in the job pass silently. | `:86-88` — `assert.ok(job.includes(\`uv run pytest -q --no-header ${gate}\`))`, `assert.ok(job.includes(\`--ignore=${gate}\`))`, `assert.equal(job.split(gate).length - 1, 2)` |
| A `continue-on-error: true` added to the pytest step | **No.** No assertion mentions `continue-on-error`. | grep of the file |
| That pytest runs on the forge that actually gates merges | **No — it asserts the opposite is acceptable.** It requires Bitbucket's PR and `main` lists to contain `*workspace-chat-first-e2e` and `*nextjs-ci-node-contracts` and requires `ruff check .` on Bitbucket, but never requires a pytest step there. | `:101-126` (ruff on "both CI providers"), `:241-248` |
| That the workflow trigger is broad enough to see working branches | **No — it pins the narrow trigger as the contract.** | `:59` — `assert.match(workflow, /pull_request:\s*\n\s*branches:\s*\[\s*main\s*\]/u)` |
| That `.github/workflows/*` is wired to a forge that receives this fork's pushes | **No.** Nothing in the file inspects remotes, and `.github/workflows/README.md:106` documents Bitbucket as canonical without a corresponding executable assertion. |
| A named Cypress spec that no longer resolves to a file | **Yes.** `assertNamedSpecsResolve` re-derives the file list and the `specPattern` and fails on an unmatched glob, for all three gate definitions. | `:1380-1430` |
| A Cypress spec that exists but is named by no gate | **No.** The check is one-directional (named -> exists). 51 component specs are unnamed. | `:1380-1405` |
| The 22-spec count silently shrinking | **Partly.** `assert.equal(specList?.length, 22)` pins the count, so removing a spec fails — but so does *adding* one, which makes the number a ratchet against growth as much as against shrinkage. | `:1063` |
| A Next.js Node test file placed outside the runner's `ROOTS` | **No.** It asserts the `ROOTS` array's literal text and that `tests/` is not excluded, but never enumerates files against it. | `:211-221`, `:1062` |
| A root `scripts/*.test.mjs` never added to `test:next-ci` | **No.** It asserts that *four specific* substrings appear in `test:next-ci`; 19 root script tests are unlisted. | `:189-210`, `:976-979` |
| `cypress/e2e/workspaceChatFirstRoute.cy.ts` never being executed | **No — and it actively creates the illusion of coverage.** Lines `:1168-1211` read that spec's source and assert ~15 regexes about its content. Nothing executes it. Source-text assertions on a never-run spec are the sharpest form of this whole failure mode. | `:1168-1211` vs `servers/nextjs/scripts/run-workspace-e2e.mjs:3209`, `:3267` |
| A pytest file that fails | **No, by construction.** The meta-test asserts *contracts about* CI, not CI results. | whole file |

**Precise summary:** the meta-test would have caught a mistyped Cypress spec
name, a renamed job, a deleted Ruff step, or a removed Gate-E split. It would
not have caught — and did not catch — that the job containing the pytest
invocation is on a forge this fork never pushes to, that the forge it does push
to runs no pytest, or that commits reach `main` by direct push rather than PR.

---

## Proposed changes (PROPOSALS — none applied)

Ranked by value. Every item is a proposal for the coordinator's approval; no
workflow, `pyproject.toml`, or runner script was edited.

| # | PROPOSAL | Value | Risk |
| --- | --- | --- | --- |
| **1** | **Add a FastAPI pytest step to `bitbucket-pipelines.yml`** — a new step that installs `python3` + `uv` the way `*workspace-chat-first-e2e` already does (`:43-48`) and runs the two-command split from `test-all.yml:63-67`. Add it to both `pull-requests: "**"` and `branches: main`. | Highest. This is the single change that converts "committed" into "executed" for 7522 tests. | **High, and quantifiable before landing:** the suite's current state on a clean Linux runner is unknown. Run it once locally/in a scratch pipeline first and record the failure count; expect a non-trivial backlog (memory records ~23 standing Node-suite failures and a 144-failure local-suite episode; the Python suite has never been gated on this forge). Landing it red blocks all merges. Stage it as non-blocking for one week, publish the failure list, then make it required. Also adds ~real minutes to every PR. |
| **2** | **Make `main` unpushable except through a Bitbucket pull request.** All 200 most recent `main` commits arrived by direct push (0 "pull request" merge subjects); the `pull-requests:` pipeline therefore gates nothing that actually reaches `main`. Enable the branch restriction plus the two merge checks already documented at `.github/workflows/README.md:109-114`. | Highest. Without it, proposal #1 only protects PRs nobody opens. | Low technically, high procedurally: it changes every contributor's daily flow and will block the current multi-session direct-merge habit (see the shared-worktree working model). Needs an explicit human decision, not a config edit. |
| **3** | **Make the forge mismatch loud.** Either (a) add a mirror push of `main` to a GitHub repo so `.github/workflows/*` genuinely executes, or (b) add an assertion to `scripts/required-ci-contract.test.mjs` that every gate named as "required" in `.github/workflows/README.md:73-79` has an executing counterpart in `bitbucket-pipelines.yml`, and fail otherwise. | High. Today the repository carries 11 GitHub workflows and a README describing required checks, none of which run — a false sense of coverage that this investigation had to discover by reading `git config`. | (a) medium: mirroring costs CI minutes and needs credentials. (b) low: a pure test addition, but it will fail immediately — that is the point, and it should be landed together with #1 or it becomes a red gate with no fix available. |
| **4** | **Extend `required-ci-contract.test.mjs` with reverse-direction assertions.** It already walks the Cypress spec tree (`:1311-1327`); add (i) every component spec must be named by at least one gate *or* appear in an explicit, commented `UNGATED` allow-list; (ii) every root `scripts/*.test.{mjs,cjs}` must appear in `test:next-ci` or in an allow-list. | High. Converts today's one-directional check into a ratchet, and makes each of the 51 + 19 omissions a deliberate, reviewed line rather than an accident. | Low-medium. Requires writing the initial allow-lists (51 Cypress + 19 script entries), which is honest bookkeeping but is a large diff. Risk of the allow-list becoming the new dumping ground; mitigate by requiring a reason string per entry, the way `pyproject.toml`'s per-file ignores are annotated. |
| **5** | **Replace the substring assertions on the pytest command with a stricter shape check.** Assert that the `test-fastapi` job's pytest lines contain no `-k`, `-m`, `--deselect`, `--maxfail`, or `-x`, and that the job body contains no `continue-on-error`. | Medium. Cheap insurance for a failure mode that has not happened yet but that `:86-88` would not see. | Very low. Pure test addition; passes today. |
| **6** | **Decide the fate of the always-skipped Linux-CI blocks.** 41 tests in 9 whole-file-gated modules and 21 PostgreSQL-gated tests will never run under proposal #1. Either provision them (a PostgreSQL service container for the PG files, as `upstream-compatibility.yml:148` already does) or move the Windows-only modules behind an explicitly scheduled Windows job. | Medium. `tests/integration/test_postgresql_owner_row_level_security.py` is a security gate that currently executes on no forge. | Medium: adding a PG service to the pipeline is straightforward but those 21 tests have not run in CI either, so expect failures. Provision them as a separate, initially non-blocking step. |
| **7** | **Delete or re-point dead assertions.** `required-ci-contract.test.mjs:1168-1211` asserts the source text of `cypress/e2e/workspaceChatFirstRoute.cy.ts`, which no runner executes; `tests/unit/test_trusted_executor_wire_transport.py:133,:251` and `tests/integration/test_public_worker_image_transport_live.py:28` are unconditionally skipped as "retired"; `test:next-ci` calls `check:provider-registry` where `test:provider-registry` was likely intended. | Low-medium. Removes coverage theatre, which is what made this gap invisible. | Low, but each item is a judgement call about intent — surface them individually rather than batching a deletion. |
| **8** | **Correct `CLAUDE.md`'s "~1,880 tests" to the measured 7522**, and note that the pytest gate is GitHub-only while `origin` is Bitbucket. | Low value, near-zero cost. | None. Note that `required-ci-contract.test.mjs:1049-1069` asserts specific `CLAUDE.md` substrings, so the edit must keep `/Ruff is locked/`, `/tests\/?\`, and \`utils\//`, and `/22 named Cypress specs/` matching. |

### What this investigation did **not** establish

- Whether the Bitbucket "Run shared Next.js CI Node suite" step currently passes
  despite invoking `uv` in an image with no Python (Q2.E). Bitbucket build
  history is not reachable from this checkout.
- Whether an external GitHub mirror of this fork exists outside the repository's
  own configuration. The conclusion in Q1.3-A rests on `git config` and on
  `.github/workflows/README.md:106`; an unrecorded mirror cannot be excluded from
  the repository alone. Condition Q1.3-B and Q1.3-C hold regardless.
- The failure count that proposal #1 would surface. That measurement should
  precede the proposal being accepted.
