# Synthetic nine-page runtime input preparation

Prepared on 2026-11-08. The final input directory is `nine-page-runtime-pin/`.
`profile-pin/` and `nine-page-pin/` are historical preparation artifacts; their
capture argument mappings are superseded. No historical capture is promoted.

## Actual pin and independent inputs

`prepare_pinned_inputs.py` calls the real
`services.svg_generation_admission.pin_svg_style` with a normal typed SVG
`GeneratePresentationRequest`, nine slides, English, 16:9, ordinary public
`common_header_mode="free"`, no title/contents slide, and no source/template or
reference material. Its child-process-only fresh V5 opt-in is restored after
pinning. The global admitted V4 revision and managed environment are unchanged.
Before/after hashes of the directly connected production code are checked.

The nine independently authored requested sources are copied from the renderer
team's `normal-profile-nine/requested-inputs.json`, SHA-256
`7b68968e4710cad3f4bbf9cf43acb02ed0e7f6c888f0d1a4e476a1c7de8d3a8d`.
Each source digest is validated against its exact content/payload, and its
pre-capture authorship provenance is retained. The typed outline uses those
exact nine content strings; no placeholder truth is present.

Order: column, coincident-line, stacked-column, donut, fixed-column,
separator-pie, marker-line, horizontal-bar, signed-zero-multiseries-area.

The real resolved profile is `art-direction-v1` version 9, default style,
`svg-quality-first` version 2, with the existing nine-page
`svg-quality-recovery-plan/v5`. Canvas is 1280 x 720, physical size 12192000 x
6858000 EMU. The real hard readability floor is 9 pt / 12 px and quality target
9.5 pt / 12.666666666666666 px. Heading/body font is Noto Sans KR; mono is
monospace. The real `svg-default-bundled-fonts/v1` source identities are retained
in the context/provenance, including Noto Sans KR and Pretendard 400/700. Source
SVGs must be newly authored and finalized under this policy before capture.

Canonical typed request digest:
`sha256:841b39cb98c16befb3dbb4f40fb7dff999fc70a1c5a8a3b43bdda72ceee5f7b7`.
Actual snapshot digest:
`sha256:b54ce62882a5ec82f6d70ce0052d758386ed6ee0327f5ff727afa98398372687`.

Final raw file SHA-256 values:

| File in nine-page-runtime-pin | SHA-256 |
| --- | --- |
| request.json | efa7bac67d12440e84fc919090b8db717e3789791b5ed5ebd1554ea86cb95925 |
| outline.json | 179a3e874d1488a98f77289e43c25627aa8b71f7bcb73565fdec84a24643c978 |
| style-snapshot.json | 1ce184390df51ec7890adf593b6493f64c262c045896fcfbd6d73f2643404e5e |
| production-capture-context.json | 80b0d27bd2a3e3498339486f4ea43a449ed4296e58dde6b66db2669b7088287c |
| available-requested-sources.json | 0d7e7f723dc342c1eac42d792387b307435bb59b99749c9b537a146169935ce9 |
| pin-provenance.json | c399039bfa6860613e308748532d5177667e168ab3b88f63567f68164657e4c7 |

## Production reconstruction and capture contract

Reconstruct the typed request from `request.json`, attach the actual snapshot
as `_svg_style_snapshot`, and restore the frozen profile/recovery choices using
the existing `validate_profile_agreement` and
`validate_quality_recovery_agreement` functions. Preserve the serialized typed
outline and independent requested sources.

The actual default request has `_document_canvas is None`. Although the normal
canvas metadata resolver returns `authored-canvas/v1` metadata, the normal
generation preparation only sets physical renderer kwargs when an actual
document canvas exists. Consequently the final capture context **omits**
`physical_canvas`, `minimum_font_px`, `source_font_profile`, and `timeout`.
Pass width 1280, height 720, instructions null, and the exact frozen
`readability_policy` from `production-capture-context.json` to the existing
`render_svg_with_metrics`. Obtain per-page witness kwargs from the existing
`chart_witness_render_kwargs` with the exact finalized SVG, bound declarations,
and V5 revision.

Use the real `services.svg_contract.finalize_svg` for indices 0 through 8 and
count 9, with the actual snapshot, **before** declaration rebinding, witness
manifest construction, or browser capture. Preserve real final page furniture.
The normal system page number is Noto Sans KR, 12 px and is subject to the
existing renderer system-page-number readability exclusion. Do not patch
historical Arial captures or producer quality fields.

Three deterministic local preparation runs were performed as independent
truth inputs and the exact production mapping became available. No tests,
server/API/auth/DB, provider, browser capture, publication, export, download, or
COM operations were executed by this preparation task. Actual generation HTTP
and provider-drawn assets remain unmeasured. The provenance explicitly records
`publicationReady=false` and `capturesExecuted=false`.

