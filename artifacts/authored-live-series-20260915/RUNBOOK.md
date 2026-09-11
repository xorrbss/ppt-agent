# Authored verbatim placeholder-authoring: live validation series runbook

- Design: `docs/authored-verbatim-placeholder-authoring-design-20260911.md` (rev 3), section 4
  ("Rollout") and section 6 (decisions). This runbook operationalizes experiments (a) and (c) from
  that section. Experiment (b) (synthetic token fixtures) is already covered by unit tests
  (`tests/unit/test_authored_admission.py`, `tests/unit/test_authored_repair_loop.py`, the placeholder
  substitution suite) and is out of scope here.
- Precondition: the provider quota reset on 2026-09-15. Nothing in this runbook makes a provider call
  before that date. **This directory's tooling is verified offline in dry-run mode against
  `artifacts/authored-full-product-validation-20260910/run-16`, `run-20`, `run-21`** (see
  `dry-run-verification/`); the live procedure below is not executed until the coordinator confirms the
  quota is available.
- Owner boundary: this runbook, `series.ps1` and `summarize_series.py` own only
  `artifacts/authored-live-series-20260915/`. They do not modify `services/`, do not restart the stack,
  and make no provider calls themselves — `series.ps1` drives the *existing*
  `artifacts/authored-full-product-validation-20260910/run.ps1`, which is what actually calls the
  product API. The only new code outside this directory is a read-only DB helper under
  `servers/fastapi/scripts/` (below), which follows the wrapper pattern of
  `servers/fastapi/scripts/run_authored_checkpoint_repair_probe.ps1`.
- Every knob write, every stack restart, and every run that spends provider quota is a **coordinator**
  action (a human, or an agent explicitly authorized to touch `services/`, the runtime stack, and
  provider spend). This agent prepares the harness; it does not run the live series.

## 1. What is being compared

Four independent switches, all `fastapi.runtime` registry keys (`servers/fastapi/constants/settings_registry.json`,
group `authored.quality`), read through `utils/get_env_programme.py`:

| key | default | values |
| --- | --- | --- |
| `AUTHORED_ADMISSION_GATE_MODE` | `shadow` | `shadow` \| `enforce` |
| `AUTHORED_HTML_NORMALIZATION` | `legacy` | `legacy` \| `tree` |
| `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` | `false` | `false` \| `true` |
| `AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP` | `false` | `false` \| `true` |
| `AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS` | `1` | `0..3`, only read when the loop is on |

The rollout is cumulative and ordered (design §4): each stage keeps every earlier stage's switch at its
validated value and flips exactly one more. `AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP=false` (the current
default) is used as the "repair loop off" control for experiment (c); it does not need its own stage,
because stage 4 already runs with every other switch at its rollout-complete value. This keeps the
series to 6 stages (30 runs) instead of 7.

| stage | label | `GATE_MODE` | `HTML_NORMALIZATION` | `PLACEHOLDER_AUTHORING` | `SINGLE_REPAIR_LOOP` | `REPAIR_LOOP_MODEL_CALLS` | rollout step | experiment |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `gate-shadow` | `shadow` | `legacy` | `false` | `false` | — | 1 (baseline) | (a) |
| 2 | `gate-enforce` | `enforce` | `legacy` | `false` | `false` | — | 1 (cutover) | (a) |
| 3 | `normalization-tree` | `enforce` | `tree` | `false` | `false` | — | 2 | (a)-style digest/receipt parity |
| 4 | `token-mode` | `enforce` | `tree` | `true` | `false` | — | 3; also the repair-loop-**off** control | (c) |
| 5 | `repair-loop-n1` | `enforce` | `tree` | `true` | `true` | `1` | 5, N=1 (design §6 default) | (c) |
| 6 | `repair-loop-n2` | `enforce` | `tree` | `true` | `true` | `2` | 5, N=2 comparison | (c) |

Stage 1 is the current production default — it is run (not skipped) so the shadow-gate parity receipt
(`quality_gate_receipt.admissionShadow`) accumulates a same-input baseline before the cutover in stage 2.
Rollout step 4 (provider-layer overload classification, T4, commit `5590702f7`) is not a separate switch
in this table: it shipped unconditionally (it changes *where* overload classification happens, not a
knob), so there is nothing to A/B here; its effect is visible only as fewer overload-retry rows in
`provider_usage_receipts`, which the stage table already reports.

