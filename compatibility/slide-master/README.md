# Reproducible local converter patch

Upstream: `byungjunjang/slide-master` revision
`166472bd2a22de9aa9fb6c8cdf8b0cdfc6b698ef`. The baseline already includes the
isolated config patch listed in `baseline-manifest.json`; its file hashes are
authoritative. MIT license and source provenance are retained.

Reconstruct into a new temporary directory from this repository's pinned local
baseline (run at repository root, supply a previously unused output path):

```powershell
uv run --project servers/fastapi python compatibility/slide-master/rebuild.py --baseline-revision eba5e731c5fd480b891098395a981567f0fb47b3 --output C:/temp/slide-master-document-canvas
```

Alternatively pass `--source` with a tree matching every baseline manifest hash.
The command verifies all baseline bytes, applies the checked-in patches in
order, and deterministically generates the manifest including the patch hashes. It never
overwrites the active vendor directory. Review changes before copying individual
files into a checkout. The readback test rebuilds in a temporary directory and
compares every manifest-covered byte with the shipped vendor.

Patch application explicitly disables Git autocrlf for that subprocess so local
Windows user configuration cannot change the pinned source bytes. Baseline
hashes are verified before application; no hash or newline mismatch is ignored.

`document-canvas.1` passes a frozen physical context into the public converter.
Serializer helpers read it through a task/thread-local ContextVar, restored in
a finally block. No module scale is modified. Nonlegacy calls support the current
flat native worker path only, reject explicit parallelism, and run serially;
other structures need their own integration evidence before being enabled.
Legacy font quantization remains 0.1pt; new-contract text uses the native OOXML
0.01pt storage grid. SVG/CSS unit parsing and logical layout are unchanged.

`text-advance.1` (applied after `document-canvas-1.patch`; `rebuild.py` lists
the patches in order) lets the caller put an exact text measurer on the
conversion context. A single-line text frame is then never narrower than its
text's advance in the deck's bundled font: LibreOffice ignores `wrap="none"`
and folded frames the per-character estimate made too narrow ("68%" broke
before "%"). The estimate stays the width whenever it is already wider, and
paragraph (`wrap="square"`) frames are unchanged. The worker supplies
`services.svg_text_advance.text_advance`, which answers only for the bundled
families and returns `None` (estimate kept) for anything else.

`xfrm-bounds.1` (applied after `text-advance-1.patch`) makes an axis-aligned
shape trace exactly the frame its `a:xfrm` declares. `rect_to_dml_xfrm` rounded
each corner of the transformed box on its own for the trace bounds, while the
xfrm stores `round(center - w/2)` and `round(w)`; the two could differ by one
EMU, and the worker's native-OOXML verifier requires the trace bounds to equal
`off + ext` (`svg_native_ooxml_trace_geometry_mismatch`). Any shape inside a
`translate` group takes this full-transform path, so a flattened vector icon
(circles at fractional scaled coordinates) failed the whole export in roughly
60% of the library icons. When neither a rotation nor a flip is emitted the
bounds are now `(off_x, off_y, off_x + ext_cx, off_y + ext_cy)`; rotated and
flipped shapes keep their per-corner visual bounds. The vendor manifest carries
the `xfrm-bounds.1` suffix and the new `utils.py` hash, which changes
`converter_digest()`, so old export bundles are rejected with
`svg_export_version_mismatch`. `CONVERTER_VERSION` is not extended: it feeds the
128-character `exporter_version` identity field (`public_artifact.exporter_version`,
`ExportIdentityKey`, `CanonicalExportIdentity`) that the longer string would
overflow; see `docs/explicit-verifier-revisions-design-20261105.md` section 7.

`chart-furniture.16` (native chart manual layouts, per-series data labels,
gap/overlap/vary-colors options, title bold/alignment; table paragraph spacing,
soft breaks and cell padding in `chart_data.py`, `chart_xml.py`, `table.py`) and
`native-fixed-text.1` (the trusted fixed-text adapter on the conversion context
in `conversion_context.py` and `elements.py`) were originally edited into the
vendor tree without a patch file, so the readback test could not reverse the
chain. They are recorded as `chart-furniture-16.patch` and
`native-fixed-text-1.patch`, each derived by diffing the baseline commit plus
the earlier patches against the shipped vendor tree with `xfrm-bounds-1.patch`
reversed. They are applied between `text-advance-1.patch` and
`xfrm-bounds-1.patch`, the order of the shipped version string, and touch
disjoint files. Any new vendor edit needs a patch file and a `PATCHES` entry in
`rebuild.py`; the manifest is generated with LF line endings.

`CONVERTER_VERSION` in `workers/public_svg_pptx_convert.py` is the exporter
identity, not the vendor manifest version: its `native-fixed-text.2` suffix is
the worker-side revision of `f86ae0f67` (v3 template fixed text), which changed
no vendored byte. It is intentionally left unchanged; changing it would change
an adapter file hashed by `converter_digest()` and the `converterVersion` that
bundles must match.

Rebuilding the manifest for those two patches changed only provenance metadata
(`presentonPatches`, `patches`, key order, line endings). The executed vendored
bytes are identical, but `converter_digest()` hashes the manifest bytes, so the
superseded manifest is kept in
`servers/fastapi/workers/svg_converter_superseded_manifests/<sha256>.json` and
registered in `workers/public_svg_converter_aliases.py` (owner decision D5,
option C). Proof and bundle checks accept the digest computed over a
registered superseded manifest only while that manifest's `files` map equals
the current one (whose bytes `converter_digest()` has just verified);
`tests/unit/test_svg_converter_digest_aliases.py` proves the identity and
fails once an entry no longer applies. Adapter files are never aliased.
The alias checks live in files of the whole-page verifier inventory, so adding
them re-pinned `whole_page_verifier_digest()` once, as any verifier edit does:
in-flight v4 bundles and completed-identity reuse are recomputed, while stored
final PPTX render proofs, which do not bind that digest, keep verifying.

`converter_digest()` now carries an explicit execution revision
(`CONVERTER_EXECUTION_REVISION` in `servers/fastapi/services/verifier_revisions.py`,
design in `docs/explicit-verifier-revisions-design-20261105.md`). The old
byte digest is `converter_source_digest()`: it still verifies every vendored
file against the manifest on each call, and it is pinned to the revision by
`tests/unit/test_svg_verifier_revisions.py`. A vendor patch, a manifest
rebuild or an adapter edit fails that test until the new source digest is
recorded in the ledger: under the current revision when the executed behaviour
is unchanged (stored proofs keep verifying; this replaces adding an alias), or
under a bumped revision when it changed (stored proofs are retired). An
unrecorded source state keeps its raw source digest as its identity.

Bundle producers import the worker's converter version and current manifest
digest. Old bundles are rejected with `svg_export_version_mismatch` (a
registered byte-identical alias above is the only exception); reconstruct
an export request under the current producer rather than silently executing an
old identity with new code. A4 writer activation remains gated on reader and
physical output evidence. Rebuilding supply and readback are local operations;
this procedure neither fetches nor publishes upstream changes.
