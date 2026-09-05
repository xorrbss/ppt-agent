import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

// Native shape/text OOXML geometry is derived entirely from CSS-pixel
// `getBoundingClientRect()` values, which do not change with the Chromium
// deviceScaleFactor used to capture the raster backplate (see
// capture-scale.ts). This test proves that decoupling holds end to end: the
// exact same fixture element list, run through `prepareNativeElements` and
// `assembleAuthoredHybridPptx` twice with `AUTHORED_HYBRID_BACKPLATE_SCALE`
// set to 1 and to 2, must produce byte-identical native `<p:sp>`/`<p:pic>`
// (non-backplate) shape XML. Only the backplate raster PNG itself may change
// size between the two runs.
//
// This does not launch Chrome: `prepareNativeElements`/`assembleAuthoredHybridPptx`
// are pure CSS-px-in, OOXML-out computations, and the backplate PNG is
// synthesized locally with `sharp`. There is nothing to skip here even
// without a Chrome binary on the host.
const WORKER_ENV_FLAG = "__AUTHORED_HYBRID_CAPTURE_SCALE_WORKER__";
const SELF_PATH = fileURLToPath(import.meta.url);

if (process.env[WORKER_ENV_FLAG] === "1") {
  await runWorkerAndExit();
} else {
  test("native shape/text XML is identical at backplate scale 1 and scale 2", () => {
    const atScale1 = runWorker("1");
    const atScale2 = runWorker("2");
    assert.equal(atScale1.status, 0, atScale1.stderr);
    assert.equal(atScale2.status, 0, atScale2.stderr);
    const shapesAtScale1 = JSON.parse(atScale1.stdout);
    const shapesAtScale2 = JSON.parse(atScale2.stdout);
    assert.ok(shapesAtScale1.nonBackplateXml.length > 0);
    assert.equal(shapesAtScale1.nonBackplateXml, shapesAtScale2.nonBackplateXml);
    // The backplate PNG dimensions are the one thing that must differ:
    // scale 2 is exactly double scale 1 in both axes.
    assert.equal(shapesAtScale1.backplateWidth * 2, shapesAtScale2.backplateWidth);
    assert.equal(shapesAtScale1.backplateHeight * 2, shapesAtScale2.backplateHeight);
  });
}

function runWorker(scale) {
  return spawnSync(
    process.execPath,
    [SELF_PATH],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        [WORKER_ENV_FLAG]: "1",
        AUTHORED_HYBRID_BACKPLATE_SCALE: scale,
      },
    }
  );
}

async function runWorkerAndExit() {
  const { prepareNativeElements } = await import("./native-plan.ts");
  const { assembleAuthoredHybridPptx } = await import("./pptx-assembler.ts");
  const { readPptxArchive, writePptxArchive } = await import("./pptx-archive.ts");
  const { backplatePixelSize } = await import("./capture-scale.ts");
  const sharp = (await import("sharp")).default;

  const bounds = (x, y, width, height) => ({
    px: { x, y, width, height },
    inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
  });
  const textStyle = {
    fontFamily: "Malgun Gothic",
    fontFamilies: ["Malgun Gothic", "sans-serif"],
    cjkFallbackFamilies: ["Malgun Gothic", "Noto Sans CJK KR"],
    fontSizePt: 28,
    fontWeight: 700,
    bold: true,
    italic: false,
    underline: false,
    strike: false,
    color: { hex: "112233", alpha: 1 },
    letterSpacingPt: 0,
    lineHeight: { points: 34, multiple: 1.2, source: "computed" },
    horizontalAlignment: "left",
    verticalAlignment: "top",
    direction: "ltr",
  };
  const text = {
    id: "editable-title",
    domPath: "body > h1",
    tagName: "h1",
    sourceIndex: 0,
    zOrder: 1,
    cssZIndex: 1,
    bounds: bounds(80, 60, 600, 100),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "고정 지오메트리",
      paragraphs: ["고정 지오메트리"],
      style: textStyle,
      runs: [],
    },
  };
  const shape = {
    id: "native-shape",
    domPath: "body > div",
    tagName: "div",
    sourceIndex: 1,
    zOrder: 2,
    cssZIndex: 2,
    bounds: bounds(64, 210, 300, 120),
    rotationDeg: 12,
    opacity: 1,
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "round-rectangle",
      fill: { hex: "22AA77", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 8,
    },
  };
  const prepared = await prepareNativeElements([text, shape]);

  const { width: backplateWidth, height: backplateHeight } = backplatePixelSize();
  const backplatePng = await sharp({
    create: {
      width: backplateWidth,
      height: backplateHeight,
      channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  }).png().toBuffer();

  const contentTypes = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>';
  const presentation = '<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>';
  const slide = '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>';
  const rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>';
  const skeleton = writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from(contentTypes)],
    ["ppt/presentation.xml", Buffer.from(presentation)],
    ["ppt/slides/slide1.xml", Buffer.from(slide)],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from(rels)],
  ]));

  const pptx = assembleAuthoredHybridPptx(skeleton, [
    { slideNumber: 1, backplatePng, elements: prepared },
  ]);
  const slideXml = readPptxArchive(pptx).get("ppt/slides/slide1.xml").toString("utf8");
  // Strip the one element whose byte content is expected to vary with scale:
  // the backplate <p:pic> (its embedded relationship id is stable, but this
  // keeps the comparison scoped to native shape/text geometry regardless of
  // any future backplate-picture serialization change).
  const nonBackplateXml = slideXml.replace(
    /<p:pic>(?:(?!<\/p:pic>).)*Presenton hybrid backplate(?:(?!<\/p:pic>).)*<\/p:pic>/s,
    ""
  );

  process.stdout.write(JSON.stringify({ nonBackplateXml, backplateWidth, backplateHeight }));
  process.exit(0);
}
