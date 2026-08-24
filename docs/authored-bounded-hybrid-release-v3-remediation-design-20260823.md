# Authored bounded Hybrid release v3 remediation design

Status: design-only revision 4, independently re-reviewed after the COM probe. The
next-execution contracts are complete for review, but attempt 03 is
`nonconforming-post-hoc-analysis` and therefore `not-verified`, not a sealed Gate -1
result. Repair-dialog witnessing, required package/QID/chart/`p:bg` collectors,
uppercase-safe carriers, result vocabulary, integer-only canonicalization, and clean
process evidence must all be corrected in a fresh 18-case rerun. This document does
not authorize implementation, schema migration, product export, deployment, merge,
promotion, or schema freeze.

Local-only qualification is now specified separately by
`authored-bounded-hybrid-local-release-v1-design-20260824.md`. That overlay adds
`localReleaseReady` without changing this document's external Gate 1,
`promotionReady`, or `deploymentAuthorized` semantics. A local completion must use a
new profile, authority domain, attempt, evidence graph, and completion record; it may
not seal or reinterpret an existing pending external completion.

The immediate product priority is instead governed by
`authored-editable-hybrid-mvp-v1-design-20260824.md`. It restricts native admission to
Korean/English text, basic solid shapes, ordinary images/rectangular crop, and basic
tables; completes the actual Scene -> Workbench edit -> native PPTX -> PowerPoint
round trip before Gate sealing; and leaves unsupported content as bounded raster
islands. For the MVP shadow path, that document supersedes this document's section
14/15 implementation sequencing: the product path may be built and corrected in
non-authoritative shadow mode before Gate -1E, but no native-ready receipt may issue
until Gate -1E is `feasible`. This does not weaken this document's broader full Gate -1,
external Gate 1, promotion, or deployment requirements.

Normative companion contracts are under
`docs/authored-bounded-hybrid-release-v3-contracts/`:

- `case-manifest-envelope.v1.schema.json`
- `gate-minus1-case-manifest.v1.json`
- `gate-minus1-result-set.v1.schema.json`
- `gate-minus1-semantic-validator-contract.v1.json`
- `gate-minus1-structured-observation-payloads.v1.schema.json`
- `gate-minus1-execution-seal.v1.schema.json`
- `semantic-validation-receipt-ref.v1.schema.json`
- `gate-minus1-attempt-03-observation-envelope.v1.json`
- `gate0-case-manifest.v1.json`
- `gate0-case-manifest.v1.schema.json`
- `gate0-result-set.v1.schema.json`
- `gate0-semantic-validator-contract.v1.json`
- `gate1-phase-manifest.v1.json`
- `gate1-phase-manifest.v1.schema.json`
- `gate1-result-set.v1.schema.json`
- `gate1-semantic-validator-contract.v1.json`
- `canonical-json-profile.v2.json`
- `evidence-policy.v2.json`

Where this remediation conflicts with the earlier bounded v3 design, this revision
is the design authority. It does not supersede product code or the existing
`release-profile.v3` artifact. The retained profile requires `ja`, `ko`, and `zh`, so
the former Korean-only `ko-kr-common-han-v1` proposal is removed. The replacement
`ja-ko-zh-bounded-fixture-v1` pins exact fixture code points and locales. Japanese and
Chinese runs are explicitly Fidelity/raster-required until a later sealed multi-host
face inventory proves those code points; no general pan-CJK claim exists.

## 1. Retained decision and scope

The approved bounded v3 goal is unchanged.

- `deckEvidenceReady`, `serializerProfileReady`, `nativeSubsetReady`,
  `releaseCommitReady`, and `promotionReady` are reachable readiness values.
- `fullNativeParity` and `independentVnextGeneration` remain `notApplicable`.
- `promotionReady` represents bounded evidence readiness, not deployment authority.
- `deploymentAuthorized` remains a separate server decision and stays false unless a
  later, separately approved cutover issues a short-lived deployment receipt.
- Unsupported representation features may use truthful raster islands. Identity,
  ownership, tenant/owner scope, binding, or artifact-integrity failures cannot be
  degraded and must block publication authority.

This remediation retains `release-profile.v3`, the existing capability IDs, v1/v2
read/export compatibility, and the current server promotion formula. It versions only
the internal evidence and policy contracts that are currently insufficient.

## 2. Authority domains and canonical identity

Structural test evidence and actual product evidence are different authority domains.

```text
structural_test  -> Gate 0 state-machine reachability only
actual_product   -> Gate 1 product evidence eligible for bounded promotion
```

The canonical evidence identity contains:

```text
authorityDomain
evidenceClass
evidenceSchemaVersion
evidencePolicyId
evidencePolicyDigest
issuerId
signerKeyId
issuedAt
expiresAt
nonce
tenantId
ownerId
presentationId
presentationRevision
operationId
generationAttemptId
manifestDigest
exportAttemptId
bindingDigest
rendererSourceDigest
inputPptxDigest
savedCopyPptxDigest
reopenInputPptxDigest
releaseProfileId
releaseProfileDigest
featureSetDigest
environmentAttestationDigest
workerAttestationDigest
```

Production validators accept only `authorityDomain=actual_product`, the reviewed
production evidence policy, and an allowed active signer key. Gate 0 retains the same
strict v3 release-profile ID so it exercises the real parser, but uses a separate
database/tenant, environment ID, issuer, and signing-key namespace. A structural
issuer is never accepted by a production validator even if its payload is otherwise
valid.

Signer policies bind issuer class, tenant, environment, key purpose, key ID,
algorithm, rotation epoch, issuance/expiry, nonce, subject digests, and canonical
payload. Structural keys have no permission to issue actual-product, deployment,
promotion, or native-subset claims. The backend transactionally enforces unique
`(issuerId, signerKeyId, nonce)` and one active seal per subject/policy. It rejects
revoked, expired, future-skewed, unknown-key, or already-consumed authority. Secrets
are provided through the reviewed key-management interface, not copied into general
process environments or seed subprocesses.

The v2 evidence policy ID and digest are part of the feature-set and receipt scope.
Existing receipts created under an older evidence policy cannot satisfy new v2
readiness. Cutover expires or revokes old active receipts; it does not reinterpret or
backfill them.

`SourceTreeCensusV1` binds repository ID/root, HEAD commit, index tree digest, staged,
tracked-modified, untracked, and ignored-allowlist path digests, capture time, tool
version, and a final `censusDigest`. Gate 0 and Gate 1 manifests pin the opening census;
result sealing captures it again and any source-path change blocks the gate. Gate -1 is
a non-product design probe: it pins its probe script/input/output artifact digests and
records the repository census only as a preservation audit, so the authorized design
document edits do not invalidate the COM observations. Generated caches are included
only through a reviewed ignore-policy digest; they may not hide tracked source changes.
Gate 1 additionally requires the census HEAD to equal the CI/backend build commit.

## 3. Canonical serialization and digest DAG

All TypeScript and Python producers use the exact
`presenton-canonical-json/v2` profile and its positive/rejection vectors. The
versioned profile fixes normalized UTF-8 key ordering, newline normalization,
registered array sort keys, typed integer domains, digest self-exclusion paths, and
expected bytes/digests. Rules are:

- UTF-8, Unicode NFC, and versioned newline normalization
- sorted object keys and explicit array sort keys
- absent and `null` are distinct
- lowercase UUID strings
- UTC RFC3339 timestamps ending in `Z`
- lowercase `sha256:<64 hex>` digests
- geometry as EMU integers
- font size as 1/100-point integers
- opacity as 1/100000 integers
- chart numbers as normalized big-endian IEEE-754 binary64 hex strings
- `NaN`, infinity, negative zero, implicit defaults, normalized duplicate keys,
  unregistered semantic-array sorting, and locale-formatted numbers are forbidden
- every authoritative manifest/result/policy schema declares its exact included path
  and exact self-excluded digest path; undeclared exclusions are rejected

