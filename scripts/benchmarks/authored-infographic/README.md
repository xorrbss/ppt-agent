# Authored infographic benchmark (50 slides)

`fixture-50.json` is the fixed benchmark for the authored infographic library:
one coherent Korean business deck of 50 slides — a smart-factory proposal for a
precision-parts manufacturer — that exercises **every** archetype in the
vocabulary (`models/authored_infographic_plan.py`) at least twice, plus six
`none` pages (cover, agenda, three section dividers, closing).

It is the 10-slide experiment fixture
(`output/infographic-experiment-20260902/fixture.json`) grown to production
size: same per-slide shape (`index`, `archetype_hint`, `title`, `takeaway`,
`bullets`) plus a `role` so cover/section/closing pages are recognisable, and a
declared `archetype_coverage` list the test asserts against.

**The fixture is fixed.** Two library versions are only comparable if they saw
identical input. Adding or rewording a slide is a new fixture version
(`fixture-51.json`, a new `benchmark` id), never an edit in place.

## Why it lives here

`scripts/benchmarks/` is where this repo already keeps executable benchmark
harnesses (`scripts/benchmarks/authored_bundle_benchmark/`). It is not under
`output/` (experiment evidence, never product input), not under
`servers/fastapi/tests/` (pytest would collect a fixture that needs a live
backend and ten minutes of provider time), and `fixtures`-named directories are
excluded from the file-size ratchet while `scripts/` is not — so the runner is
still held to the 350/500-line limits, which is what we want.

## Metrics the runner computes

From the per-slide quality receipt the backend writes
(`services/authored_slide_quality_receipt.py`, one
`deck-<digest>-slide-quality.json` per deck under
`<app data>/authored-quality-receipts`):

| Metric | Meaning |
|---|---|
| `figureShare` | fraction of slides the deck plan drew as a figure |
| `archetypesPlanned` | which archetypes the plan actually used |
| `archetypesRequestedButNeverPlanned` | a vocabulary entry the plan never reaches — the finding to chase |
| `fallbacksPerSlide` (per archetype) | mean hybrid-export raster fallbacks |
| `slidesOverFallbackBudget` | slides above 2 raster fallbacks — a quality failure |
| `criticScore`, `variantsChosen` | best-of-N and design-critic outcomes, `null` until that stage lands |
| `elapsedMs` | wall time of the generation call |

Human scoring (the blind rating page) is out of scope here; this runner
produces only the automatic half.

## Running it

### Against an isolated local backend

Do not point this at the shared stack on 8000. Follow
`output/infographic-experiment-20260902/A-baseline/README.md`; the short form:

```sh
S="<scratchpad>/bench-appdata"
mkdir -p "$S"
cp app_data/userConfig.json "$S/userConfig.json"
cp app_data/userConfig.json "$S/userConfig.json.bak"   # both files, see that README
```

```sh
cd servers/fastapi
export APP_DATA_DIRECTORY="$S" USER_CONFIG_PATH="$S/userConfig.json"
export DISABLE_AUTH=true MIGRATE_DATABASE_ON_STARTUP=true
export DATABASE_URL="sqlite+aiosqlite:///$S/bench.db"
export CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE=sqlite-local
export PUBLIC_PROVIDER_PRICING_REVISION=... PUBLIC_PROVIDER_PRICING_CATALOG=...
./.venv/Scripts/python.exe server.py --port 8091 --reload false
```

Never `uv run` — it re-syncs the shared venv and collides with another
session's backend (`os error 32`). First boot runs Alembic (~90 s); wait for
`GET /api/v1/auth/status` to answer 200.

The synchronous `/presentation/generate` route the runner posts to is admitted
only on the SQLite local runtime profile; on any other runtime it 410s. That is
deliberate — the async route cannot complete on SQLite (see the baseline
README's heartbeat note).

### The run

```sh
# smoke: two slides, real generation, real metrics
node scripts/benchmarks/authored-infographic/run-benchmark.mjs \
  --base http://127.0.0.1:8091 --limit 2 \
  --receipts-dir "$S/authored-quality-receipts" \
  --out artifacts/infographic-benchmark-smoke

# the full 50-slide benchmark (~10 minutes of provider time)
node scripts/benchmarks/authored-infographic/run-benchmark.mjs \
  --base http://127.0.0.1:8091 \
  --receipts-dir "$S/authored-quality-receipts" \
  --out artifacts/infographic-benchmark-<date>
```

Other flags: `--dry-run` (build the request, touch no backend), `--metrics-only
--presentation-id <uuid>` (recompute from a receipt already on disk),
`--fixture`, `--style`, `--timeout <seconds>`.

`--out` must not be inside `output/` or `app_data/`; `artifacts/` is the
conventional home.

### Tests

```sh
node --test scripts/benchmarks/authored-infographic/run-benchmark.test.mjs
```

These cover the fixture's shape and coverage, the request contract, the receipt
filename's agreement with the backend's hashing, and the metric arithmetic.
They never open a socket.

## Weekly report

The benchmark answers "did this library change help?". The weekly report
answers "what is production doing?" — it joins the same receipts with the
`authored_slide_editor_events` table:

```sh
cd servers/fastapi
APP_DATA_DIRECTORY=<app data> ./.venv/Scripts/python.exe \
  scripts/authored_infographic_weekly_report.py --days 7
```
