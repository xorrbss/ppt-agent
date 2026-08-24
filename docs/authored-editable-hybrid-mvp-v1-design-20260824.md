# Authored editable Hybrid MVP v1 design

Status: primary product design candidate. This document replaces raster-only local
qualification as the immediate product objective. It authorizes no implementation,
schema migration, runtime restart, evidence issuance, commit, push, merge, deployment,
or cutover.

The immediate goal is not a general native-parity claim. It is one useful, honest,
editable Hybrid slice:

> Korean/English text, basic solid shapes, ordinary images with rectangular crop, and
> basic tables remain editable in the Workbench and PowerPoint. Unsupported content
> remains a bounded raster island rather than forcing every slide to image-only.

The existing external bounded-v3 `promotionReady` and `deploymentAuthorized` meanings
are unchanged. `authored-bounded-hybrid-local-release-v1-design-20260824.md` remains an
auxiliary raster-fallback qualification design and cannot satisfy this MVP.

## 1. Product-first decision

The required work order is:

```text
minimal native scope
-> Scene/QID and UI editing round trip
-> native PPTX export and PowerPoint round trip
-> capability-specific feasibility gate
-> fail-closed structural gate
-> actual-product evidence seal
-> later capability expansion
```

Gate infrastructure must not be reported as product completion while the generated
deck still has `editableSlides=0` or every slide is image-only.

The MVP is complete only when one fresh AI-generated deck passes this actual path:

```text
AI generation
-> canonical semantic source and Scene
-> Workbench native-object edit
-> strict-CAS Scene publication
-> Hybrid PPTX export
-> PowerPoint native-object edit
-> save-copy
-> reopen/render/readback
```

## 2. Exact MVP capability boundary

### 2.1 Native editable text

Included:

- Korean Hangul and reviewed common Korean punctuation;
- Basic Latin English text;
- explicit LF paragraph and soft-break representation;
- reviewed sans/serif/mono faces that are present in the sealed font inventory;
- font family, size, bold, italic, color, alignment, and line spacing;
- text content, box position, and box size edits;
- `AutoSize=none` with measured overflow/clipping rejection.

Excluded to raster:

- Japanese kana and simplified/traditional Chinese;
- shrink autofit;
- zero-length run identity;
- unreviewed or non-embeddable fonts;
- WordArt, text effects, path text, and unsupported mixed-script shaping;
- text whose measured first-open/reopen geometry exceeds the sealed tolerance.

### 2.2 Native editable basic shapes

Included:

- rectangle, rounded rectangle, ellipse, straight line, and simple connector;
- solid fill or no fill;
- solid stroke or no stroke;
- position, size, rotation, fill color, stroke color, and stroke width edits;
- native text inside a supported shape when the text capability also passes.

Excluded to raster:

- gradient, shadow, glow, reflection, soft edge, pattern fill, and transparency graph;
- freeform/path geometry, SmartArt, diagram groups, and complex transforms;
- any owner whose paint contribution cannot be isolated without z-order ambiguity.

### 2.3 Native editable images

Included:

- PNG and JPEG with stable decoded digest;
- position and size edits;
- rectangular crop using left/top/right/bottom fractions;
- replace-image action that preserves owner QID and creates a new asset digest.

Excluded to raster:

- masks, filters, blend modes, recolor, artistic effects, non-rectangular crop, and
  unsafe external relationships.

### 2.4 Native editable basic tables

Included:

- rectangular rows and columns without merged or split cells;
- cell text satisfying the text capability;
- solid cell fill, solid border, horizontal/vertical alignment;
- cell text, table position, table size, and column-width edits.

Excluded to raster:

- merged cells, diagonal borders, gradient/pattern fills, formulas, linked data,
  nested tables, and unsupported theme effects.

### 2.5 Explicit raster-only content

The initial MVP keeps these as raster islands:

- every chart;
- SmartArt and diagrams outside the basic-shape subset;
- Japanese and Chinese text;
- complex CSS including gradient, shadow, clip-path, filter, blend, grid/flex effects
  that cannot be reduced to the supported native geometry;
- unsafe mixed paint graphs and interleaved z-order regions.

Raster fallback is permitted only for representation limits. Identity, ownership,
tenant, QID, active-revision, asset-integrity, or publication failures block the slide
and never become raster success.