There is no locale-sensitive comparison. Object keys are ordered by normalized UTF-8
bytes. Arrays preserve producer order unless their JSON path is in the profile's sort
registry. A digest producer must name the single self-excluded JSON path; similarly
named or nested digest fields remain included. Existing simpler canonicalizers are
not evidence-v2 authority until separately authorized implementation makes them pass
all vectors.

The digest graph is strictly acyclic. In particular, a manifest never references its
execution result. A result references the already sealed manifest and excludes only
its own `resultSetDigest`; this is the required manifest-to-result edge:

```text
sourceIdentityDigest
  -> observationReceiptDigest
  -> preSerializationAdmissionDigest
  -> paintPlanCoreDigest
  -> degradationReceiptDigest
  -> hybridPlanDigest
  -> inputPptxDigest
       -> packageAssemblyReceiptDigest
       -> inputOoxmlReadbackDigest
       -> comFirstOpenDigest
            -> savedCopyPptxDigest
                 -> savedOoxmlReadbackDigest
                 -> comReopenDigest
  -> postComTypographyQualificationDigest
  -> qaDecisionDigest
  -> evidenceGraphRootDigest
  -> capabilityReceiptDigests
  -> release projection and claim pins
```

`PaintPlanCoreV2` contains the final selection, physical paint graph, demotions,
raster-plane artifact, and closure result, but never contains a degradation receipt
digest. `HybridRasterDegradationReceiptV2` references `paintPlanCoreDigest`.
`HybridPlanV2` then references both digests. This removes the previous circular
dependency.

The arrow from `hybridPlanDigest` to `inputPptxDigest` denotes generation order, not a
claim that PPTX bytes reference their own digest. `PackageAssemblyReceiptV2` is the
binding node: it references `hybridPlanDigest`, the exact `inputPptxDigest`, and a
readback of the embedded plan marker. Evidence authority uses this assembly receipt,
not an inferred relationship between two standalone digests.

The package-level marker carrier is PowerPoint `Presentation.Tags`, with the exact
keys `PRESENTON_HYBRID_PLAN_DIGEST_V2`, `PRESENTON_EXPORT_ATTEMPT_ID_V2`, and
`PRESENTON_EVIDENCE_POLICY_DIGEST_V2`. Tag values never carry raw lowercase canonical
values. They use the uppercase-safe carrier grammar `D2_<64 uppercase hex>` for a
SHA-256 digest and `U2_<32 uppercase hex>` for a UUID. The validator decodes those
forms and compares the decoded value with the canonical lowercase identity. This
avoids relying on build-specific tag-value case behavior.

Input/saved OOXML and first-open/reopen COM must reconstruct the same unique semantic
tag map. OOXML verification follows the relationship graph to the user-defined tag
part; raw `rId`, part path, XML order, and tag-part byte digest are never authority.
Missing, duplicate, orphaned, truncated, undecodable, or mismatched tags fail with
`package-plan-marker-missing`, `package-plan-marker-duplicate`, or
`package-plan-marker-mismatch`. The marker never contains `inputPptxDigest`, avoiding
a self-reference; `PackageAssemblyReceiptV2` binds decoded marker values to the final
package digest.

Attempt 03 on Office 16 build 20326 reopened raw lowercase sentinel values, but it did
not exercise `D2_`, `U2_`, `Q2_`, `K2_`, `R2_`, maximum encoded QID length, or the
relationship-graph/duplicate-orphan collector. Uppercase-safe encoding remains the
decided next-execution grammar, but package marker and QID carriers are currently
`not-verified` and cannot authorize native output. The acyclic retrospective reference
manifest digest is
`sha256:772e2b5930ef73b2311e40d8675f607b9d32fdb00e3f038552393c07d7f6dd5f`.

`preSerializationAdmissionDigest` records glyph/font/representation selection before
assembly. `postComTypographyQualificationDigest` records the later run, face, line,
overflow, and reopen decision. Only the post-COM qualification can authorize the CJK
release receipt.

`qaDecisionDigest` references the exact input and saved package identities, both OOXML
readbacks, first-open/reopen COM observations, visual artifacts, policy, and decision.
`evidenceGraphRootDigest` references only already-computed immutable digests. A quality
sidecar may point to this root but is never recursively included in it.

## 4. Qualification state machine and atomic publication

PowerPoint qualification is asynchronous and never holds an export DB transaction
open. The append-only state machine is:

```text
captured
  -> planned
  -> assembled
  -> inputOoxmlVerified
  -> firstOpenObserved
  -> savedCopyVerified
  -> reopenObserved
  -> comVerified
  -> sealed | rejected
```

Rules:

- An ordinary export attempt may finish with an immutable PPTX before qualification.
  It remains non-promotable until a separate qualification job reaches `sealed`.
- Each transition uses a lease owner, heartbeat, expected-state CAS, attempt identity,
  and monotonic transition sequence.
- Artifacts are first written to a staging namespace, read back, and rehashed. They are
  then conditionally put into a content-addressed durable namespace and re-fetched by
  digest before any DB binding.
- Durable storage must be write-once or versioned. Every reference binds object-store
  version ID, digest, byte length, media type, encryption key reference, and storage
  generation. A mutable path without a pinned immutable version is not durable
  evidence.
- A short DB transaction verifies durable artifact references and inserts the immutable
  `evidenceGraphRoot` binding with unique
  `(tenantId, exportAttemptId, evidencePolicyDigest, authorityDomain)`.
- Identical retries are idempotent. A different digest for the same unique identity is
  a conflict and cannot replace the sealed row.
- Receipt issuers read only a sealed actual-product evidence graph root.
- Draft or rejected sidecars are diagnostic and never authoritative.
- Artifact state is `staged -> durable -> bound`. Crash recovery may resume only from
  a digest-verified durable object. Receipt issuance re-fetches and rehashes required
  durable objects.
- Unbound staged or durable artifacts are garbage-collected only after a policy-defined
  grace period. Artifacts referenced by an active or retained receipt cannot be
  collected.
- Failure after assembly never modifies the prior draft into success; it records a new
  terminal rejection with a stable failure code.
- The completion-seal transaction revalidates active manifest, presentation revision,
  source-tree census, evidence policy, release-profile and feature-set digests,
  kill-switch generation, worker/environment attestations, and every durable object
  version. Any change yields a conflict and no seal.

`EvidenceBundleCompletionV3` contains the active manifest/revision, evidence graph
root, exact required capability set, and final receipt digest set. Only the internal
readiness authority may issue it. A client cannot submit `complete` or `sealed` state.
Its single `pending -> sealed` CAS is signed in the same actual-product authority
domain; an identical retry is idempotent and a different second seal conflicts.
Individual receipt rows may be persisted incrementally, but the projection treats
them as non-authoritative for promotion until the completion record is sealed and
exactly matches `allRequiredCapabilityIds`.

The effective signer trust tuple is
`(authorityDomain, issuerId, signerKeyId, keyPurpose, environmentId, rotationEpoch)`.
It must match one active non-revoked trust-bundle entry. `authorityDomain` is present
in projections and claims; production validators and clients reject
`structural_test` rather than merely hiding it.

## 5. CJK typography evidence v2

### 5.1 Shared font profile and multi-host observation

Python admission and the Next serializer consume one versioned registry definition.
The initial bounded inventory is:

- Malgun Gothic Regular and Bold
- Batang Regular
- BatangChe Regular
- Consolas Regular and Bold

The attempt-03 worker host recorded all six faces under `C:\Windows\Fonts`. The
versioned local inventory digest is
`sha256:c72bc5b2303f2f064031d23d7d08a59b5f1118cb48682ed916134e3273b22ce5`;
each observed face had `fsType=8`, matching family/subfamily and an independently
derived container, extracted-SFNT, and cmap digest. This is one-host feasibility
candidate observation only. Because attempt 03 is not schema-conforming, it does not
issue a Gate -1 receipt and does not satisfy the three-host product equality
requirement.

