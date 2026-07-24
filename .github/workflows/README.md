# GitHub Actions Workflows

## Upstream compatibility controls

`upstream-compatibility.yml` is the pinned, local contract gate. It verifies the
checked-out manifest, Alembic ledger/head, Template V2 boundaries, protected
patch anchors, presentation-export `v0.4.2`, and relevant database behavior.
It does not decide whether the official upstream branch moved.

`upstream-intake.yml` is the separate remote observation gate:

- pull requests that change intake files run only dependency-free offline
  fixtures; external upstream state therefore cannot make an unrelated pull
  request fail;
- scheduled and manually dispatched runs query only the official
  `presenton/presenton:main` GitHub API using an exact resolved SHA;
- the job writes a Markdown step summary and retains Markdown/JSON evidence;
- ordinary upstream movement is a reviewable success, configured
  `contract-risk` exits `2`, and operational/metadata errors exit `1` while
  explicitly leaving `changeDetected` false;
- scheduled/dispatch runs additionally open or update a single rolling
  `upstream-intake`-labelled issue whenever drift is detected or an intake
  error occurs, so reviewable movement is not lost in a green run and repeated
  errors do not spawn duplicate issues. Detecting that the nightly stopped
  running entirely (GitHub disables cron after prolonged inactivity) needs an
  external uptime monitor and is intentionally out of scope.

Both workflows use read-only repository permissions. The intake checkout and
runtime actions are immutable digest pins, credentials are not persisted, and
the tool never fetches into a worktree, merges, or cherry-picks. To adopt a new
baseline, first review the intake report, manually port only approved changes,
run the full compatibility/test matrix, and then update the manifest, intake
policy, migration ledger, protected-patch registry, and test dispositions that
were actually affected.

Changes to the compatibility registries also trigger
`upstream-compatibility.yml`, which prepares the pinned export runtime before
running the local verifier. That runtime-dependent verifier is intentionally
not duplicated in the intake workflow's offline job.

## Workflow roles

The workflows deliberately separate portable application tests from
capability-specific integration gates:

| Workflow | Required environment | Role |
| --- | --- | --- |
| `test-all.yml` | Ubuntu, Node 22, locked `uv` environment, Chromium | Canonical full FastAPI SQLite suite, FastAPI PyInstaller build, shared Next.js Node tests, lint/build, and the selected Cypress component suite. It also validates the local runner's complete dry-run plan. |
| `upstream-compatibility.yml` | Ubuntu plus Windows path checks; PostgreSQL 16 for one job | Static/export compatibility contracts, three Windows path/synchronization guards, and the real PostgreSQL migration integration test. The PostgreSQL job is intentionally separate from the SQLite FastAPI suite. |
| `upstream-intake.yml` | Offline fixture on pull requests; GitHub API on schedule/manual dispatch | Detects upstream movement without merging it. Live intake is intentionally not a pull-request dependency. |
| `template-v2-export-fidelity.yml` | Ubuntu and Windows with Chromium, LibreOffice, and PDF tools | Template V2 structural export checks and required rendered-image fidelity on both operating systems. |
| `g4-pptx-roundtrip.yml` | Ubuntu Docker; Windows for release gates | Pinned export-runtime contract, Windows Electron/resource safety tests, optional manual Windows packaging, and required adaptive plus legacy end-to-end PPTX round trips. |
| `sync-releaes-to-r2.yml` | Release event and deployment credentials | Publishes release artifacts. This is a deployment workflow, not a pull-request test gate. |

### FastAPI consolidation

Before consolidation, `test-all.yml` ran the complete FastAPI suite from a
mutable pip install and built the binary, while `pytest.yml` repeated the same
suite from the lockfile. The canonical FastAPI job in `test-all.yml` now keeps
all of the stronger properties in one place:

- `uv sync --frozen` installs the committed lockfile;
- the Ubuntu system packages and Chromium required by the full suite remain;
- `uv run pytest -q --no-header` runs the complete SQLite/default-feature suite;
- PyInstaller still builds `server.spec`.

