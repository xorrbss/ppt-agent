# Authored release readiness v2

Status: implementation authority for the four-stage Authored-first release path.

## 1. Product boundary

- Authored remains the only user-facing design and generation engine.
- Fidelity remains the default quality-preserving export.
- authored-vNext owns semantic identity, typed editing, composition, native-object
  compilation, OOXML export, evidence, and lifecycle control.
- Template V2 and Scene remain internal compiler infrastructure. They are not
  reintroduced as independent user-facing generators.
- Unsupported representation features remain explicit raster islands. Identity,
  ownership, asset, authorization, or digest mismatches fail publication.

Independent generation is not part of this release profile. Its readiness is a
separate capability and remains false in the `authored-windows-powerpoint16`
profile.

## 2. Readiness authority

The v1 projection and its three false literals remain backward compatible. New
readiness is exposed by:

`GET /api/v1/ppt/presentation/{presentationId}/authored-emission/capability-projection-v2`

The v2 response contains:

- an immutable presentation/manifest binding;
- a scoped release profile;
- server-issued capability receipts;
- exact renderer/environment attestation;
- active kill switches;
- when the server has issued ready authority, `releaseProjectionDigest` and an
  `exportAuthorityClaim` with exact profile/projection/receipt pins. The UI
  only displays this server-issued claim and rejects mismatched pins.

For an unconfigured or non-ready projection,
`releaseAuthorityReceiptDigests` is empty and `releaseProjectionDigest` and
`exportAuthorityClaim` are `null` or absent. That is an authoritative
non-promotion state, not a client-side readiness inference or a fallback.

The client never supplies or derives a `ready` state. Only a server authority may
append, supersede, or revoke a receipt. A ready receipt is valid only when its
platform, renderer, serializer version, feature-set digest, policy digest, font
inventory, evidence digest, and expiry match the current target exactly.

`promotionReady` means that the required capability set for one named release
profile is ready. It does not mean full arbitrary HTML/CSS native parity and does
not enable independent generation.

`boundedNativeParityReady` means that every object required by the versioned
feature set and archetype matrix is native, editable, and visually accepted in
the declared platform scope. It never makes a claim about out-of-scope CSS.

## 3. Capability IDs and exact pins

The single source is `contracts/authored_vnext/semantic/release-profile.v2.json`.
The projection preserves `requiredCapabilityIds` as a compatibility alias, but
its authoritative name is `promotionCapabilityIds`; both ordered lists must be
identical. The first Windows PowerPoint 16 profile has these bounded-native IDs:

- `shape.gradient`
- `shape.shadow`
- `layout.transform`
- `layout.flex`
- `layout.grid`
- `shape.borderRadius`
- `shape.opacity`
- `shape.clip`
- `renderer.attestation`
- `evidence.visual`
- `receipt.cjk`
- `receipt.chart`
- `receipt.zBand`
- `receipt.degradation`

`promotionCapabilityIds` is the release-profile subset defined by that JSON;
`boundedNativeCapabilityIds` is the complete bounded-native set above. The
server projects `profileDefinitionDigest`, promotion/bounded-native receipt
lists, and `releaseAuthorityReceiptDigests`. It issues
`releaseProjectionDigest` and an `authored-export-authority-claim/v2` only for
ready authority. When a claim is issued, its profile ID, profile digest,
projection digest, and ordered receipt digests must exactly match their paired
projection pins. A missing pin in a non-ready state remains non-promotable; a
mismatched issued pin is unavailable, never a client fallback or inferred
readiness.

## 4. Truthful visual acceptance

PowerPoint COM success is packaging/readback evidence, not visual parity. A new
hybrid export therefore remains `not-verified` after COM alone. Visual acceptance
requires one atomic evidence bundle binding the exact presentation revision,
operation/generation lineage, manifest, semantic source, renderer, capability
receipts, PPTX, sidecars, and render digests.

A visual acceptance pass additionally requires, for every slide:

- Authored, Fidelity browser, Hybrid browser, PowerPoint first-open, and
  PowerPoint save/reopen render identities;
- the versioned automatic comparison policy to pass;
- title line-break, clipping, overlap, ghosting, boundary sentinel, and
  native/backplate collision checks to pass;
- no critical typography issue;
- a completed reviewer ledger with identity and time.

Missing evidence is `not-verified`; it is never inferred as pass.

## 5. Native scope and fallback

Native implementation is prioritized by user value: text and typography,
tables, cropped images, charts, simple shapes/diagrams, then bounded decorative
features. Arbitrary CSS is not a release prerequisite.

Logical admission, OOXML inspection, and PowerPoint readback use the same QID
and kind authority. Counts that cannot be reconciled block the capability
receipt. Out-of-scope representation features retain stable fallback reason
codes and do not masquerade as editable.

A `shadow` or `blocked` receipt cannot grant release promotion authority. It
does not by itself prohibit a non-promotable Hybrid diagnostic export; the
server remains the final authority for that export admission and its receipt.

## 6. Evidence matrix

Fresh versioned fixtures cover Korean, Chinese, and Japanese text plus card,
process, timeline, comparison, metric, infographic, table, chart, crop image,
diagram, architecture, roadmap, long text, multi-column layout, generic-class
isolation, and right/bottom sentinels.

Evidence cannot be copied between attempts, revisions, manifests, PPTX files, or
renderer environments. The verifier resolves the DB attempt and hashes rather
than trusting a self-declared report.

## 7. Operations

- Renderer production readiness requires an attested external worker. Local
  Windows development remains explicitly unattested.
- Kill switches are scoped, expiring, audited, and affect the next attempt.
- Fidelity remains available when native capabilities are disabled.
- Migration upgrade/downgrade, backup/restore, stale CAS, retry exhaustion,
  worker failure, and legacy rollback are rehearsed with immutable receipts.
- Bitbucket required checks must run successfully; configuration or an
  authenticated Git push alone is not CI evidence.
- Staging, production deployment, merge, or feature enablement remains a
  separate explicit approval.

## 8. Promotion rule

The product may report `promotionReady=true` only when every capability required
by the named release profile has a current ready receipt and the aggregate
calculation itself is sealed. `boundedNativeParityReady` is calculated
separately. `independentGenerationReady` remains false for this Authored-first
release.