Each face definition includes family, subfamily, weight, italic, source filename, TTC
collection index, required scripts, and license policy. Per-user fonts are outside the
bounded profile. Every participant resolves the actual system font root and rejects a
path outside the reviewed Windows font directory or an unexpected reparse target.

The Next serializer, PowerPoint worker, and FastAPI authority each consume the same
`FontFaceDefinitionRegistryV2` bytes and independently produce an
`ObservedFontInventoryReceiptV2` from actual system bytes. A ready CJK receipt requires
exact equality of the registry digest and all three runtime inventory digests. An
attempt may not combine inventories captured at different times or builds. Each face
reconstructs:

- source container SHA-256 and byte length
- TTC collection index and extracted SFNT face digest when applicable
- name-table family and subfamily
- cmap coverage digest
- OS/2 `fsType`
- editable/subsetting eligibility

Only `fsType & 0x000F` equal to `0` or `0x0008` is eligible. Contradictory or restricted
classes fail closed. Cache keys do not rely only on path, size, or timestamp; release
qualification rehashes bytes. A Windows/font update creates a new inventory digest and
requires an explicit release-baseline review. Runtime drift during receipt TTL makes
the projection stale and non-ready.

Attempt-specific package evidence is separate. `AttemptFontEmbeddingReceiptV2`
references the input and saved PPTX identities and records each embedded/obfuscated
font relationship, source inventory face, collection index, embedded part digest, and
saved-copy part digest. Embedded parts never contribute to the environment inventory
digest. The CJK validator compares the three host inventory receipts, then separately
validates the attempt embedding receipt against both PPTX packages.

Inventory eligibility never implies successful attempt embedding, and successful
embedding never proves host inventory equality. A pre-admission missing or
non-embeddable face deterministically rasterizes its whole semantic text owner. A
post-assembly inventory, embedded-part, or package-digest mismatch is `blocked`; it is
never converted into raster success.

### 5.2 Text span mapping

The serializer emits `NativeTypographySpanMapV2` before PPTX creation. Its canonical
coordinate system is zero-based UTF-16 code units after NFC and policy newline
normalization. Each paragraph and run records original and emitted ranges, text
digests, paragraph terminators, soft breaks, whitespace-only runs, and the expected
OOXML `a:r`/`a:br` mapping.

OOXML readback validates the emitted text stream and run mapping before COM is used.
COM indices are converted from one-based `TextRange2.Characters` coordinates into the
same canonical UTF-16 ranges. Surrogate pairs, combining sequences, `\n`, PowerPoint
`\r`, soft break `\v`, empty runs, repeated text, run merge/split, and paragraph marks
have explicit golden cases. `MsoTriStateMixed`, unknown, or inaccessible formatting is
a failed readback, never coerced to Boolean.

On Office 16 build 20326, `A🚀B` has COM `Length=4`. Both
`Characters(2,1)` and `Characters(3,1)` returned the same range
`Start=2, Length=2`. The converter therefore reads raw UTF-16, records the COM
`Start/Length`, and collapses duplicate surrogate-expanded ranges by that pair before
converting to zero-based spans. It never assumes one `Characters(i,1)` result per code
unit. CRLF source normalized to one COM `\r`; `a:br` remained COM `\v`. Zero-length run
identity is not durable in COM and is excluded as a semantic carrier, although the
ordered text/whitespace stream remains exact.

`NativeTypographyRunReadbackV2` separates observations from derived provenance:

- COM-observed text digest, Latin/East Asian family, bold/italic state, size, and range
- OOXML-observed run properties and embedded-font relationship
- registry-derived source/face/embedded-part digests
- combined exact-face decision and reason code

COM is not claimed to observe font bytes. Exact face authority is the conjunction of
the registry/embedded-part binding, OOXML run properties, and COM family/style
observation.

### 5.3 Line geometry and overflow

The bounded native-text profile permits only supported horizontal text frames with
reviewed autofit, anchoring, rotation, and writing-mode settings. Unsupported or mixed
conditions rasterize before native admission.

The reviewed future native autofit set is `AutoSize=none` only. Attempt 03 recorded
`AutoSize=2` shrink behavior on one host, including an effective 15pt font from a 16pt
request, but there is no portable shrink-factor authority. Every shrink-to-fit text
owner is therefore `raster-required` before serialization.

For each first-open and reopen snapshot, `TextRange2.Lines()` records line start,
length, text digest, and bounds in points rounded by the evidence policy. Shape and
text bounds, overflow X/Y, line-break digest, and canonical rectangles are stored.
Unavailable/mixed/empty line results fail qualification.

The numeric overflow and geometry tolerances are fixed by the candidate evidence
policy. Raw line-bound drift was 0pt in 33 first/reopen paired observations. Bounds are
rounded to 127 EMU (0.01pt) and may differ by at most 254 EMU (0.02pt). Line count,
line text/span, newline kind, and run style have zero drift allowance. Shape-side drift
and small overflow are capped at 3175 EMU (0.25pt). The 0.25pt overflow fixture
reopened as a stable 0.200001pt residual and was visually unclipped; a larger residual,
any line drift, or any clipping rasterizes before admission. The existing 5pt and
10pt counters remain diagnostic only and never relax the 0.25pt release ceiling.

These are candidate tolerances derived from attempt-03 measurements, not a Gate -1
authorization. The nonconforming result, missing collectors, cleanup contradiction,
and repair-dialog witness prevent schema freeze. Until the corrected manifest is rerun
as `feasible`, every text owner remains Fidelity/raster-only in implementation scope.

### 5.4 Typography artifacts

First-open and reopen typography snapshots are separate immutable artifacts. These
digest domains remain distinct:

- whole COM deck snapshot digest
- typography snapshot digest
- typography authority digest

`NativeTypographyReleaseEvidenceV2` references the registry receipts, span map,
embedding/OOXML receipts, first-open and reopen typography snapshots, input PPTX,
saved-copy PPTX, QA decision, policy, and attempt identity. The authority digest is
recomputed from those references and is never compared for equality with another
digest domain.

## 6. Complex CSS outcome and degradation v2

A semantic element may have a native payload and raster decorations simultaneously,
so one element-level enum is insufficient. The evidence model separates:

- `SemanticOwnerOutcomeV2`: authoritative owner and payload carrier
- `FeatureOutcomeV2`: one outcome per required capability
- `PaintNodeOutcomeV2`: physical browser paint node or fragment
- `AtomicRasterGroupV2`: indivisible compositing island

Every feature outcome is exactly one of:

```text
nativeVerified | rasterVerified | slideRasterVerified | publicationBlocked
```

A successful slide with any raster representation outcome emits
`HybridRasterDegradationReceiptV2`; this is not limited to budget or exception
fallback. Each receipt references `paintPlanCoreDigest`, final raster-plane artifact,
QIDs, atomic group IDs, capability IDs, and stable fallback reasons.

The versioned capability mapping includes at least:

| Capability | Allowed fallback reason |
| --- | --- |
| `shape.gradient` | `css-gradient-rasterized` |
| `shape.shadow` | `css-shadow-rasterized` |
| `layout.transform` | `css-transform-rasterized` |
| `layout.flex` | `css-flex-layout-rasterized` |
| `layout.grid` | `css-grid-layout-rasterized` |
| `shape.borderRadius` | `css-border-radius-rasterized` |
| `shape.opacity` | `css-opacity-rasterized` |
| `shape.clip` | `css-clip-rasterized` |

Fallback vocabularies are separated from validator failures and receipt states:

- `fallbackReasonCode`: truthful representation outcome
- `validationFailureCode`: evidence rejection detail
- `receiptReasonCode`: server-issued capability state

An atomic raster group includes a bounded paint-contribution proof from the same
browser capture generation as the final backplate: visible and suppressed capture
digests, decoded image digests, changed-pixel count, contribution bounds, renderer
digest, layout digest, paint ordinals, and member QIDs. Suppression runs only in an
isolated capture clone and uses a paint-only mechanism that preserves layout boxes and
descendant geometry; the live Fidelity DOM and its mutation-free epoch are never
modified. Pre/post layout digests, bounds, and paint ordinals must be identical. A
layout change blocks publication with `css-contribution-proof-layout-mutated`.

