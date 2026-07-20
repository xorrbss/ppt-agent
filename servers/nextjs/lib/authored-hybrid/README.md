# Authored hybrid PPTX contract

This module analyses persisted authored `html_content` and can assemble a
hybrid PPTX without changing the existing fidelity export path.

H2 consumes two stable entry points from `index.ts`:

- `extractAuthoredSlideDom(html, options)` returns the versioned
  `presenton.authored-hybrid/v1` contract for a fixed 1280x720 CSS-pixel slide.
- `renderAuthoredBackplate(html, slide, promotedElementIds, options)` returns
  `{ backplatePng, appliedPromotedElementIds, fallbackElementIds }`. The RGBA
  PNG hides only identity-verified native candidates that H2 assembled.

The returned contract includes source fingerprint/base URL, deterministic
z-order, pixel and inch geometry, text runs and CJK font fallback, image crop
metadata, simple shapes, and a reason for every raster fallback. H2 must pass
only successfully assembled IDs to `renderAuthoredBackplate`; omitting an ID is
the supported per-element fallback and leaves that element rasterised. If the
DOM path, content, kind, geometry, rotation, or opacity changes between
extraction and capture, that ID is also left visible and reported in
`fallbackElementIds` instead of failing the slide.

The original HTML string and the same `baseUrl` must be supplied for extraction
and backplate rendering. A mismatch fails closed. Chrome/Chromium is discovered
from `chromeExecutable`, `AUTHORED_HYBRID_CHROME_PATH`, or common platform
locations. No database migration or generated presentation-export runtime is
part of this contract.

V1 deliberately rasterises a candidate when it, a descendant, or an ancestor
owns a painted pseudo-element or unrepresentable outer paint. Percentage or
asymmetric corner radii are also raster-only; only uniform pixel radii are
eligible for a native simple shape. These conservative boundaries are part of
the H1 contract, not extraction failures.

## Export modes

The PPTX export API accepts `pptxMode: "fidelity" | "hybrid"`. Missing mode and
explicit `"fidelity"` both use the historical full-slide-image exporter without
entering this module. PDF ignores this PPTX-only option.

`"hybrid"` first produces that fidelity deck as a valid OOXML skeleton. Only
slides identified as authored are considered. Each selected slide is rebuilt
in browser paint order with a transparent 1280x720 PNG backplate at the bottom,
then successfully prepared native text (including rich runs and CJK font
fallback), embedded raster images, and simple shapes. An element is suppressed
from the backplate only after it can be serialized, and H1 must confirm that the
same DOM identity was hidden during capture. Any rejected, changed, unsafe, or
overlap-ambiguous element remains rasterised on the backplate. A slide or deck
failure returns the already-produced fidelity PPTX.

Hybrid execution is fail-closed. HTML is size-bounded and must be static and
self-contained: scripts, event handlers, frames, forms, external/local URLs,
CSS imports, vector data URLs, and active protocols are rejected. Native image
promotion additionally requires a bounded PNG/JPEG/WebP data URL with matching
magic bytes and safe decoded pixels. Archives reject traversal, encryption,
ZIP64, duplicate entries, unsupported compression, and oversized payloads.
Chrome work directories and interim PPTX files are deleted in `finally` paths;
download paths are contained to the configured export directory.
