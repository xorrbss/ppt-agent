# Contributing

This repository (`bitbucket.org/ibizsoftware/devax-ppt`) is a fork of the
open-source [Presenton](https://github.com/presenton/presenton) project. It has
diverged substantially — Korean UI, Windows-native operation without Docker, a
Korean-business template set, and a large second generation pipeline — and is a
**web-only** application: it does not ship or support an Electron desktop shell.
See `CLAUDE.md` for the full architecture.

## Where contributions happen

Development happens against this Bitbucket repository, not the upstream GitHub
project:

- **`origin`** (`bitbucket.org/ibizsoftware/devax-ppt`) is where branches, pull
  requests, and CI run. This is where you push changes and open PRs.
- The official Presenton project (`github.com/presenton/presenton`) is where
  this fork came from, but the fork no longer tracks or imports its changes.
  Do not open pull requests there for work on this fork.

Contributions can touch any part of the repository that a task requires —
`servers/fastapi/`, `servers/nextjs/`, `scripts/`, `docs/`, `contracts/`,
`compatibility/`, and top-level configuration. There is no directory-level
restriction like an upstream `electron/`-only policy; this fork has no
`electron/` directory.

## How to contribute

### Bugs

Open an issue (or report through your existing internal channel) and include:

- Steps to reproduce
- Expected vs. actual behavior
- Logs or screenshots

### Features

Describe the problem and a proposed solution before starting significant work.
For implementation directions that are genuinely ambiguous, ask before
implementing; for minor ambiguity, proceed with a reasonable default and note
the assumption in the PR description.

### Code contributions

1. Create a branch from `main`.
2. Implement your change.
3. Verify it locally — see "Verification" below.
4. Open a pull request against `main` on Bitbucket.

Keep PRs small and focused, and explain what changed and why. Include
screenshots for UI changes.

## Verification

Bitbucket Pipelines (`bitbucket-pipelines.yml`) is the CI that actually runs
against this fork's pull requests and `main` branch. It gates:

- `authored-release-readiness-config` — `node scripts/verify-authored-release-required-checks.mjs`
- `nextjs-ci-node-contracts` — `npm ci` (root and `servers/nextjs`), then `npm run test:next-ci`
- `dashboard-component-gate` — the `DashboardPage.cy.tsx` Cypress component spec
- `workspace-chat-first-e2e` — `uv run ruff check .` in `servers/fastapi`, the authored style sample storage check (`scripts/check_authored_style_samples.py`), and the workspace chat-first browser E2E suite (`npm run test:workspace-e2e`)

There is no other CI: the upstream GitHub Actions workflows were removed with
`.github/`, since they never ran for this fork's Bitbucket branches.

Before opening a PR, additionally run whatever local checks are narrowly
relevant to your change — see `CLAUDE.md`'s "Build / test / lint commands" for
the full command reference (FastAPI `uv run pytest ...` / `uv run ruff check .`,
Next.js `npm run lint` / `npm run build` / `npm run test:ci-node`, etc.). The
Bitbucket pipeline above does not run the full FastAPI pytest suite, so changes
to `servers/fastapi/` should still be verified with the relevant `pytest`
target locally.

## AI-assisted contributions

PRs created with AI tools (Claude, ChatGPT, Codex, etc.) are welcome. Please
mention:

- that the PR is AI-assisted
- the level of testing performed
- confirmation that you reviewed the generated code

## Code of conduct

Please follow the guidelines in `CODE_OF_CONDUCT.md`.
