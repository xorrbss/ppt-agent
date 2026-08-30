# Public service release evidence matrix

This contract separates an execution plan from release evidence. Generating a plan, running a unit test, or exercising a fixture never satisfies a release gate.

## Required matrix

The immutable plan contains 32 performance cells:

- cache state: `cold`, `warm`
- slide count: `5`, `10`, `20`, `30`
- concurrency: `C1`, `C2`, `C5`, `C10`

Every cell must contain at least `concurrency × repetitions` actual operations. Every operation must be bound to the same release candidate and source commit and must retain API, worker, provider usage, render, exporter, font, DB terminal state, artifact, phase timing, retry/provider usage, HTTP 303 download, HTTP 206 range download, and immutable evidence-bundle identities. API IDs, worker IDs, and bundle digests must be unique per operation, including fault recovery and soak operations, so one successful run cannot be replayed across cells. All attempted operations must reach the succeeded DB terminal state. A claimed success count without those operation-level bindings fails closed.

The separate fault matrix requires one actual observation each for worker termination plus checkpoint resume, database unavailability plus outbox recovery, S3-compatible object-store unavailability plus artifact-publication recovery, and provider failure plus bounded retry. Unit fault injection and planned steps are not accepted. The observation must prove fail-closed behavior, bounded recovery, terminal-state convergence, zero duplicate publication, zero accepted stale writes, and preservation of failed-run phase timings and retry/provider usage.

The soak gate requires at least 86,400 seconds of observed wall-clock time, at least 1,440 samples, actual immutable operation evidence, zero queue starvation, enforced resource limits, resource recovery within baseline, and terminal-state convergence. A short run extrapolated to 24 hours fails.

## Commands

Create a not-executed plan for an already frozen RC:

```powershell
node scripts/public-service-release-matrix.mjs plan `
  --release-candidate sha256:<source-manifest-digest> `
  --source-commit <40-character-git-commit> `
  --repetitions 1 `
  --output artifacts/public-service-release/matrix-plan.json
```

Audit a collected immutable product-path evidence bundle:

```powershell
node scripts/public-service-release-matrix.mjs audit `
  --input artifacts/public-service-release/matrix-evidence.json `
  --output artifacts/public-service-release/matrix-audit.json
```

The release gate also requires detached Ed25519 attestations for the exact bytes of the source manifest and every functional, security, render, matrix, and soak report. The trusted public key is supplied by the operator outside the repository; a key embedded in the evidence bundle is never trusted. Without both `--collector-attestations` and `--trusted-collector-public-key`, `trustedCollector` fails closed and `promotionReady` remains false.

The collector signs the report's exact-byte `sha256:` digest, not a self-declared status field. The gate derives the public-key ID from the configured Ed25519 SPKI bytes and rejects missing reports, digest substitution, signatures from another key, and unsigned evidence. The detached document contract is `contracts/public-service-release-evidence/trusted-collector-attestations.schema.json`.

The evidence input schema is `public-service-release-matrix-evidence/v1`. Programmatic gate integration uses the named export `validateReleaseMatrixEvidence(report)`, which returns `pass`, `status`, `releaseReadiness`, `deploymentAuthorized`, and individual `checks`.

`releaseReadiness` is derived as `ready` only when every matrix, fault, soak, same-RC, provenance, and immutable-digest check passes. The validator always returns `deploymentAuthorized: false`; deployment requires a separate human/operational approval and cannot be granted by this evidence bundle.

## Truth boundaries

- Accepted evidence class: `actual_product_path` only, matching the immutable release-evidence bundle contract.
- Rejected as release evidence: `fixture`, `test`, `synthetic`, `planned`, package smoke, dispatch state, and an in-progress job.
- The plan starts with `executionState: not-executed`, `releaseReadiness: not-assessed`, and `deploymentAuthorized: false`.
- The unit test validates contract logic only. It is not a provider, API/worker, DB, S3, PowerPoint, LibreOffice, load, fault, or soak result.
- The harness does not start, restart, stop, or alter any service and does not execute the 32-cell matrix or 24-hour soak by itself.
