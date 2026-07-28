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

Hybrid execution is fail-closed. Before preflight, the server may collect
Google Fonts referenced by a `fonts.googleapis.com` stylesheet link or CSS
`@import`. Collection requires HTTPS, exact allow-listed hosts, validated
redirects, MIME and font magic agreement, bounded response/file/total sizes,
and an 8-second timeout. Successful `fonts.gstatic.com` assets are converted to
validated font data URLs; collection failure removes the Google reference and
uses the local fallback stack. No other remote CSS or font host is accepted.
Chrome remains fully offline behind its deny proxy.

The central compatibility policy is used for both captured CSS layout and
native OOXML typeface names: Noto Sans KR and Pretendard use Malgun Gothic,
Noto Serif KR uses Batang, Inter/Roboto/DM Sans use Aptos, Source Serif 4 uses
Cambria, and IBM Plex Mono uses Consolas. This scope covers rendering and
typeface fallback only. Fonts are not embedded in the PPTX/OOXML, so a document
opened on another machine uses its locally installed compatible typeface.

Font-family order is intentional. The source-fidelity render keeps a collected
authored face first. Editable text measurement has a separate mapped-first
stack (`expandPowerPointLayoutFontFamilyStack`) so a native-layout pass can
measure glyph widths and line breaks with the same local face written to OOXML,
while retaining the authored face as a fallback. Export performs these as two
isolated extractions and merges only identity-matched text metrics into the
native layer; classification, paint order, and the fidelity backplate remain
anchored to the source extraction. Callers must not use the mapped-first stack
for the source-fidelity backplate.

After collection, HTML is size-bounded and must be static and self-contained:
scripts, event handlers, frames, forms, remaining external/local URLs, CSS
imports, vector data URLs, and active protocols are rejected. Native image
promotion additionally requires a bounded PNG/JPEG/WebP data URL with matching
magic bytes and safe decoded pixels. Collected font data URLs are independently
bounded and magic-validated. Archives reject traversal, encryption, ZIP64,
duplicate entries, unsupported compression, and oversized payloads. Chrome
work directories and interim PPTX files are deleted in `finally` paths;
download paths are contained to the configured export directory.

## Optional export quality metadata

Hybrid export responses may include additive `quality` metadata using
`presenton.export-quality/v1`; existing callers can continue reading only
`path`. The report contains overall slide counts, native
text/shape/group/image counts, image-fallback slide counts, element-level
fallback ids/reasons when known, and deterministic aggregate reason counts.
The added fields are optional for compatibility with previously cached v1
sidecars.

The same report is retained with the in-process hybrid cache and written beside
the generated PPTX as `<file>.pptx.quality.json`. The completion UI warns when
`status` is not `fully-editable`, or when the independent raster/image fallback
counts contradict that status.

This scope validates rendered font fallback and native PowerPoint typefaces. It
keeps OOXML font embedding default-off and exposes the explicit opt-in request,
application result, embedded-file count, and failure reason separately. A
request must never be reported as applied unless the exported PPTX actually
contains embedded font files.

## PowerPoint Desktop calibration and A/B verification

`scripts/powerpoint-desktop-calibration.ps1` is an operator-run Windows probe
for the final renderer. It copies its input before opening it, opens A, asks
PowerPoint Desktop for a B `SaveAs`, then reopens B. It writes a JSON report for
every outcome and does not claim success when COM or a reopen fails.

Run it with a new artifact directory outside `app_data`:

```powershell
./scripts/powerpoint-desktop-calibration.ps1 `
  -InputPptx C:\exports\deck.pptx `
  -OutputDirectory C:\qa-artifacts
```

The tool fails closed when `POWERPNT.EXE` is already running; it neither
attaches to nor closes a user's PowerPoint session. When safely isolated, it
also records 128 Noto Sans KR probes across Regular/Bold, font-size, single or
multiline, left or centered, and fixed or content-width buckets. Raw COM text
bounds are written in points. `buildPowerPointCalibrationProbes` and
`derivePowerPointCalibrationProfiles` expose the same keyed matrix to the
native exporter; consumers must preserve all dimensions rather than using one
global font correction.
