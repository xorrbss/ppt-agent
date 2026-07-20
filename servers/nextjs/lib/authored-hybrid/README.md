# Authored hybrid H1 contract

This module analyses persisted authored `html_content` without changing the
existing fidelity export path. It does not assemble native PPTX objects.

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