The visible decoded capture digest must equal the final raster-plane decoded digest.
Zero contribution cannot satisfy a fallback capability. Flex/grid descendants and
transformed/masked/opacity groups are proven as one atomic island rather than with an
unbounded render per descendant. Capture count and time budgets are signed evidence
policy fields; exceeding them produces a truthful full-slide raster outcome.

Identity, scope, QID ownership, binding, or asset-digest mismatch always produces
`publicationBlocked`; it cannot be relabeled as representation fallback.

## 7. Paint graph and z-band v2

The graph separates semantic owners from physical paint nodes. A physical node has:

```text
paintNodeId
semanticQid or null
nodeRole
atomicGroupId or null
parentPaintNodeId or null
paintOwner
promotionUnitId
browserPaintOrdinal
sourceIndex
boundsEmu
opacityFixed
clipDigest
transformDigest
```

Pseudo-elements and anonymous paints may have no semantic QID. Shadows, borders,
text, and containers are separate physical nodes. Transformed ancestors, masks,
filters, blend modes, and opacity groups are atomic; partial native promotion inside
an unsafe atomic group is forbidden.

The closure vertex is `promotionUnitId`, not an individual fragment. All semantic
owners in one atomic group share one promotion unit and therefore one native/raster
state. If any member is unsafe, the entire unit is demoted in one transition and every
member records the same blocker plus its capability-specific outcome. A graph with a
partly native atomic group is invalid before digest generation.

The closure is monotone:

```text
S0 superset-or-equal S1 ... superset-or-equal Sn
```

- Re-promotion during one closure run is forbidden.
- Each iteration demotes at least one selected candidate or terminates.
- Total order is browser paint ordinal, source index, semantic QID, then physical
  fragment ordinal.
- An overlap spatial index creates the blocker graph once; queue propagation computes
  closure in `O(V+E)` after graph construction.
- Maximum paint nodes and overlap edges are policy fields. Complexity overflow yields
  `paint-graph-complexity-limit` and a full-slide raster outcome.
- Opaque canvas removal, actual promoted-ID differences, or backplate collision enters
  the same queue as seed demotions and requires recomputation.

The pre-serialization fixed point compares selected IDs, planned native-owner IDs, and
final graph-native IDs. After serialization, OOXML and COM separately verify that the
actual owner set equals the planned set; serialization output is not used as a
pre-serialization termination condition.

`PaintPlanCoreV2` preserves the complete final graph, atomic groups, edges, demotions,
selection, complexity counts, closure order, raster-plane data, and backplate digest.

The bounded policy admits at most 256 semantic owners, four physical nodes per owner,
1024 paint nodes, 32 overlap neighbors per node, 16384 undirected overlap edges, 256
promotion units, and 256 closure iterations per slide. The physical z-band is a
single raster-underlay cut: every admitted native owner must be fully above the one
raster plane. Residual paint above a native owner, interleaving, or an atomic
opacity/filter/mask/transform group demotes that promotion unit. Complexity overflow
produces full-slide raster; it never increases the limits at runtime. COM/OOXML prove
the final owner set and z-order only. Browser pseudo-paints and atomic-group topology
remain pre-serialization evidence and collision remains render/manual-ledger evidence.

### Raster plane and `p:bg`

`RasterPlaneReadbackV2` is the single authority for picture and slide-background
representations:

- `present`
- `representation: picture | slideBackground | none`
- `count: 0 | 1`
- media relationship and artifact digest
- decoded RGBA digest when Office may transcode bytes
- full-slide coverage, stretch/fillRect, alpha, and effects

A mixed slide is `rasterPlane.present && nativeOwnerCount > 0`. Attempt 03 recorded a
solid `p:bg` round trip, but did not collect picture-plane count, effects count, or a
schema-conforming relationship readback. Therefore current output uses a picture
raster plane only. A future `p:bg` admission may be enabled only after the corrected
Gate -1 proves an opaque solid slide color with zero effects. Every image raster plane is
exactly one full-slide picture underlay below all native owners. Input and saved OOXML
must not contain both picture and `p:bg` raster planes, duplicated relationships,
unexpected effects, or a changed decoded visual payload. Legacy `backplateCount` is a
diagnostic projection derived from this resolver and is never a second readiness
authority.

## 8. QID single-owner and semantic-carrier contract

Every logical-native semantic object has exactly one owner that physically carries its
semantic payload. Required owner roles include:

- text: actual text/typeface/line-break carrier
- chart: actual native chart object
- table: actual native table object
- image: actual image/crop object
- shape: primary semantic geometry

Containers, shadows, outlines, overlays, borders, and other decorations are fragments
unless that object itself is the declared semantic shape. A native owner records:

```text
qid
semanticKind
semanticCarrierRole
physicalOoxmlKind
hasSemanticPayload
```

Only owners use:

```text
Presenton hybrid <semantic-kind> <qid>
```

Fragments use a grammar outside the owner matcher:

```text
Presenton fragment <role> <non-visual-id>
```

The selected secondary authority is PowerPoint `Shape.Tags`, using the exact keys
`PRESENTON_QID_V2`, `PRESENTON_KIND_V2`, and `PRESENTON_OWNER_ROLE_V2`. Values use
uppercase RFC4648 base32 without padding: `Q2_<base32(UTF-8 canonical QID)>`,
`K2_<base32(enum)>`, and `R2_<base32(enum)>`. The decoded values, display name, and
semantic plan must agree. The corresponding OOXML relationship graph is inspected in
the input and saved packages, and the same semantic tag map is read through
first-open/reopen COM. Raw part names, `rId`s, XML order, or Office-generated nonvisual
IDs may change without changing authority. Names alone are never a fallback.

QIDs use ASCII `[A-Za-z0-9._:-]`, maximum 96 characters. Kind and owner-role values are
closed enums. The encoded tag length is checked before serialization. Duplicate keys,
absent tags, invalid base32, truncation, undecodable UTF-8, or decoded name/tag
disagreement block publication. Non-visual IDs are unique and deterministic per slide.

The expected owner set is every `logical=native` tuple of
`(slideNumber, semanticKind, qid)`, never the subset already found in OOXML. Owners are
top-level in the bounded profile; unexpected grouping is a qualification failure.
Input OOXML, saved OOXML, first-open COM, and reopen COM each must contain the exact
owner set once, and owner objects must expose the expected semantic payload.

Missing, duplicate, unexpected, kind-mismatched, or non-payload owners block
publication. Ghosting or backplate collision greater than zero makes PowerPoint QA
blocked, not merely diagnostic.

## 9. Native chart observable projection v2

The future bounded chart candidate types are reduced to non-stacked, non-smoothed line
without markers and one-series non-3D pie. Attempt 03 used `AddChart2` with embedded
workbooks and omitted categories/finite values from its result, so no chart family is
currently Gate -1-qualified; all charts remain Fidelity/raster-only until a corrected
cached-literal/no-workbook carrier rerun passes. Column, bar, area, donut, 3D, stacked,
smoothed, marker-bearing, unsupported CSS, alpha colors, automatic/theme styles, and
custom chart typography rasterize with stable reasons. Expanding one family requires a
new Gate -1 case and policy digest; the old six-family statement is withdrawn.

Admission resolves raw semantic input into `ResolvedObservableChartProjectionV2`.
Only fields observable and explicitly serialized in OOXML and COM are admitted:

- normalized semantic chart family
- categories, series names, and canonical values
- title presence/text using the pinned default chart text style
- category/value axis presence using the pinned default chart text style
- legend presence and position using the pinned default chart text style
- series fill and line colors as separate channels
- pie/donut point fill colors
- actual native chart owner identity