Fixed input: `run.ps1`'s defaults (`-SourcePath` = the 35-slide / 289-text-block PPTX under
`app_data/workspace_assets/...`, sha256 `7746aaed...`, `source_mode: source_content_restyle`,
`content_policy.text: verbatim`). This is the same input as `repeatability-20260911.md` and every
`run-N` directory under `authored-full-product-validation-20260910/`, which is what makes the
before/after comparison in experiment (c) meaningful. The 43-page/96-image-occurrence stress fixture
from design §3 item 4 is not wired into `run.ps1` and is out of scope for this series; if the coordinator
wants it, pass `-SourcePath`/`-ExpectedSlides`/`-ExpectedSourceBlocks`/`-ExpectedInputSha256` overrides
through `series.ps1` (they pass straight through to `run.ps1` and `audit-full-run.ps1`) as a separate,
later series.

## 2. How the knobs are set (sanctioned path only)

These are DB-backed runtime settings (`docs/runtime-settings-multiworker-sync.md`), not environment
variables and not files. There is exactly one sanctioned write path: the admin settings endpoint
(`servers/fastapi/api/v1/admin/router.py`), which merges a partial JSON body into the singleton
`provider_settings` row under an optimistic-concurrency revision and immediately publishes the committed
snapshot to the receiving process; other processes converge through the existing revision-poll monitor.
Direct DB writes to `provider_settings.config` are not sanctioned — always go through the endpoint so
validation (`utils/runtime_settings.py` coercion, enum/int clamping) and the CAS/audit path run.

Steps, for each stage in order:

1. **Read current config** (redacted, safe to print): `GET {base}/api/v1/admin/provider-settings`
   (`base` = `http://localhost:5000` through the local single-origin proxy, or direct to FastAPI). In
   this fork's local/Electron stack `DISABLE_AUTH=true`, so no admin session cookie is required
   (`require_settings_admin` short-circuits on `is_disable_auth_enabled()`); against an auth-enabled
   deployment, an authenticated superuser session cookie is required. Confirm `CAN_CHANGE_KEYS` is not
   `"false"` for the stack (`_ensure_settings_are_mutable`) — if it is, the endpoint returns 403 and no
   further step in this runbook is possible; escalate rather than working around it.
2. **Write only the keys that change for this stage** as a partial JSON body, e.g. for stage 2:
   `PUT {base}/api/v1/admin/provider-settings` with body `{"AUTHORED_ADMISSION_GATE_MODE": "enforce"}`.
   The endpoint merges (`save_provider_settings_record`, `replace=False` by default) — it does not
   require restating every other key, but restate the full cumulative table row for the stage anyway
   (belt-and-suspenders) rather than relying on merge semantics silently carrying stage 1's values
   forward.
3. **Confirm the response body** reflects the intended values (the response is the redacted committed
   config for these non-secret keys).
4. **Restart the full local stack**: `scripts/start-local-stack.ps1` (coordinator-only; this agent does
   not run it and did not run it while preparing this harness). The multi-worker sync design means a
   restart is not strictly required for the setting to take effect cluster-wide (the poll monitor would
   converge on its own), but this runbook requires it anyway, per explicit instruction, so that:
   - every worker process starts the stage on a freshly loaded settings snapshot rather than a mid-poll
     window,
   - the capture ledger, admission-shadow ledger and any other in-process, session-scoped state
     (`services/authored_admission_session.py`, `services/authored_admission_capture.py`) starts empty
     for the stage instead of carrying rows from the previous stage's shadow parity into this stage's
     count,
   - the worker log file used for `summarize_series.py`'s admission-shadow parity count starts a fresh
     file (`start-local-stack.ps1` timestamps worker logs per process start:
     `app_data/logs/worker-generation-<stamp>.stdout.log`), so a stage's shadow-parity rows are never
     mixed with another stage's.
