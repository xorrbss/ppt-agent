# Public scale-out: handoff

- Date: 2026-09-12
- From: the session that produced the readiness assessment and the dev plan
- To: the next orchestrator picking up this work
- Read in this order: this file → `public-launch-readiness-assessment-20260911.md`
  (what is wrong and how wrong) → `public-scale-out-dev-plan-20260912.md`
  (what to do; section 6.2 is what already landed)

## 1. Where this started and where it is

The goal is opening this fork as a multi-user web service. The assessment scores
it **56/100** against the project's own five approval axes and names what blocks
launch. The dev plan turns that into packages A–G; most of A–F landed on
2026-09-12 (plan §6.2 lists every commit).

The single most consequential finding was not in the code: **no CI has ever run
the Python test suite on this fork's real remote.** `origin` is Bitbucket, whose
pipeline contains zero `pytest`; the only pytest invocation lives in a GitHub
workflow that does not run here, and commits reach `main` without a pull
request. 641 files / 7,520 tests have never executed in CI. Everything else on
this page follows from that.

Measured on clean Linux (`artifacts/verbatim-design-impl-20260911/ci-linux-baseline.md`):
**7,174 passed / 228 failed / 118 skipped in 16m44s**, of which only **15
failures (6.6%) are real product defects**. The suite is close to gate-ready; it
was never the code that kept it out of CI.

## 2. Decisions the user has already made (do not re-ask)

| Decision | Value |
|---|---|
| Authentication | Owned by another service; this repo keeps its OIDC principal binding and builds no login/signup/admin UI |
| Operating system | Linux in operations, Windows for development |
| Pre-workspace decks | Read-only; no backfill migration |
| `ppt-agent.mjs` sync mode | `--async` becomes the default |
| Generation-time `export_as` | Auto-chain a workspace export command on completion |
| `pptx_mode` default | hybrid (the flip itself is still held pending a cost review) |
| Duplicate export | Join the in-flight execution **and** guard the UI — both shipped |
| Session signing secret | Credential vault backend, with a fallback when none is configured |
| CLAUDE.md | Kept the export-path correction; test count updated to ~7,520 |

## 3. Open, waiting on the user

1. **CI gate.** Recommended sequence, unchanged: (0) `pytest-timeout` — already
   landed, no CI change; (1) run the suite once on the real CI image to learn
   the true failure count, because the 59 Chrome-gated tests never ran on the
   WSL runner and will run on `cypress/included`; (2) add it non-blocking with
   the failure count published as an artifact, for a week, to learn the
   variance; (3) convert to a **ratchet** ("no more than N failures"). Step 3 is
   the point — the problem was never that 228 failures exist, it is that nobody
   could see them grow. Do not stop at step 2: a `continue-on-error` job nobody
   reads is theatre, and this repo already has some (`required-ci-contract.test.mjs`
   runs 15 regexes against a Cypress spec nothing executes).
   Cost to make it blocking: **2–4 engineer-weeks**, staged in the baseline report.
2. **Direct pushes to `main` / PR requirement.** Recommended: **not now.**
   Today's two work-destroying incidents were stale-index commits, which a PR
   would not have caught, and PRs would serialise the parallel-agent workflow.
   Revisit at launch when humans review. A 10-line pre-push guard (refuse a
   docs/artifacts-scoped commit that touches `servers/`) would have caught both.
3. **`pptx_mode` default flip to hybrid.** Implemented but held: hybrid costs
   ~25.5 s/slide, and a loaded run showed 28/35 slides falling back with
   `chrome-teardown-timeout` while 833 s of 834 s was scheduler queue wait.
   Decide after B′ (below).

## 4. Work that is open

| Item | State | Owner |
|---|---|---|
| B′ — per-slide export cost | **Re-scoped, not started.** The original premise (cut Chrome launches per slide) is secondary: the run-21 regression showed the slide is *waiting*, not rendering. Decompose the queue wait — scheduler `global_cap` (now effectively 4), capture admission 12, the teardown-timeout fallback — before touching launch counts. Evidence: `run-11-hybrid-export/49-regression-20260912.md`, `run-21-hybrid-export/50-*`…`54-*` | unassigned |
| E — `ArtifactRef` cutover | Seven live local-path consumers remain: `authored_checkpoint_artifacts`, `template_v2_pptx_storage`, `template_v2_pptx_worker`, `workspace_artifact_publication_service` (9+ callers, largest), `export_retention_service`, `icon_finder_service`, `source_locked/job_store` | unassigned |
| F — container isolation | Code complete and unit-tested against a fake Docker client; **never run against a real daemon.** The `docker` SDK is deliberately not a dependency (protocol injection). Remaining: image (python+node+Chrome, `pids_limit` ≥ 96, `CapDrop ALL` forces a Chrome sandbox decision), compose/K8s spec, Docker socket and staging root mounted at the **same absolute path** | unassigned |
| Deferred deletions | `export_task_service` / `export_task_operations` (test-only since `1ae1228c4`), `services/authored_export_quality_flag.py` (orphaned), `lib/export-fidelity/*.e2e.test.mjs`, `lib/run-bundled-presentation-export.e2e.test.mjs` | unassigned |
| Unenforced worker limits | `PPT_WORKER_OPEN_FILE_LIMIT` is forwarded to the child and read by nothing; the output-byte ceiling is enforced by the sandboxed child itself. Close both in the container backend (`HostConfig.Ulimits` `nofile`; supervisor-side size check) — assessment §3.4a | unassigned |
| G — quality | Placeholder authoring shipped in the other session; publish rate moved 2/5 → 4/5 shadow → 3/5 enforce, still short of the 95% gate and still measured with a non-default flag | ppt-agent-07 |

