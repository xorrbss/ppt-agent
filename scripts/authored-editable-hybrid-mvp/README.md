# Authored editable hybrid MVP gates

This directory is an isolated five-slide, no-skip evidence lane. It does not
modify or inherit the existing eight-slide v3 promotion contract. Every output
directory and sealed result path must be new; a rerun never overwrites evidence.

## Pinned contract

- Profile: `authored-editable-hybrid-ko-en-basic-v1`
- Slides: exactly 5
- Native support: Korean/English text, solid shapes, rectangular image crops,
  and basic tables
- Required raster islands: one chart and one complex-CSS element
- Gate -1E: 15 actual-product capability/PowerPoint cases, no skips
- Gate 0E: 15 fail-closed mutation cases, no skips and no state delta
- Gate 1E: exactly 35 actual-product artifacts, including two PPTX packages
  and 15 independently bound 1280x720 renders

The versioned manifests and schemas are in
`docs/authored-editable-hybrid-mvp-v1-contracts`.

## PowerPoint 16 owned-PID worker

The wrapper snapshots all pre-existing `POWERPNT.EXE` processes and visible
`PPTFrameClass` windows, launches `POWERPNT.EXE /x`, and accepts COM only when
the application HWND resolves to that exact new PID. A ROT object belonging to
a baseline PID is released without opening a presentation. Ambiguous activation
is blocked; cleanup may target only the PID written by this wrapper's generated
worker. Baseline process identity plus visible presentation HWND/title inventory
must be byte-equivalent after the run.

```powershell
$root = 'C:\project\PPT-agent\ppt-agent-authored-bounded-release-v3-20260823'
& "$root\scripts\authored-editable-hybrid-mvp\run-powerpoint-owned-delta.ps1" `
  -Presentation 'C:\evidence\hybrid-input.pptx' `
  -OutputDirectory 'C:\evidence\com-owned-fresh' `
  -PresentationId '<actual-presentation-id>' `
  -PresentationRevision <actual-revision> `
  -HybridExportAttemptId '<actual-hybrid-export-attempt-id>'
```

The authoritative PID receipt is
`powerpoint-owned-process-delta.json`. Keep the sibling desktop validation,
repair observation, process census, saved copy, and first/reopen render folders
as Gate 1E inputs. The wrapper does not stop or restart product servers.

## Gate -1E

Prepare one observation envelope matching
`gate-minus1e-observation-envelope.v1.schema.json`. It must pin the actual
presentation, revision, generation/export attempts, scene and active-manifest
digests; contain all 15 case IDs in manifest order; mark every case
`state=satisfied` and `skipped=false`; and bind every case to at least one real
artifact.

```powershell
node "$root\scripts\authored-editable-hybrid-mvp\run-gate-minus1e.mjs" `
  --manifest-path "$root\docs\authored-editable-hybrid-mvp-v1-contracts\gate-minus1e-case-manifest.v1.json" `
  --observations-path 'C:\evidence\gate-minus1e-observations.json' `
  --output-dir 'C:\evidence\gate-minus1e-result-fresh'
```

## Gate 0E

For each manifest case, provide four JSON artifact bindings: request, actual
response, state immediately before, and state immediately after. The runner
requires the exact HTTP/error pair, `mutationApplied=false`,
`sealedCompletionDelta=0`, `activeBindingChanged=false`, and canonical equality
of the before/after JSON. Thus a mocked status without immutable state evidence
cannot pass.

```powershell
node "$root\scripts\authored-editable-hybrid-mvp\run-gate0e.mjs" `
  --manifest-path "$root\docs\authored-editable-hybrid-mvp-v1-contracts\gate0e-case-manifest.v1.json" `
  --observations-path 'C:\evidence\gate0e-observations.json' `
  --output-dir 'C:\evidence\gate0e-result-fresh'
```

## Gate 1E collect and seal

Create an attempt JSON with all source-map schema pins. Create a bindings JSON
object whose keys are the exact 35 artifact IDs and whose values are absolute
paths or `{ "path", "mediaType" }` objects. The builder resolves files and adds
their byte lengths and SHA-256 digests.

```powershell
node "$root\scripts\authored-editable-hybrid-mvp\build-gate1e-source-map.mjs" `
  --manifest-path "$root\docs\authored-editable-hybrid-mvp-v1-contracts\gate1e-artifact-manifest.v1.json" `
  --attempt-path 'C:\evidence\attempt.json' `
  --bindings-path 'C:\evidence\gate1e-bindings.json' `
  --output-path 'C:\evidence\gate1e-source-map.json'

node "$root\scripts\authored-editable-hybrid-mvp\run-gate1e.mjs" collect `
  --manifest-path "$root\docs\authored-editable-hybrid-mvp-v1-contracts\gate1e-artifact-manifest.v1.json" `
  --profile-path "$root\docs\authored-editable-hybrid-mvp-v1-contracts\capability-profile.v1.json" `
  --artifact-source-map-path 'C:\evidence\gate1e-source-map.json' `
  --output-dir 'C:\evidence\gate1e-collect-fresh'
```

`collect` verifies exact artifact membership and digests, package/render counts,
QID ownership, strict CAS publication, native admission, input/saved OOXML,
first-open/save-copy/reopen COM, repair observation, `/x` owned-PID cleanup,
visual comparisons, attempt sidecar, and the non-promotable completion seal. It
then emits a validation request. For this non-promotable shadow lane, a fresh
`authorityScope=local-shadow` Ed25519 receipt may seal actual-product readiness;
the trust and receipt must both retain `promotionAuthorized=false` and
`deploymentAuthorized=false`. Only a separately operated
`authorityScope=external-independent` validator could supply external promotion
evidence, which this lane neither creates nor claims.

```powershell
node "$root\scripts\authored-editable-hybrid-mvp\run-gate1e.mjs" seal `
  --result-core-path 'C:\evidence\gate1e-collect-fresh\gate1e-result-core.json' `
  --receipt-path 'C:\evidence\local-shadow-validation-receipt.json' `
  --trust-path 'C:\evidence\local-shadow-validator-trust.json' `
  --result-path 'C:\evidence\gate1e-result-set.json'
```

Gate 1E acceptance remains evidence-only: the completion artifact must retain
`promotionReady=false` and `deploymentAuthorized=false`.