5. **Re-read** `GET {base}/api/v1/admin/provider-settings` after the restart to confirm the values
   persisted across the restart (proves the write landed in the DB-backed row, not only in the writing
   process's in-memory snapshot).
6. **Record provenance**: save the step-3 and step-5 response bodies as
   `artifacts/authored-live-series-20260915/<stage>/00-provider-settings.json` (coordinator step, not
   scripted here — these are redacted and non-secret, safe to keep in the artifacts tree) and note the
   worker-log filename for that stage's window in `<stage>/00-stage-manifest.json` (see `series.ps1`
   below, which writes the run-facing half of that manifest).

## 3. Runs per stage

**5 runs per stage**, sequential (not concurrent — concurrent runs against the same fixed input would
make per-run provider-usage and capture-count attribution ambiguous, and the design's own repeatability
series, `repeatability-20260911.md`, ran sequentially). `series.ps1` drives this:

```powershell
# from the repo root, after the coordinator has completed section 2 for this stage
pwsh artifacts/authored-live-series-20260915/series.ps1 `
    -Stage "gate-enforce" `
    -RunCount 5 `
    -SeriesRoot "artifacts/authored-live-series-20260915" `
    -StageKnobs @{ AUTHORED_ADMISSION_GATE_MODE = "enforce"; AUTHORED_HTML_NORMALIZATION = "legacy"; AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING = $false; AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP = $false }
```

`-StageKnobs` is recorded into the stage manifest for provenance; `series.ps1` never sets the knobs
itself (see section 2 — that is a coordinator action against the admin endpoint, done *before* invoking
`series.ps1`, followed by a stack restart). `series.ps1` only asserts that what it can observe
(`GET /api/v1/admin/provider-settings`, redacted) matches `-StageKnobs` before starting run 1, and stops
immediately (no provider call) if it does not — this catches "forgot to restart the stack" or "forgot to
write the knob" before spending quota.

## 4. What to check between stages

After each stage's 5 runs and before writing the next stage's knobs:

1. **All 5 runs published.** `series-summary.json` for the stage shows `generationExitCode: 0` and
   `auditVerdict: "passed"` (from `audit-full-run.ps1`, online mode) for all 5. A run that did not
   complete or did not pass the audit gate is investigated before it is allowed to count toward the
   stage's table row — do not average a failed run into the comparison silently.
2. **Receipts are complete for the switches this stage turned on**, not just present:
   - stage ≥ 2 (`enforce`): `quality_gate_receipt.admissionShadow.rolloutProfile` reports
     `gateMode: "enforce"` on every run, and `capture_ownership_receipt` — the deck-level
     `quality_gate_receipt.captureOwnership` — is non-null (it is `None` only when there is no active
     admission session, which would itself mean the gate was not exercised).
   - stage ≥ 4 (`token-mode`): the run's persisted quality receipt v3 (see `summarize_series.py`'s DB
     read) shows placeholder-authored HTML, i.e. `html_repr` recorded as `token`/expanded per the
     checkpoint (design §2.3), not the legacy binder path.
   - stage ≥ 5 (repair loop on): the stage's worker log contains at least one
     `[authored-repair-loop] slides=N admitted=A model_calls=C budget=B` line per run (see §6a below for
     why this is a worker-log line and not a persisted receipt field), and `model_calls` never exceeds
     `REPAIR_LOOP_MODEL_CALLS × slides` for that line.
   A stage where any run is missing the receipt evidence that stage's own switch is supposed to produce
   is a **stop condition** (below) — it means the knob write or restart did not take, not that the
   feature is fine but unmeasured.
3. **Run `summarize_series.py` for the stage** (see section 6) and read the stage's row: publish rate,
   flagged-slide count and its `review_required`/`repair_not_admitted` split, captures per slide,
   provider calls/output tokens per deck, wall time.
4. **Compare against the previous stage's row** (the cross-stage table `summarize_series.py` also
   produces): the design's own success criterion (§4) is that the flagged-slide rate is **not worse**
   than the series before. A stage that makes it worse is not automatically a stop condition (§4's
   framing is a release criterion across the whole rollout, not a per-switch gate) but it is something
   the coordinator must explicitly accept before proceeding — record the decision in
   `<stage>/00-stage-manifest.json` or a `.spec-memory/decisions/` entry, same as other accepted
   trade-offs in this design (N=1 default, etc.).