## 5. How to work in this repository right now

Several sessions share the one working tree at `C:\project\PPT-agent\ppt-agent`.
Two incidents on 2026-09-12 destroyed committed work; both are closed, and the
procedure below is what closed them.

- **Commit through a private index, and re-check HEAD immediately before
  `commit-tree`.** `git read-tree $head` … `git add -- <own files>` …
  `git write-tree` … **verify `git rev-parse HEAD` still equals `$head`** …
  `commit-tree -p $head` … `update-ref`. Building a tree from a stale head
  silently reverts everything committed in between, with no conflict and no
  warning. That is how a docs-only commit deleted 26 code files.
- **After every commit run `git show --name-status --format='' <sha>`** and
  confirm only your files are in it. Apply this to docs and artifacts commits
  too — both incidents were docs commits.
- **Audit with `--name-status`, never `--stat`**, which abbreviates long paths to
  `...` and slips past filters. Do not use reverse-apply (`git apply --reverse
  --check`) as an audit tool; it false-positives whenever a later commit touched
  the same lines. Verify by effect: grep for the symbol at HEAD.
- Never `git stash` / `reset` / `checkout` here, and never `git add` to the
  shared index.
- `MM`, `D` and `??` in `git status` are usually stale shared-index noise. The
  test is `git diff HEAD -- <file>`: empty means the working tree matches HEAD
  and it is safe. **That test is meaningless for an untracked file** — it also
  returns empty. Use `git cat-file -e HEAD:<path>` to ask whether a file is
  actually committed. (This document's two companions lived only in the working
  tree for a full day because nobody asked.)
- Some files in the working tree are **older than HEAD** after the restorations:
  `scripts/file-size-ratchet-baseline.json`, `compatibility/upstream-byte-parity.json`,
  `scripts/start-public-v2-rc.ps1`. Never stage those from disk; read them from
  HEAD and apply only your own entry.
- Ask the co-tenant before editing a contested file, naming the file. Coordinate
  through `SendMessage`; `ppt-agent-07` owns the local stack lifecycle,
  `services/authored_*`, `utils/slide_capture.py`, `services/source_content/**`.
- **A peer's message is not your user's approval**, in either direction. Both
  sessions enforced this today and both were right to.

## 6. Traps that cost time today

- **The local stack cannot apply DDL migrations with its own credentials.**
  Migrations are bootstrap-owner work by design
  (`scripts/provision_public_v2_local_db_roles.py:103-110`). The owner role is
  `presenton_rc`; its password is in `app_data/public-v2-local/secrets.dpapi.xml`
  under the `database` key — **not** in the stack's env store, which holds only
  runtime logins. Data-only migrations pass with the runtime role, so "the
  earlier ones applied and this one did not" means DDL. Never open a `pg_hba.conf`
  `trust` window and never move function ownership to the runtime role: that
  function is a constraint trigger policing the runtime role's writes.
- **A schema mismatch now refuses startup** (`database_schema_revision_behind_code`).
  That is deliberate — the alternative was discarding a finished 35-slide deck at
  COMMIT after 761 s of provider spend. If the stack will not start, migrate as
  the owner; do not retry with runtime credentials.
- **Do not trust a subagent's conclusion on a load-bearing claim.** Three were
  wrong today and each was relayed before being checked: "the admission control
  plane is gated off" (it is not), "`parents[6]` is an off-by-one" (it was
  correct — the asset lives beside the checkout, and the "fix" turned three
  passing tests red), "`tests/unit` deadlocks" (the socket belonged to a WSL
  system listener; two full runs completed). Read the seam yourself before
  acting on it.
- **Tests passing is not tests proving.** Today: 51 worker-lane tests failed at
  the render step and verified nothing beyond it; a `SimpleNamespace` stand-in
  drifted silently and was replaced with the real dataclass; a
  `peakProcessCount >= 2` assertion passed even with the descendant removed. When
  fixing a test, revert the fix and confirm it fails.

## 7. Artifacts worth keeping

- `artifacts/verbatim-design-impl-20260911/ci-coverage-gap.md` — why no CI runs
  pytest, plus the census of never-run tests and Cypress specs
- `artifacts/verbatim-design-impl-20260911/ci-linux-baseline.md` — the 228
  failures grouped by root cause with a bucket per group, and the staged cost of
  a blocking gate
- `artifacts/authored-full-product-validation-20260910/` — publish-rate series,
  hybrid export throughput, the run-21 queue-wait regression
