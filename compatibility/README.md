# Upstream compatibility control

This directory records the U0 compatibility contract against the upstream commit
pinned in `upstream-compatibility.json` as `baseline.upstreamSha`. That manifest
is the single source of truth; the sibling registries restate the same SHA and
the verifier fails if any of them disagree. This file does not restate it,
because prose is not covered by that check and would go stale silently.

- `upstream-compatibility.json` pins application/export/frontend versions, the
  11 Template V2 renderer discriminators, the lossless upstream payload
  boundary, and key `/api/v1` endpoints.
- `migration-translation-ledger.json` records the upstream and local Alembic
  chains after their common revision, including explicit non-translations.
- `protected-local-patches.json` identifies authored-hybrid, Windows runtime
  sync, and export security behavior that must survive upstream rebases.
- `upstream-test-contracts.json` records each reviewed upstream default-template
  and generic async-task test, including machine-readable exclusion reasons and
  the non-conflicting local regression contracts retained in its place.
- `upstream-intake-policy.json` controls the separate, read-only observation of
  official `presenton/presenton:main`, including the pinned commit metadata,
  review categories, and contract-risk escalation rules.

The verifier derives renderer, endpoint, and reviewed-test totals from these
registries instead of pinning duplicate numeric counts. Baseline SHA,
repository, commit timestamp (normalized across time zones), and subject must
agree between the compatibility manifest and intake policy. Protected files may
also declare `forbiddenContains` anchors for negative invariants such as
loopback exposure or insecure Electron web preferences.

The two commands deliberately have different responsibilities:

- `node scripts/verify-upstream-compatibility.mjs` verifies the checked-out
  pinned local contract, migration ledger, test dispositions, and protected
  source anchors. It does not access the network.
- `node scripts/intake-upstream-main.mjs --fail-on-risk` resolves the official
  remote `main` ref to an exact SHA, verifies that commit's metadata, and, only
  when the SHA differs, classifies GitHub compare evidence. It does not modify
  the checkout, fetch a Git remote, merge, cherry-pick, download an exporter,
  transplant code, or update any compatibility registry.
  It never consults a local named Git remote, so a fork without an `upstream`
  remote is not interpreted as upstream movement.

Use a fixture for a deterministic offline run:

```bash
node scripts/intake-upstream-main.mjs \
  --fixture scripts/fixtures/upstream-intake/changed-contract-risk.json \
  --output upstream-intake.md \
  --json --fail-on-risk
```

The intake status/exit contract is:

| Status | Meaning | Default exit | With `--fail-on-risk` |
| --- | --- | ---: | ---: |
| `unchanged` | SHA and pinned metadata match | `0` | `0` |
| `change-detected`, `info`/`review` | reviewable delta, no configured contract risk | `0` | `0` |
| `change-detected`, `contract-risk` | backend v2, destructive API/migration/schema evidence, exporter version, protected-path overlap, or incomplete/non-forward compare | `0` | `2` |
| `intake-error` | network, timeout, rate limit, missing ref, invalid response/config, ref race, or baseline metadata failure | `1` | `1` |

An intake error always records `changeDetected: false`; it is never reported as
an upstream change. A `/api/v2` string in experimental docs or a Next
proxy/client file is reported separately from an actual FastAPI backend route.
A protected-path overlap means manual reconciliation is required, not that the
local patch is already broken. The pinned verifier and behavioral tests make
that determination.

When official `main` changes, review the report and scope a manual, minimal
port. Recompute API endpoints, the complete Alembic revision graph and heads,
Template V2 discriminators/schema, `presentationExportVersion`, export
strategy boundaries, and protected invariants. Only after review should the
baseline SHA/metadata and all affected ledger, protected-patch, and test
disposition entries be changed together. Never silence a warning by updating
only an expected SHA, and never use wholesale merge or cherry-pick as the
baseline-update procedure.

## Git ancestry (merge base with upstream)