All admitted colors are explicit `srgbClr`. The default chart text style is identified
by an exact Office/theme/environment digest rather than inferred from labels. Any
semantic request for title/axis/legend font customization rasterizes.
Unused palette entries are rejected or rasterized. Pie/donut requires one series and
at most one point color per category. Other chart types permit at most one palette
entry per series.

Style precedence is fixed:

```text
explicit component style
  > explicit series/point channel style
  > matching palette slot
  > resolved bounded-profile default
```

For line charts, series color resolves to line stroke. Pie resolves point fills by
category. Other family/channel mappings are outside this evidence policy.

COM chart constants normalize through a versioned family mapping. Only `xlLine` with
markers explicitly disabled maps to `line`; `xlLineMarkers`, stacked, 3D, or other
variants fail closed. Input OOXML, saved OOXML, first-open COM, and reopen COM compare
the same title text, category strings, finite numeric arrays, explicit line/point RGB,
axis/legend presence, and native owner identity.

Finite chart numbers use normalized big-endian binary64 hex; negative zero,
NaN, and infinity are forbidden before canonicalization. TypeScript and Python share
bit-level golden vectors. The comparison policy requires exact sRGB and exact binary64
values (zero ULP). Custom chart typography is not admitted, so there is no font-size
tolerance. Attempt 03 recorded exact first/reopen line stroke and four pie point fills,
but those partial observations are non-authoritative. Visual metrics and all declared
data/category fields remain mandatory. Any policy change changes
`evidencePolicyDigest` and invalidates older readiness.

Embedded-workbook editing is not part of bounded v3 and requires a separate reviewed
profile expansion.

## 10. UI, CI, environment, and COM worker authority

The server remains the sole promotion-decision authority. A projection snapshot adds
an evaluation ID, evaluated/expiry timestamps, decision-policy digest, blockers, kill
switches, pins, and projection digest. The client validates internal consistency only
within that one snapshot and displays server values verbatim. It cannot create or
clear readiness. A valid blocked projection is displayed as blocked, not malformed.

The snapshot digest binds presentation revision, canonical/manifest digest, profile
definition digest, every receipt digest, blocker/kill-switch generation, environment
and worker attestation, CI seal, evaluated time, and expiry. Export and any later
cutover re-fetch and revalidate the same subject at use time; a UI-displayed or cached
snapshot is never execution authority.

The compatibility `boundedNative` claim explicitly carries a semantic-version marker
stating that, for v3, it aliases `nativeSubsetReceiptDigests`. Older consumers cannot
reinterpret it as a broader historical bounded-native set.

The CI verifier requires exact status keys and pipeline steps for the exact repository
and backend commit. Its signed canonical payload includes repository, commit, pipeline
and step UUID/name, every required status key and latest state, verifier schema,
verified/expiry time, nonce, issuer, signer key ID, and rotation epoch. Missing, failed,
stale, ambiguous, replayed, pagination-incomplete, or wrong-key evidence is blocked.

Interactive qualification runs under a dedicated Windows account and visible desktop
session. The worker has:

- measured OS, PowerPoint, renderer binary/package, backend build, and font inventory
- worker identity certificate and approved signer key
- leased job ownership, heartbeat, bounded retry, and stale-lease fencing
- PowerPoint process/job-object cleanup and per-attempt fresh application policy
- dialog observation timeout, quarantine, and escalation
- desktop-interference detection
- untrusted PPTX and asset handling boundaries
- redacted logs/screenshots and evidence retention class

Worker enrollment binds host ID, dedicated account, certificate public key, allowed
environment, key purpose, and expiry to a backend trust-root allowlist. Enrollment,
host replacement, key rotation/revocation, and attestation expiry are audited. A
configured environment flag cannot substitute for a valid measured receipt.

The candidate policy fixes two attempts total with 30-second backoff, and permits a
retry only for transient infrastructure failure before `assembled`. Open and reopen
timeouts are 30 seconds, save-copy is 60 seconds, each slide render is 30 seconds, and
one attempt has a 600-second wall timeout. Lease, heartbeat, and stale fencing are
900/15/45 seconds. Process cleanup grace/timeout are 10/30 seconds. Attempt 03 recorded
open/reopen at at most 125ms and save-copy at at most 4893ms, but its raw cleanup record
retained POWERPNT PID 10576 while the analyzer hard-coded zero survivors. Cleanup is
therefore `not-verified` and the next run must bind a process census before and after
the full grace/timeout window. Repair dialog, desktop interference,
identity/artifact/tag/font mismatch, or OOXML repair is non-retryable and quarantines.

Workers must attest measured clock skew no greater than 10 seconds; validators permit
30 seconds future skew. Worker and environment attestations are at most 900 seconds
old, CI and completion evidence at most 1800 seconds old, a projection lives 300
seconds, and any later deployment receipt lives 600 seconds. Nonces are single-use.

Operational ownership is explicit: the control-plane owner manages leases and CAS,
the Windows qualification owner manages the dedicated desktop and POWERPNT quarantine,
the security owner manages signing/enrollment/revocation, and the release reviewer owns
policy/ledger approval. Repair dialogs, hangs, orphaned processes, or desktop
interference create a blocked receipt and quarantine; they are not retried into an
unqualified success. Escalation target, response SLO, and quarantine release authority
are mandatory deployment-runbook fields.

Environment variables contain expected policy values only; they cannot self-attest
observed runtime facts. Noninteractive COM, desktop unavailability, human
interference, repair dialog, timeout, stale lease, or cleanup failure produces blocked
or not-verified evidence and cannot issue renderer or promotion authority.

Deployment remains a separate quorum decision. A future deployment receipt must bind
the exact repository commit, immutable image/package digests, environment, rollout
plan, approver quorum, rollback receipt slot, issuer trust tuple, and 600-second TTL.
None exists or is authorized by this design.

## 11. Gate model

### Gate -1: COM feasibility before schema freeze

Gate -1 is a separately authorized, non-product feasibility probe for UTF-16 span
mapping, newline handling, mixed font styles, line bounds, autofit, saved-copy OOXML,
PowerPoint tags/extensions, chart style observation, and `p:bg` persistence. The user
authorized and this review executed the minimum probe in a separate evidence folder;
no product code, product test, database, runtime setting, service, or listener changed.
A failed, blocked, or unrun Gate -1 prevents evidence-v2 schema freeze.

Its result state is:

```text
feasible | blocked | failed
```

The versioned Gate -1 schema and retrospective case manifest are
`case-manifest-envelope.v1.schema.json` and
`gate-minus1-case-manifest.v1.json`. The manifest fixes 18 ordinals, the exact
13,295,163-byte input PPTX digest
`sha256:50ffe66fb1288107bc0a338c19d47c9344ae347e42de732713fe05a7a4ea0823`,
environment digest
`sha256:d6ca3ad1b02fcb10c7421532c7762d8c684f2927574023e9faaf44d6fc9b2260`,
required observation fields, assertions, and artifact identity rules. The result seals
executed count, ordered case results, raw artifact digests, environment receipt, and
result-set digest. Missing, duplicate, skipped, or unobserved required cases yield
`blocked`; only a complete `feasible` result may unlock schema freeze. Because the
corrected fixture must contain new uppercase-safe carriers and therefore has new
bytes, no future input digest is invented here. A future execution manifest must keep
the same 18-ID registry, set `manifestStatus=sealed-for-execution`, bind the fresh
input/environment/version IDs, and recompute its self-excluding digest before COM is
started. This is a deterministic execution-time pin, not an unresolved design choice.

The initial Gate -1 manifest has exactly 18 required cases:

```text
gminus1.text.utf16-surrogate
gminus1.text.combining-nfc
gminus1.text.newline-crlf
gminus1.text.soft-break
gminus1.text.empty-repeated-whitespace
gminus1.text.mixed-bold-italic
gminus1.text.east-asian-typeface
gminus1.text.autofit-none
gminus1.text.autofit-shrink
gminus1.text.line-bounds-long-ko
gminus1.text.small-overflow
gminus1.qid.shape-tags-roundtrip
gminus1.package.presentation-tags-roundtrip
gminus1.font.saved-embedded-parts
gminus1.paint.p-bg-roundtrip
gminus1.chart.line-style-roundtrip
gminus1.chart.pie-point-style-roundtrip
gminus1.com.repair-dialog-witness
```

