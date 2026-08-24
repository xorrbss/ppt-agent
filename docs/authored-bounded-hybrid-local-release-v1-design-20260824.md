# Authored bounded Hybrid local release v1 design

Status: auxiliary raster-qualification design. This document no longer defines the
primary product objective. The primary objective and work order are defined by
`authored-editable-hybrid-mvp-v1-design-20260824.md`: actual Scene/UI/native-PPTX
editing must be completed before Gate sealing. This document may be used only as an
honest image-only safety fallback and does not satisfy editable Hybrid completion. It
does not authorize implementation, schema migration, evidence issuance, commit, push,
merge, deployment, or cutover.

This document is a scope overlay on
`authored-bounded-hybrid-release-v3-remediation-design-20260823.md`. The existing
external Gate 1 contract, `release-profile.v3`, `promotionReady`, and
`deploymentAuthorized` meanings are unchanged.

Where this document's raster-first sequencing conflicts with the editable MVP design,
the editable MVP design controls the current product work order. In particular,
`localReleaseReady`, LG0/LG1 sealing, or raster sidecars must not be implemented as a
substitute for `editableSlides > 0`, native owners, UI edits, and PowerPoint round trip.

## 1. Decision

Local product qualification and external promotion are separate authorities.

```text
local product qualification  -> localReleaseReady
external CI and promotion    -> externalPromotionStatus + promotionReady
deployment or cutover        -> deploymentAuthorized
```

The local track may qualify a Fidelity/raster-only product. It does not claim native
editability. The initial local profile therefore reports:

```text
nativeSubsetStatus       = notApplicable
nativeEditableReady      = false
fullNativeParityStatus   = notApplicable
localReleaseReady        = true only after a sealed LG1 completion
externalPromotionStatus  = not-requested when no remote run was requested
promotionReady           = false until the existing external Gate 1 succeeds
deploymentAuthorized     = false until a separately approved cutover receipt exists
```

`not-requested` is a truthful workflow state. It is never accepted as a ready receipt,
CI seal, promotion authority, or deployment authority.

The corrected Gate -1 remains mandatory before any native subset, native editability,
evidence-v2 schema freeze, or existing external Gate 1 promotion claim. It is not a
precondition for this deliberately raster-only local profile because LG1 admits no
native owner. Any future local profile with `nativeEditableReady=true` must bind a
fresh schema-conforming `feasible` Gate -1 result and use a new profile version.

## 2. Profiles and authority domains

The implementation must add a new profile rather than edit the meaning of
`release-profile.v3`.

| Concern | Local profile | Existing external profile |
| --- | --- | --- |
| Profile ID | `authored-windows-powerpoint-hybrid-local-v1` | `authored-windows-powerpoint-hybrid-v1` |
| Authority domain | `local_product` | `actual_product` |
| Completion type | `localReleaseCompletion/v1` | existing completion v3 |
| Remote CI | excluded | required as `ci.requiredChecks` |
| Success projection | `localReleaseReady=true` | `promotionReady=true` |
| Deployment authority | excluded | excluded; separate cutover decision |

The local profile must have its own canonical profile definition digest, evidence
policy digest, signer purpose, nonce namespace, completion endpoint, persistence row,
and validator registry entry. A local receipt must be rejected by the external
promotion validator, and an external receipt must not be silently reclassified as
local evidence.

## 3. State contract

### 3.1 API projection

The v3 readiness response gains a sibling local projection. Existing fields remain
backward compatible.

```json
{
  "localRelease": {
    "profileId": "authored-windows-powerpoint-hybrid-local-v1",
    "status": "ready",
    "localReleaseReady": true,
    "nativeSubsetStatus": "notApplicable",
    "nativeEditableReady": false,
    "completionId": "...",
    "completionDigest": "sha256:...",
    "evaluatedAt": "...",
    "expiresAt": "...",
    "reasonCode": "local_release_formula_satisfied"
  },
  "externalPromotion": {
    "status": "not-requested",
    "promotionReady": false,
    "reasonCode": "external_promotion_not_requested"
  },
  "deployment": {
    "deploymentAuthorized": false,
    "reasonCode": "deployment_not_authorized"
  }
}
```

Allowed values are:

```text
localRelease.status       = pending | ready | blocked | expired
externalPromotion.status  = not-requested | pending | ready | blocked | expired
```

Truthful invariants:

- `localReleaseReady` is true exactly when `localRelease.status=ready`, a matching
  sealed local completion exists, all local receipts are current, and no local kill
  switch is active.
- `promotionReady` retains the existing external formula and is never derived from
  `localReleaseReady`.
