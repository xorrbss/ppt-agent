# Operational, release, and security runbook

Status date: 2026-07-26

This runbook separates checks that can run locally or in ordinary CI from work
that genuinely requires managed infrastructure, signing identity, or release
credentials. A green dry run is not evidence that an external service was
contacted.

## Managed PostgreSQL canary

The wrapper rejects SQLite, missing database names, loopback hosts in managed
mode, non-staging/production tiers, an OFF flag, and an empty allowlist. It does
not print the URL, host, user, password, or template IDs.

```bash
export DATABASE_URL='postgresql+psycopg://...'
export TEMPLATE_V2_DEPLOYMENT_TIER=staging
export ENABLE_TEMPLATE_V2=true
export TEMPLATE_V2_TEMPLATE_ALLOWLIST='canary-template-id'
node scripts/operational-release-preflight.mjs canary --dry-run
node scripts/operational-release-preflight.mjs canary
```

The second command invokes the existing content-free FastAPI readiness probe.
It must run in the deployed application environment so the private-storage
check observes the real storage mount. Running it on a generic hosted CI runner
against only the database would be misleading.

No managed database URL or private storage credentials are committed. Until an
operator supplies them in the target environment, managed canary execution
remains externally blocked.

### Local equivalent rehearsal

Use only a disposable local PostgreSQL database. The explicit option prevents a
local rehearsal from being reported as managed evidence.

```bash
export DATABASE_URL='postgresql+psycopg://postgres:postgres@127.0.0.1:5432/presenton_test'
export TEMPLATE_V2_DEPLOYMENT_TIER=staging
export ENABLE_TEMPLATE_V2=true
export TEMPLATE_V2_TEMPLATE_ALLOWLIST='local-canary'
node scripts/operational-release-preflight.mjs canary --allow-local-rehearsal
```

The destructive migration integration suite has its own database-name guard and
is not a readiness probe:

```bash
cd servers/fastapi
export PPT_AGENT_POSTGRES_TEST_URL="$DATABASE_URL"
export PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1
uv run pytest -q --no-header tests/integration/test_postgresql_template_v2_migrations.py \
  tests/integration/test_postgresql_template_v2_canary_rollback.py
```

## Feature flag OFF rollback drill

1. Stop synthetic and user canary submissions.
2. While the deployed flag is still ON, require a clean drain:

   ```bash
   node scripts/operational-release-preflight.mjs rollback-drain
   ```

3. Set `ENABLE_TEMPLATE_V2=false` in the deployment controller and restart the
   API and worker processes. Do not delete Template V2 rows or private sources.
4. Run the verification from the restarted deployment environment:

   ```bash
   export ENABLE_TEMPLATE_V2=false
   node scripts/operational-release-preflight.mjs verify-off
   ```

5. Confirm a legacy generate/edit/export smoke succeeds and a new Template V2
   submission is rejected by policy. The wrapper proves the explicit OFF
   configuration and flag-independent database health; the process restart and
   external HTTP behavior require the deployment platform.

## Windows signing, AppX, and R2

Run a value-redacted preflight:

```bash
node scripts/operational-release-preflight.mjs release
node scripts/operational-release-preflight.mjs release --require-signing --require-r2
```

The strict command remains blocked until all of the following are true:

- a trusted code-signing certificate is provided as `CSC_LINK` (and
  `CSC_KEY_PASSWORD` when applicable);
- `electron/build.js` uses a fork-owned AppX identity and a publisher exactly
  matching the certificate subject;
- the protected `sync_r2` GitHub environment contains `R2_ACCOUNT_ID`,
  `R2_ACCESS_KEY`, and `R2_SECRET_KEY`;
- the workflow's read/write bucket probe proves access to the intended prefix.

The preflight reports only presence booleans. Never print or persist secret
values. The G4 signed gate and R2 sync workflow call this same preflight.

## Linux and macOS package/visual gates

`cross-platform-packaging-security.yml` performs full Next.js, FastAPI, and
Electron packaging on `ubuntu-latest` and `macos-14`. It disables automatic
signing and publishing, creates SHA-256 manifests, and uploads candidates for
seven days. These unsigned artifacts are quarantined evidence, not release
inputs.

`template-v2-export-fidelity.yml` runs structural and visual fidelity on Linux,
macOS, and Windows. Platform-specific LibreOffice and Poppler installations are
explicit so a missing renderer fails rather than silently skipping visual
assertions.

## Malware, retention, and access policy

- CI candidates: repository readers with Actions access only, seven-day
  retention, no publishing permission, and no release secrets.
- Fidelity failure evidence: fourteen-day retention and no document content
  from managed/private sources.
- Malware gate: a fresh ClamAV database is mandatory; failure to refresh is a
  failed gate. Scan all downloaded package candidates with archive inspection.
- Promotion: rebuild from the reviewed commit, sign, regenerate SHA-256
  manifests, scan again, and retain the release workflow/run URL. Never promote
  the unsigned CI candidates.
- Private PPTX sources: follow `docs/template-v2-canary-runbook.md`; cleanup is
  flag-independent, and confirmed sources remain auditable according to the
  existing retention policy. Do not place customer decks in Actions artifacts.
- Access review: protect signing and R2 environments with required reviewers,
  grant the workflow only `contents: read`, rotate leaked/revoked credentials,
  and audit environment access before each public release.

## Generated-file hygiene

`.gitignore` excludes `*.tsbuildinfo`, Python bytecode, package output, and
visual test artifacts. `servers/nextjs/tsconfig.tsbuildinfo` is already tracked,
so ignore rules alone do not remove it from the index. This follow-up
intentionally does not delete or rewrite that user-owned tracked file. A later
dedicated commit may run:

```bash
git rm --cached servers/nextjs/tsconfig.tsbuildinfo
```

only after its current diff is reviewed and the team agrees to stop tracking
it.