## 5. Stop conditions

Stop the series — do not write the next stage's knobs, do not spend more quota — on any of:

- **Deck death.** Any run in the current stage where the final status is not `completed`, or is
  `completed` but `data.presentation_id`/the download evidence is missing, or the downloaded PPTX's
  `ppt/slides/slideN.xml` count does not match `ExpectedSlides` (`audit-full-run.ps1`'s own checks
  already encode this — treat any `auditVerdict != "passed"` on the `job.published`,
  `job.slideCount`, `pptx.*` rows as deck death, not merely a "flagged" outcome). A *provider quota
  pause* (`usage_limit_reached`, design §2.4 — a coded pause with a resume path) is not deck death;
  resume the paused run and continue, but note the pause in the stage manifest.
- **Receipt incompleteness**, as in §4 item 2 above — a stage's own switch effect is not visible in the
  evidence it is supposed to produce.
- **Parity mismatch.** For stage 1 and stage 2 specifically (the shadow-vs-gate comparison, experiment
  (a)): any admission-shadow row where `existingVerdict != gateVerdict` that is not already an
  understood, accounted-for difference. The design's target for step 1 is *zero unexplained verdict
  differences over one repeatability series* before deleting the per-path checks; stage 2
  (`enforce`) is exactly that cutover, so a mismatch discovered there means the cutover is not safe yet.
  `summarize_series.py`'s stage table reports the mismatch count and (via
  `scripts/authored_admission_shadow_report.py`, invoked underneath) the per-producer/verdict
  breakdown needed to diagnose it.

On any stop condition: leave the partially-run stage's directory as-is (do not delete), record the stop
reason in `<stage>/00-stage-manifest.json`, and hand off to whoever owns `services/authored_admission*`,
`services/authored_repair_loop*`, or the relevant producer for a fix — this agent's harness does not
patch those files.

## 6. Producing the table

`summarize_series.py` (section "Usage" in its own docstring) reads one stage directory at a time and
prints a stage row plus, given multiple stages, a cross-stage comparison table. It has two modes:

- **Live mode** (default): also reads the optional per-run `provider-usage.json` sidecar (provider
  calls, output tokens; produced by the read-only DB helper below) and the stage's worker log for
  admission-shadow parity and repair-loop call/admit totals (§6a).
- **Dry-run mode** (`--dry-run`): uses only what is already in each run directory's `01`–`07` JSON files
  (no DB, no worker log, no live stack required). Fields that only exist in the DB-persisted receipts or
  worker logs are reported as `"unavailable (dry-run / pre-T2-T6 run)"` rather than guessed. This is the
  mode used to verify the tooling today against `run-16`/`run-20`/`run-21` — see
  `dry-run-verification/`.

```bash
# live, after a stage's 5 runs and (optionally) the provider-usage export below
uv run python artifacts/authored-live-series-20260915/summarize_series.py \
    --stage-dir artifacts/authored-live-series-20260915/gate-shadow \
    --stage-dir artifacts/authored-live-series-20260915/gate-enforce \
    --worker-log app_data/logs/worker-generation-<stamp>.stdout.log \
    --out artifacts/authored-live-series-20260915/stage-table.md

# dry run, today, against historical runs that predate this stage layout
python artifacts/authored-live-series-20260915/summarize_series.py \
    --dry-run \
    --run-dir artifacts/authored-full-product-validation-20260910/run-16 \
    --run-dir artifacts/authored-full-product-validation-20260910/run-20 \
    --run-dir artifacts/authored-full-product-validation-20260910/run-21 \
    --out artifacts/authored-live-series-20260915/dry-run-verification/stage-table.md
```

### 6a. A gap found while building this harness: repair-loop/vision-review evidence is not durable