The retrospective attempt-03 reference manifest is sealed against the exact observed
input with digest
`sha256:772e2b5930ef73b2311e40d8675f607b9d32fdb00e3f038552393c07d7f6dd5f`;
its ordered required-case ID digest is
`sha256:37b5a3dc034e9eab066ffcdd3e6dc2bb143af9123e75b732da10be71cc2c51f1`.
It has no embedded result reference. The separate result schema requires actual
missing/duplicate/unexpected arrays, ordered results, environment and artifact
inventory digests, and excludes only `$.resultSetDigest`; this removes the former
manifest/result hash cycle.

`gate-minus1-semantic-validator-contract.v1.json` fixes the case-specific structured
collectors for tag relationship/decoded carrier maps, chart category/value arrays and
cached-literal carrier, `p:bg` plane/effects, and attended window events. Its digest is
`sha256:fe1cbd606accab30def980e874c37578160b1c0a6bba74afab6abc1870e48ab8`.
Both manifest and result pin this digest. A `sealed-for-execution` manifest additionally
binds an immutable signed execution-seal artifact. That payload requires exactly six
role-bearing immutable artifacts—input PPTX, probe, dialog watcher, cleanup census,
environment receipt, and source-tree census—whose ordered role-set digest is
`sha256:c952db10303f25aea35ebed316e1a866be2924135964884c6103a0c66ae90190`.
Changing status alone can therefore never turn the retrospective manifest into an
execution manifest.

The result has a dedicated 21-entry `commonObservations` array in addition to the 18
ordered case results. Both sets use the exact typed field registry. Every
`requiredSchemas` identifier resolves through the validator's pinned registry into
`gate-minus1-structured-observation-payloads.v1.schema.json`; the validator pins that
document's byte digest and rejects missing definitions, wrong case IDs, wrong field-ID
sets, or payloads whose outer immutable artifact binding does not re-hash. Execution
timestamps use canonical millisecond UTC strings, and semantic validation enforces
`freshInputCreatedAt <= manifestSealedAt <= executionSealIssuedAt < executionSealExpiresAt`
plus the active policy's environment-age and future-skew limits.

A self-declared semantic Boolean is never authority. The result first seals a
`resultCoreDigest` excluding its receipt reference. An immutable semantic-validation
receipt payload binds that core digest, manifest digest, validator-contract digest,
issuer, signer key, algorithm, nonce, and issuance/expiry; Ed25519 signs the receipt
digest. The receipt payload never contains its own artifact SHA-256. Only the outer
result reference binds receipt version, length, media type, and SHA-256. The final
result-set digest includes that outer reference, preserving an acyclic DAG. The same
signed-receipt pattern is mandatory for Gate 0 and Gate 1.

The execution seal is a separate signed immutable JSON artifact. To keep the graph
acyclic, it binds a manifest-core digest that excludes `executionPins` and
`manifestDigest`; the final manifest then binds the seal's outer artifact reference,
and the result core repeats that exact reference before the semantic receipt is issued.
The seal projection excludes only its own digest and signature, and binds signer,
Ed25519 algorithm, nonce, issuance/expiry, policy, all six exact role-bearing pins,
fresh input/environment, and source census. The semantic validator additionally
requires before-open, after-cleanup-grace, and after-timeout POWERPNT censuses and zero
unexpected survivor PIDs for `feasible`.
The Ed25519 input is the exact 71 ASCII bytes of the lowercase
`sha256:<64 lowercase hex>` `executionSealDigest` string, with no BOM, terminator,
whitespace, or newline. The outer reference must re-hash the exact payload bytes and
its `payloadExecutionSealDigest` must equal the parsed payload value.

Every Gate -1 observation field is assigned to exactly one validator rule with fixed
`valueType`, unit, and integer scale; unregistered or multiply registered fields fail.
Complex relationship graphs, arrays, rectangles, and metric sets are referenced by a
digest of their typed case-specific artifact. This prevents, for example, encoding
`comLength` with a geometry unit or an arbitrary scale.

Attempt 03 did run 18 named probes, but its legacy digest
`sha256:c16c28ad57c6a313c465fa1a04ce99a7f5dcbe6fe3e120ba7ac1f911a43b93ee`
is explicitly non-authoritative. The analyzer used fractional JSON numbers, underscore
status values, post-hoc manifest binding, incomplete required collectors, and a
hard-coded cleanup count that conflicts with raw PID evidence. The durable
`gate-minus1-attempt-03-observation-envelope.v1.json` therefore records
`schemaConformingCaseCount=0`, `suiteDisposition=not-verified`, and preserves the
legacy digest only for traceability. It is not a Gate -1 result.

Candidate measurements from three completed trials on PowerPoint 16.0 build 20326 are:

- `A🚀B` COM length 4; surrogate-internal character requests expanded to the same
  `Start=2, Length=2`; CRLF appeared as one CR; soft break appeared as U+000B; mixed
  style returned raw `-2`.
- Six embedded font part digest/length pairs were identical across input and three
  saved packages on one host.
- Maximum sampled line-bound drift was 0 micro-point; the small-overflow residual was
  200001 micro-point.
- Maximum changed-pixel ratio was 972222222 ppm-microunits and minimum block-8 luma
  SSIM was 998912730747 parts per trillion.
- Maximum open/reopen time was 125ms and save-copy time was 4893ms.

These measurements may justify conservative candidate numbers, but cannot qualify a
native carrier. QID/package relationships and uppercase-safe encodings, chart
categories/values and cached-literal carrier, `p:bg` planes/effects, a clean process
census, and the attended repair-dialog witness must all be collected in a fresh run.
Until that run is schema-conforming and `feasible`, every Hybrid representation is
Fidelity/raster-only and renderer attestation, policy freeze, implementation, and
Gate 1 remain NO-GO.

### Gate 0: structural reachability

Gate 0 uses the real router, database, publication service, production binding service
seam, artifact endpoints, receipt endpoints, and projection endpoint in an isolated
structural authority domain. Synthetic values are labeled
`evidenceClass=synthetic_structural_only` and signed only by the structural issuer.

Its result state is:

```text
structurally_reachable | blocked | failed
```

The isolated projection may prove its state machine reaches `promotionReady=true`, but
the report remains release `not-verified`, and the structural evidence cannot cross
into production authority.

Negative cases include every binding identity field mutation, synthetic font bytes,
stale revision/manifest/attempt, partial capabilities, malformed/future/expired time,
cross-tenant/owner evidence, reused nonce/attempt/receipt, changed active manifest,
wrong signing key, artifact-byte mutation, and mismatched binding digest. Every
rejection also proves that no sealed authoritative completion record was persisted.

The Gate 0 manifest has one positive structural case and exactly 33 mutation cases:
one for each of the 28 canonical identity fields and five non-field integrity cases.
The previous 27-case grouping was incomplete and is withdrawn. The 33 mutation IDs
are:

```text
g0.mutate.authority-domain
g0.mutate.evidence-class
g0.mutate.evidence-schema-version
g0.mutate.evidence-policy-id
g0.mutate.evidence-policy-digest
g0.mutate.issuer-id
g0.mutate.signer-key-id
g0.mutate.issued-at
g0.mutate.expires-at
g0.mutate.nonce
g0.mutate.tenant-id
g0.mutate.owner-id
g0.mutate.presentation-id
g0.mutate.presentation-revision
g0.mutate.operation-id
g0.mutate.generation-attempt-id
g0.mutate.manifest-digest
g0.mutate.export-attempt-id
g0.mutate.binding-digest
g0.mutate.renderer-source-digest
g0.mutate.input-pptx-digest
g0.mutate.saved-copy-pptx-digest
g0.mutate.reopen-input-pptx-digest
g0.mutate.release-profile-id
g0.mutate.release-profile-digest
g0.mutate.feature-set-digest
g0.mutate.environment-attestation-digest
g0.mutate.worker-attestation-digest
g0.mutate.synthetic-font-bytes
g0.mutate.partial-capability-set
g0.mutate.active-manifest-after-issue
g0.mutate.artifact-bytes-after-issue
g0.mutate.completion-second-seal
```

