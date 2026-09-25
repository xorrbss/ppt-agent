# C4 handoff (2026-09-26)

Session `c4-runtime-export`. First 4 commits merged into
`integrate/generation-core` at `50896bcf9` (0 conflicts); 6 more commits
landed afterward while running the live check on raster-quality's V stack
(8100) — **not yet merged, ppt-agent-fe still needs to merge
`900b58edf..33bec3912`**. Source commits, in order:

1. `305f29802` — fix(batch-remake): dispatch batch remake targets by source_format
2. `62d737ce3` — feat(generation-core): resolve svg concurrency from a runtime profile
3. `4d3d36ce8` — feat(svg-export): print SVG decks to a vector PDF with a real text layer
4. `b4d74330f` — feat(svg-export): font embedding, real release-v1 gate, evidence digests
5. `6a1b5bc43` — docs(c4): this handoff memo (first version)
6. `9e82bf6f9` — fix(svg-export): read quality_policy from the command context too
7. `900b58edf` — fix(lifespan): register the durable preview + raster analysis handlers (embedded worker mode)
8. `8e5f843df` — fix(workspace-worker): route batch remake preview/publish to the generation controller (external worker mode, what V runs)
9. `aae0c2314` — fix(export): svg font_embedding no longer requires authored's hybrid mode
10. `33bec3912` — fix(release-v1): read the receipt's actual camelCase wire keys

Full detail (numbers, measured comparisons, field contracts) is in project
memory `c4-runtime-export-state-20260926.md`. This file is the actionable
punch list for whoever picks up the remaining pieces.

## Live check: DONE, all four passed (2026-09-26, on V @ 33bec3912)

Every check was verified against a real request/response on a running
stack, with artifacts downloaded and independently inspected (not just
"the API said success"):

- **Batch remake, 2 svg slides**: `completed: 2, total: 2`. Was stuck
  "queued" for ~30 minutes before commits 900b58edf/8e5f843df (see below);
  after the fix, claimed immediately on restart and finished in 75s.
- **PDF vector text**: downloaded the artifact, extracted text with
  `pypdfium2` independently of the worker's own evidence — real Korean and
  English text came back from both pages.
- **Font embedding**: downloaded the pptx, independently unzipped it and
  confirmed `ppt/fonts/*.fntdata` parts exist with sha256 digests matching
  the receipt, `embedTrueTypeFonts="1"` and `embeddedFontLst` are present in
  `ppt/presentation.xml`, and `python-pptx` opens the file cleanly.
- **release-v1**: rejected with the specific `svg_release_quality_visual_review_not_passed`
  (not the old blanket "unsupported", and not a shape error) — correct,
  expected behavior until c1-quality lands real advisory evidence.

### Four bugs found and fixed only by running the live check (not from reading code)

None of these were in the original brief; all four were found because the
live check actually exercised the real client-facing paths instead of
stopping at "the function I wrote has a unit test":

1. **`quality_policy` unreachable from any real client** (`9e82bf6f9`) — only
   read from `request_payload`'s top level, but the only live path for an
   svg export (`create_workspace_command`) places request fields under
   `request_payload["context"]`. Now reads both.
2. **Batch preview/publish operations admitted, never dispatched** (`900b58edf`,
   `8e5f843df`) — `api/lifespan.py` (embedded worker mode) and
   `services/workspace_operation_worker.py`'s `external_workspace_operation_kinds`
   + `run_external_worker` (external worker mode, what V and presumably
   every real deployment runs) each hand-maintain their own copy of "every
   operation kind's handler registration / routing table", and all of them
   had independently drifted to omit `slide.regenerate.batch.preview` /
   `slide.regenerate.batch`. `register_raster_analysis_operation_handler`
   was also missing from the embedded list (raster-quality confirmed this
   was unintentional and wanted it fixed too). **This is a systemic pattern,
   not a one-off**: three separate hand-copied lists of "every handler",
   found by diffing two of them programmatically
   (`tests/unit/test_lifespan_handler_registration_parity.py`). If another
   operation kind is added in the future without touching all three lists,
   it will silently sit "queued" forever exactly like this one did. A real
   structural fix (making `api/lifespan.py` and `run_external_worker` call
   the one complete list function instead of hand-copying it) is out of
   scope for this session but worth flagging to ppt-agent-fe as a
   follow-up.
3. **svg font_embedding blocked by an authored-only precondition** (`aae0c2314`) —
   `WorkspaceExportOperationHandler` (the one handler for every
   `export.pptx`/`export.pdf`, both authored and svg) validated
   `font_embedding` against `pptx_mode == "hybrid"` *before* branching on
   `presentation.mode`. svg has no `pptx_mode` concept at all, so this made
   every real svg font_embedding request fail in under a second, never
   reaching the svg-side validation this session built in `b4d74330f`.
4. **release-v1 gate read the wrong case** (`33bec3912`) — checked
   `visual_review`/`release_readiness` (the Pydantic model's Python field
   names) instead of `visualReview`/`releaseReadiness` (the actual
   `by_alias=True` wire keys both receipt builders emit). The unit tests
   didn't catch this because the fixture was hand-typed in the same wrong
   case as the bug. Fixed, and added a regression test that exercises the
   real receipt builder function instead of a hand-typed dict.