## Existing supported workspace export route

After normal publication succeeds, use the same genuine product session and
tenant context for these existing HTTP operations. Do not manufacture a job,
admission, review, or publication record. Do not disable a frozen quality gate.

POST `/api/v1/ppt/workspace/presentations/{presentation_id}/commands` with JSON
fields `command` (a nonempty description), `command_type="export.pptx"`, a fresh
nonempty `idempotency_key`, `expected_revision` equal to the actual published
revision, `asset_ids=[]`, and `context={"quality_policy":"interactive-v1"}`.
Keep normal SVG font embedding and omit authored-only settings. DTO:
`models/workspace.py:WorkspaceCommandRequest`; handler:
`WorkspaceExportOperationHandler.execute` to `execute_svg_workspace_export`.
The latter requires the real admission, current owner/revision, complete SVG
indices and normal frozen lineage/readability/font/source guards.

The response has `receipt_id`, `operation_id`, `status`, `duplicate`, and
`timeline`, plus compatibility flattened timeline fields. GET
`/api/v1/ppt/workspace/presentations/{presentation_id}/timeline`; find the item
with matching `operation_id` and `anchor_type="operation"`. Its operation
status/result and revision fields are authoritative. Preserve failures and
uncertain outcomes; do not blindly submit a new export after a timeout.

On actual success the operation result contains `artifact_id`, `artifact_kind`,
`artifact_sha256`, `artifact_size_bytes`, `format`, `source_revision`,
`expires_at`, and worker metrics; the publication path additionally returns
`public_artifact_id` and `quality_receipt`. `artifact_id` is the workspace
artifact identifier for authenticated GET
`/api/v1/ppt/workspace/artifacts/{artifact_id}/download`.
`public_artifact_id` is a different identifier. Match downloaded raw bytes to
`artifact_sha256` and `artifact_size_bytes`, and retain the real operation,
receipt, artifact, and revision binding privately.

The worker evidence is at operation result
`public_export_worker_evidence.structuralReadback`; its surrounding evidence
contains the actual artifact/canvas/input bindings. Preserve the complete
original receipt and distinguish completed-identity reuse from a fresh
conversion. These structural receipts do not claim visual quality.

## Actual PPTX native editability and final PNG evidence

The existing `workers/public_svg_pptx_convert.py:convert_pptx` invokes
`SvgPptxInputs.verify_readback` and emits `slideCount`, `nativeTextRuns`, and
`nativeObjects`. `services/generation_core/drivers/svg/export_scene.py` exposes
`SvgPptxInputs.receipt()` with offered/native/fellBack and per-object reasons;
its readback checks actual per-page chart/table counts. Generic/source OOXML
coverage in the real worker is bound to the resulting artifact digest.

For the exact downloaded PPTX, existing
`workers.public_svg_pptx_readback.iter_shapes` recursively traverses groups.
Use actual chart shapes/series/categories/values and embedded workbook parts to
verify all six chart types and expected native object count against the final
nine independent requested payloads and bound declarations. Do not infer
requested truth from exported declarations. The existing
`_chart_workbook_bound(chart,payload)` checks the actual workbook and chart XML
through `services.source_content.pptx_chart_provenance._verify_workbook`; no
raster screenshot can substitute for native chart/workbook evidence. Preserve
readback exceptions and recorded fallbacks.

For root's Windows final rendering, the existing callable is
`workers.public_svg_pptx_render.render_final_pptx(target, output_directory,
page_count=9, width_px=1280, height_px=720, timeout=120)` from the managed backend
Python environment. Use the exact downloaded PPTX, an explicit new private
output directory, and the genuine managed stack environment. This callable
owns PowerPoint acquisition/locking/cleanup; do not launch a separate ad hoc
PowerPoint process. Record its measured status, nine PNG artifact digests,
PPTX before/after digest invariant and cleanup/ownership evidence.

If formal artifact-bound deterministic render proof is needed, the existing
`services.svg_final_pptx_review.load_final_review_inputs` and
`render_final_review_inputs` validate the real successful operation/artifact,
owner/tenant/revision and add the claim binding. Do not invoke the full
`quality.review` service: that later path requires a vision advisory and is
outside this provider-free verification. A deterministic COM render remains
separate from release readiness or paid visual review acceptance.

Required pending inputs are newly finalized normal-profile nine-case SVGs,
lossless actual default renderer results with matching immutable manifests,
positive fresh strict V5 consumer results, genuine private identity/session
context, actual normal publication receipts, and the real supported export
and final render artifacts. None is fabricated by this preparation.