- `externalPromotion.status=not-requested` requires no remote execution ID, CI seal,
  PR, pipeline, or remote status receipt in the local evidence graph.
- `deploymentAuthorized` remains false unless the existing separate deployment
  authority issues an approved receipt.
- `nativeEditableReady=false` is required while the profile remains raster-only.

### 3.2 UI wording

The Workbench displays three independent rows:

```text
로컬 release-ready       준비됨 | 대기 | 차단 | 만료
외부 CI / promotion      요청 안 함 | 진행 중 | 준비됨 | 차단 | 만료
배포 / cutover           승인 안 됨 | 승인됨
```

For the initial raster-only profile, it also displays:

```text
편집 가능 네이티브 범위   해당 없음
표현 방식                 Fidelity/raster-only
```

The UI must not summarize these rows as a single `promotionReady=false` banner and
must not describe raster-only output as editable Hybrid output.

## 4. Local capability set

The local profile excludes `ci.requiredChecks` and adds a local source identity
capability. The proposed exact required capability set is:

```text
evidence.visual
layout.flex
layout.grid
layout.transform
receipt.degradation
renderer.attestation
shape.borderRadius
shape.clip
shape.gradient
shape.opacity
shape.shadow
local.sourceIdentity
local.powerPointReadback
local.artifactIntegrity
```

`local.sourceIdentity` pins:

- repository root identity
- release-candidate commit SHA
- source-tree census digest
- dirty-path inventory digest
- profile definition digest
- evidence policy digest
- executable and dependency inventory digests

A dirty worktree is permitted because existing user changes must be preserved, but it
is never hidden. Every included and excluded dirty path is listed and digest-bound.
Any source census change after issuance invalidates local readiness.

`local.powerPointReadback` requires PowerPoint 16 first-open, save-copy, reopen,
render, and OOXML/COM readback evidence. COM success and visual parity are separate
fields and both are required.

`local.artifactIntegrity` binds the exact immutable artifact inventory, sizes, media
types, version IDs, digests, graph root, and active manifest at seal time.

## 5. Completion model

The current pending completion and all earlier attempts remain immutable historical
records. They must not be sealed, edited, or reused for the local track.

The implementation adds a separate local completion resource:

```text
POST /authored-emission/local-release-completions-v1
GET  /authored-emission/local-release-completions-v1/{completionId}
POST /authored-emission/local-release-completions-v1/{completionId}/seal
```

State transition:

```text
pending, casVersion=0 -> sealed, casVersion=1
```

Required behavior:

- create is idempotent only for the same graph root and exact local capability set;
- the seal request requires `expectedCasVersion=0`;
- a replayed signer nonce returns conflict and changes no state;
- a stale CAS returns conflict and changes no state;
- a different second seal returns conflict and preserves the first sealed record;
- a byte-identical retry may read back the existing record but cannot issue a new
  authority event;
- active manifest, presentation revision, export attempt, artifact versions, source
  census, receipt set, and policy digest are revalidated inside the seal transaction;
- local completion is never accepted by the external completion matcher.

## 6. LG0 structural gate

LG0 is a new versioned local contract. It does not mutate the existing Gate 0 manifest.
It retains 34 cases: one positive case and 33 fail-closed mutations.

The positive case must prove the exact tuple:

```text
localReleaseReady=true
externalPromotionStatus=not-requested
promotionReady=false
deploymentAuthorized=false
nativeEditableReady=false
releaseReportStatus=local-verified
productionAuthorityGranted=false
```

The mutation registry keeps the existing identity, policy, signer, nonce, tenant,
owner, revision, manifest, artifact, environment, worker, capability-set, active-state,
and CAS mutations. References to the external release profile are replaced by local
profile identity. The second-seal case asserts that the original sealed completion
remains byte-identical and no replacement seal is persisted.

Success requires `34/34`, zero skips, zero missing/duplicate/unexpected IDs, a signed
semantic-validation receipt, and no externally valid promotion receipt.

## 7. LG1 local product gate

LG1 is a new actual local-product attempt. Existing attempt bytes and sidecars are
never edited or mixed into it.

The existing eight-slide fixture, 384-cell manual ledger, and 70-artifact inventory
shape are retained. The 37 phase count is retained with these explicit substitutions:

```text
g1.authority.ci-current-commit
  -> lg1.authority.local-source-identity

g1.projection.promotion-ready
  -> lg1.projection.local-release-ready

g1.projection.deployment-not-authorized
  -> lg1.projection.external-and-deployment-boundaries
```

The artifact inventory replaces exactly one artifact:

```text
ci-seal-json
  -> local-source-identity-attestation-json
```

Therefore LG1 still requires:

- phases `37/37`;
- artifacts `70/70` with exact set equality;
- manual ledger `384/384`;
- one fresh manifest, policy digest, execution nonce, operation, generation attempt,
  Fidelity export, Hybrid export, evidence graph, and local completion;
- immutable input/saved PPTX, browser PNG, first-open/reopen COM PNG, OOXML readback,
  COM snapshot, independent metrics, and sidecar bytes;
- `localReleaseReady=true`, `externalPromotionStatus=not-requested`,
  `promotionReady=false`, and `deploymentAuthorized=false` at completion.

The retained raster-only scope means native counts may be zero only when every slide
has a valid degradation receipt and visual parity passes. `nativeAdmissionReceipt`
remains absent/N/A and `nativeEditableReady` remains false.

## 8. Sidecar and visual authority

Old sidecars are immutable. A new attempt emits new sidecars bound to its export
attempt ID and graph root.

The authoritative quality sidecar must record independently:

```text
packageReadbackStatus
comFirstOpenStatus
comSaveCopyStatus
comReopenStatus
renderHashStabilityStatus
visualParityStatus
repairDialogStatus
unexpectedPowerPointSurvivorCount
nativeEditableReady
rasterFallbackSlideCount
```

`COM passed` cannot imply `visualParity passed`. A stale sidecar, a sidecar bound to a
different attempt, or a sidecar produced before final PowerPoint QA blocks LG1.

PowerPoint cleanup uses process ownership deltas, not a global `POWERPNT=0` assertion:

- capture the pre-run PowerPoint census;
- record PIDs started or adopted by the qualification worker;
- close only owned/adopted processes;
- require `unexpectedPowerPointSurvivorCount=0` after the run;
- never terminate an unrelated user PowerPoint process.

## 9. Commit and evidence ordering

Evidence must bind the code it evaluates. The required order is:

1. preserve and inventory unrelated dirty paths;
2. finish the authorized local-profile code and policy changes;
3. perform focused review and create the release-candidate local commit;
4. run LG0 and LG1 against that exact commit;
5. store evidence in the immutable artifact store or ignored evidence directory;
6. issue a handoff that references the release-candidate commit and evidence root.

No code or policy commit may be added after LG1 and still reuse its readiness claim.
Documentation-only handoff material should remain outside the evaluated source census
or be prepared before the release-candidate commit.

## 10. Final local verification contract

Local release-ready requires all of the following:

- LG0 `34/34` accepted;
- LG1 `37/37`, artifacts `70/70`, ledger `384/384` accepted;
- actual UI load and raster-only Fidelity/Hybrid export from one active revision;
- PowerPoint 16 first-open, save-copy, reopen, render, and readback passed;
- independent visual parity passed;
- no repair dialog;
- zero unexpected owned/adopted PowerPoint survivor processes;
- completion sealed with CAS `0 -> 1`;
- replay, stale CAS, and different second seal fail closed;
- DB integrity and listener ownership checked without changing unrelated services;
- dirty-path census unchanged from the local source identity receipt;
- an independent audit reports P0=0 and P1=0;
- no remote pipeline, PR, push, merge, deployment, or cutover action occurred.

## 11. Non-goals and truthful verdict

This local design does not complete UI-native editing. It qualifies only the current
Fidelity/raster product path. The release verdict must use the full label:

```text
Local Fidelity/raster release-ready
External promotion not requested
Native editability not available
Deployment not authorized
```

The words `Hybrid complete`, `editable Hybrid`, `externally promoted`, or `deployed`
must not be inferred from local readiness.

## 12. Deferred auxiliary implementation slices

This section is sequencing only and is deferred until the editable Hybrid MVP product
path is complete or an explicit owner decision chooses raster-only fallback as the
product outcome. It must not run ahead of the primary product work merely because its
Gate contracts are easier to close.

1. Add local profile/schema models and the split readiness projection.
2. Add the isolated local completion store, endpoints, nonce purpose, and CAS logic.
3. Add LG0 contracts/runner and preserve the 34-case fail-closed matrix.
4. Add LG1 contracts/runner with the three phase substitutions and one artifact
   substitution while preserving 37/70/384 exactness.
5. Generate attempt-bound sidecars and ownership-aware PowerPoint cleanup evidence.
6. Update Workbench wording and API consumers without changing existing v3 fields.
7. Execute focused tests, create the release-candidate commit, then generate fresh
   LG0/LG1 evidence and obtain an independent P0/P1 audit.