## 3. Canonical ownership and object identity

Authored remains the content and visual-quality authority. Scene is the editable and
exportable geometry/semantic authority.

Every editable owner must satisfy:

```text
semantic elementId
  == HTML data-qid
  == Scene ownerId
  == Workbench editable object ID
  == native export plan owner ID
  == OOXML/PowerPoint QID tag
```

The canonical bundle binds:

- immutable semantic source;
- immutable Fidelity HTML/CSS bundle;
- immutable Scene revision;
- asset digests;
- per-owner native eligibility decisions;
- raster-island plans and degradation receipts;
- active manifest and presentation revision;
- composition and export-plan digests.

Missing or duplicate QIDs, owner ghosts, owner reuse across slides, and semantic/Scene
count mismatches block publication.

## 4. Native admission and mixed-slide composition

Each semantic owner receives exactly one decision:

```text
nativeEditable | rasterIsland | publicationBlocked
```

`nativeEditable` requires a capability-specific admission receipt. `rasterIsland`
requires a degradation receipt with the unsupported feature, source owner set,
captured pixel digest, geometry, z-band, and final raster-plane digest.

A slide is editable when it contains at least one admitted native owner. A slide may
combine native owners with raster islands only when paint closure proves one of:

- a non-overlapping raster rectangle;
- a raster underlay fully below all admitted native owners;
- an atomic raster group with no native descendant or interleaving paint contribution.

If the paint graph cannot prove one of these forms, the whole slide may fall back to
raster for safety, but that slide does not count toward `editableSlides` and cannot
provide native-ready evidence.

The exporter must not emit both a native owner and a raster copy of the same owner.
Backplate collision, duplicated image relationships, and ghost QIDs are blocking
errors.

## 5. Workbench editing contract

Workbench loads editable objects from the active Scene revision rather than inferring
them from slide pixels.

Required MVP actions:

- select, move, resize, and layer an admitted native owner;
- edit Korean/English text and its supported typography fields;
- edit basic-shape fill/stroke and geometry;
- replace and rectangularly crop an image;
- edit basic-table cell text, fills, borders, and column widths;
- show unsupported raster islands as read-only with a user-facing reason;
- undo/redo only within the active Scene revision chain.

Every mutation sends:

```text
presentationId
presentationRevision
slideId
expectedSlideRevision
expectedSceneDigest
ownerId
mutation kind and bounded payload
```

The server validates ownership and capability, applies one strict CAS, reseals the
slide Scene and deck digest, and returns the new revision. A stale revision returns
409 and never applies an implicit merge.

## 6. Native PPTX export contract

The Hybrid exporter consumes only the sealed active Scene and its admission/raster
receipts. It must not reconstruct editable objects by reverse parsing raster pixels or
untrusted browser DOM state.

Supported owners are emitted as native PowerPoint objects through the retained native
OOXML/Template V2 infrastructure. Unsupported owners are emitted as bounded raster
islands. Fidelity export remains an independent reference output from the same active
manifest and revision.

Native export must preserve:

- QID through input PPTX, first-open COM, saved OOXML, and reopen COM;
- text content, runs, face, size, style, alignment, line breaks, and box geometry;
- shape geometry and solid paint;
- image asset digest, crop fractions, and geometry;
- table cell matrix, text, fills, borders, widths, and geometry;
- native/raster z-order and absence of duplicate owner paint.

PowerPoint edits after export are not imported back into Workbench in MVP v1. They
must, however, remain native, save without repair, and survive reopen.

## 7. Primary five-slide product fixture

One fresh AI-generated five-slide deck is the first completion target:

1. Korean/English title and body text with solid rectangles;
2. ordinary image, rectangular crop, and native caption;
3. basic table with editable cells and native heading;
4. native text/basic shapes plus one chart raster island;
5. native title/callouts plus one safely isolated complex-CSS raster island.

The fixture must originate from the actual generation API and active product UI. A
hand-authored PPTX or synthetic report cannot substitute for it.

Minimum acceptance values:

```text
totalSlides                 = 5
editableSlides              = 5
imageFallbackSlides         = 0
nativeTextElements          > 0 on every slide
nativeShapeElements         > 0 across the deck
nativeImageElements         >= 1
nativeTableElements         >= 1
rasterFallbackElements      >= 2
unexpectedRasterFallbacks   = 0
nativeEditableReady         = true
nativeRasterDuplicateOwners = 0
missingOrGhostQids          = 0
```