The companion Gate 0 schema and manifest fix all 34 ordinals, JSON paths, mutation
operators, mutation phases/preconditions, stable error codes, HTTP statuses, and
required absence of a sealed completion. Payload mutations default to
`payload-before-issue`; active-manifest, artifact-version, and second-seal cases bind
their distinct post-issue state. Its self-excluding digest is
`sha256:1e08200155d65fe5fa34d86bc03f5ae1403f744932e367a8190e51688d2d94cc`.
The ordered 34-ID set digest is
`sha256:8d73e6d97d2288aeecede9205562453fd0d6196f12e33c842863dd56b11a9e1a`.
The manifest, runtime result, seal, and signed semantic-validator receipt all pin the
same evidence-policy digest. `structurally-reachable` is valid only when the exact ID
set is present, missing/duplicate/unexpected arrays are empty, no result is
blocked/failed, and that receipt binds manifest, result, policy, and artifact-inventory
digests.
Gate 0 separately pins semantic-validator contract digest
`sha256:60ee7d4cba760a50e0a31d3e2ead11da054d6050393c71de7687ac1044a1ba51`;
the signed receipt distinguishes `subjectManifestDigest` from this contract digest.

Test UUIDs and time use deterministic inputs. Freshness cases use an injected test
clock. Fixture assets are digest-pinned. Missing dependencies are blocked, not skipped.
Every Gate 0 suite has a signed case manifest, expected/executed counts, ordered result
set, and result-set digest; missing or duplicate case IDs block the gate.

### Gate 1: actual product evidence

Gate 1 uses `authorityDomain=actual_product` and starts with a fresh product operation
and eight-slide semantic/HTML fixture derived from actual source content:

1. native table
2. native chart
3. diagram/simple shapes
4. real image crop
5. long Korean text with serif/sans/mono mixed runs
6. multi-column layout
7. mixed CSS with repeated generic class names isolated per slide
8. right/bottom sentinels

Archetype, language, feature, and CSS-isolation coverage are reconstructed from the
actual semantic/HTML source and evidence, never trusted from a report declaration.
`LanguageCoverageManifestV2` lists the exact code points/locales exercised by the
bounded profile. To remain compatible with the retained `release-profile.v3`, the
initial `cjkCoverageProfileId` is `ja-ko-zh-bounded-fixture-v1`. Slide 5 binds six exact
run IDs and their Unicode code-point arrays. Korean/Latin may become bounded-native
only after corrected Gate -1 plus multi-host cmap/embedding proof. Japanese kana and
simplified/traditional Chinese runs are explicitly Fidelity/raster-required because no
reviewed face is bound for them. This makes no general Japanese, Chinese, or pan-CJK
claim.
The UI and evidence report display the exact coverage profile ID. Expanding it
requires a new reviewed evidence policy and real runs. Each slide records
semantic-source, archetype, language, feature, and CSS-isolation proof digests.

Gate 1 creates new Fidelity and Hybrid attempts bound to the same active manifest and
revision, actual input/saved PPTX bytes, actual PNG bytes, input/saved OOXML readbacks,
actual interactive first-open/reopen COM evidence, and independently recomputed visual
metrics. Self-declared metrics are not authority.

The Gate 1 case manifest is signed in the actual-product authority domain and has
exactly 37 required phase IDs. Five explicit phases close the former hidden gaps for
source-tree census, pre-serialization admission, package assembly, evidence-root
publication, and seal-time active-manifest CAS:

```text
g1.identity.fresh-operation
g1.identity.active-manifest-revision
g1.identity.source-dirty-census
g1.export.fidelity
g1.export.hybrid
g1.artifact.input-pptx
g1.artifact.saved-pptx
g1.artifact.fidelity-render-set
g1.artifact.hybrid-render-set
g1.ooxml.input-package
g1.ooxml.saved-package
g1.com.first-open
g1.com.save-copy
g1.com.reopen
g1.quality.pre-serialization-admission
g1.artifact.package-assembly-binding
g1.quality.typography
g1.quality.chart
g1.quality.table
g1.quality.image-crop
g1.quality.css-degradation
g1.quality.paint-z-band
g1.quality.qid-ownership
g1.quality.geometry-overflow-clipping
g1.quality.cross-slide-css-isolation
g1.quality.right-bottom-sentinels
g1.visual.independent-metrics
g1.visual.manual-ledger
g1.authority.environment-worker
g1.authority.ci-current-commit
g1.authority.freshness-replay
g1.authority.evidence-graph-root
g1.authority.required-receipts
g1.authority.completion-seal
g1.authority.active-manifest-at-seal
g1.projection.promotion-ready
g1.projection.deployment-not-authorized
```

The companion Gate 1 schema and manifest fix eight slides, 37 phases, 70 exact artifact
IDs, 12 defect fields, and 384 manual-ledger cells
(`8 slides * 2 export modes * 2 displayed COM phases * 12 fields`). The 70 artifacts
include 16 browser source PNGs: Fidelity and Hybrid for every slide. Save-copy is an
immutable package transition with mandatory digest/readback, not a displayed ledger
phase; first-open and reopen are the two visual ledger axes. The exact artifact-ID set
digest is
`sha256:36326d7447a867e06cbc00730ad51e0172dfb3c6d5e363db098bb704d71bbb5f`.
The ordered 37-phase ID digest is
`sha256:a0862767236b7e7ee368a089979e6ca21b91c24521a9403656588576398a39c9`.
The manifest also fixes the five exact Bitbucket step names and seal-time CAS inputs.
Runtime subject IDs/digests are mandatory pins issued before execution; null or
placeholder pins block the gate. Missing or duplicate phase IDs, slides, artifacts,
or ledger cells yield `blocked`. The manifest digest is
`sha256:e8470dcd7ddf000390082aa8546fe4de56f26c682b396bb7a9b617061e00f259`.
The result inventory supplies each of the 70 IDs with immutable `versionId`, SHA-256,
byte length, and media type. A mandatory signed semantic-validator receipt proves the
artifact IDs are unique and exactly equal the manifest set, every version is
re-fetched/re-hashed, the ordered phase IDs are exact, the 384 ledger-cell key set is
exact, and the manifest/runtime/result/active policy digests are equal. JSON Schema
enforces typed envelopes; the signed semantic validator enforces cross-array set and
Cartesian-product invariants.
Gate 1 separately pins semantic-validator contract digest
`sha256:35fb934011a98587304d67c5a081e012e2a6d22af3d6fc77be5274b6865fcf44`;
its signed receipt also distinguishes manifest subject, result core, and validator
contract digests.
Gate 1 additionally pins the corrected Gate -1 manifest digest, `feasible` result-set
digest, immutable semantic-validation receipt artifact digest, and semantic-validator
contract digest before execution. Seal-time CAS revalidates all four. A status label or
unbound Gate -1 hash cannot unlock Gate 1.

Its result state is:

```text
accepted | not-verified | blocked | failed
```

There is no `skip` state. Missing PowerPoint, fonts, Bitbucket access, worker desktop,
or any required artifact yields `blocked` or `not-verified`. Retries preserve every
prior failure and cannot hide a mismatch by reporting only a later success.

### Evidence policy and manual ledger

The signed evidence policy contains mandatory numeric values for:

- geometry and overflow tolerances
- line-bound rounding
- first-open/reopen and Fidelity/Hybrid visual metrics
- crop and sentinel tolerances
- maximum paint graph nodes/edges
- evidence TTL/future skew
- retry and timeout limits