**Takeaway for future sessions**: a function passing its own unit tests is
not evidence it is reachable or correct end-to-end when its inputs (a
receipt dict, a request payload) are shaped by a different module's
serialization choices. Where possible, build test fixtures from the real
producer function, not a hand-typed literal that can encode the same wrong
assumption the code under test made.

## N1 (native-objects, Phase 2): join wiring this session deliberately left out

`svg_export_operation.py` still does not call `services/public_export_identity.py`'s
single-flight authority (`claim_export_identity` / `complete_export_identity` /
`CanonicalExportIdentity`) at all — it keeps its own inline `_digest()`-based
bundle identity. Scoped down from the original item-5 brief after agreement
with ppt-agent-fe (2026-09-26), specifically to avoid a second Alembic head
next to K1's `f432fc854225` (both chain from `f1c3e5a7b9d2`). What's already
in place for N1 to build on:

- `services/public_export_identity.py::ExportMode` already accepts
  `"svg-native"` (Python-level literal + `__post_init__` validation, plus the
  pdf-format restriction relaxed to allow `svg-native`). **The DB CHECK
  constraint (`ck_public_export_identity_output_mode` on
  `public_export_identities`, table `models/sql/public_artifact.py` ~L279-330)
  does not have `'svg-native'` yet** — add it via `op.batch_alter_table` +
  `drop_constraint`/`create_check_constraint`, following the precedent in
  `alembic/versions/f1c3e5a7b9d2_add_svg_image_artifact_kinds.py`. Chain the
  new migration's `down_revision` from whatever is `main`'s alembic head at
  the time (re-check; `f432fc854225` may have grown a chain by then).
- `services/generation_core/export/svg_evidence.py::build_svg_export_evidence_digests`
  already builds the five `ExportEvidenceDigests` fields (scene/semantic/
  quality/exporter/font) for svg and records them at `receipt["evidenceDigests"]`
  in `svg_export_operation.py::execute_svg_workspace_export`. This is the
  input N1's `CanonicalExportIdentity.build(...)` call needs — it is not
  wired to one yet.
- To actually join: build a `CanonicalExportIdentity` with `export_mode="svg-native"`
  from the evidence digests above plus per-slide `SlideCheckpointArtifact`s
  (svg does not currently store slide SVGs as checkpoint-retention artifacts;
  that's also unbuilt — see the original brief's item 5 text), call
  `claim_export_identity`/`complete_export_identity` around the worker
  dispatch in `render_and_export_svgs`, the way authored's export path does
  (grep `claim_export_identity` for that caller as the pattern to follow).
  Never let svg identify as `"fidelity-raster"` — that value means the old
  raster PDF/PPTX raster path.
- `svg_export_release_gate.py::require_release_v1_receipt` is a receipt-content
  check only (no identity join). Once N1 wires the join, decide whether
  release-v1 should *also* require a successful identity claim — not decided
  by this session.

## c1-quality: field contract already recorded

`quality.visual_review` contract (status enum + `evidence.advisoryDigest`)
is written into the session design folder's `C1-quality.md`. Until c1-quality
implements the real vision-advisory outcome, `visual_review.status` stays
hard-coded `"failed"` in `services/svg_export_quality.py` (~L76), so
release-v1 stays correctly rejected end to end — expected, not a defect.

## Known file-size debt (pre-existing, not fully resolved this session)

- `workers/public_svg_export_worker.py`: 15 ruff errors remain (`E702`
  semicolon statements, `E501` long lines), all above line ~330 — code this
  session was told not to edit (svg-format-specific conversion logic owned
  by the file's original author, not the pdf branch this session touched).
  Was 19 errors before this session (net improvement from the vector-PDF
  work), not zero. Not in `scripts/file-size-ratchet-baseline.json`'s
  exceptions list, so it is invisible to the ratchet gate at its current
  361→358 line count (under the 500-line default), but still not
  ruff-clean. Whoever next edits this file should split the megalines
  first.
- `services/svg_export_operation.py`: 409 lines (was 392 before this
  session; this session added the font_embedding/release-v1/evidence-digest
  wiring and partially mitigated growth by extracting
  `services/svg_export_release_gate.py`). Over the session's own 350-line
  target, under the ratchet's 500-line default. A clean further split:
  extract `render_and_export_svgs`'s bundle-building body (the
  payload-dict construction and asset-staging loop, ~L163-246) into a
  sibling module (`svg_export_bundle.py` or similar), leaving
  `execute_svg_workspace_export`'s admission/receipt orchestration as the
  file's only remaining large function.
- `utils/get_env.py`: 478 lines (was 472; this session only added 6 import
  lines for the new `SVG_*` concurrency re-exports). Pre-existing debt,
  untouched otherwise.

## Files this session created

`services/generation_core/__init__.py`, `services/generation_core/runtime/`
(`__init__.py`, `profile.py`), `services/generation_core/export/`
(`__init__.py`, `svg_evidence.py`), `services/svg_export_release_gate.py`,
`services/svg_pptx_font_embedding.py`, `utils/slide_capture_pipe_pdf.py`,
`workers/public_svg_pdf_export.py`, plus test files
`test_generation_core_runtime_profile.py`,
`test_svg_export_release_and_font_embedding_guards.py`,
`test_svg_export_evidence_digests.py`, `test_svg_pptx_font_embedding.py`,
`test_svg_vector_pdf_export.py`.