The exact expected owner/QID inventory and each owner's expected
`nativeEditable|rasterIsland` decision are sealed before execution. Every supported
fixture owner must remain native, the two intentional unsupported regions must remain
raster islands, and any additional raster fallback fails the fixture. Passing by
reducing the expected count or changing an expected decision after generation is
forbidden.

## 8. Required user-visible round-trip scenario

The acceptance operator performs at least these edits in Workbench:

1. replace Korean title text and change its color;
2. move and resize one solid shape;
3. replace one image and modify its crop;
4. edit one table cell and one column width;
5. export a new Hybrid PPTX from the edited Scene revision.

The operator then opens the PPTX in PowerPoint 16, edits one native text object and one
native shape, saves a copy, closes it, reopens it, and renders all slides.

Required proof:

- every Workbench edit is present in the sealed Scene and exported OOXML;
- PowerPoint exposes supported owners as native editable objects;
- PowerPoint edits survive save/reopen;
- Korean/English font, line break, alignment, and geometry remain within tolerance;
- raster islands retain their pixel and geometry digests;
- no repair dialog, missing content, duplicate owner, backplate collision, clipping,
  or unexpected layout drift occurs.

## 9. Readiness projection and naming

Do not overload `localReleaseReady` or alter the existing v3 projection. Add a
versioned v4 display contract or a separate endpoint with this explicit product state:

```json
{
  "editableHybridMvp": {
    "profileId": "authored-editable-hybrid-ko-en-basic-v1",
    "releaseScope": "ko-en-text-solid-shape-image-crop-basic-table",
    "status": "not-evaluated",
    "editableHybridMvpReady": false,
    "nativeEditableReady": false,
    "reasonCode": "editable_hybrid_mvp_not_evaluated"
  },
  "localRasterQualification": {
    "status": "not-evaluated",
    "localRasterReleaseReady": false
  },
  "externalPromotion": {
    "status": "not-evaluated",
    "promotionReady": false
  },
  "deployment": {
    "deploymentAuthorized": false
  }
}
```

Allowed editable-MVP states:

```text
not-evaluated | shadow | ready | blocked | expired
```

`editableHybridMvpReady=true` requires the five-slide product acceptance, current
capability receipts, sealed MVP completion, no blockers, and no MVP kill switch.
It never sets `promotionReady` or `deploymentAuthorized`.

The UI label for this state is `편집 가능한 Hybrid MVP`, not generic release-ready.

## 10. Profile and receipt isolation

For the fastest safe implementation, retain `authorityDomain=actual_product` but use
an exact new profile and namespaced capability IDs that the existing external v3
profile does not consume:

```text
editable.text.ko-en.v1
editable.shape.solid-basic.v1
editable.image.rect-crop.v1
editable.table.basic.v1
editable.scene-cas.v1
editable.native-export.v1
editable.powerpoint-roundtrip.v1
editable.visual-parity.v1
editable.raster-island-integrity.v1
editable.artifact-integrity.v1
```

The MVP completion store and matcher are separate from external promotion completion.
Every receipt query is filtered by tenant, owner, profile ID/digest, authority domain,
evidence policy, export attempt, binding digest, and capability ID. An MVP receipt is
never accepted as `ci.requiredChecks` or any external promotion receipt.

## 11. Product-first gate sequence

### 11.1 Shadow implementation before qualification

Implement the Scene/UI/export round trip first behind a shadow-only MVP profile.
Shadow output may be downloaded for attended development verification but cannot issue
an editable-ready receipt or affect external promotion.

### 11.2 Gate -1E: supported-capability feasibility

Run a new capability-specific feasibility manifest after the shadow product path works.
It probes only the native MVP boundary:

- Korean/English UTF-16 and run mapping;
- LF/soft-break save/reopen behavior;
- reviewed font face and effective size;
- line bounds, overflow, and `AutoSize=none`;
- text and shape QID tag round trip;
- basic solid-shape geometry/paint;
- image decoded digest and rectangular crop;
- basic-table cell matrix/style/geometry;
- first-open, save-copy, reopen, render, repair-dialog witness, and owned-process census.