No null or implicit default is valid. Structural defects require zero repair dialogs,
missing/duplicate/unexpected QIDs, clipping, ghosting, native/backplate collisions, and
unreviewed line-break drift. The candidate policy fixes the release geometry ceiling at
0.25pt; 5pt and 10pt remain diagnostic/severe counters only. Decoded 1280x720 evidence
requires changed pixels at most 1500 ppm, block-8 luma SSIM at least 0.998500, mean
absolute channel delta at most 0.100, and RMSE at most 3.200. These values apply to
first/reopen, Fidelity/source, and Hybrid/Fidelity pairs; categorical defects still
have zero tolerance.

All mandatory numeric fields, including 127/254 EMU line rounding/tolerance, 3175 EMU
overflow/shape drift, one rendered crop/sentinel pixel, 1024 paint nodes, 16384 overlap
edges, TTL, retry, lease, and timeout values, are fixed in `evidence-policy.v2.json`.
Every fractional measurement is encoded as a signed 64-bit fixed-point integer whose
field name declares its unit/scale; JSON fractional numbers are forbidden. Its
self-excluding digest is
`sha256:81a822b8b51b973492784316c7cec4c04f45d0b54fd3365db7e7f80be7f87d4f`.
The artifact status is `frozen-after-corrected-gate-minus1` and
`policyFreezeAuthorized=true`; three corrected sealed calibration runs supplied
schema-conforming 18/18, skip-zero observations before the final frozen-policy rerun.

The manual ledger covers the Cartesian product of every slide and required defect
field. Reviewer ID alone is not authority; the signed review contains reviewer,
reviewed-at, scope, per-field decision, artifact references, and ledger digest.

Only `Gate1=accepted`, a sealed evidence completion record, and current CI authority
may result in production `promotionReady=true`. Deployment authorization remains
separate.

## 12. Persistence, retention, deletion, and compatibility

Additive append-only records are separated into qualification jobs, artifact
references, evidence graph bindings, completion records, and receipts. Required
indexes include owner/tenant/attempt/policy identity, unique graph root, transition
sequence, nonce replay protection, and active receipt references.

Artifact references contain digest, byte length, media type, source attempt, retention
class, encryption key reference, created/expiry time, and legal-hold state. Active or
audit-retained receipts pin their evidence. The evidence-v2 policy retains attempt
metadata for 180 days and evidence artifacts for 30 days, preserving the reviewed v1
values. Unbound digest-verified objects have a 24-hour GC grace. Automatic physical
deletion remains false. Owner deletion creates a tombstone; separately authorized
cryptographic erasure may run only after evidence retention expires, all receipt pins
are released, and legal hold is absent. Append-only does not mean undeletable
plaintext, but this design authorizes no erasure controller or deletion operation.

COM snapshots and reports containing user text are encrypted and redacted from logs.
Original Microsoft font files are not copied into evidence storage; only metadata,
digests, and the font parts already embedded in the authorized PPTX are referenced.

Existing v1/v2 projections and legacy HTML/Fidelity read/export behavior are unchanged.
Existing v3 attempts and receipts lacking the evidence-v2 policy binding remain
diagnostic and non-promotable. No backfill occurs. If evidence-v2 is killed or rolled
back, new v2 qualification stops and existing v2 readiness expires; the system does
not reactivate weaker old receipts.

No deployment or active release cutover is authorized by this remediation.

Rollback or evidence-v2 kill stops new qualifications and lets readiness expire. It
never reactivates old receipts. A future cutover receipt must bind exact commit,
immutable package/image digests, target environment, rollout plan, approval quorum,
rollback receipt slot, trust tuple, and a 600-second TTL. Until that separate receipt
exists, cutover is NO-GO even if bounded promotion evidence later becomes ready.

## 13. Acceptance conditions before any implementation completion claim

After separate implementation authorization, completion requires one fresh Gate 1
identity and sealed evidence graph proving:

- actual CJK multi-host registry equality, embedding, UTF-16 run map, exact face,
  East Asian typeface, line geometry, overflow, first-open, saved-copy, and reopen
- capability-specific native or raster evidence for all complex CSS features
- final monotone paint closure, exact raster-plane digest, and safe z-band
- exact semantic-carrier QID sets in plan, input/saved OOXML, and first/reopen COM
- complete observable chart data/style equality across semantic, OOXML, and COM
- table/crop data, geometry thresholds, clipping, overlap, ghosting, and collision
- actual fresh PPTX/PNG/report/manifest hashes in one evidence graph root
- authenticated, non-replayed current-commit CI authority
- all required v3 receipts and a sealed completion record yielding
  `promotionReady=true`
- `deploymentAuthorized=false` unless a later approved deployment receipt exists

## 14. Proposed work order after explicit approval

This section is sequencing only and does not authorize work.

1. Implement only after separate authorization a non-product probe revision that
   collects the uppercase-safe package/QID relationship graph, exact chart
   category/value/cached-literal carrier, `p:bg` plane/effect state, clean process
   census, and attended non-dismiss repair-dialog watcher. Rerun all 18 cases against
   the corrected sealed Gate -1 manifest with a new immutable result and require
   `feasible`.
2. Freeze canonical serialization, digest DAG, authority domains, evidence policies,
   state transitions, shared registries, failure vocabularies, and persistence schemas.
3. Implement producers and qualification worker: typography/embedding, CSS
   contribution, paint plan, QID carrier, chart projection, OOXML, and COM artifacts.
4. Implement trusted validators, UI blocker handling, CI signing/replay control,
   completion sealing, Gate 0 negatives, and one fresh Gate 1 evidence bundle.

## 15. Final design review and implementation verdict

The design is review-complete: every earlier decision gap now has a versioned contract,
fixed count/value, deterministic failure rule, or explicit scope reduction. Attempt 03
is deliberately retained as a nonconforming observation envelope and is not described
as a Gate -1 result.

| Area | Score / 10 | Decision |
| --- | ---: | --- |
| Canonical serialization and digest DAG | 9.5 | Explicit projection per schema; manifest-result DAG acyclic |
| Gate -1 schema, 18-case manifest, and result contract | 9.3 | Next-run contract complete; attempt 03 not conforming |
| Actual Gate -1 evidence | 6.0 | Useful candidate measurements only; zero authoritative feasible cases |
| CJK/UTF-16/font/COM design | 8.6 | Exact fixture boundary; all native text locked pending rerun/multi-host proof |
| Package marker and QID ownership | 8.8 | Uppercase-safe design fixed; actual carrier probe still required |
| Chart, `p:bg`, and paint graph | 8.5 | Current raster-only; future line/pie and solid `p:bg` conditional on rerun |
| Issuer, CAS, worker, TTL/retry/retention/cutover | 9.3 | Values and authority fixed; cleanup/cutover NO-GO |
| Gate 0 and Gate 1 manifests | 9.3 | 34 Gate 0 cases, 37 Gate 1 phases, 70 exact artifacts, 384 ledger cells |
| Overall remediation design | 8.9 | Review GO; schema freeze and implementation NO-GO |

Final verdicts:

- Design review: **GO**. The document and companion artifacts are internally decided
  and reviewable.
- Scope: **reduced**. Current authority is Fidelity/raster-only. Future bounded native
  text excludes shrink autofit and zero-length run identity; Japanese/Chinese fixture
  runs remain raster; future charts are line/pie only; image `p:bg` is removed; paint
  uses a single raster-underlay cut.
- Evidence-v2 schema freeze: **NO-GO** because attempt 03 is nonconforming and the
  required marker/QID/chart/`p:bg`/cleanup/dialog collectors are incomplete.
- Product implementation: **NO-GO** until all 18 cases under the corrected sealed
  Gate -1 manifest produce a schema-conforming `feasible` result, followed by separate
  explicit implementation authorization.
- Gate 1, promotion, deployment, merge, commit, push, and cutover: **NO-GO**. No actual
  product authority, CI seal, 70-artifact bundle, completion seal, deployment receipt,
  or cutover evidence exists.