The PostgreSQL test was not folded into that job. It uses a digest-pinned real
PostgreSQL service, requires a test-only database name, and exercises
`test_postgresql_template_v2_migrations.py`; those dependencies and semantics
are distinct from the full SQLite suite.

## Action and image pinning policy

Reusable GitHub Actions are pinned to full immutable commit SHAs with a nearby
release-version comment. Service containers are digest-pinned. Compatibility
runtime lines remain Node 22 and Python 3.11; changing those lines is a
deliberate compatibility decision rather than routine action churn.

The pinned `presentation-export` archive is verified against a per-asset SHA-256
recorded in `compatibility/upstream-compatibility.json` (`exportRuntime.assets`);
`sync-presentation-export.cjs` fails closed if the downloaded bytes do not match,
so a re-uploaded release for the pinned tag cannot silently replace the runtime.

Operating-system package repositories and the release upload helper downloaded by
`sync-releaes-to-r2.yml` remain external mutable inputs. They are kept visible as
residual supply-chain risk rather than described as digest-pinned.

## Local CI parity runner

Use the same cross-platform entrypoint from PowerShell, Command Prompt, or a
POSIX shell:

```bash
npm run test:local
npm run test:local -- --dry-run
npm run test:local -- --dry-run --all
```

`./test-local.sh` is a thin Bash compatibility wrapper around that Node
entrypoint. The default core profile runs:

- locked root and Next.js installs;
- the `presentation-export` v0.4.2 synchronization contract, idempotent sync,
  and installed-runtime verification (a download occurs only when absent or
  stale);
- static upstream compatibility and the offline intake fixture;
- the locked complete FastAPI SQLite suite with feature flags at their default
  OFF state;
- the shared Next.js Node suite, lint, and production build.

Capability-specific gates are explicit:

| Flag | Additional gate and precondition |
| --- | --- |
| `--with-fastapi-binary` | Host PyInstaller build toolchain. Required in the canonical Ubuntu workflow. |
| `--with-cypress` | Installed Cypress binary; Linux also requires `xvfb-run`. |
| `--with-fidelity` | Chromium, LibreOffice, and `pdftocairo`; runs the current host OS only. CI remains authoritative for both Ubuntu and Windows. |
| `--with-postgres` | `PPT_AGENT_POSTGRES_TEST_URL` pointing to a database whose name ends in `test` or `tests`. |
| `--with-electron` | Windows only; runs the release code/resource tests and typecheck/build checks. Package creation and install/uninstall smoke remain an explicit manual workflow input. |
| `--with-g4` | Linux, Bash, and Docker; starts the development stack and requires both adaptive and legacy PPTX round trips. |
| `--all` | Requests every capability gate and fails when any precondition is unavailable. |

For PostgreSQL, keep credentials outside command history:

```bash
export PPT_AGENT_POSTGRES_TEST_URL='postgresql+asyncpg://user:password@localhost/presenton_tests'
npm run test:local -- --with-postgres
```

The runner reports exact meanings:

- `PASS`: the command actually ran and exited successfully;
- `FAIL`: a selected command failed or its required precondition was missing;
- `NOT RUN`: an optional gate was not requested and does not count as local/CI
  parity;
- `PLAN`: dry-run output only; no command or precondition was executed.

A successful host run therefore describes only the gates marked `PASS`.
GitHub remains authoritative for unavailable operating-system services and
cross-OS jobs.

## Targeted developer commands

The following commands are useful for focused iteration, but a targeted pass is
not a substitute for the complete parity profile:

```bash
npm run test:presentation-export-sync
node --test scripts/verify-upstream-compatibility.test.mjs
node --test scripts/intake-upstream-main.test.mjs

cd servers/fastapi
uv sync --frozen
uv run pytest -q --no-header

cd ../nextjs
npm ci
npm run test:ci-node
npm run lint
npm run build
```