The design's evidence for `repairLoop.modelCalls`/`.admitted` and `visionReview.slidesReviewed` is
assembled in-process per attempt (`services/authored_quality_receipt_v3_pipeline.py`'s `evidence` dict,
`visualReview` key, built by `services/authored_repair_loop_evidence.py:visual_review_evidence_only`)
and handed to `QualityReceiptV3Ledger.create_or_append`. Traced end to end
(`services/authored_quality_receipt_v2.py`): the ledger's `QualityReceiptV3` dataclass stores only
`evidence_digest` (a SHA-256 of the evidence dict), never the evidence dict itself, and that is exactly
what both the persisted manifest file (`quality_receipt_v3_manifest_path`, the
`local-development` durability profile this stack runs under) and the embedded
`quality_gate_receipt.qualityReceiptV3` in the generation-status API response contain — confirmed
against `run-16/06-final-status.json`, whose embedded `qualityReceiptV3` has `evidenceDigest` but no
`providerCalls`/`visualReview` key. The full evidence dict is real only in the process's memory during
that one generation attempt; the `public` durability profile's `persistence_port.append_quality_receipt_v3`
call is the only path that durably stores it, and this local stack is not running that profile.

This is a real gap, not a harness limitation to route around: **there is currently no durable, offline
way to read `repairLoop.modelCalls/admitted` or `visionReview.slidesReviewed` after a run completes**,
short of adding a persistence path for the local profile or a bench-only evidence sink. Two things
partially compensate and this harness uses them, clearly labeled as such rather than presented as the
receipt field:

- `services/authored_repair_loop.py:328-336` logs
  `[authored-repair-loop] slides=%d admitted=%d model_calls=%d budget=%d` once per generation attempt
  that runs the loop. `summarize_series.py` parses this from the stage's worker log and reports it as a
  **stage-level total** (it does not attempt to attribute one log line to one run inside a shared
  worker-log file by timestamp, which would be a guess this runbook is unwilling to make).
- `visionReview.slidesReviewed` has no equivalent log line. `summarize_series.py` reports it as
  `"unavailable"` for every stage. Do not substitute `pageSummary.reviewRequired` or the flagged-slide
  count for it — those count something else (published-byte defects and substitute pages, not "how many
  slides the vision reviewer looked at").

If the live series needs this number with per-run precision, that is a structural change to
`services/authored_quality_receipt_v3_pipeline.py`/`authored_repair_loop_evidence.py` (durably persist
the evidence dict, or add a dedicated bench sink), not something this read-only harness can add on its
own — flagging this for the design/implementation owner rather than working around it.

### Provider usage (read-only, optional, live mode only)

`provider_usage_receipts` is a DB table with no request/prompt content — only accounting scalars
(`servers/fastapi/models/sql/provider_usage_receipt.py`). Reading it needs the DB credential, which is
never typed into this directory. The sanctioned pattern (`servers/fastapi/scripts/run_authored_checkpoint_repair_probe.ps1`)
is reused as-is: a PowerShell wrapper decrypts the stack's environment in-memory only, exports it to a
child `uv run python` process, and restores the parent environment afterward, printing no env values or
secrets.

```powershell
servers/fastapi/scripts/run_authored_live_series_provider_usage.ps1 `
    -Job <job-guid-from-run's-audit.json .job.id> `
    -Out "artifacts/authored-live-series-20260915/<stage>/run-<n>/provider-usage.json"
```

Run this once per run directory after `audit-full-run.ps1` has produced `audit.json` (which carries
`job.id`). `summarize_series.py` reads `provider-usage.json` when present and reports the field as
unavailable when it is not (e.g. in dry-run mode, or if the coordinator skips this optional step).

## 7. Directory layout this series produces

```
artifacts/authored-live-series-20260915/
  RUNBOOK.md
  series.ps1
  summarize_series.py              # CLI + markdown rendering (facade)
  series_metrics.py                # per-run/per-stage metric extraction
  dry-run-verification/
    stage-table.md                 # produced today, see report
  <stage>/                         # e.g. gate-shadow, gate-enforce, normalization-tree, ...
    00-stage-manifest.json         # -StageKnobs as series.ps1 observed them, start/end timestamps, stop reason if any
    00-provider-settings.json      # coordinator-saved GET responses (redacted), before/after restart
    series-summary.json            # per-run summary array (series.ps1)
    run-1/ .. run-5/                # run.ps1 output (01-07 json + pptx) plus:
      audit.json                    # audit-full-run.ps1 output
      provider-usage.json           # optional, from the DB helper above
  stage-table.md                   # cross-stage comparison, produced by summarize_series.py once all stages are done
```
