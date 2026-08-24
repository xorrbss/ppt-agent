# Authored bounded Hybrid release v3

Status: server-issued bounded-release readiness display contract. This document
does not authorize a deployment, serializer, or export.

## Endpoint and compatibility

The primary endpoint is:

`GET /api/v1/ppt/presentation/{presentationId}/authored-emission/capability-projection-v3`

It must use `schemaVersion: "workbench-capability-projection/v3"` and a
`releaseProfile` with
`schemaVersion: "authored-bounded-hybrid-release-readiness/v3"`. The
Next.js client accepts only the exact v3 schema and known capability IDs. An
unknown schema, incomplete capability set, profile alias mismatch, malformed
receipt, or claim-pin mismatch is unavailable; the client never repairs or
derives readiness.

Only a v3 `404`, `410`, or `501` may fall back to v2. Only the same three v2
statuses may then fall back to v1. Authentication failures, rate limits, 5xx,
and invalid JSON/schema responses fail closed. v1 retains its three false
readiness literals and v2 retains its existing server-issued semantics.

## v3 readiness display

The UI displays these server-issued `releaseProfile` booleans verbatim:

- `deckEvidenceReady`
- `serializerProfileReady`
- `nativeSubsetReady`
- `releaseCommitReady`
- `promotionReady`
- `deploymentAuthorized`

`nativeSubsetReady` is the primary v3 label. Deprecated `fullNativeParity` and
`independentVnextGeneration` are not primary booleans in v3; their displayed
statuses are both exactly `notApplicable`.

`promotionReady` is not deployment authorization. `deploymentAuthorized` is
an independent server-issued decision and the UI does not promote, combine, or
infer either decision.

## Scope, receipts, and pins

v3 retains the v2 presentation revision, immutable canonical/manifest binding,
scoped capabilities, renderer attestation, kill switches, and claim wire
shape. Its v3-only sets are `deckEvidenceCapabilityIds`,
`serializerProfileCapabilityIds`, `nativeSubsetCapabilityIds`,
`releaseCommitCapabilityIds`, and `allRequiredCapabilityIds`; it does not use
v2 `promotionCapabilityIds`, `requiredCapabilityIds`,
`boundedNativeCapabilityIds`, or `readyReceiptDigests`.

`nativeSubsetCapabilityIds` must be a subset of
`serializerProfileCapabilityIds`. `allRequiredCapabilityIds` must be the exact
ordered canonical union of deck-evidence, serializer-profile, native-subset,
and release-commit sets. Every all-required ID has exactly one capability
entry. The v3 capability vocabulary additionally includes `receipt.table`,
`receipt.imageCrop`, and `ci.requiredChecks`.

Each set has the corresponding ordered receipt pins:
`deckEvidenceReceiptDigests`, `serializerProfileReceiptDigests`,
`nativeSubsetReceiptDigests`, and `releaseCommitReceiptDigests`. The four
readiness booleans are true exactly when their own pins exactly map to the
current capability receipt digests; otherwise they are false. The
`promotionReceiptDigests` must exactly map to all-required capability receipts
and is the promotion pin set. `promotionReady` is true only when those four
readiness values, all-required ready capability states, the promotion pins, and
the absence of an active kill switch agree. The client validates these
relations and never fills an omitted or partial server pin.

The existing `authored-export-authority-claim/v2` wire shape remains valid for
direct export pins. A promotion claim uses `promotionReceiptDigests`; the
compatibility `boundedNative` claim uses `nativeSubsetReceiptDigests`. A claim,
when issued, must be `ready` and exactly match the release profile, profile
definition digest, release projection digest, and the appropriate ordered
receipt list. Unconfigured/non-ready server states can have empty authority
receipts with no projection digest or claim; that does not become a
browser-created readiness decision.

`shadow` or `blocked` receipts cannot grant release promotion authority. They
do not by themselves decide whether a non-promotable Hybrid diagnostic export
is admitted: the server makes and receipts that decision.

## Reachability and trusted evidence

The five v3 readiness booleans are intentionally reachable, but only for the
bounded Windows/PowerPoint profile. Gate 0 is a real HTTP/database test: it
publishes an eight-slide bundle graph, resolves immutable export attempts and
bindings, publishes signed visual and CI evidence artifacts, issues each
receipt through `POST /authored-emission/capability-receipts-v3`, and reads the
v3 projection. The positive path must reach `promotionReady=true`; separate
negative cases prove that synthetic font bytes, stale identity pins, partial
capability sets, and malformed wire datetimes remain rejected.

Native and serializer receipts for one deck are all bound to the Hybrid export
attempt named by the exact visual-evidence binding for the current active
manifest/revision. A receipt from another export, revision, manifest, release
profile digest, evidence policy, or feature-set digest is blocked. The CI
receipt is the only separately commit-scoped artifact. It is accepted only
after an authenticated Bitbucket verifier seals the exact backend commit and
required successful checks; operators publish that seal through
`POST /authored-emission/ci-evidence-artifacts-v3` before issuing
`ci.requiredChecks`.

`receipt.cjk` is reconstructed from actual registered Windows font bytes and
the persisted typography admission/readback evidence. The release validator
does not trust a family label alone. Native chart authority likewise compares
the declared semantic payload with OOXML and PowerPoint readback. Complex CSS
features are satisfied only by an identity-bound native readback or an explicit
raster/backplate outcome with stable reason codes and a degradation receipt.
The z-band receipt requires a fixed-point paint plan; unsafe overlap,
transparency, nesting, or backplate collision closes the native band or demotes
the slide.

PowerPoint qualification uses visible PowerPoint 16 COM observation with
`AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION=true`. It opens, renders,
saves, reopens, and renders the same fresh package while observing repair
dialogs without dismissing them. Object/QID identity, geometry drift above
5pt/10pt, text and line breaks, typefaces, table/chart data, crop, overflow,
and first-open/reopen hashes are immutable attempt evidence. A noninteractive
COM run may remain diagnostic but cannot issue `renderer.attestation`.

## Operations boundary

Operators use the displayed profile, attestation, kill switches, receipt
reason codes, and immutable pins as evidence references only. A deployment
requires the separate server-issued `deploymentAuthorized` decision plus the
normal approved release process. Local UI state, a successful HTTP fetch, a
configured Bitbucket check, or `promotionReady=true` alone is not deployment
evidence.