Every case is required and no skip is allowed. Only `feasible` unlocks MVP native
admission. The existing full 18-case Gate -1 remains required for the broader external
v3 profile and is not weakened by Gate -1E.

### 11.3 Gate 0E: fail-closed product structure

After Gate -1E, verify at minimum:

- stale Scene and slide CAS;
- cross-owner and cross-tenant mutation;
- missing/duplicate/ghost QID;
- unsupported capability falsely marked native;
- native/raster duplicate owner;
- changed active manifest or artifact bytes before completion seal;
- signer nonce replay, stale completion CAS, and different second seal;
- local MVP receipt presented to the external promotion validator.

Every mutation must leave the active Scene, export binding, and sealed completion
unchanged.

### 11.4 Gate 1E: actual five-slide product evidence

Gate 1E uses the actual five-slide deck and the exact Workbench/PowerPoint scenario in
this document. It seals actual source, Scene, mutation, PPTX, OOXML, COM, PNG, sidecar,
artifact-inventory, and completion bytes from one active revision.

The MVP gate intentionally does not require full CJK, native charts, complex CSS native
conversion, or the broader 8-slide/384-cell promotion matrix. Those remain expansion
and external-promotion work after MVP completion.

## 12. Visual and COM acceptance

COM readback and visual parity are independent requirements.

The attempt-bound sidecar records:

```text
sceneMutationStatus
nativeAdmissionStatus
packageReadbackStatus
comFirstOpenStatus
comSaveCopyStatus
comReopenStatus
renderHashStabilityStatus
visualParityStatus
repairDialogStatus
unexpectedPowerPointSurvivorCount
nativeEditableReady
editableSlides
native owner counts
raster island counts
native/raster duplicate owner count
```

Visual thresholds, font inventory, Office/OS build, renderer bytes, fixture bytes, and
expected QID inventory are pinned before the run. A successful COM open never implies
visual parity.

PowerPoint cleanup is ownership-aware. Qualification runs in a clean worktree at the
release-candidate commit, records its pre-run census, closes only worker-owned/adopted
processes, and requires zero unexpected worker-owned survivors. It never terminates an
unrelated user PowerPoint process.

## 13. Dirty-worktree and reproducibility rule

The user's existing dirty checkout remains untouched. Qualification uses a separate,
clean worktree created from the exact release-candidate commit. Servers, runners, and
PowerPoint workers must resolve their executable/source paths inside that worktree.

Evidence pins:

- release-candidate commit;
- clean source-tree census;
- dependency lock and runtime inventory;
- environment, font, renderer, Office, and OS digests;
- listener and process ownership;
- active manifest, Scene, export attempt, and artifact graph digests.

No code or policy commit after Gate 1E may reuse its evidence.

## 14. MVP completion conditions

The product may be called `편집 가능한 Hybrid MVP 완료` only when:

- every capability in section 2 is implemented through actual UI and export paths;
- the section 7 exact five-slide inventory passes;
- the section 8 Workbench and PowerPoint edits pass;
- Gate -1E is `feasible` with no skipped case;
- Gate 0E mutations fail closed;
- Gate 1E seals one fresh actual-product evidence graph;
- `editableHybridMvpReady=true` and `nativeEditableReady=true`;
- `promotionReady=false` unless the separate external v3 gate later succeeds;
- `deploymentAuthorized=false` unless separately approved;
- an independent review of the exact evidence graph reports P0=0 and P1=0.

Unit tests, schemas, mock PPTX files, image-only output, raster qualification, or Gate
runner success without the real editing round trip cannot satisfy this definition.

## 15. Implementation slices

This section is sequencing only.

1. Add the exact per-owner native eligibility contract and supported Scene nodes.
2. Load admitted Scene owners into Workbench and implement bounded edits with strict
   CAS; keep unsupported owners read-only as raster islands.
3. Connect edited Scene revisions to native text/shape/image/table PPTX emission and
   eliminate native/raster duplicate owners.
4. Complete the actual five-slide UI -> PPTX -> PowerPoint -> reopen round trip in
   shadow mode and correct product-path failures.
5. Execute Gate -1E and enable only the capability receipts that pass.
6. Execute Gate 0E, seal Gate 1E evidence, and expose the v4 MVP readiness projection.
7. After MVP completion, expand charts, complex CSS, Japanese/Chinese, and broader v3
   promotion evidence as separately versioned capabilities.
