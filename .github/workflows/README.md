# GitHub Actions Workflows

## Upstream compatibility controls

`upstream-compatibility.yml` is the pinned, local contract gate. It verifies the
checked-out manifest, Alembic ledger/head, Template V2 boundaries, protected
patch anchors, presentation-export `v0.4.2`, and relevant database behavior.
It does not decide whether the official upstream branch moved.

`upstream-intake.yml` is the separate remote observation gate:

- pull requests that change intake files run only dependency-free offline
  fixtures plus the existing local verifier; external upstream state therefore
  cannot make an unrelated pull request fail;
- scheduled and manually dispatched runs query only the official
  `presenton/presenton:main` GitHub API using an exact resolved SHA;
- the job writes a Markdown step summary and retains Markdown/JSON evidence;
- ordinary upstream movement is a reviewable success, configured
  `contract-risk` exits `2`, and operational/metadata errors exit `1` while
  explicitly leaving `changeDetected` false.

Both workflows use read-only repository permissions. The intake checkout and
runtime actions are immutable digest pins, credentials are not persisted, and
the tool never fetches into a worktree, merges, or cherry-picks. To adopt a new
baseline, first review the intake report, manually port only approved changes,
run the full compatibility/test matrix, and then update the manifest, intake
policy, migration ledger, protected-patch registry, and test dispositions that
were actually affected.

## Test All Applications (`test-all.yml`)

This workflow runs comprehensive tests for all parts of the application:

- **Main FastAPI** - Python tests for the main backend
- **Main Next.js** - Lint, build, and Cypress component tests
- **Docker Build** - Verifies Docker image builds successfully

## Testing Locally

Before pushing, you can test everything locally using the provided script:

```bash
./test-local.sh
```

This script runs the same tests that GitHub Actions will run, so you can catch issues early.

## Manual Testing

If you prefer to test individual components:

### FastAPI Tests
```bash
# Main FastAPI
cd servers/fastapi
export APP_DATA_DIRECTORY=/tmp/app_data
export TEMP_DIRECTORY=/tmp/presenton
export DATABASE_URL=sqlite+aiosqlite:///./test.db
export DISABLE_ANONYMOUS_TRACKING=true
export DISABLE_IMAGE_GENERATION=true
export PYTHONPATH=$(pwd)
pytest tests/ -v
```

### Next.js Tests
```bash
# Main Next.js
cd servers/nextjs
npm run lint
npm run build
```

### Docker Build
```bash
docker build -t presenton:test -f Dockerfile .
docker images | grep presenton:test
```
