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
`svg_export_version_mismatch`. `CONVERTER_VERSION` is not extended: it feeds a
160-character identity field that the longer string would overflow.

Bundle producers import the worker's converter version and current manifest
digest. Old bundles are rejected with `svg_export_version_mismatch`; reconstruct
an export request under the current producer rather than silently executing an
old identity with new code. A4 writer activation remains gated on reader and
physical output evidence. Rebuilding supply and readback are local operations;
this procedure neither fetches nor publishes upstream changes.