This fork's history is a filtered copy of upstream: the import removed large
binaries (`servers/fastapi/build/*`, `electron/servers/fastapi/build/*`,
`electron/resources/export/py/*`, `electron/.cache/export-runtime/*`,
`servers/fastapi/chroma/models/onnx/model.onnx`,
`servers/fastapi/fastembed_cache/**`, `servers/fastapi/assets/icons_vectorstore.json`).
Rewriting those blobs changed every commit SHA, so `git merge-base origin/main
upstream/main` fails by default and any `git log`/`git diff` against upstream
reports the entire upstream history instead of the actual delta.

Content-level divergence is exact: fork `e8675134` is a copy of upstream
`c11f34ba` (2026-05-23) whose tree differs only by two stripped binaries
(`electron/resources/export/py/convert-linux-x64`, one `fastembed_cache` blob).
Restore the ancestry with two local replace refs — the fork's first two
independent commits are re-parented onto that upstream commit:

```bash
git remote add upstream https://github.com/presenton/presenton.git   # once
git fetch upstream
git replace --graft e43420e059cb490f7ef3f9692db09e108ee9ce29 \
    c11f34ba24f4e7064234d632a832ed29dbc0a625
git replace --graft fe35a083bceaf19514562884eb441b7813ccbc5a \
    c11f34ba24f4e7064234d632a832ed29dbc0a625 e43420e059cb490f7ef3f9692db09e108ee9ce29
```

Verify (values at pinned baseline `57b194b2`):

```bash
git merge-base origin/main upstream/main            # c11f34ba24f4e7064234d632a832ed29dbc0a625
git rev-list --count upstream/main..origin/main     # 416  (fork-only commits)
git rev-list --count origin/main..upstream/main     # 850  (upstream delta to review)
```

Properties and limits:

- No object is rewritten. Commit SHAs, branches, tags, and PR links are
  unchanged, and `git replace -d <sha>` removes a graft. `git push` is
  unaffected because replace refs are not part of the pushed history.
- The refs are local to a clone (shared across worktrees of the same
  repository). Re-run the two commands in every clone; CI does not need them.
  `GIT_NO_REPLACE_OBJECTS=1` or `--no-replace-objects` bypasses them.
- Do not push `refs/replace/*`. That would drag upstream's pre-divergence
  objects, including the stripped multi-hundred-megabyte binaries, into this
  repository.
- The graft states only what is already true — the two trees at the graft point
  are identical apart from the stripped binaries — so it does not assert that
  any upstream change is present here. A synthetic `git merge -s ours
  --allow-unrelated-histories` merge would assert exactly that, and would hide
  every unported upstream change from all future comparisons. Do not use it.
- This does not change the intake policy above. Upstream changes are still
  reviewed and ported manually; the graft only makes `git log`, `git diff`, and
  rename detection against upstream report the real delta.

After a future baseline move, re-derive the graft point if the fork ever
replays more upstream history: the divergence commit is the newest commit
reachable from `origin/main` whose entire ancestry consists of upstream copies
(identical author, timestamp, subject; tree identical apart from the stripped
paths above), and the grafts go on its non-copied children.

## Database compatibility

- SQLite is the default runtime and unit-test database. Presentation deletion
  rolls back and returns a retryable `503` with `Retry-After: 1` for SQLite
  `BUSY`/`LOCKED`; permanent ownership or foreign-key conflicts remain `409`.
- PostgreSQL is verified by a dedicated live-database CI gate. It runs the
  official Alembic lineage from an empty database and covers Template V2
  upgrade/downgrade/re-upgrade, legacy and populated-data preservation,
  foreign keys, unique constraints, indexes, and child-first delete safety.
- MySQL and MariaDB are not supported by this compatibility contract. Upstream
  URL adapters and defensive dialect branches are retained, but there is no
  documented server/version/CI contract and a clean Alembic SQL compilation
  fails on unbounded `String` columns (`VARCHAR requires a length`).

Run the PostgreSQL gate locally only against a disposable database whose name
ends in `test` or `tests`:

```bash
cd servers/fastapi
export PPT_AGENT_POSTGRES_TEST_URL='postgresql+psycopg://user:password@127.0.0.1:5432/presenton_test'
uv run pytest -q --no-header tests/integration/test_postgresql_template_v2_migrations.py
```

Without the URL, local collection skips this destructive integration test with
an explicit reason. CI also sets `PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1`, so
the dedicated job fails instead of skipping if its service URL is missing.
